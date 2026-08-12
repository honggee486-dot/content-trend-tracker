from __future__ import annotations

from functools import wraps
from typing import Any, Mapping, Sequence

from src.config import DEFAULT_DB_PATH, PROJECT_ROOT
from src.services.web_update_service import (
    WorkBranchCandidate,
    check_update_readiness,
    deserialize_candidates,
    discover_work_branches,
    is_local_request,
    launch_update_and_restart,
    read_update_status,
    serialize_candidates,
)

TOP_NAVIGATION_KEY = "app_top_navigation"
UPDATE_POPOVER_LABEL = "앱 업데이트"
_BRANCH_CACHE_KEY = "web_update_branch_candidates"
_BRANCH_ERROR_KEY = "web_update_branch_error"
_BRANCH_SELECTION_KEY_PREFIX = "web_update_selected_branch"
_CONFIRM_KEY_PREFIX = "web_update_confirm"


def _request_headers(st_module: Any) -> Mapping[str, Any]:
    try:
        context = getattr(st_module, "context")
        headers = getattr(context, "headers")
        return dict(headers)
    except Exception:
        return {}


def _render_previous_status(st_module: Any) -> None:
    status = read_update_status()
    if not status:
        return
    state = str(status.get("status") or "")
    branch = str(status.get("branch_name") or "")
    short_sha = str(status.get("expected_sha") or "")[:8]
    message = str(status.get("message") or "").strip()
    updated_at = str(status.get("updated_at") or "").strip()
    prefix = " · ".join(value for value in (branch, short_sha, updated_at) if value)
    detail = f"{prefix}\n\n{message}" if prefix and message else prefix or message
    if state == "success":
        st_module.success(detail or "최근 앱 업데이트를 완료했습니다.")
    elif state in {"failed", "failed_restarted", "failed_restart_required"}:
        st_module.error(detail or "최근 앱 업데이트가 실패했습니다.")
    elif state:
        st_module.info(detail or "앱 업데이트 작업 상태를 확인하고 있습니다.")


def _refresh_candidates(st_module: Any) -> list[WorkBranchCandidate]:
    try:
        with st_module.spinner("원격 origin/work/* 브랜치를 확인하는 중입니다..."):
            candidates = discover_work_branches(PROJECT_ROOT)
        st_module.session_state[_BRANCH_CACHE_KEY] = serialize_candidates(candidates)
        st_module.session_state.pop(_BRANCH_ERROR_KEY, None)
        return candidates
    except Exception as exc:
        st_module.session_state[_BRANCH_ERROR_KEY] = str(exc)
        st_module.session_state[_BRANCH_CACHE_KEY] = []
        return []


def _cached_candidates(st_module: Any) -> list[WorkBranchCandidate]:
    values = st_module.session_state.get(_BRANCH_CACHE_KEY)
    if not isinstance(values, list):
        return []
    try:
        return deserialize_candidates(value for value in values if isinstance(value, dict))
    except Exception:
        st_module.session_state[_BRANCH_CACHE_KEY] = []
        return []


def _format_branch(candidate: WorkBranchCandidate) -> str:
    return (
        f"{candidate.branch_name} · {candidate.commit_sha[:8]} · "
        f"ahead {candidate.ahead}"
    )


def _render_candidate_information(
    st_module: Any,
    candidate: WorkBranchCandidate,
    *,
    compact: bool,
) -> None:
    if compact:
        st_module.markdown(
            "\n".join(
                (
                    f"**브랜치** `{candidate.branch_name}`",
                    f"**커밋** `{candidate.commit_sha[:12]}`",
                    f"**main 대비** ahead {candidate.ahead} · behind {candidate.behind}",
                    f"**변경 파일** {candidate.changed_files:,}개",
                )
            )
        )
    else:
        info_cols = st_module.columns(4, gap="small")
        info_cols[0].metric("작업 브랜치", candidate.branch_name, border=True)
        info_cols[1].metric("커밋", candidate.commit_sha[:12], border=True)
        info_cols[2].metric(
            "main 대비",
            f"ahead {candidate.ahead} · behind {candidate.behind}",
            border=True,
        )
        info_cols[3].metric("변경 파일", f"{candidate.changed_files:,}개", border=True)
    st_module.caption(f"원격 커밋 시각: {candidate.committed_at or '확인 불가'}")


