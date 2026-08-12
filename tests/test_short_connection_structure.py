from __future__ import annotations

import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _function_node(path: Path, name: str) -> ast.FunctionDef:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    raise AssertionError(f"{name} 함수를 찾지 못했습니다: {path}")


def test_dashboard_action_owns_short_connections_instead_of_receiving_one():
    node = _function_node(ROOT / "app.py", "run_trend_dashboard_action")
    argument_names = [argument.arg for argument in node.args.args]
    source = ast.unparse(node)

    assert "con" not in argument_names
    assert "refresh_trend_sources_short_connections" in source
    assert "prepare_trend_ranking_rebuild" in source
    assert "execute_prepared_topic_angles" in source
    assert "finalize_prepared_topic_angles" in source


def test_scheduler_uses_short_collection_and_split_gemini_phases():
    source = (ROOT / "scripts" / "refresh_trends.py").read_text(encoding="utf-8")

    assert "refresh_trend_sources_short_connections(" in source
    assert "prepare_missing_topic_angles(" in source
    assert "execute_prepared_topic_angles(" in source
    assert "finalize_prepared_topic_angles(" in source
    assert "generate_missing_topic_angles(" not in source


def test_preloaded_signal_import_separates_adapter_read_from_database_write():
    source = (ROOT / "src" / "services" / "topic_service.py").read_text(
        encoding="utf-8"
    )

    assert "def import_preloaded_source_signals(" in source
    assert "def record_source_import_failure(" in source
    assert "adapter.load_signals(limit=limit)" in source
    assert "def _save_signal_batch(" in source
    assert "def import_source_signals(" in source
