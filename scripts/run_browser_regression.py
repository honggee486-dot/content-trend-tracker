from __future__ import annotations

import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
COPY_IGNORE_PATTERNS = (
    ".git",
    ".venv",
    "venv",
    "env",
    "data",
    "exports",
    "logs",
    "log",
    "reports",
    "backups",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    ".env",
    ".env.*",
    "secrets.toml",
    "secrets*.toml",
    "*oauth*.json",
    "*credentials*.json",
    "*.duckdb",
    "*.duckdb.wal",
    "*.db",
    "*.sqlite*",
    "*.parquet",
    "*.feather",
    "*.arrow",
    "*.log",
    "*.zip",
)
SECRET_ENV_MARKERS = (
    "API_KEY",
    "SECRET",
    "TOKEN",
    "PASSWORD",
    "COOKIE",
    "OAUTH",
    "GEMINI",
    "NAVER",
    "KAKAO",
)
PAGE_CONTROLS = {
    "AI 요청서": "AI 요청서",
    "AI 결과 가져오기": "AI 결과",
    "글 편집": "글 편집",
    "발행 보조": "발행 보조",
}


def _copy_isolated_project(destination: Path) -> Path:
    isolated_root = destination / "repository"
    shutil.copytree(
        PROJECT_ROOT,
        isolated_root,
        ignore=shutil.ignore_patterns(*COPY_IGNORE_PATTERNS),
    )
    (isolated_root / "data").mkdir(parents=True, exist_ok=True)
    return isolated_root


def _sanitized_environment(project_root: Path) -> dict[str, str]:
    env = {
        key: value
        for key, value in os.environ.items()
        if not any(marker in key.upper() for marker in SECRET_ENV_MARKERS)
    }
    env["PYTHONPATH"] = str(project_root)
    env["PYTHON_DOTENV_DISABLED"] = "1"
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    return env


def _prepare_isolated_database(project_root: Path, env: dict[str, str]) -> None:
    seed_code = r'''
from datetime import datetime

from src.config import DEFAULT_DB_PATH
from src.database import connect_database, init_database

init_database(DEFAULT_DB_PATH)
now = datetime(2026, 8, 8, 15, 0, 0)
with connect_database(DEFAULT_DB_PATH) as con:
    con.execute(
        """
        INSERT INTO topics(
            topic_id, title, normalized_title, summary, category, status,
            priority, is_interested, memo, source_count,
            first_seen_at, last_seen_at, created_at, updated_at, archived_at
        ) VALUES (
            'browser_topic', '브라우저 회귀검증 주제', '브라우저 회귀검증 주제',
            '격리 환경에서 제작 화면 이동을 검증하는 테스트 주제', '테스트',
            'ai_ready', 3, TRUE, '', 0, ?, ?, ?, ?, NULL
        )
        """,
        [now, now, now, now],
    )
    con.execute(
        """
        INSERT INTO topic_references(
            reference_id, topic_id, reference_type, title, publisher,
            url, normalized_url, published_at, memo,
            created_at, updated_at, archived_at
        ) VALUES (
            'browser_reference', 'browser_topic', 'official',
            '브라우저 테스트 참고 자료', '테스트 기관',
            'https://example.com/browser-regression',
            'https://example.com/browser-regression', '2026-08-08',
            '격리 브라우저 회귀검증에서만 사용하는 참고 자료입니다.',
            ?, ?, NULL
        )
        """,
        [now, now],
    )
'''
    subprocess.run(
        [sys.executable, "-c", seed_code],
        cwd=project_root,
        env=env,
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )


def _reserve_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _wait_for_health(base_url: str, process: subprocess.Popen[bytes]) -> None:
    deadline = time.monotonic() + 60.0
    health_url = f"{base_url}/_stcore/health"
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError(
                f"Streamlit exited before becoming healthy: {process.returncode}"
            )
        try:
            with urllib.request.urlopen(health_url, timeout=2) as response:
                if response.status == 200:
                    return
        except OSError:
            pass
        time.sleep(0.5)
    raise TimeoutError(f"Streamlit health check timed out: {health_url}")


def _stop_process(process: subprocess.Popen[bytes]) -> None:
    if process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)


def _assert_healthy_page(page, expected_title: str | None = None) -> None:
    page.locator('[data-testid="stApp"]').wait_for(timeout=60_000)
    title = page.locator(".app-page-title-text")
    title.first.wait_for(state="visible", timeout=60_000)
    page.wait_for_function(
        "document.querySelectorAll('.app-page-title-text').length === 1",
        timeout=60_000,
    )
    if expected_title:
        page.wait_for_function(
            "expected => document.querySelector('.app-page-title-text')?.textContent.trim() === expected",
            arg=expected_title,
            timeout=60_000,
        )
    body = page.locator("body").inner_text()
    for marker in (
        "Traceback (most recent call last)",
        "streamlit.errors.StreamlitAPIException",
        "This app has encountered an error",
    ):
        if marker in body:
            raise AssertionError(f"Streamlit error marker found: {marker}")
    if page.locator('[data-testid="stException"]').count():
        raise AssertionError("Streamlit exception element is visible")


