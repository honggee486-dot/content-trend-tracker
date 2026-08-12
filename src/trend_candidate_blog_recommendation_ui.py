from __future__ import annotations

import base64
from functools import wraps
import html
import re
from typing import Any

from src.services.blog_channel_strategy_service import (
    list_managed_blog_channel_strategies,
)
from src.services.blog_profile_service import list_blog_profiles
from src.services.trend_blog_recommendation_service import (
    PLATFORM_PREFIXES,
    TISTORY_CHANNEL_KEY,
    build_trend_blog_recommendation_labels,
    format_recommended_blog_label,
    get_recommendation_display_name,
    set_recommendation_display_name,
)


_RECOMMENDATION_TOKEN_PREFIX = "blog-rec-"
_RECOMMENDATION_TOKEN_RE = re.compile(r"blog-rec-([A-Za-z0-9_-]+)")
_STATUS_RE = re.compile(r"status-(추천|검토|보류)")

_CANDIDATE_BLOG_RECOMMENDATION_CSS = """
<style>
.st-key-trend_candidate_table_header [data-testid="stHorizontalBlock"],
[class*="st-key-trend_candidate_row_"] [data-testid="stHorizontalBlock"] {
    grid-template-columns: 38px 78px 48px minmax(180px, 1fr) 46px 44px 44px 50px 70px 52px !important;
    min-width: 670px !important;
}
.trend-blog-recommendation-cell {
    flex-direction: column !important;
    align-items: center !important;
    justify-content: center !important;
    gap: 0.06rem !important;
    line-height: 1.08 !important;
}
.trend-blog-recommendation-cell .trend-blog-judgement {
    display: block;
    font-size: 0.68rem !important;
    font-weight: 750;
    line-height: 1.05;
}
.trend-blog-recommendation-cell .trend-blog-label {
    display: block;
    max-width: 72px;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
    font-size: 0.64rem !important;
    font-weight: 650;
    line-height: 1.05;
    opacity: 0.82;
}
</style>
"""


def encode_recommendation_label(value: object) -> str:
    raw = str(value or "").encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def decode_recommendation_label(value: object) -> str:
    token = str(value or "").strip()
    if not token:
        return ""
    padding = "=" * (-len(token) % 4)
    try:
        return base64.urlsafe_b64decode(token + padding).decode("utf-8")
    except (ValueError, UnicodeDecodeError):
        return ""


def decorate_rankings_with_blog_recommendations(con, rankings):
    if rankings is None or getattr(rankings, "empty", True):
        return rankings
    if "cluster_id" not in rankings.columns or "판정" not in rankings.columns:
        return rankings

    labels = build_trend_blog_recommendation_labels(
        con,
        rankings.to_dict("records"),
    )
    if not labels:
        return rankings

    decorated = rankings.copy()
    decorated["판정"] = [
        (
            f"{str(status)} {_RECOMMENDATION_TOKEN_PREFIX}"
            f"{encode_recommendation_label(labels.get(str(cluster_id), ''))}"
            if labels.get(str(cluster_id))
            else str(status)
        )
        for status, cluster_id in zip(
            decorated["판정"].tolist(),
            decorated["cluster_id"].tolist(),
            strict=True,
        )
    ]
    return decorated


def rewrite_candidate_markdown(value: object) -> object:
    if not isinstance(value, str):
        return value
    if 'candidate-tbl-hdr cell-center">판정</div>' in value:
        return value.replace(
            'candidate-tbl-hdr cell-center">판정</div>',
            'candidate-tbl-hdr cell-center">추천·검토</div>',
        )
    if "candidate-tbl-cell cell-center status-tag" not in value:
        return value

    token_match = _RECOMMENDATION_TOKEN_RE.search(value)
    status_match = _STATUS_RE.search(value)
    if token_match is None or status_match is None:
        return value
    label = decode_recommendation_label(token_match.group(1))
    if not label:
        return value

    ai_class = ""
    if "ai-ready" in value:
        ai_class = " ai-ready"
    elif "ai-pending" in value:
        ai_class = " ai-pending"
    status = html.escape(status_match.group(1))
    safe_label = html.escape(label)
    return (
        f'<div class="candidate-tbl-cell cell-center status-tag{ai_class} '
        'trend-blog-recommendation-cell">'
        f'<span class="trend-blog-judgement">{status}</span>'
        f'<span class="trend-blog-label" title="{safe_label}">{safe_label}</span>'
        '</div>'
    )


class _CandidateColumnProxy:
    def __init__(self, target: Any) -> None:
        self._target = target

    def __getattr__(self, name: str):
        return getattr(self._target, name)

    def __enter__(self):
        entered = self._target.__enter__()
        return self if entered is self._target else _CandidateColumnProxy(entered)

    def __exit__(self, exc_type, exc, tb):
        return self._target.__exit__(exc_type, exc, tb)

    def markdown(self, body: object, *args, **kwargs):
        return self._target.markdown(
            rewrite_candidate_markdown(body),
            *args,
            **kwargs,
        )


class _CandidateStreamlitProxy:
    def __init__(self, target: Any) -> None:
        self._target = target

    def __getattr__(self, name: str):
        return getattr(self._target, name)

    def columns(self, *args, **kwargs):
        columns = self._target.columns(*args, **kwargs)
        return [_CandidateColumnProxy(column) for column in columns]


def _install_ranked_trend_recommendation(caller_globals: dict[str, object]) -> None:
    target = caller_globals.get("list_ranked_trends")
    if not callable(target) or getattr(target, "_trend_blog_recommendation_wrapper", False):
        return

    @wraps(target)
    def wrapped(con, *args, **kwargs):
        rankings = target(con, *args, **kwargs)
        return decorate_rankings_with_blog_recommendations(con, rankings)

    wrapped._trend_blog_recommendation_wrapper = True  # type: ignore[attr-defined]
    caller_globals["list_ranked_trends"] = wrapped


