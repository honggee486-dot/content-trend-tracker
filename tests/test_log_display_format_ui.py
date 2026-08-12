from __future__ import annotations

from functools import wraps

import pandas as pd

from src.log_display_format_ui import (
    format_log_dataframe,
    format_log_integer,
    format_log_metric_value,
    format_log_seconds_from_milliseconds,
    install_log_display_formatting,
)


def test_log_integer_and_duration_use_commas_and_seconds_only() -> None:
    assert format_log_integer(1_234_567) == "1,234,567"
    assert format_log_seconds_from_milliseconds(850) == "0.85초"
    assert format_log_seconds_from_milliseconds(125_000) == "125.00초"
    assert "분" not in format_log_seconds_from_milliseconds(3_725_000)
    assert format_log_seconds_from_milliseconds(3_725_000) == "3,725.00초"


def test_log_metric_values_use_the_same_contract() -> None:
    assert format_log_metric_value("전체 토큰", "1234567") == "1,234,567"
    assert format_log_metric_value("부분 성공·실패", "1234회") == "1,234회"
    assert format_log_metric_value("재시도 누적 대기", "2분 3초") == "123.00초"
    assert format_log_metric_value("마지막 성공 후 경과", "2분 전") == "2분 전"


def test_metric_preserves_dates_versions_ratios_scores_and_ranks() -> None:
    assert (
        format_log_metric_value("마지막 전체 성공", "2026-08-06 13:58:00")
        == "2026-08-06 13:58:00"
    )
    assert format_log_metric_value("스키마 버전", "1.2.3") == "1.2.3"
    assert format_log_metric_value("최근 10회 저장률", "91.2%") == "91.2%"
    assert format_log_metric_value("최근 요청 대비 저장", "1234/5678개") == "1234/5678개"
    assert format_log_metric_value("검색 결과 순위", 1234) == 1234
    assert format_log_metric_value("글감 점수", 1234) == 1234


def test_log_dataframe_formats_quantity_tokens_and_mixed_durations() -> None:
    frame = pd.DataFrame(
        [
            {
                "시각": "2026-08-06 13:58:00",
                "상태": "성공",
                "요청 항목": 12_345,
                "입력 토큰": 1_234_567,
                "소요 시간": "2분 3초",
            }
        ]
    )

    formatted, matched = format_log_dataframe(frame)

    assert matched is True
    assert formatted.columns.tolist() == [
        "시각",
        "상태",
        "요청 항목",
        "입력 토큰",
        "소요 시간(초)",
    ]
    assert formatted.iloc[0].to_dict() == {
        "시각": "2026-08-06 13:58:00",
        "상태": "성공",
        "요청 항목": "12,345",
        "입력 토큰": "1,234,567",
        "소요 시간(초)": "123.00",
    }


def test_millisecond_log_column_is_renamed_and_converted() -> None:
    frame = pd.DataFrame(
        [
            {
                "상태": "완료",
                "처리": 4_321,
                "총 토큰": 987_654,
                "시간(ms)": 1_234_567,
            }
        ]
    )

    formatted, matched = format_log_dataframe(frame)

    assert matched is True
    assert "시간(ms)" not in formatted.columns
    assert formatted.loc[0, "처리"] == "4,321"
    assert formatted.loc[0, "총 토큰"] == "987,654"
    assert formatted.loc[0, "시간(초)"] == "1,234.57"


def test_non_log_dataframe_is_left_unchanged() -> None:
    frame = pd.DataFrame([{"제목": "예시", "점수": 1234}])

    formatted, matched = format_log_dataframe(frame)

    assert matched is False
    assert formatted is frame


def test_general_status_or_duration_table_is_not_treated_as_log() -> None:
    status_frame = pd.DataFrame([{"제목": "예시", "상태": "추천", "항목": 1234}])
    duration_frame = pd.DataFrame([{"제목": "예시", "소요 시간": 1234, "페이지": 5678}])

    formatted_status, status_matched = format_log_dataframe(status_frame)
    formatted_duration, duration_matched = format_log_dataframe(duration_frame)

    assert status_matched is False
    assert formatted_status is status_frame
    assert duration_matched is False
    assert formatted_duration is duration_frame


class _FakeStreamlit:
    def __init__(self) -> None:
        self.frames = []
        self.metrics = []

    def dataframe(self, data=None, *args, **kwargs):
        self.frames.append((data, args, kwargs))
        return "rendered"

    def metric(self, label, value, *args, **kwargs):
        self.metrics.append((label, value, args, kwargs))
        return "metric"


def test_installer_formats_visible_dataframe_metrics_and_is_idempotent(monkeypatch) -> None:
    fake = _FakeStreamlit()
    monkeypatch.setattr(
        "src.log_display_format_ui._install_duration_helper_contract",
        lambda: None,
    )

    install_log_display_formatting(fake)
    first_dataframe = fake.dataframe
    first_metric = fake.metric
    install_log_display_formatting(fake)

    result = fake.dataframe(
        pd.DataFrame([{"상태": "성공", "요청": 1_234, "소요": "850ms"}]),
        hide_index=True,
    )
    metric_result = fake.metric("전체 토큰", "1234567", "재시도 1234회")

    assert fake.dataframe is first_dataframe
    assert fake.metric is first_metric
    assert result == "rendered"
    assert metric_result == "metric"
    visible = fake.frames[0][0]
    assert visible.loc[0, "요청"] == "1,234"
    assert visible.loc[0, "소요(초)"] == "0.85"
    assert fake.frames[0][2]["hide_index"] is True
    assert fake.metrics == [
        ("전체 토큰", "1,234,567", ("재시도 1,234회",), {})
    ]


def test_installer_detects_its_marker_through_other_wrappers(monkeypatch) -> None:
    fake = _FakeStreamlit()
    monkeypatch.setattr(
        "src.log_display_format_ui._install_duration_helper_contract",
        lambda: None,
    )
    install_log_display_formatting(fake)

    inner_dataframe = fake.dataframe
    inner_metric = fake.metric

    @wraps(inner_dataframe)
    def outer_dataframe(*args, **kwargs):
        return inner_dataframe(*args, **kwargs)

    @wraps(inner_metric)
    def outer_metric(*args, **kwargs):
        return inner_metric(*args, **kwargs)

    fake.dataframe = outer_dataframe
    fake.metric = outer_metric
    install_log_display_formatting(fake)

    assert fake.dataframe is outer_dataframe
    assert fake.metric is outer_metric
    fake.metric("마지막 전체 성공", "2026-08-06 13:58:00")
    assert fake.metrics[-1][1] == "2026-08-06 13:58:00"