def _click_control(page, label: str) -> None:
    candidates = (
        page.get_by_role("button", name=label, exact=True),
        page.get_by_role("tab", name=label, exact=True),
        page.get_by_role("radio", name=label, exact=True),
        page.get_by_text(label, exact=True),
    )
    for locator in candidates:
        for index in range(locator.count()):
            item = locator.nth(index)
            if item.is_visible():
                item.click()
                return
    visible_buttons = page.locator("button:visible").all_inner_texts()
    raise AssertionError(f"control not found: {label}; buttons={visible_buttons}")


def _run_browser(base_url: str, expected_version: str) -> None:
    try:
        from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise RuntimeError(
            "Playwright가 필요합니다. CI에서는 브라우저 회귀 workflow가 별도로 설치합니다."
        ) from exc

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        context = browser.new_context(viewport={"width": 1600, "height": 1100})
        page = context.new_page()
        try:
            page.goto(base_url, wait_until="domcontentloaded", timeout=60_000)
            _assert_healthy_page(page, "오늘의 트렌드")
            expected_browser_title = f"콘텐츠 트렌드 트래커 · v{expected_version}"
            if page.title() != expected_browser_title:
                raise AssertionError(
                    f"unexpected browser title: {page.title()!r}; "
                    f"expected {expected_browser_title!r}"
                )

            expander = page.locator('[data-testid="stExpander"] summary').filter(
                has_text="AI 요청서 준비"
            )
            expander.wait_for(state="visible", timeout=30_000)
            if expander.count() != 1:
                raise AssertionError(f"unexpected request-ready expander count: {expander.count()}")
            expander.click()
            _click_control(page, "AI 요청서 만들기")
            _assert_healthy_page(page, "AI 요청서")

            for _ in range(2):
                for target, control_label in PAGE_CONTROLS.items():
                    _click_control(page, control_label)
                    _assert_healthy_page(page, target)

            page.reload(wait_until="domcontentloaded", timeout=60_000)
            _assert_healthy_page(page)
            page.goto(
                f"{base_url}/?browser_regression=1",
                wait_until="domcontentloaded",
                timeout=60_000,
            )
            _assert_healthy_page(page)
            try:
                page.go_back(wait_until="domcontentloaded", timeout=15_000)
                _assert_healthy_page(page)
                page.go_forward(wait_until="domcontentloaded", timeout=15_000)
                _assert_healthy_page(page)
            except PlaywrightTimeoutError as exc:
                raise AssertionError("back/forward navigation timed out") from exc
        except Exception:
            print(f"[browser-regression] URL: {page.url}")
            print(f"[browser-regression] TITLE: {page.title()}")
            print("[browser-regression] BODY:")
            print(page.locator("body").inner_text()[:12000])
            raise
        finally:
            browser.close()


def main() -> int:
    version = (PROJECT_ROOT / "VERSION").read_text(encoding="utf-8-sig").strip()
    with tempfile.TemporaryDirectory(prefix="content-trend-browser-") as temp_dir:
        isolated_root = _copy_isolated_project(Path(temp_dir))
        port = _reserve_port()
        base_url = f"http://127.0.0.1:{port}"
        log_path = Path(temp_dir) / "streamlit-browser.log"
        env = _sanitized_environment(isolated_root)
        _prepare_isolated_database(isolated_root, env)
        command = [
            sys.executable,
            "-m",
            "streamlit",
            "run",
            "app.py",
            "--server.headless",
            "true",
            "--server.address",
            "127.0.0.1",
            "--server.port",
            str(port),
            "--browser.gatherUsageStats",
            "false",
        ]
        with log_path.open("wb") as log_file:
            process = subprocess.Popen(
                command,
                cwd=isolated_root,
                env=env,
                stdout=log_file,
                stderr=subprocess.STDOUT,
            )
            try:
                _wait_for_health(base_url, process)
                _run_browser(base_url, version)
            except Exception:
                log_file.flush()
                print("[browser-regression] Streamlit log:")
                print(log_path.read_text(encoding="utf-8", errors="replace")[-12000:])
                raise
            finally:
                _stop_process(process)

    print("[browser-regression] PASS: isolated Streamlit navigation regression")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