def _install_candidate_table_renderer(caller_globals: dict[str, object]) -> None:
    target = caller_globals.get("render_trend_dashboard")
    streamlit_module = caller_globals.get("st")
    if (
        not callable(target)
        or streamlit_module is None
        or getattr(target, "_trend_blog_recommendation_render_wrapper", False)
    ):
        return

    @wraps(target)
    def wrapped(*args, **kwargs):
        original_streamlit = caller_globals.get("st")
        caller_globals["st"] = _CandidateStreamlitProxy(original_streamlit)
        try:
            return target(*args, **kwargs)
        finally:
            caller_globals["st"] = original_streamlit

    wrapped._trend_blog_recommendation_render_wrapper = True  # type: ignore[attr-defined]
    caller_globals["render_trend_dashboard"] = wrapped


def _profile_channel_key(
    profile: dict[str, object],
    strategy_by_profile_id: dict[str, str],
) -> str:
    profile_id = str(profile.get("blog_profile_id") or "")
    strategy_code = strategy_by_profile_id.get(profile_id, "")
    if strategy_code:
        return strategy_code
    if str(profile.get("platform") or "") == "tistory":
        return TISTORY_CHANNEL_KEY
    return ""


def render_blog_recommendation_name_settings(con, *, st_module) -> None:
    profiles = [
        dict(profile)
        for profile in list_blog_profiles(con)
        if str(profile.get("platform") or "") in PLATFORM_PREFIXES
    ]
    strategies = list_managed_blog_channel_strategies(con)
    strategy_by_profile_id = {
        str(item.get("blog_profile_id") or ""): str(item.get("strategy_code") or "")
        for item in strategies
    }
    settings_rows: list[tuple[str, str, str, str]] = []
    for profile in profiles:
        channel_key = _profile_channel_key(profile, strategy_by_profile_id)
        platform = str(profile.get("platform") or "")
        if not channel_key or platform not in PLATFORM_PREFIXES:
            continue
        prefix = PLATFORM_PREFIXES[platform]
        role_name = str(profile.get("profile_name") or "").strip()
        settings_rows.append((channel_key, platform, prefix, role_name))

    if not settings_rows:
        return
    order = {"blogger_life": 0, "blogger_tech": 1, "blogger_current": 2, "naver_local": 3, TISTORY_CHANNEL_KEY: 4}
    settings_rows.sort(key=lambda row: order.get(row[0], 99))

    st_module.markdown("#### 글감 목록 추천 표시 이름")
    st_module.caption(
        "실제 블로그 이름을 선택 입력하세요. 글감 목록에는 B:/N:/T: 뒤에 이 이름을 표시하며, "
        "비워 두면 B:, N:, T:처럼 플랫폼 문자만 표시합니다. 내부 발행 전략 이름은 바뀌지 않습니다."
    )
    values: dict[str, str] = {}
    with st_module.form("trend_blog_recommendation_display_names"):
        for channel_key, platform, prefix, role_name in settings_rows:
            current = get_recommendation_display_name(con, channel_key)
            values[channel_key] = st_module.text_input(
                f"{prefix}: · {role_name}",
                value=current,
                placeholder=f"비워 두면 {format_recommended_blog_label(platform)}",
                key=f"trend_blog_display_name_{channel_key}",
                help="실제 블로그에서 보이는 이름만 입력합니다. 추천 분류 규칙과 연결 주소에는 영향을 주지 않습니다.",
            )
        submitted = st_module.form_submit_button(
            "추천 표시 이름 저장",
            type="primary",
            width="stretch",
        )
    if submitted:
        for channel_key, display_name in values.items():
            set_recommendation_display_name(con, channel_key, display_name)
        st_module.success("글감 목록 추천 표시 이름을 저장했습니다.")
        st_module.rerun()


def _install_recommendation_name_settings(caller_globals: dict[str, object]) -> None:
    target = caller_globals.get("_render_blog_profile_settings")
    streamlit_module = caller_globals.get("st")
    if (
        not callable(target)
        or streamlit_module is None
        or getattr(target, "_trend_blog_recommendation_settings_wrapper", False)
    ):
        return

    @wraps(target)
    def wrapped(con, *args, **kwargs):
        result = target(con, *args, **kwargs)
        render_blog_recommendation_name_settings(
            con,
            st_module=streamlit_module,
        )
        return result

    wrapped._trend_blog_recommendation_settings_wrapper = True  # type: ignore[attr-defined]
    caller_globals["_render_blog_profile_settings"] = wrapped


def install_trend_candidate_blog_recommendation_ui(ui_module) -> None:
    """Extend the existing candidate-angle installer without duplicating app flow."""
    original = getattr(ui_module, "_install_candidate_angle_status_ui", None)
    if not callable(original) or getattr(original, "_trend_blog_recommendation_installer", False):
        return

    @wraps(original)
    def wrapped(caller_globals: dict[str, object]) -> None:
        original(caller_globals)
        _install_ranked_trend_recommendation(caller_globals)
        _install_candidate_table_renderer(caller_globals)
        _install_recommendation_name_settings(caller_globals)
        streamlit_module = caller_globals.get("st")
        if streamlit_module is not None:
            streamlit_module.markdown(
                _CANDIDATE_BLOG_RECOMMENDATION_CSS,
                unsafe_allow_html=True,
            )

    wrapped._trend_blog_recommendation_installer = True  # type: ignore[attr-defined]
    ui_module._install_candidate_angle_status_ui = wrapped
