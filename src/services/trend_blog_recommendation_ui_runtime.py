from __future__ import annotations

import html
from typing import Any

import src.trend_candidate_blog_recommendation_ui as recommendation_ui


DETAIL_DRAWER_STATE_KEY = "trend_candidate_detail_drawer_open"
DETAIL_DRAWER_TOGGLE_BUTTON_KEY = "trend_candidate_detail_drawer_toggle_button"

_CANDIDATE_DRAWER_CSS = """
<style>
.st-key-trend_candidate_master_list {
    width: 100% !important;
}
.st-key-trend_candidate_table_header [data-testid="stHorizontalBlock"],
[class*="st-key-trend_candidate_row_"] [data-testid="stHorizontalBlock"] {
    grid-template-columns: 42px 58px 112px 78px 60px minmax(300px, 1fr) 64px 54px 54px 64px 88px 68px !important;
    min-width: 1120px !important;
}
.candidate-tbl-hdr { font-size: 0.76rem !important; }
.candidate-tbl-cell { font-size: 0.81rem !important; }
.rank-val { font-size: 0.84rem !important; }
[class*="st-key-trend_candidate_row_"] .stButton > button p {
    font-size: 0.84rem !important;
}
.trend-recommendation-column {
    font-size: 0.74rem !important;
    font-weight: 750 !important;
}
.trend-recommendation-column.status-추천::after,
.trend-recommendation-column.status-검토::after,
.trend-recommendation-column.status-보류::after {
    font-size: 0.74rem !important;
    font-weight: 750 !important;
}
.trend-blog-column,
.trend-adsense-column {
    justify-content: center !important;
    text-align: center !important;
    overflow: hidden !important;
    text-overflow: ellipsis !important;
    white-space: nowrap !important;
}
.trend-blog-column {
    font-size: 0.68rem !important;
    font-weight: 650 !important;
    opacity: 0.88;
}
.trend-adsense-column {
    font-size: 0.67rem !important;
    font-weight: 720 !important;
    cursor: help;
}
.trend-blog-empty,
.trend-adsense-empty {
    opacity: 0.38 !important;
    font-weight: 500 !important;
}
.trend-adsense-column.adsense-fit { opacity: 0.96; }
.trend-adsense-column.adsense-review { opacity: 0.78; }
.trend-adsense-column.adsense-avoid { opacity: 0.64; }
.st-key-trend_candidate_detail_drawer {
    position: fixed !important;
    top: 7.4rem !important;
    right: 0.65rem !important;
    z-index: 1100 !important;
    width: min(46vw, 760px) !important;
    max-width: calc(100vw - 2.25rem) !important;
    height: calc(100vh - 8.15rem) !important;
    max-height: calc(100vh - 8.15rem) !important;
    overflow-y: auto !important;
    overflow-x: visible !important;
    padding: 0.72rem 0.78rem 1rem !important;
    border: 1px solid rgba(128, 128, 128, 0.38) !important;
    border-radius: 0.72rem !important;
    background: var(--background-color) !important;
    box-shadow: 0 0.85rem 2.3rem rgba(0, 0, 0, 0.34) !important;
    box-sizing: border-box !important;
    transition: transform 0.22s ease, opacity 0.18s ease !important;
}
.st-key-trend_candidate_detail_drawer > [data-testid="stVerticalBlock"] {
    overflow: visible !important;
}
.st-key-trend_candidate_detail_toggle {
    position: fixed !important;
    top: 8.05rem !important;
    z-index: 1102 !important;
    width: auto !important;
    margin: 0 !important;
    transition: right 0.22s ease !important;
}
.st-key-trend_candidate_detail_toggle [data-testid="stButton"] {
    width: auto !important;
}
.st-key-trend_candidate_detail_toggle .stButton > button {
    min-height: 2.45rem !important;
    width: auto !important;
    padding: 0.32rem 0.62rem !important;
    border-radius: 0.52rem !important;
    box-shadow: 0 0.35rem 1rem rgba(0, 0, 0, 0.24) !important;
    white-space: nowrap !important;
}
.st-key-trend_candidate_detail_toggle .stButton > button p {
    font-size: 0.82rem !important;
    font-weight: 720 !important;
    white-space: nowrap !important;
}
.st-key-trend_candidate_detail_drawer .st-key-trend_selected_detail h3 {
    font-size: 1.22rem !important;
}
.st-key-trend_candidate_detail_drawer .st-key-trend_selected_detail h4 {
    font-size: 1.06rem !important;
}
.st-key-trend_candidate_detail_drawer .st-key-trend_selected_detail [data-testid="stMarkdownContainer"] p,
.st-key-trend_candidate_detail_drawer .st-key-trend_selected_detail [data-testid="stMarkdownContainer"] li {
    font-size: 1.06rem !important;
}
.st-key-trend_candidate_detail_drawer .st-key-trend_selected_detail [data-testid="stCaptionContainer"] p {
    font-size: 0.81rem !important;
}
.st-key-trend_candidate_detail_drawer .st-key-trend_selected_detail [data-testid="stRadio"] label p,
.st-key-trend_candidate_detail_drawer .st-key-trend_selected_detail [data-testid="stCheckbox"] label p {
    font-size: 0.94rem !important;
}
.st-key-trend_candidate_detail_drawer .explainable-metric-label {
    font-size: 0.76rem !important;
}
.st-key-trend_candidate_detail_drawer .explainable-metric-value {
    font-size: 1.31rem !important;
}
.st-key-trend_candidate_detail_drawer .explainable-metric-delta {
    font-size: 0.70rem !important;
}
.st-key-trend_candidate_detail_drawer .explainable-metric-help {
    font-size: 0.76rem !important;
}
@media (max-width: 1440px) {
    [class*="st-key-trend_candidate_row_"] .stButton > button p {
        font-size: 0.80rem !important;
    }
}
</style>
"""


