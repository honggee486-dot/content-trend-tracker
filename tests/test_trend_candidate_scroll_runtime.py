from __future__ import annotations

from types import SimpleNamespace

from src.services.trend_candidate_scroll_runtime import (
    TREND_CANDIDATE_CONTAINER_KEY,
    build_trend_candidate_scroll_bridge_html,
    install_trend_candidate_scroll_runtime,
)


def test_scroll_bridge_preserves_page_and_candidate_list_positions() -> None:
    source = build_trend_candidate_scroll_bridge_html()

    assert "sessionStorage" in source
    assert ".st-key-trend_candidate_list" in source
    assert "scrollTop" in source
    assert "parentWindow.scrollTo" in source
    assert "MutationObserver" in source
    assert "beforeunload" in source
    assert "__cttTrendCandidateScrollBridgeV1" in source
    assert "destroy" in source


def test_scroll_runtime_only_injects_for_candidate_list_and_installs_once() -> None:
    container_calls: list[dict[str, object]] = []
    rendered_html: list[str] = []

    def original_container(*args, **kwargs):
        container_calls.append({"args": args, "kwargs": kwargs})
        return SimpleNamespace()

    st_module = SimpleNamespace(container=original_container)
    install_trend_candidate_scroll_runtime(
        st_module,
        html_renderer=rendered_html.append,
    )
    first_wrapper = st_module.container
    install_trend_candidate_scroll_runtime(
        st_module,
        html_renderer=rendered_html.append,
    )

    assert st_module.container is first_wrapper

    st_module.container(key="other_container")
    assert rendered_html == []

    st_module.container(key=TREND_CANDIDATE_CONTAINER_KEY, height=620, border=True)
    assert len(rendered_html) == 1
    assert ".st-key-trend_candidate_list" in rendered_html[0]
    assert container_calls[-1]["kwargs"]["height"] == 620
