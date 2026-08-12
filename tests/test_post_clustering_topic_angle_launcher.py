from __future__ import annotations

from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_background_clustering_launcher_resumes_topic_angles_after_success() -> None:
    text = (PROJECT_ROOT / "scripts" / "process_cluster_backlog.py").read_text(
        encoding="utf-8"
    )

    assert "run_clustering_job(" in text
    assert "run_topic_angles_after_clustering(" in text
    assert text.index("run_clustering_job(") < text.index(
        "run_topic_angles_after_clustering("
    )
    assert "if exit_code == 0:" in text
    assert "return exit_code" in text
