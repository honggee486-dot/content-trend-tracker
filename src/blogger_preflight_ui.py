from __future__ import annotations

from typing import Any

import pandas as pd
import streamlit as st

from src.services.blogger_preflight_service import build_blogger_preflight_report


_STATUS_LABELS = {
    "pass": "통과",
    "warning": "확인 필요",
    "fail": "실패",
}


def _check_rows(report: Any) -> list[dict[str, str]]:
    return [
        {
            "상태": _STATUS_LABELS.get(check.status, check.status),
            "검사 항목": check.label,
            "결과": check.message,
        }
        for check in report.checks
    ]


def render_blogger_preflight(*, st_module=st) -> Any:
    report = build_blogger_preflight_report()
    with st_module.expander(
        "Blogger OAuth·API 사전점검",
        expanded=not report.ready_for_api,
    ):
        st_module.caption(
            "OAuth 클라이언트와 로컬 토큰의 구조만 검사합니다. "
            "비밀값은 화면에 표시하지 않으며 네트워크 요청·DB 쓰기를 수행하지 않습니다."
        )
        if report.ready_for_api:
            st_module.success(report.summary)
        elif report.ready_for_authorization:
            st_module.warning(report.summary)
        else:
            st_module.error(report.summary)
        st_module.dataframe(
            pd.DataFrame(_check_rows(report)),
            hide_index=True,
            width="stretch",
        )
    return report
