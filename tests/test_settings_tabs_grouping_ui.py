from __future__ import annotations

import ast
from pathlib import Path


APP_PATH = Path("app.py")
BACKUP_UI_PATH = Path("src/database_backup_ui.py")
UI_PATH = Path("src/ui.py")


def _source(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _function_source(path: Path, function_name: str) -> str:
    source = _source(path)
    tree = ast.parse(source)
    function = next(
        node
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name == function_name
    )
    return ast.get_source_segment(source, function) or ""


def test_floating_settings_wrappers_are_removed() -> None:
    source = _source(UI_PATH)

    assert "_install_database_backup_ui" not in source
    assert 'caller_globals["render_settings"]' not in source
    assert "_database_backup_wrapper" not in source


def test_quality_diagnostics_are_grouped_inside_ai_settings_tab() -> None:
    source = _source(APP_PATH)
    render_source = _function_source(APP_PATH, "render_settings")

    assert "render_quality_diagnostic_panels" in source
    assert '["모델 설정", "품질·운영 진단"]' in render_source
    assert "render_quality_diagnostic_panels(con, st_module=st)" in render_source
    assert "render_topic_angle_quality_diagnostic_panel(" in render_source


def test_backup_restore_is_inside_data_integration_tab() -> None:
    render_source = _function_source(APP_PATH, "render_settings")

    assert '["데이터 보관", "백업·복구", "API 상태", "YouTube 연동"]' in render_source
    assert "with data_tabs[1]:" in render_source
    assert "render_database_backup_panel(st_module=st)" in render_source
    assert "with data_tabs[2]:" in render_source
    assert "with data_tabs[3]:" in render_source


def test_backup_module_separates_read_only_quality_and_backup_renderers() -> None:
    source = _source(BACKUP_UI_PATH)
    quality_source = _function_source(
        BACKUP_UI_PATH,
        "render_quality_diagnostic_panels",
    )
    backup_source = _function_source(BACKUP_UI_PATH, "render_database_backup_panel")

    assert "_render_gemini_stability" in quality_source
    assert "_render_source_diversity" in quality_source
    assert "connect_database(DEFAULT_DB_PATH, read_only=True)" in source
    assert "render_gemini_stability_panel" not in backup_source
    assert "render_source_diversity_panel" not in backup_source
    assert "데이터베이스 백업·안전 복구" in backup_source


def test_settings_tabs_are_centered_wide_and_page_scoped() -> None:
    style_source = _function_source(APP_PATH, "_render_settings_navigation_styles")
    global_style_source = _function_source(APP_PATH, "apply_global_styles")
    render_source = _function_source(APP_PATH, "render_settings")

    assert '[data-testid="stTabs"]' in style_source
    assert "min-width: 12.5rem" in style_source
    assert "justify-content: center" in style_source
    assert "white-space: nowrap" in style_source
    assert "overflow-x: auto" in style_source
    assert '[data-testid="stTabs"]' not in global_style_source
    assert "_render_settings_navigation_styles()" in render_source
    assert 'key="settings_section_content"' not in render_source
