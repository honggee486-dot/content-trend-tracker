from __future__ import annotations

import base64
from functools import wraps
import hashlib
import html
import inspect

import streamlit as st
import streamlit.components.v1 as components

from src.app_version import format_app_version_label


PAGE_HEADER_TITLES = {
    "오늘의 트렌드": "오늘의 트렌드",
    "주제·트렌드": "주제·트렌드",
    "AI 요청서": "AI 요청서",
    "AI 결과 가져오기": "AI 결과 가져오기",
    "글 편집": "글 편집",
    "발행 보조": "발행 보조",
    "설정": "설정",
}


_CANDIDATE_ANGLE_STATUS_CSS = """
<style>
.st-key-trend_candidate_list::before {
    content: "연한 녹색: 방향 API 저장 완료 · 연한 주황색: 방향 API 미저장";
    display: block;
    margin: 0 0 0.38rem 0;
    padding: 0.34rem 0.55rem;
    border: 1px solid rgba(128, 128, 128, 0.22);
    border-radius: 0.45rem;
    background: rgba(128, 128, 128, 0.06);
    color: var(--text-color);
    font-size: 0.72rem;
    line-height: 1.3;
}
[class*="st-key-trend_candidate_row_"]:has(.status-tag.ai-ready):not([class*="_selected"]) {
    background-color: rgba(16, 185, 129, 0.11) !important;
}
[class*="st-key-trend_candidate_row_"]:has(.status-tag.ai-pending):not([class*="_selected"]) {
    background-color: rgba(245, 158, 11, 0.10) !important;
}
.status-tag.ai-ready,
.status-tag.ai-pending {
    font-size: 0 !important;
}
.status-tag.ai-ready {
    background-color: rgba(16, 185, 129, 0.16);
}
.status-tag.ai-pending {
    background-color: rgba(245, 158, 11, 0.16);
}
.status-tag.status-추천::after,
.status-tag.status-검토::after,
.status-tag.status-보류::after {
    font-size: 0.68rem;
    font-weight: 700;
}
.status-tag.status-추천::after { content: "추천"; }
.status-tag.status-검토::after { content: "검토"; }
.status-tag.status-보류::after { content: "보류"; }
[class*="st-key-trend_candidate_row_"][class*="_selected"] {
    background-color: rgba(99, 102, 241, 0.15) !important;
}
</style>
"""


_LEGACY_GEMINI_CAPACITY_SENTENCE = "기본 구성은 100개를 1회 요청으로 처리합니다."
_CURRENT_GEMINI_CAPACITY_SENTENCE = "현재 설정값 기준으로 위 버튼 1회 최대치가 적용됩니다."
_VERSION_CAPTION_PREFIX = "현재 버전:"
_CONTENT_PACK_PROMPT_LABEL = "ChatGPT 또는 Gemini에 그대로 붙여넣기"
_FIXED_CLUSTERING_NUMBER_INPUTS = {
    "Gemini 요청 1회당 1차 군집": {
        "value": 300,
        "min_value": 20,
        "max_value": 300,
        "step": 20,
        "help": (
            "현재 백그라운드 2차 군집 작업은 요청당 최대 300개로 고정됩니다. "
            "요청 문자 상한에 따라 실제 요청 수는 더 작을 수 있습니다."
        ),
    },
    "백그라운드 작업 1회당 최대 Gemini 요청": {
        "value": 20,
        "min_value": 1,
        "max_value": 20,
        "step": 1,
        "help": (
            "현재 백그라운드 2차 군집 작업은 최대 20회로 고정되며, "
            "미처리 자료가 없으면 그 전에 종료합니다."
        ),
    },
}
_GEMINI_CAPTION_REPLACEMENTS = (
    (
        "Flash-Lite는 1차 군집 최대 200개를",
        "Flash-Lite는 1차 군집 최대 300개를",
    ),
    (
        "수동 실행은 별도 프로세스에서 최대 5배치를 처리하고",
        "수동 실행은 별도 프로세스에서 최대 20배치를 처리하고",
    ),
    (
        "수동 실행은 별도 프로세스에서 최대 10배치를 처리하고",
        "수동 실행은 별도 프로세스에서 최대 20배치를 처리하고",
    ),
)


def _rewrite_gemini_capacity_caption(value: object) -> str:
    """과거 고정 처리량 문구를 화면의 현재 계산값과 충돌하지 않게 보정합니다."""
    text = str(value or "").replace(
        _LEGACY_GEMINI_CAPACITY_SENTENCE,
        _CURRENT_GEMINI_CAPACITY_SENTENCE,
    )
    for legacy, current in _GEMINI_CAPTION_REPLACEMENTS:
        text = text.replace(legacy, current)
    return text


