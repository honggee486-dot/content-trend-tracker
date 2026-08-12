from __future__ import annotations

from dataclasses import dataclass
from functools import wraps
from typing import Any, Callable, Mapping

from src.services.program_log_service import record_program_event
from src.services.topic_angle_demand_contract import build_evidence_contract
from src.services.trend_discovery_service import get_trend_cluster_items


@dataclass(frozen=True)
class TopicAngleCandidateDiagnostics:
    total_clusters: int
    eligible_status_clusters: int
    score_eligible_clusters: int
    already_complete_clusters: int
    generation_needed_clusters: int
    inspected_clusters: int
    skipped_sensitive_clusters: int
    skipped_no_evidence_clusters: int
    selected_clusters: int
    deferred_uninspected_clusters: int
    min_opportunity_score: float
    selection_limit: int

    def as_metadata(self) -> dict[str, object]:
        return {
            "total_clusters": self.total_clusters,
            "eligible_status_clusters": self.eligible_status_clusters,
            "score_eligible_clusters": self.score_eligible_clusters,
            "already_complete_clusters": self.already_complete_clusters,
            "generation_needed_clusters": self.generation_needed_clusters,
            "inspected_clusters": self.inspected_clusters,
            "skipped_sensitive_clusters": self.skipped_sensitive_clusters,
            "skipped_no_evidence_clusters": self.skipped_no_evidence_clusters,
            "selected_clusters": self.selected_clusters,
            "deferred_uninspected_clusters": self.deferred_uninspected_clusters,
            "min_opportunity_score": self.min_opportunity_score,
            "selection_limit": self.selection_limit,
        }


CandidateInspector = Callable[[Any, str, str], str]


def _safe_int(value: object) -> int:
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError, OverflowError):
        return 0


def _default_candidate_inspector(con: Any, cluster_id: str, title: str) -> str:
    from src.services.gemini_service import scan_sensitive_fields

    cleaned_title = str(title or "").strip()
    if not cleaned_title or scan_sensitive_fields([("글감 제목", cleaned_title)]):
        return "sensitive"
    items = get_trend_cluster_items(con, str(cluster_id))
    evidence, _evidence_map = build_evidence_contract(
        items,
        safe_public_text=lambda value, field: (
            ""
            if scan_sensitive_fields([(field, str(value or "").strip())])
            else str(value or "").strip()
        ),
        maximum=8,
    )
    return "selected" if evidence else "no_evidence"


def collect_topic_angle_candidate_diagnostics(
    con: Any,
    *,
    min_opportunity_score: float,
    selection_limit: int,
    selected_clusters: int,
    candidate_inspector: CandidateInspector = _default_candidate_inspector,
) -> TopicAngleCandidateDiagnostics:
    """주제 방향 후보가 제외된 단계를 읽기 전용으로 집계합니다."""
    threshold = max(0.0, min(100.0, float(min_opportunity_score)))
    limit = max(1, min(int(selection_limit), 400))
    aggregate = con.execute(
        """
        WITH angle_counts AS (
            SELECT cluster_id, COUNT(*) AS angle_count
            FROM trend_cluster_ai_angles
            GROUP BY cluster_id
        ),
        profile_flags AS (
            SELECT cluster_id,
                   MAX(
                       CASE
                           WHEN COALESCE(TRIM(content_plan_json), '') NOT IN ('', '{}')
                           THEN 1 ELSE 0
                       END
                   ) AS has_content_plan
            FROM trend_cluster_ai_profiles
            GROUP BY cluster_id
        )
        SELECT
            COUNT(*) AS total_clusters,
            SUM(
                CASE WHEN COALESCE(tc.recommendation_status, 'review')
                              IN ('recommended', 'review')
                     THEN 1 ELSE 0 END
            ) AS eligible_status_clusters,
            SUM(
                CASE WHEN COALESCE(tc.recommendation_status, 'review')
                              IN ('recommended', 'review')
                           AND COALESCE(tc.opportunity_score, 0) >= ?
                     THEN 1 ELSE 0 END
            ) AS score_eligible_clusters,
            SUM(
                CASE WHEN COALESCE(tc.recommendation_status, 'review')
                              IN ('recommended', 'review')
                           AND COALESCE(tc.opportunity_score, 0) >= ?
                           AND COALESCE(ac.angle_count, 0) >= 3
                           AND COALESCE(pf.has_content_plan, 0) = 1
                     THEN 1 ELSE 0 END
            ) AS already_complete_clusters,
            SUM(
                CASE WHEN COALESCE(tc.recommendation_status, 'review')
                              IN ('recommended', 'review')
                           AND COALESCE(tc.opportunity_score, 0) >= ?
                           AND (
                               COALESCE(ac.angle_count, 0) < 3
                               OR COALESCE(pf.has_content_plan, 0) = 0
                           )
                     THEN 1 ELSE 0 END
            ) AS generation_needed_clusters
        FROM trend_clusters tc
        LEFT JOIN angle_counts ac ON ac.cluster_id = tc.cluster_id
        LEFT JOIN profile_flags pf ON pf.cluster_id = tc.cluster_id
        """,
        [threshold, threshold, threshold],
    ).fetchone() or (0, 0, 0, 0, 0)

    rows = con.execute(
        """
        WITH angle_counts AS (
            SELECT cluster_id, COUNT(*) AS angle_count
            FROM trend_cluster_ai_angles
            GROUP BY cluster_id
        ),
        profile_flags AS (
            SELECT cluster_id,
                   MAX(
                       CASE
                           WHEN COALESCE(TRIM(content_plan_json), '') NOT IN ('', '{}')
                           THEN 1 ELSE 0
                       END
                   ) AS has_content_plan
            FROM trend_cluster_ai_profiles
            GROUP BY cluster_id
        )
        SELECT tc.cluster_id, tc.canonical_title
        FROM trend_clusters tc
        LEFT JOIN angle_counts ac ON ac.cluster_id = tc.cluster_id
        LEFT JOIN profile_flags pf ON pf.cluster_id = tc.cluster_id
        WHERE COALESCE(tc.recommendation_status, 'review') IN ('recommended', 'review')
          AND COALESCE(tc.opportunity_score, 0) >= ?
          AND (
              COALESCE(ac.angle_count, 0) < 3
              OR COALESCE(pf.has_content_plan, 0) = 0
          )
        ORDER BY tc.opportunity_score DESC, tc.trend_score DESC, tc.last_seen_at DESC
        LIMIT ?
        """,
        [threshold, limit],
    ).fetchall()

    sensitive = 0
    no_evidence = 0
    for cluster_id, title in rows:
        outcome = str(candidate_inspector(con, str(cluster_id), str(title or "")))
        if outcome == "sensitive":
            sensitive += 1
        elif outcome == "no_evidence":
            no_evidence += 1

    total, eligible, score_eligible, complete, needed = (
        _safe_int(value) for value in aggregate
    )
    inspected = len(rows)
    return TopicAngleCandidateDiagnostics(
        total_clusters=total,
        eligible_status_clusters=eligible,
        score_eligible_clusters=score_eligible,
        already_complete_clusters=complete,
        generation_needed_clusters=needed,
        inspected_clusters=inspected,
        skipped_sensitive_clusters=sensitive,
        skipped_no_evidence_clusters=no_evidence,
        selected_clusters=max(0, int(selected_clusters)),
        deferred_uninspected_clusters=max(0, needed - inspected),
        min_opportunity_score=threshold,
        selection_limit=limit,
    )


