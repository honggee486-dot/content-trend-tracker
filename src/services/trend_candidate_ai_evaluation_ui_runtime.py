from __future__ import annotations

from functools import wraps
from typing import Any

from src.services.trend_candidate_ai_evaluation_service import (
    get_candidate_ai_evaluation_summary,
    get_cluster_ai_evaluation,
)
from src.services.trend_discovery_service import get_trend_cluster


_STATUS_LABELS = {
    "recommended": "추천",
    "review": "검토",
    "hold": "보류",
}


def _format_int(value: Any) -> str:
    try:
        return f"{int(value or 0):,}"
    except (TypeError, ValueError, OverflowError):
        return "0"


def _format_seconds(value: Any) -> str:
    try:
        numeric = float(value or 0.0)
    except (TypeError, ValueError, OverflowError):
        numeric = 0.0
    return f"{numeric:,.1f}초"


def build_ai_evaluation_comparison(
    cluster: dict[str, Any] | None,
    evaluation: dict[str, Any] | None,
) -> list[tuple[str, str, str]]:
    if not cluster or not evaluation:
        return []
    return [
        (
            "트렌드",
            f"데이터 {float(cluster.get('trend_score') or 0):.1f}",
            f"AI {int(evaluation.get('ai_trend_score') or 0)}",
        ),
        (
            "글감기회",
            f"데이터 {float(cluster.get('opportunity_score') or 0):.1f}",
            f"AI {int(evaluation.get('ai_opportunity_score') or 0)}",
        ),
        (
            "자료완성도",
            f"데이터 {float(cluster.get('quality_score') or 0):.1f}",
            f"AI {int(evaluation.get('ai_evidence_quality_score') or 0)}",
        ),
        (
            "사실확인",
            f"데이터 위험 {float(cluster.get('fact_risk_score') or 0):.1f}/30",
            f"AI 난이도 {int(evaluation.get('fact_check_difficulty_score') or 0)}/100",
        ),
    ]


def render_candidate_ai_evaluation_panel(
    con: Any,
    *,
    st_module: Any,
    selected_cluster_id: str = "",
) -> None:
    summary = get_candidate_ai_evaluation_summary(con)
    current = int(summary.get("current_clusters") or 0)
    evaluated = int(summary.get("evaluated_clusters") or 0)
    latest = summary.get("latest_run") if isinstance(summary.get("latest_run"), dict) else {}

    st_module.markdown("#### Flash-Lite 전체 글감 평가")
    caption = f"현재 최종 글감 {current:,}개 중 AI 평가 저장 {evaluated:,}개"
    if latest:
        caption += f" · 최근 모델 {latest.get('model') or '-'}"
    st_module.caption(caption)

    if latest:
        with st_module.expander("최근 전체 글감 평가 사용량", expanded=False):
            cols = st_module.columns(4)
            cols[0].metric("요청", _format_int(latest.get("request_count")))
            cols[1].metric("요청 글감", _format_int(latest.get("requested_items")))
            cols[2].metric("실제 입력 토큰", _format_int(latest.get("input_tokens")))
            cols[3].metric("총 토큰", _format_int(latest.get("total_tokens")))
            st_module.caption(
                " · ".join(
                    [
                        f"예상 입력 {_format_int(latest.get('estimated_input_tokens'))}",
                        f"출력 {_format_int(latest.get('output_tokens'))}",
                        f"사고 {_format_int(latest.get('thought_tokens'))}",
                        f"TPM 대기 {_format_seconds(latest.get('tpm_wait_seconds'))}",
                        f"API 시간 {_format_seconds(float(latest.get('duration_ms') or 0) / 1000.0)}",
                        f"실패 요청 {_format_int(latest.get('failed_requests'))}",
                    ]
                )
            )

    cluster_id = str(selected_cluster_id or "").strip()
    if not cluster_id:
        return
    cluster = get_trend_cluster(con, cluster_id)
    evaluation = get_cluster_ai_evaluation(con, cluster_id)
    if cluster is None:
        return
    if evaluation is None:
        st_module.caption("선택한 글감은 아직 Flash-Lite 전체 평가가 저장되지 않았습니다.")
        return

    st_module.markdown("##### 선택 글감 · 데이터 점수 ↔ AI 점수")
    comparison = build_ai_evaluation_comparison(cluster, evaluation)
    columns = st_module.columns(len(comparison))
    for column, row in zip(columns, comparison, strict=True):
        metric_label, data_value, ai_value = row
        column.metric(metric_label, ai_value, delta=data_value, delta_color="off")

    extra = st_module.columns(4)
    extra[0].metric("검색가치", int(evaluation.get("search_value_score") or 0))
    extra[1].metric("정보성", int(evaluation.get("information_value_score") or 0))
    extra[2].metric("실용성", int(evaluation.get("practicality_score") or 0))
    extra[3].metric("지속성", int(evaluation.get("durability_score") or 0))
    status = _STATUS_LABELS.get(
        str(evaluation.get("recommendation_status") or ""),
        str(evaluation.get("recommendation_status") or "검토"),
    )
    st_module.caption(
        f"AI 판정: {status} · {evaluation.get('reason') or ''} · "
        f"{evaluation.get('model_name') or '-'}"
    )


def _install_dashboard_panel(caller_globals: dict[str, object]) -> None:
    target = caller_globals.get("render_trend_dashboard")
    db_connection = caller_globals.get("db_connection")
    st_module = caller_globals.get("st")
    if (
        not callable(target)
        or not callable(db_connection)
        or st_module is None
        or getattr(target, "_trend_candidate_ai_evaluation_ui", False)
    ):
        return

    @wraps(target)
    def wrapped(*args, **kwargs):
        result = target(*args, **kwargs)
        try:
            selected = str(st_module.session_state.get("selected_trend_cluster_id") or "")
            with db_connection() as con:
                render_candidate_ai_evaluation_panel(
                    con,
                    st_module=st_module,
                    selected_cluster_id=selected,
                )
        except Exception as exc:
            st_module.caption(f"Flash-Lite 글감 평가를 불러오지 못했습니다: {exc}")
        return result

    wrapped._trend_candidate_ai_evaluation_ui = True  # type: ignore[attr-defined]
    caller_globals["render_trend_dashboard"] = wrapped


def install_trend_candidate_ai_evaluation_ui_contract(ui_module: Any) -> None:
    original = getattr(ui_module, "_install_candidate_angle_status_ui", None)
    if not callable(original) or getattr(original, "_trend_candidate_ai_evaluation_installer", False):
        return

    @wraps(original)
    def wrapped(caller_globals: dict[str, object]) -> None:
        original(caller_globals)
        _install_dashboard_panel(caller_globals)

    wrapped._trend_candidate_ai_evaluation_installer = True  # type: ignore[attr-defined]
    ui_module._install_candidate_angle_status_ui = wrapped
