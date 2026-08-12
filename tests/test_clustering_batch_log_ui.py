from __future__ import annotations

import pandas as pd

from src.clustering_batch_log_ui import (
    BATCH_LOG_HELP,
    format_clustering_batch_log_frame,
    install_clustering_batch_log_ui,
)


def _batch_frame() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "배치": 1,
                "상태": "success",
                "검색 미처리": 4000,
                "전체 1차 군집": 3997,
                "요청 1차 군집": 350,
                "요청 원문": 351,
                "입력 토큰": 112345,
                "출력 토큰": 23456,
                "사고 토큰": 0,
                "총 토큰": 135801,
                "시간(ms)": 123456,
                "오류": "",
            }
        ]
    )


def test_batch_log_uses_clear_labels_seconds_and_comma_formatting() -> None:
    formatted, matched = format_clustering_batch_log_frame(_batch_frame())

    assert matched is True
    assert "전체 1차 후보" in formatted.columns
    assert "2차 검토 후보" in formatted.columns
    assert "전체 1차 군집" not in formatted.columns
    assert "요청 1차 군집" not in formatted.columns
    assert "시간(초)" in formatted.columns
    assert "시간(ms)" not in formatted.columns
    assert formatted.loc[0, "검색 미처리"] == "4,000"
    assert formatted.loc[0, "전체 1차 후보"] == "3,997"
    assert formatted.loc[0, "2차 검토 후보"] == "350"
    assert formatted.loc[0, "입력 토큰"] == "112,345"
    assert formatted.loc[0, "총 토큰"] == "135,801"
    assert formatted.loc[0, "시간(초)"] == "123.5"
    assert "225,000토큰" in BATCH_LOG_HELP
    assert "여러 요청을 합산" in BATCH_LOG_HELP


def test_unrelated_dataframe_is_not_changed() -> None:
    original = pd.DataFrame([{"항목": 1000}])

    formatted, matched = format_clustering_batch_log_frame(original)

    assert matched is False
    assert formatted is original


def test_installer_adds_help_and_formats_only_matching_table() -> None:
    rendered = []
    captions = []

    class FakeStreamlit:
        def dataframe(self, data, *args, **kwargs):
            rendered.append((data, args, kwargs))
            return "rendered"

        def caption(self, value):
            captions.append(value)

    fake = FakeStreamlit()
    install_clustering_batch_log_ui(fake)

    result = fake.dataframe(_batch_frame(), hide_index=True, width="stretch")

    assert result == "rendered"
    assert captions == [BATCH_LOG_HELP]
    assert rendered[0][0].loc[0, "총 토큰"] == "135,801"
    assert rendered[0][0].loc[0, "시간(초)"] == "123.5"
    assert rendered[0][2] == {"hide_index": True, "width": "stretch"}