def render_web_update_panel(st_module: Any, *, compact: bool = False) -> None:
    if compact:
        st_module.markdown("**로컬 앱 업데이트**")
    else:
        st_module.subheader("로컬 앱 업데이트")
    st_module.caption(
        "원격 origin/work/* 누적 브랜치를 조회해 기존 apply_update.bat으로 "
        "검증·적용한 뒤 앱을 다시 실행합니다. main은 변경하거나 push하지 않습니다."
    )
    _render_previous_status(st_module)

    local_access = is_local_request(_request_headers(st_module))
    if not local_access:
        st_module.error(
            "이 기능은 localhost, 127.0.0.1 또는 ::1로 접속한 로컬 브라우저에서만 사용할 수 있습니다."
        )

    refresh_clicked = st_module.button(
        "원격 브랜치 새로고침",
        key="web_update_refresh_branches",
        width="stretch",
    )
    candidates = _refresh_candidates(st_module) if refresh_clicked else _cached_candidates(st_module)
    error_message = str(st_module.session_state.get(_BRANCH_ERROR_KEY) or "")
    if error_message:
        st_module.error(f"원격 작업 브랜치를 조회하지 못했습니다: {error_message}")
        return
    if not candidates:
        st_module.info(
            "원격 브랜치 새로고침을 누르면 적용 가능한 work/<버전> 브랜치와 커밋을 표시합니다."
        )
        return

    eligible = [candidate for candidate in candidates if candidate.eligible]
    rejected = [candidate for candidate in candidates if not candidate.eligible]
    if rejected:
        with st_module.expander(f"적용 제외 브랜치 {len(rejected):,}개", expanded=False):
            for candidate in rejected:
                st_module.write(
                    f"- `{candidate.branch_name}`: {candidate.reason or '적용 조건 불충족'}"
                )
    if not eligible:
        st_module.info("origin/main보다 뒤처지지 않고 최소 1개 커밋 앞선 work/* 브랜치가 없습니다.")
        return

    option_names = [candidate.branch_name for candidate in eligible]
    selection_key = _BRANCH_SELECTION_KEY_PREFIX + "_" + str(abs(hash(tuple(option_names))))
    selected_name = st_module.selectbox(
        "적용할 누적 작업 브랜치",
        option_names,
        index=0,
        key=selection_key,
        format_func=lambda branch: _format_branch(
            next(candidate for candidate in eligible if candidate.branch_name == branch)
        ),
    )
    selected = next(
        candidate for candidate in eligible if candidate.branch_name == selected_name
    )
    _render_candidate_information(st_module, selected, compact=compact)

    try:
        readiness = check_update_readiness(
            selected,
            PROJECT_ROOT,
            DEFAULT_DB_PATH,
        )
    except Exception as exc:
        st_module.error(f"적용 전 상태를 확인하지 못했습니다: {exc}")
        return

    st_module.caption(
        f"현재 로컬: {readiness.current_branch or '분리 HEAD'} · "
        f"{readiness.current_sha[:12] or '확인 불가'}"
    )
    for blocker in readiness.blockers:
        st_module.warning(blocker)

    confirm_key = _CONFIRM_KEY_PREFIX + "_" + selected.commit_sha[:12]
    confirmed = st_module.checkbox(
        f"{selected.branch_name} · {selected.commit_sha[:12]} 적용과 앱 재시작을 확인했습니다.",
        key=confirm_key,
        disabled=not local_access or not readiness.ready,
    )
    apply_clicked = st_module.button(
        "선택한 작업 브랜치 적용 후 앱 재시작",
        type="primary",
        key="web_update_apply_and_restart",
        width="stretch",
        disabled=not local_access or not readiness.ready or not confirmed,
    )
    if not apply_clicked:
        return

    try:
        with st_module.spinner("원격 커밋과 실행 상태를 마지막으로 다시 확인하는 중입니다..."):
            latest = discover_work_branches(PROJECT_ROOT)
            verified = next(
                (
                    candidate
                    for candidate in latest
                    if candidate.branch_name == selected.branch_name
                ),
                None,
            )
            if verified is None:
                raise RuntimeError("선택한 원격 작업 브랜치가 더 이상 존재하지 않습니다.")
            if verified.commit_sha.casefold() != selected.commit_sha.casefold():
                raise RuntimeError(
                    "화면 확인 후 원격 브랜치 커밋이 변경되었습니다. 브랜치를 새로고침한 뒤 다시 확인하세요."
                )
            final_readiness = check_update_readiness(
                verified,
                PROJECT_ROOT,
                DEFAULT_DB_PATH,
            )
            if not final_readiness.ready:
                raise RuntimeError(" · ".join(final_readiness.blockers))
            worker_pid = launch_update_and_restart(verified, PROJECT_ROOT)
    except Exception as exc:
        st_module.error(f"업데이트 작업을 시작하지 못했습니다: {exc}")
        return

    st_module.success(
        f"업데이트 전용 프로세스를 시작했습니다 · PID {worker_pid}. "
        "약 2초 뒤 현재 앱 연결이 잠시 끊기며, 검증이 끝나면 자동으로 다시 실행됩니다."
    )
    st_module.caption(
        "적용 또는 검증이 실패해도 앱 재실행을 시도합니다. 자동 재시작까지 실패한 경우 run_app.bat을 직접 실행하세요."
    )


