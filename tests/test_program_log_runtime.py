from __future__ import annotations

import json
from pathlib import Path

from src.services.program_log_runtime import _request_context


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_clustering_request_context_reads_view_and_candidate_count() -> None:
    request = "군집 지침\n\n" + json.dumps(
        {
            "view": "title",
            "candidates": [{"id": "a"}, {"id": "b"}, {"id": "c"}],
        },
        ensure_ascii=False,
    )

    view, count = _request_context(request)

    assert view == "title"
    assert count == 3


def test_topic_angle_request_context_reads_cluster_count() -> None:
    request = "주제 방향 지침\n\n[글감 목록]\n" + json.dumps(
        {"clusters": [{"cluster_id": "1"}, {"cluster_id": "2"}]},
        ensure_ascii=False,
    )

    view, count = _request_context(request)

    assert view == ""
    assert count == 2


def test_background_entrypoints_install_program_logging_before_runtime() -> None:
    clustering = (PROJECT_ROOT / "scripts" / "process_cluster_backlog.py").read_text(
        encoding="utf-8"
    )
    refresh = (PROJECT_ROOT / "scripts" / "refresh_trends_safe.py").read_text(
        encoding="utf-8"
    )

    for text in (clustering, refresh):
        assert "install_program_logging_contract()" in text
        assert text.index("install_program_logging_contract()") < text.index(
            "install_trend_cluster_runtime_contract()"
        )