def candidate_drawer_runtime_css(opened: bool) -> str:
    transform = "translateX(0)" if opened else "translateX(calc(100% + 1.35rem))"
    opacity = "1" if opened else "0"
    pointer_events = "auto" if opened else "none"
    toggle_right = "calc(min(46vw, 760px) + 0.18rem)" if opened else "0.45rem"
    table_columns = (
        "42px 58px 112px 78px 60px 270px 64px 54px 54px 64px 88px 68px"
        if opened
        else "42px 58px 112px 78px 60px minmax(300px, 1fr) 64px 54px 54px 64px 88px 68px"
    )
    return f"""
<style>
.st-key-trend_candidate_detail_drawer {{
    transform: {transform} !important;
    opacity: {opacity} !important;
    pointer-events: {pointer_events} !important;
}}
.st-key-trend_candidate_detail_toggle {{ right: {toggle_right} !important; }}
.st-key-trend_candidate_table_header [data-testid="stHorizontalBlock"],
[class*="st-key-trend_candidate_row_"] [data-testid="stHorizontalBlock"] {{
    grid-template-columns: {table_columns} !important;
}}
</style>
"""


def _status_parts(value: object) -> tuple[str, str, dict[str, str], str] | None:
    if not isinstance(value, str):
        return None
    status_match = recommendation_ui._STATUS_RE.search(value)
    if status_match is None:
        return None
    blog_match = recommendation_ui._RECOMMENDATION_TOKEN_RE.search(value)
    adsense_match = recommendation_ui._ADSENSE_TOKEN_RE.search(value)
    blog_label = (
        recommendation_ui.decode_recommendation_label(blog_match.group(1))
        if blog_match is not None
        else ""
    )
    adsense = (
        recommendation_ui.decode_adsense_assessment(adsense_match.group(1))
        if adsense_match is not None
        else {}
    )
    ai_class = ""
    if "ai-ready" in value:
        ai_class = " ai-ready"
    elif "ai-pending" in value:
        ai_class = " ai-pending"
    return status_match.group(1), blog_label, adsense, ai_class