class _StreamlitCaptionProxy:
    def __init__(self, target) -> None:
        self._target = target

    def __getattr__(self, name: str):
        return getattr(self._target, name)

    def caption(self, value: object, *args, **kwargs):
        return self._target.caption(
            _rewrite_gemini_capacity_caption(value),
            *args,
            **kwargs,
        )

    def number_input(self, label: object, *args, **kwargs):
        contract = _FIXED_CLUSTERING_NUMBER_INPUTS.get(str(label or ""))
        if contract is None:
            return self._target.number_input(label, *args, **kwargs)

        render_kwargs = dict(kwargs)
        render_kwargs.update(contract)
        render_kwargs["disabled"] = True
        return self._target.number_input(label, *args, **render_kwargs)


class _StreamlitInlineVersionProxy:
    def __init__(self, target) -> None:
        self._target = target
        self._inline_version_caption_proxy = True

    def __getattr__(self, name: str):
        return getattr(self._target, name)

    def caption(self, value: object, *args, **kwargs):
        if str(value or "").strip().startswith(_VERSION_CAPTION_PREFIX):
            return None
        return self._target.caption(value, *args, **kwargs)


class _ContentPackRequestLayoutProxy:
    """Render the generated AI request at half width with compact actions beside it."""

    def __init__(self, target) -> None:
        self._target = target
        self._request_action_parent = None

    def __getattr__(self, name: str):
        return getattr(self._target, name)

    def text_area(self, label: object, *args, **kwargs):
        if str(label or "") != _CONTENT_PACK_PROMPT_LABEL:
            return self._target.text_area(label, *args, **kwargs)

        layout_cols = self._target.columns(
            [1.0, 1.0],
            gap="medium",
            vertical_alignment="top",
        )
        with layout_cols[0]:
            result = self._target.text_area(label, *args, **kwargs)
        self._request_action_parent = layout_cols[1]
        return result

    def columns(self, spec, *args, **kwargs):
        is_request_action_row = (
            self._request_action_parent is not None
            and isinstance(spec, (list, tuple))
            and tuple(spec) == (1, 1)
        )
        if not is_request_action_row:
            return self._target.columns(spec, *args, **kwargs)

        action_parent = self._request_action_parent
        self._request_action_parent = None
        with action_parent:
            return self._target.columns(
                [0.9, 1.1],
                gap="small",
                vertical_alignment="top",
            )


def _install_inline_version_caption_ui(caller_globals: dict[str, object]) -> None:
    streamlit_module = caller_globals.get("st")
    if streamlit_module is None or getattr(
        streamlit_module,
        "_inline_version_caption_proxy",
        False,
    ):
        return
    caller_globals["st"] = _StreamlitInlineVersionProxy(streamlit_module)


def build_page_header_title(value: object, version: object) -> str:
    page = str(value or "").strip()
    title = PAGE_HEADER_TITLES.get(page, "콘텐츠 트렌드 트래커")
    version_label = format_app_version_label(version)
    return (
        f'<span class="app-page-title-text">{html.escape(title)}</span>'
        '<span class="app-page-version" '
        'style="margin-left:0.45rem;color:rgba(128,128,128,0.78);'
        'font-size:0.72rem;font-weight:500;letter-spacing:0;white-space:nowrap;">'
        f'{html.escape(version_label)}</span>'
    )


def _decorate_ranked_trends_with_angle_state(con, rankings):
    """목록 행 렌더링에 쓸 방향 API 저장 상태를 판정 문자열의 CSS 토큰으로 붙입니다."""
    if rankings is None or getattr(rankings, "empty", True):
        return rankings
    if "cluster_id" not in rankings.columns or "판정" not in rankings.columns:
        return rankings

    cluster_ids = [str(value) for value in rankings["cluster_id"].tolist()]
    if not cluster_ids:
        return rankings

    placeholders = ", ".join("?" for _ in cluster_ids)
    try:
        rows = con.execute(
            f"""
            SELECT cluster_id, COUNT(*) AS angle_count
            FROM trend_cluster_ai_angles
            WHERE cluster_id IN ({placeholders})
            GROUP BY cluster_id
            """,
            cluster_ids,
        ).fetchall()
    except Exception:
        return rankings

    completed_ids = {
        str(cluster_id)
        for cluster_id, angle_count in rows
        if int(angle_count or 0) >= 3
    }
    decorated = rankings.copy()
    decorated["판정"] = [
        f"{str(status).split(' ai-', 1)[0]} "
        f"{'ai-ready' if cluster_id in completed_ids else 'ai-pending'}"
        for status, cluster_id in zip(
            decorated["판정"].tolist(),
            cluster_ids,
            strict=True,
        )
    ]
    return decorated


