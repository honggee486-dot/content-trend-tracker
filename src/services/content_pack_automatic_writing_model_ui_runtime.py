from __future__ import annotations

from functools import wraps
from typing import Any

from src.services.content_pack_automatic_writing_model_service import (
    CATALOG_TTL_SECONDS,
    PERFORMANCE_TTL_SECONDS,
    PRIORITY_SLOT_COUNT,
    PROVIDER_LABELS,
    load_model_catalog,
    load_performance,
    load_priority,
    model_catalog_due,
    performance_due,
    ranked_model_rows,
    refresh_model_catalog,
    refresh_performance_if_due,
    save_priority,
)


def _score_text(value: object) -> str:
    if isinstance(value, (int, float)):
        return f"{float(value):.0f}"
    return "미평가"


def _age_caption(seconds: int) -> str:
    if seconds % 3600 == 0:
        return f"{seconds // 3600}시간"
    return f"{seconds // 60}분"


def _format_option(key: str, row_map: dict[str, dict[str, Any]]) -> str:
    if not key:
        return "선택 안 함"
    row = row_map.get(key)
    if row is None:
        provider, _, model_id = key.partition(":")
        return f"{PROVIDER_LABELS.get(provider, provider)} · {model_id} · 현재 목록 없음"
    stale = " · 이전 목록" if row.get("stale") else ""
    return (
        f"{row['provider_label']} · {row['model_id']} · "
        f"글쓰기 {_score_text(row.get('writing'))} · "
        f"추론 {_score_text(row.get('reasoning'))}{stale}"
    )


def _provider_state_text(snapshot) -> str:
    if not snapshot.providers:
        return "Provider 조회 기록 없음"
    parts: list[str] = []
    for item in snapshot.providers:
        label = PROVIDER_LABELS.get(item.provider, item.provider)
        if item.status == "ok":
            parts.append(f"{label} {item.model_count}개")
        else:
            detail = f" ({item.error_message})" if item.error_message else ""
            parts.append(f"{label} {item.status}{detail}")
    return " · ".join(parts)


