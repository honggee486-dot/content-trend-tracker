from __future__ import annotations

from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_app_navigation_uses_shared_workflow_cleanup() -> None:
    source = (PROJECT_ROOT / "app.py").read_text(encoding="utf-8")

    assert "from src.services.workflow_navigation_service import (" in source
    assert (
        "prepare_workflow_navigation_state(st.session_state, page, state_updates)"
        in source
    )
    assert source.count(
        "prepare_workflow_navigation_state(st.session_state, current_page)"
    ) == 2
    assert "navigate_to_page(item)" in source
    assert "prefill_topic_id=topic_id" in source
    assert "prefill_angle=selected_angle_value" in source


def test_app_has_no_direct_page_or_trend_prefill_navigation_writes() -> None:
    source = (PROJECT_ROOT / "app.py").read_text(encoding="utf-8")

    assert 'st.session_state["page"] =' not in source
    assert 'st.session_state["prefill_topic_id"] = topic_id' not in source
    assert 'st.session_state["prefill_angle"] = selected_angle_value' not in source
