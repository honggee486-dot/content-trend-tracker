from __future__ import annotations

import ast
from pathlib import Path


def test_curated_profile_ui_uses_shared_platform_tabs_and_colors() -> None:
    source = Path("src/curated_blog_profile_ui.py").read_text(encoding="utf-8")
    presentation = Path("src/blog_platform_presentation.py").read_text(
        encoding="utf-8"
    )

    assert "BLOG_PLATFORM_PRESENTATION" in source
    for value in (
        "🔵 Blogger 3",
        "🟢 네이버 1",
        "🟠 티스토리 1",
        "#4285F4",
        "#03C75A",
        "#F97316",
    ):
        assert value in presentation
    assert "archive_blog_profile" not in source
    assert "restore_blog_profile" not in source
    assert "새 블로그 프로필 추가" not in source


def test_app_delegates_to_fixed_profile_sync_and_editor() -> None:
    source = Path("app.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    settings_function = next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef)
        and node.name == "_render_blog_profile_settings"
    )
    called_names = {
        node.func.id
        for node in ast.walk(settings_function)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }

    assert "synchronize_curated_blog_profiles" in called_names
    assert "render_blog_channel_strategy_settings" in called_names
    assert "render_curated_blog_profile_settings" in called_names
    function_source = ast.get_source_segment(source, settings_function) or ""
    assert function_source.index("render_curated_blog_profile_settings") < function_source.index(
        "render_blog_channel_strategy_settings"
    )
    assert "새 블로그 프로필 추가" not in function_source
    assert "숨긴 프로필" not in function_source


def test_publish_screen_uses_curated_profile_result() -> None:
    source = Path("app.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    publish_function = next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "render_publish"
    )
    called_names = {
        node.func.id
        for node in ast.walk(publish_function)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }
    assert "synchronize_curated_blog_profiles" in called_names
