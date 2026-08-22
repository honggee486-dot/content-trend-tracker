from __future__ import annotations

import base64
import hashlib
import ipaddress
import json
import os
import socket
import subprocess
import tempfile
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence
from urllib.parse import urlparse
import urllib.request

from src.services.content_pack_image_acquisition_service import (
    build_image_acquisition_plans,
)

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CAPTURE_OUTPUT_DIR = PROJECT_ROOT / "exports" / "captures"
PUBLIC_CAPTURE_BLOCKED_PATH_MARKERS = (
    "/login",
    "/signin",
    "/sign-in",
    "/auth",
    "/oauth",
    "/account",
    "/billing",
    "/dashboard",
    "/admin",
    "/administrator",
    "/console",
)
_MINIMAL_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUB"
    "AScY42YAAAAASUVORK5CYII="
)


def _now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat()


def validate_public_capture_url(
    value: object,
    *,
    resolver: Callable[..., Sequence[tuple[Any, ...]]] = socket.getaddrinfo,
) -> tuple[bool, str]:
    text = str(value or "").strip()
    if not text:
        return False, "공식 페이지 URL이 없습니다."

    parsed = urlparse(text)
    if parsed.scheme.casefold() not in {"http", "https"} or not parsed.hostname:
        return False, "http 또는 https 공개 페이지 URL이 아닙니다."
    if parsed.username or parsed.password:
        return False, "인증정보가 포함된 URL은 자동 캡처하지 않습니다."

    host = parsed.hostname.casefold()
    if host == "localhost" or host.endswith(".localhost") or host.endswith(".local"):
        return False, "로컬 주소는 자동 캡처 대상이 아닙니다."

    try:
        direct_ip = ipaddress.ip_address(host)
    except ValueError:
        direct_ip = None
    if direct_ip is not None and not direct_ip.is_global:
        return False, "사설·로컬·예약 IP 주소는 자동 캡처하지 않습니다."

    if direct_ip is None:
        try:
            resolved = resolver(host, None, socket.AF_UNSPEC, socket.SOCK_STREAM)
        except OSError:
            return False, "도메인의 공개 IP 주소를 확인할 수 없습니다."
        resolved_ips: set[str] = set()
        for item in resolved:
            try:
                address_text = str(item[4][0])
                address = ipaddress.ip_address(address_text)
            except (IndexError, TypeError, ValueError):
                continue
            resolved_ips.add(address_text)
            if not address.is_global:
                return False, f"도메인이 사설·로컬·예약 IP({address_text})로 해석되어 자동 캡처하지 않습니다."
        if not resolved_ips:
            return False, "도메인의 공개 IP 주소를 확인할 수 없습니다."

    path = parsed.path.casefold()
    for marker in PUBLIC_CAPTURE_BLOCKED_PATH_MARKERS:
        normalized = marker.rstrip("/")
        if path == normalized or path.startswith(normalized + "/"):
            return False, "로그인·계정·관리자 성격의 URL은 자동 캡처하지 않습니다."
    return True, ""


@dataclass(frozen=True)
class CapturePlan:
    source_id: str
    source_url: str
    capture_target: str
    capture_anchor: str
    capture_note: str = ""
    checked_at: str = ""
    output_dir: Path | str | None = None
    timeout_seconds: float = 20.0


@dataclass(frozen=True)
class CaptureResult:
    status: str
    source_id: str = ""
    source_url: str = ""
    final_url: str = ""
    page_title: str = ""
    capture_target: str = ""
    capture_anchor: str = ""
    captured_at: str = ""
    image_path: str = ""
    image_format: str = "png"
    dimensions: dict[str, int] = field(default_factory=dict)
    clip_rect: dict[str, float] = field(default_factory=dict)
    provenance: dict[str, Any] = field(default_factory=dict)
    review_reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class CaptureExecutor:
    def capture_public_source(self, plan: CapturePlan) -> CaptureResult:
        raise NotImplementedError


class UnavailableCaptureExecutor(CaptureExecutor):
    def __init__(self, reason: str) -> None:
        self.reason = str(reason or "실행 가능한 격리 브라우저를 사용할 수 없습니다.")

    def capture_public_source(self, plan: CapturePlan) -> CaptureResult:
        return CaptureResult(
            status="needs_review",
            source_id=plan.source_id,
            source_url=plan.source_url,
            capture_target=plan.capture_target,
            capture_anchor=plan.capture_anchor,
            captured_at=_now_iso(),
            review_reason=self.reason,
        )


