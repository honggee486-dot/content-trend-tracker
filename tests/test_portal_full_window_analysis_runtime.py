from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

from src.database import connect_database, init_database
from src.services import trend_discovery_service as trend_service
from src.services.portal_full_window_analysis_runtime import (
    PORTAL_FULL_WINDOW_QUERY_LIMIT,
    install_portal_full_window_streamlit_contract,
)


def _insert_portal_rows(con, *, count: int) -> None:
    now = datetime.now().replace(microsecond=0)
    rows = []
    for index in range(count):
        observed_at = now - timedelta(seconds=index)
        rows.append(
            [
                f"naver_full_{index:04d}",
                "naver_news",
                f"external_{index:04d}",
                f"전체 분석 테스트 {index}",
                f"전체 분석 테스트 {index}",
                f"https://example.com/full/{index}",
                "테스트뉴스",
                observed_at,
                observed_at,
                1.0,
                '{"discovery_query":"전체 분석"}',
                observed_at,
                observed_at,
                observed_at,
                1,
                observed_at,
            ]
        )
    con.executemany(
        """
        INSERT INTO source_items(
            source_item_id, source_type, external_id, raw_title, normalized_title,
            source_url, source_name, published_at, observed_at, signal_value,
            metadata_json, first_imported_at, previous_imported_at, last_imported_at,
            observation_count, imported_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        rows,
    )


def test_legacy_portal_limit_uses_entire_recent_window(tmp_path: Path) -> None:
    db_path = tmp_path / "portal-full-window.duckdb"
    init_database(db_path)
    with connect_database(db_path) as con:
        _insert_portal_rows(con, count=510)
        items = trend_service._parse_source_rows(
            con,
            72,
            source_limits={"naver": 500},
        )

    naver_items = [item for item in items if item["source_type"] == "naver_news"]
    assert len(naver_items) == 510
    assert trend_service._normalized_analysis_limits({"naver": 500})["naver"] == (
        PORTAL_FULL_WINDOW_QUERY_LIMIT
    )
    assert trend_service.DEFAULT_ANALYSIS_SOURCE_LIMITS["naver"] == 0
    assert trend_service.DEFAULT_ANALYSIS_SOURCE_LIMITS["daum"] == 0


def test_small_internal_portal_limit_remains_available_for_bounded_fixtures() -> None:
    limits = trend_service._normalized_analysis_limits({"naver": 10, "daum": 20})

    assert limits["naver"] == 10
    assert limits["daum"] == 20
    assert trend_service._balanced_candidate_limit(10) < PORTAL_FULL_WINDOW_QUERY_LIMIT


class _FakeStreamlit:
    def __init__(self) -> None:
        self.text_inputs: list[tuple[object, dict[str, object]]] = []
        self.markdowns: list[object] = []
        self.captions: list[object] = []

    def number_input(self, label, *args, **kwargs):
        return kwargs.get("value", 123)

    def text_input(self, label, *args, **kwargs):
        self.text_inputs.append((label, dict(kwargs)))
        return kwargs.get("value", "")

    def markdown(self, value, *args, **kwargs):
        self.markdowns.append(value)
        return value

    def caption(self, value, *args, **kwargs):
        self.captions.append(value)
        return value


def test_streamlit_contract_replaces_legacy_portal_limit_controls() -> None:
    st = _FakeStreamlit()

    install_portal_full_window_streamlit_contract(st)
    install_portal_full_window_streamlit_contract(st)

    value = st.number_input(
        "NAVER",
        min_value=500,
        max_value=20000,
        value=8000,
        step=500,
    )
    untouched = st.number_input(
        "YouTube",
        min_value=100,
        max_value=20000,
        value=2000,
        step=100,
    )
    st.markdown("##### 순위 계산 시 출처별 최대 분석량")
    st.caption(
        "최근 분석 범위 안에서도 한 출처가 문서량만으로 다른 출처를 밀어내지 않도록 "
        "출처별 상한을 적용합니다."
    )

    assert value == 0
    assert untouched == 2000
    assert st.text_inputs == [
        (
            "NAVER",
            {
                "value": "최근 분석 범위 전체",
                "disabled": True,
                "help": (
                    "NAVER·Daum은 별도 개수 상한을 두지 않고 설정한 순위 분석 시간 범위의 "
                    "원문 전체를 사용합니다. 기본 분석 범위는 최근 72시간입니다."
                ),
                "key": "portal_full_window_naver_analysis",
            },
        )
    ]
    assert st.markdowns[-1] == "##### 순위 계산 시 출처별 분석 범위"
    assert "NAVER·Daum은 최근 분석 시간 범위 전체" in str(st.captions[-1])