class _TopNavigationContext:
    def __init__(self, st_module: Any, original_context: Any) -> None:
        self._st_module = st_module
        self._original_context = original_context

    def __enter__(self):
        self._st_module._web_update_top_navigation_active = True
        self._st_module._web_update_top_navigation_column = None
        return self._original_context.__enter__()

    def __exit__(self, exc_type, exc, traceback):
        update_column = getattr(
            self._st_module,
            "_web_update_top_navigation_column",
            None,
        )
        self._st_module._web_update_top_navigation_active = False
        self._st_module._web_update_top_navigation_column = None
        if exc_type is None and update_column is not None:
            try:
                with update_column.popover(
                    UPDATE_POPOVER_LABEL,
                    use_container_width=True,
                ):
                    render_web_update_panel(self._st_module, compact=True)
            except Exception as render_exc:
                self._st_module.error(
                    f"앱 업데이트 메뉴를 표시하지 못했습니다: {render_exc}"
                )
        return self._original_context.__exit__(exc_type, exc, traceback)


def _is_column_specification(value: object) -> bool:
    return isinstance(value, Sequence) and not isinstance(value, (str, bytes)) and len(value) >= 2


def install_web_update_top_navigation_ui(st_module: Any) -> None:
    original_container = getattr(st_module, "container", None)
    original_columns = getattr(st_module, "columns", None)
    if (
        not callable(original_container)
        or not callable(original_columns)
        or getattr(st_module, "_web_update_top_navigation_installed", False)
    ):
        return

    st_module._web_update_top_navigation_installed = True
    st_module._web_update_top_navigation_active = False
    st_module._web_update_top_navigation_column = None

    @wraps(original_container)
    def wrapped_container(*args: Any, **kwargs: Any):
        context = original_container(*args, **kwargs)
        if str(kwargs.get("key") or "") != TOP_NAVIGATION_KEY:
            return context
        return _TopNavigationContext(st_module, context)

    @wraps(original_columns)
    def wrapped_columns(spec: Any, *args: Any, **kwargs: Any):
        columns = original_columns(spec, *args, **kwargs)
        if (
            bool(getattr(st_module, "_web_update_top_navigation_active", False))
            and _is_column_specification(spec)
            and len(columns) == len(spec)
        ):
            st_module._web_update_top_navigation_column = columns[-1]
        return columns

    st_module.container = wrapped_container
    st_module.columns = wrapped_columns
