from __future__ import annotations

import ast
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _function_metric_calls(source: str, function_name: str) -> list[ast.Call]:
    tree = ast.parse(source)
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == function_name:
            return [
                item
                for item in ast.walk(node)
                if isinstance(item, ast.Call)
                and isinstance(item.func, ast.Attribute)
                and item.func.attr == "metric"
            ]
    raise AssertionError(f"함수를 찾을 수 없습니다: {function_name}")


def _assert_metric_cards_have_help_and_border(calls: list[ast.Call]) -> None:
    assert calls
    for call in calls:
        keywords = {item.arg: item.value for item in call.keywords if item.arg}
        assert "help" in keywords
        border = keywords.get("border")
        assert isinstance(border, ast.Constant) and border.value is True


def test_settings_status_metrics_are_bordered_and_explained() -> None:
    source = (PROJECT_ROOT / "app.py").read_text(encoding="utf-8")

    for function_name in (
        "_render_actual_quota_usage",
        "_render_refresh_scheduler_settings",
        "_render_gemini_model_settings",
        "render_settings",
    ):
        _assert_metric_cards_have_help_and_border(
            _function_metric_calls(source, function_name)
        )


def test_api_usage_values_do_not_mix_used_and_limit_in_one_number() -> None:
    source = (PROJECT_ROOT / "app.py").read_text(encoding="utf-8")

    assert 'f"{usage.daily_used:,}/{usage.daily_limit:,}회"' not in source
    assert 'f"{usage.monthly_used:,}/{usage.monthly_limit:,}회"' not in source
    assert 'f"{kakao_usage.daily_used:,}/{kakao_usage.daily_limit:,}회"' not in source
    assert 'f"{kakao_usage.monthly_used:,}/{kakao_usage.monthly_limit:,}회"' not in source


def test_collection_history_metrics_are_bordered_and_explained() -> None:
    source = (PROJECT_ROOT / "src" / "collection_history_ui.py").read_text(encoding="utf-8")

    for function_name in ("_render_gemini_usage", "render_collection_history"):
        _assert_metric_cards_have_help_and_border(
            _function_metric_calls(source, function_name)
        )

    assert "f\"API 요청 · 참고 RPD {int(usage['reference_limit']):,}회\"" in source
    assert 'f"1회 최대 입력 · 한도 {input_limit_text}"' in source
    assert 'f"1회 최대 생성 · 출력 한도 참고 {output_limit_text}"' in source
    assert "f\"{int(usage['request_count']):,} /" not in source
