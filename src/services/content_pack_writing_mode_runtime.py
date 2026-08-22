from __future__ import annotations

import json
from functools import wraps
from typing import Any, Mapping

from src.services.topic_angle_writing_mode_runtime import (
    WRITING_MODE_AUTO,
    WRITING_MODE_MANUAL,
    writing_mode_from_plan,
)


def _load_cluster_plan(con: Any, cluster_id: str) -> dict[str, Any]:
    if not cluster_id:
        return {}
    row = con.execute(
        """
        SELECT content_plan_json
        FROM trend_cluster_ai_profiles
        WHERE cluster_id = ?
        """,
        [cluster_id],
    ).fetchone()
    if row is None:
        return {}
    try:
        parsed = json.loads(str(row[0] or "{}"))
    except (TypeError, json.JSONDecodeError):
        return {}
    return dict(parsed) if isinstance(parsed, Mapping) else {}


def _install_defaults_contract(module) -> None:
    target = module.get_topic_content_defaults
    if getattr(target, "_writing_mode_defaults_contract", False):
        return

    @wraps(target)
    def wrapped(con, *args, **kwargs):
        defaults = dict(target(con, *args, **kwargs))
        cluster_id = str(defaults.get("source_cluster_id") or "").strip()
        plan = _load_cluster_plan(con, cluster_id)
        mode, reason = writing_mode_from_plan(plan)
        defaults["writing_mode_recommendation"] = mode
        defaults["writing_mode_reason"] = reason
        defaults["writing_mode_source"] = (
            "topic_angle_ai"
            if str(plan.get("writing_mode_recommendation") or "").strip()
            else "legacy_safe_default"
        )
        return defaults

    wrapped._writing_mode_defaults_contract = True  # type: ignore[attr-defined]
    module.get_topic_content_defaults = wrapped


def install_content_pack_writing_mode_contract() -> None:
    """Expose topic-angle writing mode recommendation through content-pack defaults."""

    import src.services.content_pack_service as module

    if getattr(module, "_writing_mode_contract_installed", False):
        return
    _install_defaults_contract(module)
    module._writing_mode_contract_installed = True


def _mode_label(mode: str) -> str:
    return "자동 작성" if mode == WRITING_MODE_AUTO else "수동 작성"


def render_writing_mode_recommendation(
    defaults: Mapping[str, Any],
    *,
    topic_id: str,
    st_module,
) -> str:
    """Render the advisory route selector and return the user's current choice."""

    st = st_module
    mode = str(defaults.get("writing_mode_recommendation") or WRITING_MODE_MANUAL)
    if mode not in {WRITING_MODE_AUTO, WRITING_MODE_MANUAL}:
        mode = WRITING_MODE_MANUAL
    reason = str(defaults.get("writing_mode_reason") or "").strip()
    source = str(defaults.get("writing_mode_source") or "")

    if mode == WRITING_MODE_AUTO:
        st.success(
            "작성 방식 추천: 자동 작성",
            icon=":material/automation:",
        )
    else:
        st.warning(
            "작성 방식 추천: 수동 작성",
            icon=":material/person_edit:",
        )
    if reason:
        st.caption(reason)
    if source == "legacy_safe_default":
        st.caption(
            "기존 주제 분석에는 작성 방식 판정이 없어 안전하게 수동 추천으로 표시합니다. "
            "새 주제 방향 분석부터 자동/수동 판정이 함께 저장됩니다."
        )

    primary_reason = str(defaults.get("primary_direction_reason") or "").strip()
    if primary_reason:
        st.caption(
            "주제 방향 기본값: 검증 점수 1순위 방향이 ‘글의 관점’에 이미 선택되어 있습니다. "
            + primary_reason
        )

    choice_key = f"content_pack_writing_mode_choice_{topic_id or 'unknown'}"
    current = str(st.session_state.get(choice_key) or mode)
    if current not in {WRITING_MODE_AUTO, WRITING_MODE_MANUAL}:
        current = mode

    st.caption("추천된 작성 방식은 강조 표시되며, 반대 방식을 눌러 언제든 변경할 수 있습니다.")
    auto_col, manual_col = st.columns(2, gap="small")
    auto_clicked = auto_col.button(
        "자동 작성",
        type="primary" if mode == WRITING_MODE_AUTO else "secondary",
        width="stretch",
        key=f"{choice_key}_auto",
    )
    manual_clicked = manual_col.button(
        "수동 작성",
        type="primary" if mode == WRITING_MODE_MANUAL else "secondary",
        width="stretch",
        key=f"{choice_key}_manual",
    )
    if auto_clicked:
        current = WRITING_MODE_AUTO
        st.session_state[choice_key] = current
    elif manual_clicked:
        current = WRITING_MODE_MANUAL
        st.session_state[choice_key] = current

    st.caption(f"현재 선택: {_mode_label(current)}")
    if current != mode:
        st.caption(
            "추천과 다른 방식을 선택했습니다. 수동 추천 주제를 자동으로 진행하면 사실 확인 부담이 더 클 수 있습니다."
            if mode == WRITING_MODE_MANUAL
            else "추천과 다른 방식을 선택했습니다. 기존 수동 ChatGPT 전달 흐름을 그대로 사용할 수 있습니다."
        )
    return current


def _install_content_pack_ui_wrapper(caller_globals: dict[str, object]) -> None:
    target = caller_globals.get("render_content_pack")
    streamlit_module = caller_globals.get("st")
    if (
        not callable(target)
        or streamlit_module is None
        or getattr(target, "_writing_mode_ui_wrapper", False)
    ):
        return

    @wraps(target)
    def wrapped(*args, **kwargs):
        current_defaults = caller_globals.get("get_topic_content_defaults")
        if not callable(current_defaults):
            return target(*args, **kwargs)

        @wraps(current_defaults)
        def defaults_with_recommendation_ui(con, *default_args, **default_kwargs):
            defaults = current_defaults(con, *default_args, **default_kwargs)
            topic_id = str(
                default_kwargs.get("topic_id")
                or (default_args[0] if default_args else "")
                or defaults.get("source_cluster_id")
                or ""
            )
            render_writing_mode_recommendation(
                defaults,
                topic_id=topic_id,
                st_module=streamlit_module,
            )
            return defaults

        caller_globals["get_topic_content_defaults"] = defaults_with_recommendation_ui
        try:
            return target(*args, **kwargs)
        finally:
            caller_globals["get_topic_content_defaults"] = current_defaults

    wrapped._writing_mode_ui_wrapper = True  # type: ignore[attr-defined]
    caller_globals["render_content_pack"] = wrapped


def install_content_pack_writing_mode_ui_runtime(ui_module) -> None:
    """Install the recommendation panel before the existing content-pack form."""

    target = getattr(ui_module, "_install_content_pack_history_ui", None)
    if not callable(target) or getattr(
        target,
        "_writing_mode_ui_runtime_wrapper",
        False,
    ):
        return

    @wraps(target)
    def wrapped(caller_globals: dict[str, object]) -> None:
        _install_content_pack_ui_wrapper(caller_globals)
        target(caller_globals)

    wrapped._writing_mode_ui_runtime_wrapper = True  # type: ignore[attr-defined]
    ui_module._install_content_pack_history_ui = wrapped