def _install_candidate_angle_status_ui(caller_globals: dict[str, object]) -> None:
    target = caller_globals.get("list_ranked_trends")
    if not callable(target) or getattr(target, "_candidate_angle_status_wrapper", False):
        return

    @wraps(target)
    def wrapped(con, *args, **kwargs):
        rankings = target(con, *args, **kwargs)
        return _decorate_ranked_trends_with_angle_state(con, rankings)

    wrapped._candidate_angle_status_wrapper = True  # type: ignore[attr-defined]
    caller_globals["list_ranked_trends"] = wrapped


def _install_content_work_queue_ui(caller_globals: dict[str, object]) -> None:
    target = caller_globals.get("render_trend_dashboard")
    db_connection = caller_globals.get("db_connection")
    navigate = caller_globals.get("navigate_to_page")
    if (
        not callable(target)
        or not callable(db_connection)
        or not callable(navigate)
        or getattr(target, "_content_work_queue_wrapper", False)
    ):
        return

    @wraps(target)
    def wrapped(*args, **kwargs):
        pending_key = str(caller_globals.get("TREND_REFRESH_ACTION_KEY") or "")
        pending_action = st.session_state.get(pending_key) if pending_key else None
        if not pending_action:
            from src.content_work_queue_ui import render_content_work_queue

            try:
                with db_connection() as con:
                    render_content_work_queue(
                        con,
                        st_module=st,
                        navigate=navigate,
                    )
            except Exception as exc:
                st.caption(f"콘텐츠 작업 대기열을 불러오지 못했습니다: {exc}")
        return target(*args, **kwargs)

    wrapped._content_work_queue_wrapper = True  # type: ignore[attr-defined]
    caller_globals["render_trend_dashboard"] = wrapped


def _install_gemini_capacity_caption_ui(caller_globals: dict[str, object]) -> None:
    target = caller_globals.get("_render_gemini_model_settings")
    streamlit_module = caller_globals.get("st")
    if (
        not callable(target)
        or streamlit_module is None
        or getattr(target, "_gemini_capacity_caption_wrapper", False)
    ):
        return

    @wraps(target)
    def wrapped(*args, **kwargs):
        original_streamlit = caller_globals.get("st")
        caller_globals["st"] = _StreamlitCaptionProxy(original_streamlit)
        try:
            return target(*args, **kwargs)
        finally:
            caller_globals["st"] = original_streamlit

    wrapped._gemini_capacity_caption_wrapper = True  # type: ignore[attr-defined]
    caller_globals["_render_gemini_model_settings"] = wrapped


def _install_publish_history_ui(caller_globals: dict[str, object]) -> None:
    target = caller_globals.get("render_publish")
    db_connection = caller_globals.get("db_connection")
    if (
        not callable(target)
        or not callable(db_connection)
        or getattr(target, "_publish_history_wrapper", False)
    ):
        return

    @wraps(target)
    def wrapped(*args, **kwargs):
        result = target(*args, **kwargs)
        from src.publish_history_ui import render_publish_history_panel

        try:
            with db_connection() as con:
                render_publish_history_panel(con, st_module=st)
        except Exception as exc:
            st.caption(f"발행 기록 관리 화면을 불러오지 못했습니다: {exc}")
        return result

    wrapped._publish_history_wrapper = True  # type: ignore[attr-defined]
    caller_globals["render_publish"] = wrapped


