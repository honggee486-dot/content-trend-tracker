from __future__ import annotations

from pathlib import Path

from src.app_version import (
    VERSION_UNAVAILABLE,
    build_browser_page_title,
    format_app_version_label,
    read_app_version,
)
from src.topic_angle_status_ui import install_topic_angle_status_explainer
from src.ui import (
    _install_gemini_capacity_caption_ui,
    _install_inline_version_caption_ui,
    build_page_header_title,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class _FakeStreamlit:
    def __init__(self) -> None:
        self.captions: list[str] = []
        self.warnings: list[str] = []

    def caption(self, value, *args, **kwargs):
        self.captions.append(str(value))

    def warning(self, value, *args, **kwargs):
        self.warnings.append(str(value))


def test_read_app_version_accepts_semantic_version_and_utf8_bom(tmp_path: Path) -> None:
    version_file = tmp_path / "VERSION"
    version_file.write_text("\ufeff 0.10.88 \n", encoding="utf-8")

    assert read_app_version(version_file) == "0.10.88"


def test_read_app_version_falls_back_for_missing_or_invalid_file(tmp_path: Path) -> None:
    assert read_app_version(tmp_path / "missing") == VERSION_UNAVAILABLE

    invalid_file = tmp_path / "VERSION"
    invalid_file.write_text("release-0.10.88", encoding="utf-8")
    assert read_app_version(invalid_file) == VERSION_UNAVAILABLE


def test_version_labels_are_safe_for_visible_ui_and_browser_title() -> None:
    assert format_app_version_label("0.10.88") == "v0.10.88"
    assert format_app_version_label(VERSION_UNAVAILABLE) == "버전 확인 불가"
    assert (
        build_browser_page_title("콘텐츠 트렌드 트래커", "0.10.88")
        == "콘텐츠 트렌드 트래커 · v0.10.88"
    )


def test_page_header_places_version_beside_title_without_long_label() -> None:
    header_html = build_page_header_title("오늘의 트렌드", "0.10.88")

    assert "오늘의 트렌드" in header_html
    assert "v0.10.88" in header_html
    assert "현재 버전:" not in header_html
    assert 'class="app-page-version"' in header_html


def test_streamlit_caption_wrappers_compose_without_duplication(monkeypatch) -> None:
    monkeypatch.setenv("GEMINI_TOPIC_ANGLE_ITEMS_PER_REQUEST", "20")
    fake = _FakeStreamlit()
    install_topic_angle_status_explainer(fake)
    caller_globals: dict[str, object] = {"st": fake}

    _install_inline_version_caption_ui(caller_globals)
    inline_proxy = caller_globals["st"]
    _install_inline_version_caption_ui(caller_globals)

    assert caller_globals["st"] is inline_proxy

    inline_proxy.caption("현재 버전: v0.10.88")
    inline_proxy.caption(
        "자동·예약 분석 모델: gemini-3.6-flash · "
        "실행당 새 분석 대상 상위 15개 · 3시간 주기라면 하루 약 8회 실행됩니다."
    )
    inline_proxy.caption("일반 안내 문구")

    assert len(fake.captions) == 2
    assert "실행당 새 분석 대상 상위 20개" in fake.captions[0]
    assert fake.captions[1] == "일반 안내 문구"
    assert all("현재 버전:" not in caption for caption in fake.captions)


def test_gemini_caption_wrapper_restores_streamlit_and_keeps_all_rewrites(
    monkeypatch,
) -> None:
    monkeypatch.setenv("GEMINI_TOPIC_ANGLE_ITEMS_PER_REQUEST", "20")
    fake = _FakeStreamlit()
    install_topic_angle_status_explainer(fake)
    caller_globals: dict[str, object] = {"st": fake}
    _install_inline_version_caption_ui(caller_globals)
    inline_proxy = caller_globals["st"]

    def render_model_settings() -> None:
        caller_globals["st"].caption(
            "자동·예약 분석 모델: gemini-3.6-flash · "
            "실행당 새 분석 대상 상위 15개 · "
            "기본 구성은 100개를 1회 요청으로 처리합니다."
        )

    caller_globals["_render_gemini_model_settings"] = render_model_settings
    _install_gemini_capacity_caption_ui(caller_globals)
    wrapped = caller_globals["_render_gemini_model_settings"]
    _install_gemini_capacity_caption_ui(caller_globals)

    assert caller_globals["_render_gemini_model_settings"] is wrapped

    wrapped()

    assert caller_globals["st"] is inline_proxy
    assert len(fake.captions) == 1
    assert "실행당 새 분석 대상 상위 20개" in fake.captions[0]
    assert "현재 설정값 기준으로 위 버튼 1회 최대치가 적용됩니다." in fake.captions[0]
    assert "기본 구성은 100개를 1회 요청으로 처리합니다." not in fake.captions[0]


def test_app_exposes_version_on_every_page_and_browser_tab() -> None:
    app_source = (PROJECT_ROOT / "app.py").read_text(encoding="utf-8")
    ui_source = (PROJECT_ROOT / "src" / "ui.py").read_text(encoding="utf-8")

    assert 'APP_VERSION = read_app_version(PROJECT_ROOT / "VERSION")' in app_source
    assert 'page_title=build_browser_page_title("콘텐츠 트렌드 트래커", APP_VERSION)' in app_source
    assert 'st.caption(f"현재 버전: {format_app_version_label(APP_VERSION)}")' in app_source
    assert '_install_inline_version_caption_ui(caller_globals)' in ui_source
    assert 'return build_page_header_title(page, caller_globals.get("APP_VERSION"))' in ui_source
