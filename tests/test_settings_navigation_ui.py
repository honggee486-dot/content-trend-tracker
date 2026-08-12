from __future__ import annotations

import ast
from pathlib import Path


APP_PATH = Path("app.py")


def _app_source() -> str:
    return APP_PATH.read_text(encoding="utf-8")


def test_settings_use_saas_section_navigation() -> None:
    source = _app_source()
    tree = ast.parse(source)

    options = next(
        node
        for node in tree.body
        if isinstance(node, ast.Assign)
        and any(
            isinstance(target, ast.Name)
            and target.id == "SETTINGS_SECTION_OPTIONS"
            for target in node.targets
        )
    )
    assert isinstance(options.value, ast.Tuple)
    assert [item.value for item in options.value.elts] == [
        "기본 설정",
        "AI·품질",
        "발행 채널",
        "자동화·이력",
        "데이터·연동",
    ]

    render_settings = next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef)
        and node.name == "render_settings"
    )
    calls = {
        node.func.attr
        for node in ast.walk(render_settings)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
    }
    called_names = {
        node.func.id
        for node in ast.walk(render_settings)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }
    assert {"segmented_control", "tabs", "form"}.issubset(calls)
    assert "_render_settings_navigation_styles" in called_names
    assert 'key="settings_section_navigation"' in source
    assert ".st-key-settings_section_navigation" in source
    assert '[data-testid="stTabs"]' in source


def test_settings_sections_keep_existing_features_reachable() -> None:
    source = _app_source()
    render_source = source[
        source.index("def render_settings() -> None:") :
        source.index("page = render_top_navigation()")
    ]

    for section in (
        "기본 설정",
        "AI·품질",
        "발행 채널",
        "자동화·이력",
    ):
        assert f'settings_section == "{section}"' in render_source
    assert "else:" in render_source

    for call in (
        "_render_gemini_model_settings(con)",
        "render_quality_diagnostic_panels(con, st_module=st)",
        "render_topic_angle_quality_diagnostic_panel(",
        "_render_blog_profile_settings(con)",
        "_render_refresh_scheduler_settings(con)",
        "render_collection_history(con)",
        "get_database_stats(",
        "render_database_backup_panel(st_module=st)",
        "get_naver_search_usage(",
        "get_kakao_daum_usage(",
        "YouTubeParquetAdapter(configured_path).inspect()",
    ):
        assert call in render_source


def test_collection_settings_keep_atomic_save_contract() -> None:
    source = _app_source()
    render_source = source[
        source.index("def render_settings() -> None:") :
        source.index("page = render_top_navigation()")
    ]

    assert '["기본 정보", "탐색 범위", "공개 데이터", "보관·한도"]' in render_source
    assert 'st.form("settings_form")' in render_source
    assert '"설정 저장", type="primary", width="stretch"' in render_source
    for setting_key in (
        "youtube_parquet_path",
        "trend_seed_queries",
        "trend_analysis_youtube_limit",
        "naver_search_daily_safety_limit",
        "kakao_daum_monthly_safety_limit",
    ):
        assert setting_key in render_source


def test_operational_sections_have_secondary_tabs() -> None:
    source = _app_source()
    assert '["모델 설정", "품질·운영 진단"]' in source
    assert '["예약 실행", "수집 이력"]' in source
    assert '["데이터 보관", "백업·복구", "API 상태", "YouTube 연동"]' in source


def test_settings_entry_resets_to_basic_settings() -> None:
    source = _app_source()
    navigate_source = source[
        source.index("def navigate_to_page(page: str, **state_updates) -> None:") :
        source.index("def render_content_workflow_progress")
    ]

    assert 'if page == "설정":' in navigate_source
    assert 'st.session_state["settings_section"] = "기본 설정"' in navigate_source
    render_source = source[
        source.index("def render_settings() -> None:") :
        source.index("page = render_top_navigation()")
    ]
    assert 'if st.session_state.get("settings_section") not in SETTINGS_SECTION_OPTIONS:' in render_source
    assert 'default=SETTINGS_SECTION_OPTIONS[0]' in render_source