class FakeCaptureExecutor(CaptureExecutor):
    def __init__(
        self,
        *,
        forced_status: str = "success",
        forced_reason: str = "",
        url_validator: Callable[[object], tuple[bool, str]] | None = None,
    ) -> None:
        self.forced_status = forced_status
        self.forced_reason = forced_reason
        self.url_validator = url_validator or validate_public_capture_url
        self.captured_plans: list[CapturePlan] = []

    def capture_public_source(self, plan: CapturePlan) -> CaptureResult:
        self.captured_plans.append(plan)
        safe, reason = self.url_validator(plan.source_url)
        if not safe:
            return CaptureResult(
                status="needs_review",
                source_id=plan.source_id,
                source_url=plan.source_url,
                capture_target=plan.capture_target,
                capture_anchor=plan.capture_anchor,
                captured_at=_now_iso(),
                review_reason=f"보안 정책 제외: {reason}",
            )
        if not plan.capture_anchor.strip():
            return CaptureResult(
                status="needs_review",
                source_id=plan.source_id,
                source_url=plan.source_url,
                capture_target=plan.capture_target,
                captured_at=_now_iso(),
                review_reason="anchor_not_found: capture_anchor가 없습니다.",
            )
        if self.forced_status != "success":
            return CaptureResult(
                status=self.forced_status,
                source_id=plan.source_id,
                source_url=plan.source_url,
                final_url=plan.source_url,
                capture_target=plan.capture_target,
                capture_anchor=plan.capture_anchor,
                captured_at=_now_iso(),
                review_reason=self.forced_reason or f"강제 {self.forced_status} 상태",
            )

        output_dir = Path(plan.output_dir or DEFAULT_CAPTURE_OUTPUT_DIR)
        output_dir.mkdir(parents=True, exist_ok=True)
        digest = hashlib.sha256(
            f"{plan.source_url}:{plan.capture_anchor}".encode("utf-8")
        ).hexdigest()[:12]
        image_path = output_dir / f"fake_capture_{digest}.png"
        image_path.write_bytes(_MINIMAL_PNG)
        captured_at = _now_iso()
        file_hash = hashlib.sha256(image_path.read_bytes()).hexdigest()
        provenance = {
            "source_id": plan.source_id,
            "source_url": plan.source_url,
            "final_url": plan.source_url,
            "page_title": "Fake public capture",
            "capture_target": plan.capture_target,
            "capture_anchor": plan.capture_anchor,
            "captured_at": captured_at,
            "browser_engine": "fake",
            "region_locator": "test_fixture",
            "sha256": file_hash,
            "safety_checked": True,
        }
        return CaptureResult(
            status="success",
            source_id=plan.source_id,
            source_url=plan.source_url,
            final_url=plan.source_url,
            page_title="Fake public capture",
            capture_target=plan.capture_target,
            capture_anchor=plan.capture_anchor,
            captured_at=captured_at,
            image_path=str(image_path),
            dimensions={"width": 1, "height": 1},
            clip_rect={"x": 0.0, "y": 0.0, "width": 1.0, "height": 1.0},
            provenance=provenance,
        )


def _browser_candidates() -> list[Path]:
    candidates: list[Path] = []
    custom = (
        os.environ.get("CONTENT_TREND_TRACKER_CHROME_PATH")
        or os.environ.get("BROWSER_EXECUTABLE")
    )
    if custom and Path(custom).is_file():
        candidates.append(Path(custom))

    roots = (
        Path(os.environ.get("PROGRAMFILES", r"C:\Program Files")),
        Path(os.environ.get("PROGRAMFILES(X86)", r"C:\Program Files (x86)")),
        Path(os.environ.get("LOCALAPPDATA", "")),
    )
    relative_paths = (
        Path("Google/Chrome/Application/chrome.exe"),
        Path("Microsoft/Edge/Application/msedge.exe"),
    )
    for root in roots:
        if not str(root):
            continue
        for relative in relative_paths:
            candidate = root / relative
            if candidate.is_file() and candidate not in candidates:
                candidates.append(candidate)
    return candidates