def render_automatic_writing_model_settings(con: Any, *, st_module) -> None:
    st = st_module
    st.divider()
    st.markdown("#### 자동 작성 모델")
    st.caption(
        "자동 작성 fallback은 비용 0원으로 현재 확인된 모델만 사용합니다. "
        "정해진 시각의 타이머가 아니라 모델 목록이 필요할 때 마지막 조회 시각을 확인하는 방식입니다."
    )

    forced = st.button(
        "전체 모델 로드",
        key="automatic_writing_load_all_models",
        help=(
            "OpenRouter·Groq·OpenCode Zen의 현재 모델 목록과 0원 여부를 다시 확인합니다. "
            "최근 조회가 1시간 이내여도 이 버튼은 강제로 새로고침합니다."
        ),
    )
    try:
        if forced or model_catalog_due(con):
            with st.spinner("현재 0원 모델 목록을 확인하는 중입니다..."):
                catalog = refresh_model_catalog(con, force=forced)
        else:
            catalog = load_model_catalog(con)
    except Exception as exc:
        st.warning(f"전체 모델 목록을 갱신하지 못해 이전 저장 목록을 사용합니다: {exc}")
        catalog = load_model_catalog(con)

    st.caption(
        f"모델 목록 캐시 {_age_caption(CATALOG_TTL_SECONDS)} · "
        f"마지막 전체 조회 {catalog.checked_at or '없음'} · {_provider_state_text(catalog)}"
    )

    try:
        if performance_due(con):
            with st.spinner("24시간이 지난 모델 성능 데이터를 무료 fallback으로 갱신하는 중입니다..."):
                performance_result = refresh_performance_if_due(con)
            if performance_result.status == "refreshed":
                st.caption(
                    f"모델 성능 자동 갱신 완료 · "
                    f"{performance_result.snapshot.evaluator_provider}/"
                    f"{performance_result.snapshot.evaluator_model_id}"
                )
            elif performance_result.status not in {"priority_not_configured", "no_models"}:
                st.caption(
                    f"모델 성능 갱신 {performance_result.status} · "
                    f"{performance_result.message or '이전 정상 데이터 유지'}"
                )
        performance = load_performance(con, catalog)
    except Exception as exc:
        st.caption(f"모델 성능 자동 갱신을 건너뜁니다: {exc}")
        performance = load_performance(con, catalog)

    rows = ranked_model_rows(catalog, performance)
    row_map = {str(row["key"]): row for row in rows}
    saved = list(load_priority(con))
    options = [""]
    for row in rows:
        key = str(row["key"])
        if key not in options:
            options.append(key)
    for key in saved:
        if key not in options:
            options.append(key)

    st.markdown("##### 자동 작성 fallback 순서")
    st.caption(
        "위에서부터 순서대로 시도하고 첫 성공에서 즉시 종료합니다. "
        "4개가 모두 실패하면 자동 작성을 종료하며 유료 모델로 전환하지 않습니다."
    )
    selected: list[str] = []
    columns = st.columns(PRIORITY_SLOT_COUNT, gap="small")
    for index, column in enumerate(columns):
        current = saved[index] if index < len(saved) else ""
        selected.append(
            column.selectbox(
                f"{index + 1}순위",
                options,
                index=options.index(current) if current in options else 0,
                format_func=lambda key, rows=row_map: _format_option(key, rows),
                key=f"automatic_writing_priority_{index + 1}",
            )
        )

    if st.button(
        "자동 작성 순서 저장",
        type="primary",
        key="automatic_writing_priority_save",
    ):
        try:
            saved_values = save_priority(con, selected)
        except ValueError as exc:
            st.error(str(exc))
        else:
            st.success(
                "자동 작성 순서를 저장했습니다."
                if saved_values
                else "자동 작성 순서를 비웠습니다."
            )
            st.rerun()

    st.caption(
        f"성능 데이터 캐시 {_age_caption(PERFORMANCE_TTL_SECONDS)} · "
        f"마지막 정상 성능 갱신 {performance.refreshed_at or '없음'} · "
        "성능 갱신은 별도 타이머가 아니라 필요한 시점에 24시간 경과 여부를 확인합니다."
    )

    with st.expander(
        f"전체 0원 모델 목록 · {len(rows)}개 · 글쓰기 성능순",
        expanded=False,
    ):
        if not rows:
            st.info(
                "현재 저장된 0원 모델이 없습니다. API 키/Free 플랜 설정을 확인한 뒤 "
                "‘전체 모델 로드’를 실행하세요."
            )
            return
        table_rows = [
            {
                "Provider": row["provider_label"],
                "모델": row["model_id"],
                "글쓰기": _score_text(row.get("writing")),
                "분석/추론": _score_text(row.get("reasoning")),
                "지시준수": _score_text(row.get("instruction_following")),
                "종합": _score_text(row.get("overall")),
                "신뢰도": row.get("confidence") or "unknown",
                "0원 확인": row.get("zero_cost_reason") or "",
                "상태": "이전 목록" if row.get("stale") else "현재",
            }
            for row in rows
        ]
        st.dataframe(
            table_rows,
            width="stretch",
            hide_index=True,
        )
        st.caption(
            "목록 정렬은 글쓰기 → 분석/추론 → 지시준수 → 종합 순입니다. "
            "성능 미평가 모델도 숨기지 않고 아래쪽에 표시합니다."
        )


def _install_settings_wrapper(caller_globals: dict[str, object]) -> None:
    target = caller_globals.get("_render_gemini_model_settings")
    streamlit_module = caller_globals.get("st")
    if (
        not callable(target)
        or streamlit_module is None
        or getattr(target, "_automatic_writing_model_settings_wrapper", False)
    ):
        return

    @wraps(target)
    def wrapped(con, *args, **kwargs):
        result = target(con, *args, **kwargs)
        render_automatic_writing_model_settings(con, st_module=streamlit_module)
        return result

    wrapped._automatic_writing_model_settings_wrapper = True  # type: ignore[attr-defined]
    caller_globals["_render_gemini_model_settings"] = wrapped


def install_automatic_writing_model_settings_runtime(ui_module) -> None:
    target = getattr(ui_module, "_install_inline_version_caption_ui", None)
    if not callable(target) or getattr(
        target,
        "_automatic_writing_model_settings_runtime_wrapper",
        False,
    ):
        return

    @wraps(target)
    def wrapped(caller_globals: dict[str, object]) -> None:
        _install_settings_wrapper(caller_globals)
        target(caller_globals)

    wrapped._automatic_writing_model_settings_runtime_wrapper = True  # type: ignore[attr-defined]
    ui_module._install_inline_version_caption_ui = wrapped