def split_candidate_status_markdown(value: object) -> tuple[object, object, object] | None:
    if not isinstance(value, str):
        return None
    if 'candidate-tbl-hdr cell-center">판정</div>' in value:
        return (
            '<div class="candidate-tbl-hdr cell-center">추천</div>',
            '<div class="candidate-tbl-hdr cell-center">블로그</div>',
            (
                '<div class="candidate-tbl-hdr cell-center" '
                'title="A:적합=초기 심사 우선 후보 · A:검토=최신성·독창성 보강 후 사용 · '
                'A:피함=초기 심사 전 보수적 회피 · 승인 보장은 아님">애드센스</div>'
            ),
        )
    if "candidate-tbl-cell cell-center status-tag" not in value:
        return None

    parts = _status_parts(value)
    if parts is None:
        return None
    status, blog_label, adsense, ai_class = parts
    safe_blog = html.escape(blog_label) if blog_label else "-"
    blog_title = html.escape(blog_label, quote=True) if blog_label else "추천 블로그 미지정"
    blog_empty = " trend-blog-empty" if not blog_label else ""

    adsense_label = str(adsense.get("label") or "").strip()
    adsense_reason = str(adsense.get("reason") or "").strip()
    safe_adsense = html.escape(adsense_label) if adsense_label else "-"
    safe_reason = html.escape(adsense_reason or "AdSense 보조 판단 없음", quote=True)
    adsense_class = recommendation_ui._adsense_css_class(adsense_label) if adsense_label else ""
    adsense_empty = " trend-adsense-empty" if not adsense_label else ""

    return (
        (
            f'<div class="candidate-tbl-cell cell-center status-tag status-{status}{ai_class} '
            f'trend-recommendation-column">{html.escape(status)}</div>'
        ),
        (
            f'<div class="candidate-tbl-cell trend-blog-column{blog_empty}" '
            f'title="{blog_title}">{safe_blog}</div>'
        ),
        (
            f'<div class="candidate-tbl-cell trend-adsense-column {adsense_class}{adsense_empty}" '
            f'title="{safe_reason}">{safe_adsense}</div>'
        ),
    )


def rewrite_role_header(value: object, role: str) -> object:
    if not isinstance(value, str):
        return value
    replacements = {
        "opportunity": (
            'candidate-tbl-hdr cell-right">기회</div>',
            'candidate-tbl-hdr cell-right" title="글감 기회 점수">기획</div>',
        ),
        "daum": (
            'candidate-tbl-hdr cell-right">Daum</div>',
            'candidate-tbl-hdr cell-right">DAUM</div>',
        ),
        "youtube": (
            'candidate-tbl-hdr cell-right">YouTube</div>',
            'candidate-tbl-hdr cell-right">YOUTUBE</div>',
        ),
        "google": (
            'candidate-tbl-hdr cell-right">Google Trends</div>',
            'candidate-tbl-hdr cell-right">GOOGLETRENDS</div>',
        ),
    }
    pair = replacements.get(role)
    if pair and pair[0] in value:
        return value.replace(*pair)
    return value


class _ColumnProxy:
    def __init__(self, target: Any, *, st_module: Any, role: str = "") -> None:
        self._target = target
        self._st_module = st_module
        self._role = role

    def __getattr__(self, name: str):
        return getattr(self._target, name)

    def __enter__(self):
        entered = self._target.__enter__()
        return self if entered is self._target else _ColumnProxy(
            entered,
            st_module=self._st_module,
            role=self._role,
        )

    def __exit__(self, exc_type, exc, tb):
        return self._target.__exit__(exc_type, exc, tb)

    def markdown(self, body: object, *args, **kwargs):
        return self._target.markdown(
            rewrite_role_header(body, self._role),
            *args,
            **kwargs,
        )

    def button(self, *args, **kwargs):
        clicked = self._target.button(*args, **kwargs)
        if clicked and self._role == "title":
            was_open = bool(
                self._st_module.session_state.get(DETAIL_DRAWER_STATE_KEY, False)
            )
            self._st_module.session_state[DETAIL_DRAWER_STATE_KEY] = True
            if not was_open and kwargs.get("type") == "primary":
                self._st_module.rerun()
        return clicked