class _CdpSession:
    def __init__(
        self,
        websocket,
        *,
        url_validator: Callable[[object], tuple[bool, str]],
    ) -> None:
        self.websocket = websocket
        self.url_validator = url_validator
        self._next_id = 0
        self.blocked_document_reason = ""

    def _allocate_id(self) -> int:
        self._next_id += 1
        return self._next_id

    def _send_without_wait(self, method: str, params: Mapping[str, Any]) -> None:
        command_id = self._allocate_id()
        self.websocket.send(
            json.dumps(
                {"id": command_id, "method": method, "params": dict(params)},
                ensure_ascii=False,
            )
        )

    def _handle_event(self, message: Mapping[str, Any]) -> None:
        if message.get("method") != "Fetch.requestPaused":
            return
        params = message.get("params")
        if not isinstance(params, Mapping):
            return
        request = params.get("request")
        request_id = str(params.get("requestId") or "")
        if not isinstance(request, Mapping) or not request_id:
            return
        request_url = str(request.get("url") or "")
        safe, reason = self.url_validator(request_url)
        if safe:
            self._send_without_wait(
                "Fetch.continueRequest",
                {"requestId": request_id},
            )
            return

        self._send_without_wait(
            "Fetch.failRequest",
            {"requestId": request_id, "errorReason": "Aborted"},
        )
        if str(params.get("resourceType") or "") == "Document":
            self.blocked_document_reason = reason or "안전하지 않은 문서 요청을 차단했습니다."

    def call(
        self,
        method: str,
        params: Mapping[str, Any] | None = None,
        *,
        timeout_seconds: float = 10.0,
    ) -> dict[str, Any]:
        command_id = self._allocate_id()
        self.websocket.send(
            json.dumps(
                {"id": command_id, "method": method, "params": dict(params or {})},
                ensure_ascii=False,
            )
        )
        deadline = time.monotonic() + timeout_seconds
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError(f"CDP 응답 대기 시간 초과: {method}")
            raw = self.websocket.recv(timeout=remaining)
            message = json.loads(raw)
            if not isinstance(message, Mapping):
                continue
            if "method" in message:
                self._handle_event(message)
                continue
            if message.get("id") != command_id:
                continue
            if "error" in message:
                raise RuntimeError(f"CDP 오류 ({method}): {message['error']}")
            result = message.get("result")
            return dict(result) if isinstance(result, Mapping) else {}

    def pump(self, duration_seconds: float) -> None:
        deadline = time.monotonic() + max(0.0, duration_seconds)
        while time.monotonic() < deadline:
            remaining = min(0.1, max(0.0, deadline - time.monotonic()))
            if remaining <= 0:
                break
            try:
                raw = self.websocket.recv(timeout=remaining)
            except TimeoutError:
                continue
            message = json.loads(raw)
            if isinstance(message, Mapping) and "method" in message:
                self._handle_event(message)