def _install_content_pack_history_ui(caller_globals: dict[str, object]) -> None:
    target = caller_globals.get("render_content_pack")
    db_connection = caller_globals.get("db_connection")
    original_get_defaults = caller_globals.get("get_topic_content_defaults")
    original_save_content_pack = caller_globals.get("save_content_pack")
    if (
        not callable(target)
        or not callable(db_connection)
        or not callable(original_get_defaults)
        or not callable(original_save_content_pack)
        or getattr(target, "_content_pack_history_wrapper", False)
    ):
        return

    @wraps(target)
    def wrapped(*args, **kwargs):
        from src.content_pack_history_ui import (
            REUSE_FLASH_KEY,
            REUSE_PAYLOAD_KEY,
            render_active_content_pack_reuse_notice,
            render_content_pack_history_panel,
        )

        payload_value = st.session_state.get(REUSE_PAYLOAD_KEY)
        payload = dict(payload_value) if isinstance(payload_value, dict) else None
        if payload is not None:
            topic_id = str(payload.get("topic_id") or "")
            if topic_id:
                st.session_state["prefill_topic_id"] = topic_id
            if topic_id and not bool(payload.get("evidence_applied")):
                st.session_state[f"selected_evidence_{topic_id}"] = list(
                    payload.get("selected_source_item_ids") or []
                )
                st.session_state[f"selected_factual_references_{topic_id}"] = list(
                    payload.get("selected_reference_ids") or []
                )
                payload["evidence_applied"] = True
                st.session_state[REUSE_PAYLOAD_KEY] = payload

            def reused_get_defaults(con, *default_args, **default_kwargs):
                requested_topic_id = str(
                    default_kwargs.get("topic_id")
                    or (default_args[0] if default_args else "")
                )
                if requested_topic_id == topic_id:
                    return dict(payload.get("defaults") or {})
                return original_get_defaults(con, *default_args, **default_kwargs)

            def tracked_save_content_pack(con, *save_args, **save_kwargs):
                result = original_save_content_pack(con, *save_args, **save_kwargs)
                saved_topic_id = str(
                    save_kwargs.get("topic_id")
                    or (save_args[0] if save_args else "")
                )
                if saved_topic_id == topic_id:
                    st.session_state.pop(REUSE_PAYLOAD_KEY, None)
                    st.session_state[REUSE_FLASH_KEY] = (
                        f"과거 자료팩 입력값을 검토해 새 자료팩 v{int(result.get('version') or 0)}로 저장했습니다."
                    )
                return result

            caller_globals["get_topic_content_defaults"] = reused_get_defaults
            caller_globals["save_content_pack"] = tracked_save_content_pack

        original_streamlit = caller_globals.get("st")
        if original_streamlit is not None:
            caller_globals["st"] = _ContentPackRequestLayoutProxy(original_streamlit)

        render_active_content_pack_reuse_notice(st_module=st)
        try:
            result = target(*args, **kwargs)
        finally:
            if original_streamlit is not None:
                caller_globals["st"] = original_streamlit
            if payload is not None:
                caller_globals["get_topic_content_defaults"] = original_get_defaults
                caller_globals["save_content_pack"] = original_save_content_pack

        try:
            with db_connection() as con:
                render_content_pack_history_panel(con, st_module=st)
        except Exception as exc:
            st.caption(f"자료팩 버전 기록을 불러오지 못했습니다: {exc}")
        return result

    wrapped._content_pack_history_wrapper = True  # type: ignore[attr-defined]
    caller_globals["render_content_pack"] = wrapped


def _install_draft_revision_ui(caller_globals: dict[str, object]) -> None:
    target = caller_globals.get("render_editor")
    db_connection = caller_globals.get("db_connection")
    original_get_draft = caller_globals.get("get_draft")
    if (
        not callable(target)
        or not callable(db_connection)
        or not callable(original_get_draft)
        or getattr(target, "_draft_revision_wrapper", False)
    ):
        return

    @wraps(target)
    def wrapped(*args, **kwargs):
        captured_draft_ids: list[str] = []

        def tracking_get_draft(con, draft_id):
            captured_draft_ids.append(str(draft_id))
            return original_get_draft(con, draft_id)

        caller_globals["get_draft"] = tracking_get_draft
        try:
            result = target(*args, **kwargs)
        finally:
            caller_globals["get_draft"] = original_get_draft

        if captured_draft_ids:
            from src.draft_revision_ui import render_draft_revision_panel

            try:
                with db_connection() as con:
                    render_draft_revision_panel(
                        con,
                        captured_draft_ids[-1],
                        st_module=st,
                    )
            except Exception as exc:
                st.caption(f"초안 버전 기록을 불러오지 못했습니다: {exc}")
        return result

    wrapped._draft_revision_wrapper = True  # type: ignore[attr-defined]
    caller_globals["render_editor"] = wrapped


def page_header_title(value: object) -> str:
    page = str(value or "").strip()
    caller = inspect.currentframe().f_back
    caller_globals = caller.f_globals if caller is not None else {}
    if "list_ranked_trends" in caller_globals and "st" in caller_globals:
        _install_candidate_angle_status_ui(caller_globals)
        st.markdown(_CANDIDATE_ANGLE_STATUS_CSS, unsafe_allow_html=True)
    if "render_trend_dashboard" in caller_globals:
        _install_content_work_queue_ui(caller_globals)
    if "_render_gemini_model_settings" in caller_globals:
        _install_gemini_capacity_caption_ui(caller_globals)
    if "render_publish" in caller_globals:
        _install_publish_history_ui(caller_globals)
    if "render_content_pack" in caller_globals:
        _install_content_pack_history_ui(caller_globals)
    if "render_editor" in caller_globals:
        _install_draft_revision_ui(caller_globals)
    if "APP_VERSION" in caller_globals and "st" in caller_globals:
        _install_inline_version_caption_ui(caller_globals)
        return build_page_header_title(page, caller_globals.get("APP_VERSION"))
    return PAGE_HEADER_TITLES.get(page, "콘텐츠 트렌드 트래커")


