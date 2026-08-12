from __future__ import annotations

import os
import subprocess
import webbrowser
from pathlib import Path
from urllib.parse import urlparse


def validate_write_url(url: str) -> str:
    clean_url = str(url or "").strip()
    parsed = urlparse(clean_url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("https:// 또는 http://로 시작하는 올바른 글쓰기 URL을 입력하세요.")
    return clean_url


def _chrome_candidates() -> list[Path]:
    values = [
        Path(os.environ.get("PROGRAMFILES", r"C:\Program Files"))
        / "Google/Chrome/Application/chrome.exe",
        Path(os.environ.get("PROGRAMFILES(X86)", r"C:\Program Files (x86)"))
        / "Google/Chrome/Application/chrome.exe",
        Path(os.environ.get("LOCALAPPDATA", ""))
        / "Google/Chrome/Application/chrome.exe",
    ]
    return [path for path in values if str(path)]


def open_in_regular_chrome(url: str) -> str:
    safe_url = validate_write_url(url)
    if os.name == "nt":
        for chrome_path in _chrome_candidates():
            if chrome_path.is_file():
                subprocess.Popen([str(chrome_path), safe_url])
                return f"일반 Chrome에서 열었습니다: {safe_url}"
        try:
            subprocess.Popen(["cmd", "/c", "start", "", "chrome", safe_url])
            return f"Chrome 명령으로 열었습니다: {safe_url}"
        except OSError:
            pass
    opened = webbrowser.open_new_tab(safe_url)
    if opened:
        return f"기본 브라우저에서 열었습니다: {safe_url}"
    raise RuntimeError("브라우저를 열지 못했습니다. 설정의 URL을 직접 열어주세요.")