class HeadlessCdpCaptureExecutor(CaptureExecutor):
    def __init__(
        self,
        browser_path: str | Path | None = None,
        *,
        url_validator: Callable[[object], tuple[bool, str]] | None = None,
    ) -> None:
        if browser_path:
            selected = Path(browser_path)
            if not selected.is_file():
                raise RuntimeError("지정한 Chrome/Edge 실행 파일을 찾을 수 없습니다.")
            self.browser_path = selected
        else:
            candidates = _browser_candidates()
            if not candidates:
                raise RuntimeError("실행 가능한 Chrome 또는 Edge 브라우저를 찾을 수 없습니다.")
            self.browser_path = candidates[0]
        self.url_validator = url_validator or validate_public_capture_url

    def capture_public_source(self, plan: CapturePlan) -> CaptureResult:
        safe, reason = self.url_validator(plan.source_url)
        if not safe:
            return CaptureResult(
                status="needs_review",
                source_id=plan.source_id,
                source_url=plan.source_url,
                capture_target=plan.capture_target,
                capture_anchor=plan.capture_anchor,
                captured_at=_now_iso(),
                review_reason=f"보안 정책 제외: {reason}",
            )
        anchor = plan.capture_anchor.strip()
        if not anchor:
            return CaptureResult(
                status="needs_review",
                source_id=plan.source_id,
                source_url=plan.source_url,
                capture_target=plan.capture_target,
                captured_at=_now_iso(),
                review_reason="anchor_not_found: capture_anchor가 없습니다.",
            )

        try:
            from websockets.sync.client import connect
        except ImportError as exc:
            return CaptureResult(
                status="needs_review",
                source_id=plan.source_id,
                source_url=plan.source_url,
                capture_target=plan.capture_target,
                capture_anchor=anchor,
                captured_at=_now_iso(),
                review_reason=f"브라우저 CDP 의존성을 사용할 수 없습니다: {exc}",
            )

        temp_profile = tempfile.TemporaryDirectory(prefix="ctt_public_capture_")
        process: subprocess.Popen[bytes] | None = None
        browser_ws = None
        page_ws = None
        try:
            command = [
                str(self.browser_path),
                "--headless=new",
                "--remote-debugging-port=0",
                f"--user-data-dir={temp_profile.name}",
                "--no-first-run",
                "--no-default-browser-check",
                "--disable-background-networking",
                "--disable-sync",
                "--disable-extensions",
                "--disable-default-apps",
                "--window-size=1280,960",
                "about:blank",
            ]
            process = subprocess.Popen(
                command,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            port_file = Path(temp_profile.name) / "DevToolsActivePort"
            deadline = time.monotonic() + min(max(plan.timeout_seconds, 5.0), 30.0)
            while not port_file.exists():
                if process.poll() is not None:
                    raise RuntimeError("격리 브라우저가 초기화 중 종료됐습니다.")
                if time.monotonic() >= deadline:
                    raise TimeoutError("브라우저 DevTools 포트 준비 시간이 초과됐습니다.")
                time.sleep(0.05)

            port_lines = port_file.read_text(encoding="utf-8").splitlines()
            port = int(port_lines[0])
            with urllib.request.urlopen(
                f"http://127.0.0.1:{port}/json/version",
                timeout=5,
            ) as response:
                browser_info = json.loads(response.read().decode("utf-8"))
            browser_socket_url = str(browser_info.get("webSocketDebuggerUrl") or "")
            if not browser_socket_url:
                raise RuntimeError("브라우저 CDP WebSocket 주소를 확인할 수 없습니다.")

            browser_ws = connect(browser_socket_url, open_timeout=5, close_timeout=2)
            browser_session = _CdpSession(
                browser_ws,
                url_validator=self.url_validator,
            )
            create_target = browser_session.call(
                "Target.createTarget",
                {"url": "about:blank"},
            )
            target_id = str(create_target.get("targetId") or "")
            if not target_id:
                raise RuntimeError("CDP 대상 페이지를 만들 수 없습니다.")

            page_socket_url = f"ws://127.0.0.1:{port}/devtools/page/{target_id}"
            page_ws = connect(page_socket_url, open_timeout=5, close_timeout=2)
            page_session = _CdpSession(
                page_ws,
                url_validator=self.url_validator,
            )
            page_session.call("Page.enable")
            page_session.call("Runtime.enable")
            page_session.call(
                "Fetch.enable",
                {
                    "patterns": [
                        {"urlPattern": "http://*/*", "requestStage": "Request"},
                        {"urlPattern": "https://*/*", "requestStage": "Request"},
                    ]
                },
            )
            navigation = page_session.call(
                "Page.navigate",
                {"url": plan.source_url},
                timeout_seconds=min(max(plan.timeout_seconds, 5.0), 30.0),
            )
            if navigation.get("errorText"):
                raise RuntimeError(f"페이지 이동 실패: {navigation['errorText']}")

            page_session.pump(min(max(plan.timeout_seconds / 4.0, 1.0), 3.0))
            if page_session.blocked_document_reason:
                return CaptureResult(
                    status="needs_review",
                    source_id=plan.source_id,
                    source_url=plan.source_url,
                    capture_target=plan.capture_target,
                    capture_anchor=anchor,
                    captured_at=_now_iso(),
                    review_reason=(
                        "리다이렉트 또는 문서 요청을 안전 정책으로 차단했습니다: "
                        + page_session.blocked_document_reason
                    ),
                )

            url_result = page_session.call(
                "Runtime.evaluate",
                {"expression": "window.location.href", "returnByValue": True},
            )
            final_url = str(
                url_result.get("result", {}).get("value") or plan.source_url
            )
            final_safe, final_reason = self.url_validator(final_url)
            if not final_safe:
                return CaptureResult(
                    status="needs_review",
                    source_id=plan.source_id,
                    source_url=plan.source_url,
                    final_url=final_url,
                    capture_target=plan.capture_target,
                    capture_anchor=anchor,
                    captured_at=_now_iso(),
                    review_reason=f"최종 URL 안전 검사 실패: {final_reason}",
                )

            title_result = page_session.call(
                "Runtime.evaluate",
                {"expression": "document.title", "returnByValue": True},
            )
            page_title = str(
                title_result.get("result", {}).get("value") or ""
            ).strip()

            anchor_json = json.dumps(anchor, ensure_ascii=False)
            locate_script = f"""
            (() => {{
              const query = String({anchor_json} || "").trim().toLowerCase();
              if (!query) return {{status: "not_found", count: 0}};
              const root = document.body || document.documentElement;
              const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
              const matched = [];
              let node;
              while ((node = walker.nextNode())) {{
                const text = String(node.nodeValue || "").trim().toLowerCase();
                if (!text || !text.includes(query)) continue;
                const element = node.parentElement;
                if (!element) continue;
                const style = getComputedStyle(element);
                const rect = element.getBoundingClientRect();
                if (
                  style.display === "none" ||
                  style.visibility === "hidden" ||
                  style.opacity === "0" ||
                  rect.width <= 0 ||
                  rect.height <= 0
                ) continue;
                matched.push(element);
              }}
              if (matched.length === 0) return {{status: "not_found", count: 0}};
              const semantic = (element) => {{
                return element.closest(
                  "table,section,article,figure,main,"
                  + "div.table-responsive,div[class*='table'],"
                  + "div[class*='pricing'],div[class*='card']"
                ) || element;
              }};
              const unique = Array.from(new Set(matched.map(semantic)));
              if (unique.length !== 1) {{
                return {{status: "ambiguous", count: unique.length}};
              }}
              const target = unique[0];
              target.scrollIntoView({{block: "center", inline: "nearest"}});
              const rect = target.getBoundingClientRect();
              const pad = 12;
              return {{
                status: "found",
                count: 1,
                rect: {{
                  x: Math.max(0, rect.left + window.scrollX - pad),
                  y: Math.max(0, rect.top + window.scrollY - pad),
                  width: Math.min(1920, Math.max(100, rect.width + pad * 2)),
                  height: Math.min(2400, Math.max(60, rect.height + pad * 2)),
                  scale: 1
                }}
              }};
            }})()
            """
            locate_result = page_session.call(
                "Runtime.evaluate",
                {"expression": locate_script, "returnByValue": True},
            )
            located = locate_result.get("result", {}).get("value")
            if not isinstance(located, Mapping):
                located = {}
            if located.get("status") == "not_found":
                return CaptureResult(
                    status="needs_review",
                    source_id=plan.source_id,
                    source_url=plan.source_url,
                    final_url=final_url,
                    page_title=page_title,
                    capture_target=plan.capture_target,
                    capture_anchor=anchor,
                    captured_at=_now_iso(),
                    review_reason=f"anchor_not_found: '{anchor}' 문구를 찾을 수 없습니다.",
                )
            if located.get("status") == "ambiguous":
                return CaptureResult(
                    status="needs_review",
                    source_id=plan.source_id,
                    source_url=plan.source_url,
                    final_url=final_url,
                    page_title=page_title,
                    capture_target=plan.capture_target,
                    capture_anchor=anchor,
                    captured_at=_now_iso(),
                    review_reason=(
                        f"anchor_ambiguous: '{anchor}' 문구가 서로 다른 "
                        f"{int(located.get('count') or 0)}개 영역에서 발견됐습니다."
                    ),
                )

            clip = located.get("rect")
            if not isinstance(clip, Mapping):
                return CaptureResult(
                    status="needs_review",
                    source_id=plan.source_id,
                    source_url=plan.source_url,
                    final_url=final_url,
                    page_title=page_title,
                    capture_target=plan.capture_target,
                    capture_anchor=anchor,
                    captured_at=_now_iso(),
                    review_reason="capture_region_missing: 캡처 영역을 결정할 수 없습니다.",
                )
            clip_rect = {
                "x": float(clip.get("x") or 0),
                "y": float(clip.get("y") or 0),
                "width": float(clip.get("width") or 0),
                "height": float(clip.get("height") or 0),
                "scale": 1.0,
            }
            screenshot = page_session.call(
                "Page.captureScreenshot",
                {
                    "format": "png",
                    "captureBeyondViewport": True,
                    "clip": clip_rect,
                },
            )
            encoded = str(screenshot.get("data") or "")
            if not encoded:
                return CaptureResult(
                    status="needs_review",
                    source_id=plan.source_id,
                    source_url=plan.source_url,
                    final_url=final_url,
                    page_title=page_title,
                    capture_target=plan.capture_target,
                    capture_anchor=anchor,
                    captured_at=_now_iso(),
                    review_reason="screenshot_failed: 캡처 데이터가 비어 있습니다.",
                )

            image_bytes = base64.b64decode(encoded)
            output_dir = Path(plan.output_dir or DEFAULT_CAPTURE_OUTPUT_DIR)
            output_dir.mkdir(parents=True, exist_ok=True)
            stable = hashlib.sha256(
                f"{plan.source_id}:{final_url}:{anchor}".encode("utf-8")
            ).hexdigest()[:16]
            image_path = output_dir / f"official_capture_{stable}.png"
            image_path.write_bytes(image_bytes)
            captured_at = _now_iso()
            file_hash = hashlib.sha256(image_bytes).hexdigest()
            provenance = {
                "source_id": plan.source_id,
                "source_url": plan.source_url,
                "final_url": final_url,
                "page_title": page_title,
                "capture_target": plan.capture_target,
                "capture_anchor": anchor,
                "capture_note": plan.capture_note,
                "captured_at": captured_at,
                "browser_engine": str(browser_info.get("Browser") or "Chromium"),
                "region_locator": "visible_text_nearest_semantic_container",
                "clip_rect": dict(clip_rect),
                "sha256": file_hash,
                "safety_checked": True,
            }
            return CaptureResult(
                status="success",
                source_id=plan.source_id,
                source_url=plan.source_url,
                final_url=final_url,
                page_title=page_title,
                capture_target=plan.capture_target,
                capture_anchor=anchor,
                captured_at=captured_at,
                image_path=str(image_path),
                dimensions={
                    "width": int(round(clip_rect["width"])),
                    "height": int(round(clip_rect["height"])),
                },
                clip_rect=dict(clip_rect),
                provenance=provenance,
            )
        except Exception as exc:
            return CaptureResult(
                status="needs_review",
                source_id=plan.source_id,
                source_url=plan.source_url,
                capture_target=plan.capture_target,
                capture_anchor=anchor,
                captured_at=_now_iso(),
                review_reason=f"브라우저 실행 오류: {exc}",
            )
        finally:
            for connection in (page_ws, browser_ws):
                if connection is not None:
                    try:
                        connection.close()
                    except Exception:
                        pass
            if process is not None:
                try:
                    process.terminate()
                    process.wait(timeout=3)
                except Exception:
                    try:
                        process.kill()
                    except Exception:
                        pass
            try:
                temp_profile.cleanup()
            except Exception:
                pass


_DEFAULT_EXECUTOR: CaptureExecutor | None = None


def get_default_capture_executor() -> CaptureExecutor:
    global _DEFAULT_EXECUTOR
    if _DEFAULT_EXECUTOR is None:
        try:
            _DEFAULT_EXECUTOR = HeadlessCdpCaptureExecutor()
        except Exception as exc:
            _DEFAULT_EXECUTOR = UnavailableCaptureExecutor(str(exc))
    return _DEFAULT_EXECUTOR


def capture_public_source(
    plan: CapturePlan,
    *,
    executor: CaptureExecutor | None = None,
) -> CaptureResult:
    return (executor or get_default_capture_executor()).capture_public_source(plan)


def process_content_pack_captures(
    data: Mapping[str, Any],
    *,
    executor: CaptureExecutor | None = None,
    output_dir: Path | str | None = None,
) -> list[CaptureResult]:
    plans = build_image_acquisition_plans(data)
    results: list[CaptureResult] = []
    for plan in plans:
        if plan.get("strategy") != "official_capture":
            continue
        if plan.get("status") != "ready" or plan.get("action") != "capture_public_source":
            results.append(
                CaptureResult(
                    status="needs_review",
                    source_id=str(plan.get("source_id") or ""),
                    source_url=str(plan.get("source_url") or ""),
                    capture_target=str(plan.get("capture_target") or ""),
                    capture_anchor=str(plan.get("capture_anchor") or ""),
                    captured_at=_now_iso(),
                    review_reason=str(plan.get("reason") or "자동 캡처 준비가 완료되지 않았습니다."),
                )
            )
            continue
        capture_plan = CapturePlan(
            source_id=str(plan.get("source_id") or ""),
            source_url=str(plan.get("source_url") or ""),
            capture_target=str(plan.get("capture_target") or ""),
            capture_anchor=str(plan.get("capture_anchor") or ""),
            capture_note=str(plan.get("capture_note") or ""),
            output_dir=output_dir,
        )
        results.append(capture_public_source(capture_plan, executor=executor))
    return results