TREND_DASHBOARD_ACTION_LABELS = {
    "refresh": "최신 데이터 수집·분석",
    "rebuild": "저장 자료 정리·순위 다시 계산",
    "angles": "주제 방향 자동 생성",
}


def normalize_trend_dashboard_action(value: object) -> str:
    action = str(value or "").strip()
    return action if action in TREND_DASHBOARD_ACTION_LABELS else ""


def trend_dashboard_navigation_locked(value: object) -> bool:
    return bool(normalize_trend_dashboard_action(value))


def trend_dashboard_action_label(value: object) -> str:
    action = normalize_trend_dashboard_action(value)
    return TREND_DASHBOARD_ACTION_LABELS.get(action, "")


def _component_token(key: str) -> str:
    return hashlib.sha1(str(key).encode("utf-8")).hexdigest()[:16]


def render_copy_button(label: str, text: str, *, key: str, height: int = 44) -> None:
    payload = base64.b64encode(str(text or "").encode("utf-8")).decode("ascii")
    safe_label = html.escape(label)
    token = _component_token(key)
    components.html(
        f"""
        <div id="copy-{token}">
          <button id="copy-button-{token}"
            style="width:100%;padding:8px 12px;border:1px solid #bbb;border-radius:8px;
                   background:white;cursor:pointer;font-size:14px;">
            {safe_label}
          </button>
          <span id="copy-msg-{token}" style="font-size:12px;margin-left:8px;"></span>
        </div>
        <script>
        document.getElementById("copy-button-{token}").addEventListener("click", async () => {{
          const raw = atob("{payload}");
          const bytes = Uint8Array.from(raw, c => c.charCodeAt(0));
          const text = new TextDecoder().decode(bytes);
          const msg = document.getElementById("copy-msg-{token}");
          try {{
            await navigator.clipboard.writeText(text);
            msg.textContent = "복사됨";
          }} catch (err) {{
            msg.textContent = "복사 실패: 아래 내용을 직접 복사하세요.";
          }}
        }});
        </script>
        """,
        height=height,
    )


def render_chatgpt_request_button(
    text: str,
    *,
    key: str,
    label: str = "ChatGPT에서 요청하기",
    height: int = 68,
) -> None:
    """Copy an AI request and open ChatGPT without automating input or submission."""
    payload = base64.b64encode(str(text or "").encode("utf-8")).decode("ascii")
    safe_label = html.escape(label)
    token = _component_token(key)
    components.html(
        f"""
        <div id="chatgpt-request-{token}" style="width:100%;">
          <a id="chatgpt-button-{token}"
             href="https://chatgpt.com/"
             target="_blank"
             rel="noopener noreferrer"
             style="display:flex;align-items:center;justify-content:center;width:100%;
                    box-sizing:border-box;padding:9px 12px;border:1px solid #111827;
                    border-radius:8px;background:#111827;color:white;text-decoration:none;
                    cursor:pointer;font-size:14px;font-weight:600;">
            {safe_label}
          </a>
          <div id="chatgpt-msg-{token}"
               style="font-size:12px;line-height:1.35;margin-top:5px;color:#4b5563;"></div>
        </div>
        <script>
        document.getElementById("chatgpt-button-{token}").addEventListener("click", () => {{
          const raw = atob("{payload}");
          const bytes = Uint8Array.from(raw, c => c.charCodeAt(0));
          const text = new TextDecoder().decode(bytes);
          const msg = document.getElementById("chatgpt-msg-{token}");
          msg.textContent = "요청서를 복사하는 중입니다...";
          try {{
            if (!navigator.clipboard || !navigator.clipboard.writeText) {{
              throw new Error("clipboard unavailable");
            }}
            navigator.clipboard.writeText(text).then(() => {{
              msg.textContent = "요청서를 복사했습니다. 열린 ChatGPT에서 Ctrl+V 후 전송하세요.";
            }}).catch(() => {{
              msg.textContent = "ChatGPT는 열었지만 복사하지 못했습니다. 위 요청서를 직접 복사하세요.";
            }});
          }} catch (err) {{
            msg.textContent = "ChatGPT는 열었지만 복사하지 못했습니다. 위 요청서를 직접 복사하세요.";
          }}
        }});
        </script>
        """,
        height=height,
    )
