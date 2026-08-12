from __future__ import annotations

import re
from pathlib import Path

from src.topic_angle_status_ui import install_topic_angle_status_explainer


VERSION_UNAVAILABLE = "확인 불가"
_VERSION_PATTERN = re.compile(r"^\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.-]+)?$")


def read_app_version(version_file: str | Path) -> str:
    """VERSION 파일을 읽되 누락·인코딩·형식 오류로 앱을 중단하지 않습니다."""
    try:
        raw_value = Path(version_file).read_text(encoding="utf-8-sig")
    except (OSError, UnicodeError):
        return VERSION_UNAVAILABLE

    value = raw_value.strip()
    if not _VERSION_PATTERN.fullmatch(value):
        return VERSION_UNAVAILABLE
    return value


def format_app_version_label(version: object) -> str:
    value = str(version or "").strip()
    if not value or value == VERSION_UNAVAILABLE:
        return "버전 확인 불가"
    return f"v{value}"


def build_browser_page_title(product_name: object, version: object) -> str:
    product = str(product_name or "").strip() or "콘텐츠 트렌드 트래커"
    return f"{product} · {format_app_version_label(version)}"


# app.py가 항상 읽는 가벼운 시작 모듈에서 상세 화면 상태 설명을 한 번 설치합니다.
install_topic_angle_status_explainer()