def format_topic_angle_candidate_diagnostics(
    diagnostics: TopicAngleCandidateDiagnostics,
) -> str:
    return (
        f"전체 군집 {diagnostics.total_clusters:,}개 · "
        f"추천/검토 {diagnostics.eligible_status_clusters:,}개 · "
        f"기회 점수 {diagnostics.min_opportunity_score:g}점 이상 "
        f"{diagnostics.score_eligible_clusters:,}개 · "
        f"기존 방향 완료 {diagnostics.already_complete_clusters:,}개 · "
        f"생성 필요 {diagnostics.generation_needed_clusters:,}개 · "
        f"이번 확인 {diagnostics.inspected_clusters:,}개 · "
        f"민감 제목 {diagnostics.skipped_sensitive_clusters:,}개 · "
        f"근거 없음 {diagnostics.skipped_no_evidence_clusters:,}개 · "
        f"이번 생성 대상 {diagnostics.selected_clusters:,}개 · "
        f"요청 범위 밖 미검사 {diagnostics.deferred_uninspected_clusters:,}개"
    )


def _extract_config(args: tuple[Any, ...], kwargs: Mapping[str, Any]) -> Any | None:
    config = kwargs.get("config")
    if config is not None:
        return config
    return args[0] if args else None


def install_topic_angle_candidate_diagnostic_contract() -> None:
    """기존 대상 집계 뒤 제외 사유를 별도 프로그램 로그 한 행으로 기록합니다."""
    from src.services import topic_angle_ai_service as module

    original = getattr(module, "prepare_missing_topic_angles", None)
    if not callable(original) or getattr(
        original,
        "_topic_angle_candidate_diagnostic",
        False,
    ):
        return

    @wraps(original)
    def wrapped(con: Any, *args: Any, **kwargs: Any):
        result = original(con, *args, **kwargs)
        config = _extract_config(args, kwargs)
        if config is None:
            return result
        items_per_request = max(1, int(getattr(config, "topic_angle_batch_limit", 1)))
        max_parallel = max(
            1,
            int(getattr(config, "topic_angle_max_parallel_requests", 1)),
        )
        requested_limit = kwargs.get("limit")
        total_limit = (
            int(requested_limit)
            if requested_limit is not None
            else items_per_request * max_parallel
        )
        total_limit = max(1, min(total_limit, items_per_request * max_parallel, 400))
        selected_count = len(getattr(result, "clusters", ()) or ())
        try:
            diagnostics = collect_topic_angle_candidate_diagnostics(
                con,
                min_opportunity_score=float(
                    getattr(config, "topic_angle_min_opportunity_score", 0.0)
                ),
                selection_limit=total_limit,
                selected_clusters=selected_count,
            )
            detail = format_topic_angle_candidate_diagnostics(diagnostics)
            record_program_event(
                event_type="stage",
                status="completed",
                source="topic_angle_ai",
                action="주제 방향 대상 선정 상세",
                detail=detail,
                item_count=diagnostics.selected_clusters,
                metadata=diagnostics.as_metadata(),
                con=con,
            )
        except Exception as exc:
            # 추가 진단 실패가 기존 주제 방향 생성 성공을 취소하지 않습니다.
            record_program_event(
                event_type="stage",
                status="skipped",
                source="topic_angle_ai",
                action="주제 방향 대상 선정 상세",
                detail=f"선정 상세 집계 실패 · {type(exc).__name__}: {str(exc)[:500]}",
                item_count=selected_count,
                con=con,
            )
        return result

    wrapped._topic_angle_candidate_diagnostic = True  # type: ignore[attr-defined]
    module.prepare_missing_topic_angles = wrapped
