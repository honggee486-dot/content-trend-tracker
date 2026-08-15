from __future__ import annotations

import html
from typing import Any

import src.trend_candidate_blog_recommendation_ui as recommendation_ui


_MASTER_DETAIL_SOURCE_SPEC = (1.55, 1.75)
_MASTER_DETAIL_WIDE_LIST_SPEC = [1.90, 1.40]
_CANDIDATE_LIST_HEIGHT = 760

_CANDIDATE_LAYOUT_CSS = """
<style>
.st-key-trend_candidate_table_header [data-testid="stHorizontalBlock"],
[class*="st-key-trend_candidate_row_"] [data-testid="stHorizontalBlock"] {
    grid-template-columns: 36px 48px 108px 50px 48px minmax(270px, 1fr) 48px 42px 42px 42px 42px 42px !important;
    min-width: 818px !important;
    width: 100% !important;
    max-width: 100% !important;
    box-sizing: border-box !important;
}
.candidate-tbl-hdr {
    font-size: 0.76rem !important;
}
.candidate-tbl-cell {
    font-size: 0.81rem !important;
}
.rank-val {
    font-size: 0.84rem !important;
}
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
    font-size: 0.94rem !important;
    font-weight: 800 !important;
    line-height: 1 !important;
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
.trend-source-header {
    font-size: 0.68rem !important;
    line-height: 0.78rem !important;
    white-space: normal !important;
    text-align: center !important;
}
@media (max-width: 1440px) {
    [class*="st-key-trend_candidate_row_"] .stButton > button p {
        font-size: 0.80rem !important;
    }
}
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


def adsense_display_symbol(label: object) -> str:
    normalized = str(label or "").strip()
    if normalized.endswith("적합"):
        return "○"
    if normalized.endswith("피함"):
        return "×"
    if normalized:
        return "△"
    return "-"


def split_candidate_status_markdown(value: object) -> tuple[object, object, object] | None:
    if not isinstance(value, str):
        return None
    if 'candidate-tbl-hdr cell-center">판정</div>' in value:
        return (
            '<div class="candidate-tbl-hdr cell-center">추천</div>',
            '<div class="candidate-tbl-hdr cell-center">블로그</div>',
            (
                '<div class="candidate-tbl-hdr cell-center" '
                'title="○=초기 심사 우선 후보 · △=최신성·독창성 보강 후 사용 · '
                '×=초기 심사 전 보수적 회피 · 승인 보장은 아님">애드센스</div>'
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
    adsense_symbol = adsense_display_symbol(adsense_label)
    tooltip_parts = [part for part in (adsense_label, adsense_reason) if part]
    safe_reason = html.escape(
        " · ".join(tooltip_parts) or "AdSense 보조 판단 없음",
        quote=True,
    )
    adsense_class = (
        recommendation_ui._adsense_css_class(adsense_label) if adsense_label else ""
    )
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
            f'title="{safe_reason}">{adsense_symbol}</div>'
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
            'candidate-tbl-hdr cell-right trend-source-header">DAUM</div>',
        ),
        "youtube": (
            'candidate-tbl-hdr cell-right">YouTube</div>',
            'candidate-tbl-hdr cell-right trend-source-header">YOU<br>TUBE</div>',
        ),
        "google": (
            'candidate-tbl-hdr cell-right">Google Trends</div>',
            'candidate-tbl-hdr cell-right trend-source-header">GOOGLE<br>TRENDS</div>',
        ),
        "wikipedia": (
            'candidate-tbl-hdr cell-right">위키백과</div>',
            'candidate-tbl-hdr cell-right trend-source-header">위키백과</div>',
        ),
    }
    pair = replacements.get(role)
    if pair and pair[0] in value:
        return value.replace(*pair)
    return value


def _is_master_detail_spec(spec: object) -> bool:
    if not isinstance(spec, (list, tuple)) or len(spec) != 2:
        return False
    try:
        return all(
            abs(float(actual) - expected) < 0.001
            for actual, expected in zip(spec, _MASTER_DETAIL_SOURCE_SPEC, strict=True)
        )
    except (TypeError, ValueError):
        return False


class _ColumnProxy:
    def __init__(self, target: Any, *, role: str = "") -> None:
        self._target = target
        self._role = role

    def __getattr__(self, name: str):
        return getattr(self._target, name)

    def __enter__(self):
        entered = self._target.__enter__()
        return self if entered is self._target else _ColumnProxy(
            entered,
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


def _patched_columns(self, *args, **kwargs):
    st_module = self._target
    spec = args[0] if args else kwargs.get("spec")

    if _is_master_detail_spec(spec):
        call_args = list(args)
        if call_args:
            call_args[0] = _MASTER_DETAIL_WIDE_LIST_SPEC
            columns = st_module.columns(*call_args, **kwargs)
        else:
            adjusted_kwargs = dict(kwargs)
            adjusted_kwargs["spec"] = _MASTER_DETAIL_WIDE_LIST_SPEC
            columns = st_module.columns(**adjusted_kwargs)
        return [recommendation_ui._CandidateColumnProxy(column) for column in columns]

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
            _ColumnProxy(columns[0], role="rank"),
            _StatusColumnsProxy((columns[1], columns[2], columns[3])),
            _ColumnProxy(columns[4], role="trend"),
            _ColumnProxy(columns[5], role="title"),
            _ColumnProxy(columns[6], role="opportunity"),
            _ColumnProxy(columns[7], role="naver"),
            _ColumnProxy(columns[8], role="daum"),
            _ColumnProxy(columns[9], role="youtube"),
            _ColumnProxy(columns[10], role="google"),
            _ColumnProxy(columns[11], role="wikipedia"),
        ]

    columns = st_module.columns(*args, **kwargs)
    return [recommendation_ui._CandidateColumnProxy(column) for column in columns]


def _patched_container(self, *args, **kwargs):
    adjusted_kwargs = dict(kwargs)
    if str(adjusted_kwargs.get("key") or "") == "trend_candidate_list":
        adjusted_kwargs["height"] = _CANDIDATE_LIST_HEIGHT
    return self._target.container(*args, **adjusted_kwargs)


def install_trend_blog_recommendation_ui_runtime(*, st_module: Any) -> None:
    """Keep side-by-side details while giving the candidate list more readable width."""
    del st_module
    proxy_cls = recommendation_ui._CandidateStreamlitProxy
    if getattr(proxy_cls, "_trend_candidate_table_runtime", False):
        return

    proxy_cls.columns = _patched_columns
    proxy_cls.container = _patched_container
    proxy_cls._trend_candidate_table_runtime = True
    recommendation_ui._CANDIDATE_BLOG_RECOMMENDATION_CSS += _CANDIDATE_LAYOUT_CSS
