from __future__ import annotations

from pathlib import Path
from typing import Any, Callable, Mapping, MutableMapping


TopicAngleRunner = Callable[[str | Path], tuple[dict[str, object], str]]


_BLOCKED_CLUSTERING_STATUSES = {
    "skipped_overlap",
    "failed",
    "error",
    "queued",
    "running",
}


def _as_mapping(value: object) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def should_resume_deferred_topic_angles(refresh_result: Mapping[str, Any]) -> bool:
    """이번 실행에서 군집 결과가 저장됐다면 전체 backlog와 무관하게 방향 생성을 이어갑니다."""
    topic_angles = _as_mapping(refresh_result.get("topic_angles"))
    if str(topic_angles.get("status") or "") != "deferred_for_clustering_backlog":
        return False

    ranking = _as_mapping(refresh_result.get("ranking"))
    clustering = _as_mapping(ranking.get("ai_clustering"))
    status = str(clustering.get("status") or "").strip().casefold()
    if status in _BLOCKED_CLUSTERING_STATUSES:
        return False
    if bool(clustering.get("defer_topic_angles")):
        return False

    processed_items = max(0, int(clustering.get("processed_items") or 0))
    cluster_count = max(0, int(ranking.get("clusters") or 0))
    return processed_items > 0 and cluster_count > 0


def resume_deferred_topic_angles(
    refresh_result: MutableMapping[str, Any],
    *,
    runner: TopicAngleRunner,
    db_path: str | Path,
) -> tuple[MutableMapping[str, Any], str]:
    """저장 완료 군집의 누락 방향을 한 묶음 생성하고, 남은 backlog는 메타데이터로 보존합니다."""
    if not should_resume_deferred_topic_angles(refresh_result):
        return refresh_result, ""

    ranking = _as_mapping(refresh_result.get("ranking"))
    clustering = _as_mapping(ranking.get("ai_clustering"))
    remaining_items = max(0, int(clustering.get("remaining_items") or 0))

    payload, warning = runner(db_path)
    normalized = dict(payload or {})
    normalized.setdefault("status", "unknown")
    normalized["clustering_remaining_items"] = remaining_items
    normalized["resumed_with_clustering_backlog"] = remaining_items > 0
    refresh_result["topic_angles"] = normalized

    if warning:
        warnings = refresh_result.get("warnings")
        if not isinstance(warnings, dict):
            warnings = {}
            refresh_result["warnings"] = warnings
        warnings["topic_angles"] = str(warning)
    return refresh_result, str(warning or "")
