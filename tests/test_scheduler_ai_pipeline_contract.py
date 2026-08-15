from __future__ import annotations

from pathlib import Path


def _wrapper_markers(function) -> set[str]:
    markers: set[str] = set()
    current = function
    seen: set[int] = set()
    while callable(current) and id(current) not in seen:
        seen.add(id(current))
        if getattr(current, "_trend_blog_ai_routing_contract", False):
            markers.add("blog_routing")
        if getattr(current, "_trend_candidate_ai_evaluation_contract", False):
            markers.add("candidate_evaluation")
        next_function = getattr(current, "__wrapped__", None)
        if not callable(next_function):
            break
        current = next_function
    return markers


def test_scheduler_refresh_reuses_same_ai_postprocessing_wrappers() -> None:
    from scripts import refresh_trends
    from src.services import trend_discovery_service as discovery

    assert refresh_trends.refresh_trend_sources_short_connections is discovery.refresh_trend_sources_short_connections
    assert _wrapper_markers(discovery.refresh_trend_sources_short_connections) == {
        "candidate_evaluation",
        "blog_routing",
    }


def test_scheduler_gemini_calls_use_common_rate_limit_gateway() -> None:
    from src.services import gemini_service

    assert getattr(
        gemini_service.call_gemini_structured_output,
        "_gemini_common_rate_limited",
        False,
    ) is True


def test_scheduler_safe_entrypoint_keeps_refresh_and_deferred_angle_pipeline() -> None:
    project_root = Path(__file__).resolve().parents[1]
    batch_text = (project_root / "run_trend_refresh.bat").read_text(encoding="utf-8")
    safe_text = (project_root / "scripts" / "refresh_trends_safe.py").read_text(encoding="utf-8-sig")

    assert 'scripts\\refresh_trends_safe.py' in batch_text
    assert "original_runner = base_refresh._run_refresh_body" in safe_text
    assert "resume_deferred_topic_angles(" in safe_text
    assert "return base_refresh.main()" in safe_text
