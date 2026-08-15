from __future__ import annotations

import base64
from functools import wraps
import html
import json
import re
from typing import Any

from src.services.adsense_candidate_service import build_adsense_candidate_assessments
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
_ADSENSE_TOKEN_PREFIX = "adsense-hint-"
_ADSENSE_TOKEN_RE = re.compile(r"adsense-hint-([A-Za-z0-9_-]+)")
_STATUS_RE = re.compile(r"status-(추천|검토|보류)")

_CANDIDATE_BLOG_RECOMMENDATION_CSS = """
<style>
.st-key-trend_candidate_table_header [data-testid="stHorizontalBlock"],
[class*="st-key-trend_candidate_row_"] [data-testid="stHorizontalBlock"] {
    grid-template-columns: 38px 96px 48px minmax(180px, 1fr) 46px 44px 44px 50px 70px 52px !important;
    min-width: 688px !important;
}
.trend-blog-recommendation-cell {
    flex-direction: column !important;
    align-items: center !important;
    justify-content: center !important;
    gap: 0.04rem !important;
    min-height: 3.05rem !important;
    line-height: 1.05 !important;
}
.trend-blog-recommendation-cell .trend-blog-judgement {
    display: block;
    font-size: 0.68rem !important;
    font-weight: 750;
    line-height: 1.03;
}
.trend-blog-recommendation-cell .trend-blog-label {
    display: block;
    max-width: 90px;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
    font-size: 0.62rem !important;
    font-weight: 650;
    line-height: 1.03;
    opacity: 0.82;
}
.trend-blog-recommendation-cell .trend-adsense-label {
    display: block;
    max-width: 90px;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
    font-size: 0.61rem !important;
    font-weight: 720;
    line-height: 1.03;
    cursor: help;
}
.trend-blog-recommendation-cell .trend-adsense-label.adsense-fit {
    opacity: 0.96;
}
.trend-blog-recommendation-cell .trend-adsense-label.adsense-review {
    opacity: 0.78;
}
.trend-blog-recommendation-cell .trend-adsense-label.adsense-avoid {
    opacity: 0.64;
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


def encode_adsense_assessment(value: object) -> str:
    if not isinstance(value, dict):
        return ""
    payload = json.dumps(
        {
            "label": str(value.get("label") or "").strip(),
            "reason": str(value.get("reason") or "").strip(),
        },
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return encode_recommendation_label(payload)


def decode_adsense_assessment(value: object) -> dict[str, str]:
    payload = decode_recommendation_label(value)
    if not payload:
        return {}
    try:
        parsed = json.loads(payload)
    except json.JSONDecodeError:
        return {}
    if not isinstance(parsed, dict):
        return {}
    label = str(parsed.get("label") or "").strip()
    reason = str(parsed.get("reason") or "").strip()
    if not label:
        return {}
    return {"label": label, "reason": reason}


def decorate_rankings_with_blog_recommendations(con, rankings):
    if rankings is None or getattr(rankings, "empty", True):
        return rankings
    if "cluster_id" not in rankings.columns or "판정" not in rankings.columns:
        return rankings

    rows = rankings.to_dict("records")
    labels = build_trend_blog_recommendation_labels(con, rows)
    adsense_assessments = build_adsense_candidate_assessments(con, rows)
    if not labels and not adsense_assessments:
        return rankings

    decorated = rankings.copy()
    decorated_statuses: list[str] = []
    for status, cluster_id in zip(
        decorated["판정"].tolist(),
        decorated["cluster_id"].tolist(),
        strict=True,
    ):
        normalized_id = str(cluster_id)
        parts = [str(status)]
        blog_label = labels.get(normalized_id, "")
        if blog_label:
            parts.append(
                f"{_RECOMMENDATION_TOKEN_PREFIX}{encode_recommendation_label(blog_label)}"
            )
        adsense_assessment = adsense_assessments.get(normalized_id, {})
        adsense_token = encode_adsense_assessment(adsense_assessment)
        if adsense_token:
            parts.append(f"{_ADSENSE_TOKEN_PREFIX}{adsense_token}")
        decorated_statuses.append(" ".join(parts))
    decorated["판정"] = decorated_statuses
    return decorated


def _adsense_css_class(label: str) -> str:
    if label.endswith("적합"):
        return "adsense-fit"
    if label.endswith("피함"):
        return "adsense-avoid"
    return "adsense-review"


def rewrite_candidate_markdown(value: object) -> object:
    if not isinstance(value, str):
        return value
    if 'candidate-tbl-hdr cell-center">판정</div>' in value:
        return value.replace(
            'candidate-tbl-hdr cell-center">판정</div>',
            (
                'candidate-tbl-hdr cell-center" '
                'title="A:적합=초기 심사 우선 후보 · A:검토=최신성·독창성 보강 후 사용 · '
                'A:피함=초기 심사 전 보수적 회피 · 승인 보장은 아님">추천·AdSense</div>'
            ),
        )
    if "candidate-tbl-cell cell-center status-tag" not in value:
        return value

    blog_match = _RECOMMENDATION_TOKEN_RE.search(value)
    adsense_match = _ADSENSE_TOKEN_RE.search(value)
    status_match = _STATUS_RE.search(value)
    if status_match is None or (blog_match is None and adsense_match is None):
        return value

    blog_label = (
        decode_recommendation_label(blog_match.group(1)) if blog_match is not None else ""
    )
    adsense_assessment = (
        decode_adsense_assessment(adsense_match.group(1))
        if adsense_match is not None
        else {}
    )
    if not blog_label and not adsense_assessment:
        return value

    ai_class = ""
    if "ai-ready" in value:
        ai_class = " ai-ready"
    elif "ai-pending" in value:
        ai_class = " ai-pending"
    status = html.escape(status_match.group(1))
    blog_html = ""
    if blog_label:
        safe_label = html.escape(blog_label)
        blog_html = (
            f'<span class="trend-blog-label" title="{safe_label}">{safe_label}</span>'
        )
    adsense_html = ""
    if adsense_assessment:
        adsense_label = str(adsense_assessment.get("label") or "").strip()
        adsense_reason = str(adsense_assessment.get("reason") or "").strip()
        safe_adsense_label = html.escape(adsense_label)
        safe_adsense_reason = html.escape(adsense_reason, quote=True)
        css_class = _adsense_css_class(adsense_label)
        adsense_html = (
            f'<span class="trend-adsense-label {css_class}" '
            f'title="{safe_adsense_reason}">{safe_adsense_label}</span>'
        )
    return (
        f'<div class="candidate-tbl-cell cell-center status-tag{ai_class} '
        'trend-blog-recommendation-cell">'
        f'<span class="trend-blog-judgement">{status}</span>'
        f"{blog_html}{adsense_html}"
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
        "기본값은 위에서 주소와 함께 저장한 블로그 프로필 이름입니다. "
        "글감 목록에서 다른 이름으로 표시하고 싶을 때만 바꾸세요. "
        "블로그 프로필 이름도 없으면 B:, N:, T:처럼 플랫폼 문자만 표시합니다."
    )
    values: dict[str, tuple[str, str]] = {}
    with st_module.form("trend_blog_recommendation_display_names"):
        for channel_key, platform, prefix, role_name in settings_rows:
            current = get_recommendation_display_name(con, channel_key)
            values[channel_key] = (
                st_module.text_input(
                    f"{prefix}: · {role_name}",
                    value=current or role_name,
                    placeholder=f"블로그 이름이 없으면 {format_recommended_blog_label(platform)}",
                    key=f"trend_blog_display_name_{channel_key}",
                    help=(
                        "기본은 블로그 프로필 이름을 그대로 사용합니다. "
                        "추천 목록에서만 다른 이름을 쓰고 싶을 때 수정합니다."
                    ),
                ),
                role_name,
            )
        submitted = st_module.form_submit_button(
            "추천 표시 이름 저장",
            type="primary",
            width="stretch",
        )
    if submitted:
        for channel_key, (display_name, role_name) in values.items():
            normalized_display = str(display_name or "").strip()
            normalized_role = str(role_name or "").strip()
            set_recommendation_display_name(
                con,
                channel_key,
                "" if normalized_display == normalized_role else normalized_display,
            )
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