class _StatusColumnsProxy:
    def __init__(self, targets: tuple[Any, Any, Any]) -> None:
        self._targets = targets

    def __getattr__(self, name: str):
        return getattr(self._targets[0], name)

    def markdown(self, body: object, *args, **kwargs):
        cells = split_candidate_status_markdown(body)
        if cells is None:
            return self._targets[0].markdown(body, *args, **kwargs)
        result = None
        for target, cell in zip(self._targets, cells, strict=True):
            result = target.markdown(cell, *args, **kwargs)
        return result


def _is_master_detail_spec(spec: object) -> bool:
    if not isinstance(spec, (list, tuple)) or len(spec) != 2:
        return False
    try:
        return abs(float(spec[0]) - 1.55) < 0.001 and abs(float(spec[1]) - 1.75) < 0.001
    except (TypeError, ValueError):
        return False


def _render_detail_toggle(st_module: Any) -> bool:
    opened = bool(st_module.session_state.get(DETAIL_DRAWER_STATE_KEY, False))
    with st_module.container(key="trend_candidate_detail_toggle"):
        label = "선택한 글감 ▶" if opened else "◀ 선택한 글감"
        if st_module.button(
            label,
            key=DETAIL_DRAWER_TOGGLE_BUTTON_KEY,
            help=(
                "선택한 글감 상세를 오른쪽에서 열거나 숨깁니다. "
                "글감 제목을 새로 선택하면 상세 창이 자동으로 열립니다."
            ),
        ):
            st_module.session_state[DETAIL_DRAWER_STATE_KEY] = not opened
            st_module.rerun()
    return opened


def _patched_columns(self, *args, **kwargs):
    st_module = self._target
    spec = args[0] if args else kwargs.get("spec")

    if _is_master_detail_spec(spec):
        opened = _render_detail_toggle(st_module)
        st_module.markdown(candidate_drawer_runtime_css(opened), unsafe_allow_html=True)
        return [
            _ColumnProxy(
                st_module.container(key="trend_candidate_master_list"),
                st_module=st_module,
                role="candidate-list",
            ),
            _ColumnProxy(
                st_module.container(key="trend_candidate_detail_drawer"),
                st_module=st_module,
                role="candidate-detail",
            ),
        ]

    if isinstance(spec, int) and spec == 10 and kwargs.get("gap") is None:
        call_args = list(args)
        if call_args:
            call_args[0] = 12
            columns = st_module.columns(*call_args, **kwargs)
        else:
            adjusted_kwargs = dict(kwargs)
            adjusted_kwargs["spec"] = 12
            columns = st_module.columns(**adjusted_kwargs)
        return [
            _ColumnProxy(columns[0], st_module=st_module, role="rank"),
            _StatusColumnsProxy((columns[1], columns[2], columns[3])),
            _ColumnProxy(columns[4], st_module=st_module, role="trend"),
            _ColumnProxy(columns[5], st_module=st_module, role="title"),
            _ColumnProxy(columns[6], st_module=st_module, role="opportunity"),
            _ColumnProxy(columns[7], st_module=st_module, role="naver"),
            _ColumnProxy(columns[8], st_module=st_module, role="daum"),
            _ColumnProxy(columns[9], st_module=st_module, role="youtube"),
            _ColumnProxy(columns[10], st_module=st_module, role="google"),
            _ColumnProxy(columns[11], st_module=st_module, role="wikipedia"),
        ]

    columns = st_module.columns(*args, **kwargs)
    return [recommendation_ui._CandidateColumnProxy(column) for column in columns]


def install_trend_blog_recommendation_ui_runtime(*, st_module: Any) -> None:
    """Patch the existing candidate UI proxy with a wide table and right-side drawer."""
    proxy_cls = recommendation_ui._CandidateStreamlitProxy
    if getattr(proxy_cls, "_trend_candidate_drawer_runtime", False):
        return

    proxy_cls.columns = _patched_columns
    proxy_cls._trend_candidate_drawer_runtime = True
    recommendation_ui._CANDIDATE_BLOG_RECOMMENDATION_CSS += _CANDIDATE_DRAWER_CSS
    st_module.markdown(_CANDIDATE_DRAWER_CSS, unsafe_allow_html=True)
