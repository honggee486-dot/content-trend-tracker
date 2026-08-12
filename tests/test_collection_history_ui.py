from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

from src import collection_history_ui
from src.database import connect_database, init_database
from src.services.collection_history_service import (
    finish_collection_run,
    record_skipped_overlap,
    start_collection_run,
)


class _Context:
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        return None


class _FilterColumn:
    def selectbox(self, _label: str, *, options, **_kwargs):
        return options[0]


class _FakeStreamlit:
    def __init__(self) -> None:
        self.info_messages: list[str] = []
        self.warning_messages: list[str] = []
        self.dataframes: list[object] = []
        self.metrics: list[tuple[tuple[object, ...], dict[str, object]]] = []
        self.session_state: dict[str, object] = {}

    def subheader(self, _value: str) -> None:
        return None

    def markdown(self, _value: str) -> None:
        return None

    def container(self, **_kwargs) -> _Context:
        return _Context()

    def expander(self, *_args, **_kwargs) -> _Context:
        return _Context()

    def columns(self, count: int) -> list[_FilterColumn]:
        return [_FilterColumn() for _ in range(count)]

    def metric(self, *args, **kwargs) -> None:
        self.metrics.append((args, kwargs))

    def caption(self, _value: str) -> None:
        return None

    def info(self, value: str) -> None:
        self.info_messages.append(value)

    def warning(self, value: str) -> None:
        self.warning_messages.append(value)

    def dataframe(self, value, **_kwargs) -> None:
        self.dataframes.append(value)

    def selectbox(self, _label: str, *, options, **_kwargs):
        return options[-1]


def _result(*, partial: bool = False) -> dict[str, object]:
    return {
        "youtube": None,
        "google_trends": None,
        "wikipedia": None,
        "naver": {
            "status": "partial" if partial else "success",
            "items_read": 3,
            "items_added": 2,
            "items_updated": 1,
            "items_skipped": 0,
            "request_count": 2,
            "retry_count": 1 if partial else 0,
        },
        "daum": None,
        "errors": {},
        "warnings": {"naver": "일부 요청 실패"} if partial else {},
        "timings": {"naver": 0.2},
    }


def test_history_section_renders_empty_state(tmp_path: Path, monkeypatch) -> None:
    db_path = tmp_path / "empty.duckdb"
    init_database(db_path)
    fake_st = _FakeStreamlit()
    monkeypatch.setattr(collection_history_ui, "st", fake_st)

    with connect_database(db_path) as con:
        collection_history_ui.render_collection_history(con)

    assert fake_st.info_messages == ["아직 저장된 수집 실행 이력이 없습니다."]
    assert len(fake_st.dataframes) == 1
    assert {"출처", "상태", "마지막 정상", "연속 문제"}.issubset(
        set(fake_st.dataframes[0].columns)
    )


def test_history_section_renders_success_partial_skip_and_failure(
    tmp_path: Path,
    monkeypatch,
) -> None:
    db_path = tmp_path / "rows.duckdb"
    init_database(db_path)
    now = datetime.now()
    with connect_database(db_path) as con:
        success_id = start_collection_run(
            con,
            "manual_refresh",
            started_at=now - timedelta(minutes=4),
        )
        finish_collection_run(con, success_id, result=_result())
        partial_id = start_collection_run(
            con,
            "background_refresh",
            started_at=now - timedelta(minutes=3),
        )
        finish_collection_run(con, partial_id, result=_result(partial=True))
        record_skipped_overlap(
            con,
            "ranking_rebuild",
            recorded_at=now - timedelta(minutes=2),
        )
        failure_id = start_collection_run(
            con,
            "ranking_rebuild",
            started_at=now - timedelta(minutes=1),
        )
        finish_collection_run(con, failure_id, error="순위 계산 실패")

        fake_st = _FakeStreamlit()
        monkeypatch.setattr(collection_history_ui, "st", fake_st)
        collection_history_ui.render_collection_history(con)

    statuses = set(fake_st.dataframes[1]["상태"].tolist())
    assert statuses == {"전체 성공", "부분 성공", "중복 실행 생략", "실패"}
    assert len(fake_st.dataframes) == 3
    assert set(fake_st.dataframes[2]["출처"].tolist()) == {"NAVER 뉴스·블로그"}


def test_history_detail_formats_gemini_counts_separately(
    tmp_path: Path,
    monkeypatch,
) -> None:
    db_path = tmp_path / "gemini-detail.duckdb"
    init_database(db_path)
    result = _result()
    result["topic_angles"] = {
        "status": "partial_success",
        "requested_clusters": 10,
        "generated_clusters": 8,
        "generated_angles": 24,
        "attempts": 3,
        "requested_batches": 2,
        "duration_seconds": 4.5,
        "error_message": "일부 글감 미처리",
    }

    with connect_database(db_path) as con:
        run_id = start_collection_run(con, "background_refresh")
        finish_collection_run(con, run_id, result=result)

        fake_st = _FakeStreamlit()
        monkeypatch.setattr(collection_history_ui, "st", fake_st)
        collection_history_ui.render_collection_history(con)

    detail_frame = fake_st.dataframes[2]
    gemini_row = detail_frame.loc[
        detail_frame["출처"] == "Gemini 주제 방향"
    ].iloc[0]
    assert gemini_row["상태"] == "부분 성공"
    assert gemini_row["소요 시간"] == "4.5초"
    assert gemini_row["요청"] == 3
    assert gemini_row["재시도"] == 1
    assert gemini_row["처리 결과"] == "글감 8개 · 방향 24개 · 미처리 2개"


def test_gemini_usage_section_shows_character_and_token_details(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from src.config import GeminiConfig
    from src.services.gemini_service import record_gemini_api_call

    db_path = tmp_path / "gemini-usage-ui.duckdb"
    init_database(db_path)
    config = GeminiConfig(
        api_key="test-key",
        model="gemini-3.6-flash",
        app_id="content-trend-tracker",
        quota_scope_id="scope",
        timeout_seconds=60,
        retry_wait_seconds=2.0,
        retry_max_wait_seconds=30.0,
    )
    with connect_database(db_path) as con:
        record_gemini_api_call(
            con,
            config=config,
            content_pack_id="pack_test",
            request_hash="request_hash",
            attempt_number=1,
            cache_hit=False,
            status="success",
            http_status=200,
            error_type="",
            retry_reason="",
            retry_wait_seconds=0,
            input_tokens=12_345,
            output_tokens=6_789,
            thought_tokens=1_111,
            total_tokens=20_245,
            duration_ms=500,
            error_message="",
            request_text="한글 요청 ABC",
            response_text='{"답변":"한글 응답"}',
            requested_item_count=18,
            configured_items_per_request=25,
            thinking_level="high",
            request_timeout_seconds=600,
            finish_reason="STOP",
        )

        before = con.execute("SELECT COUNT(*) FROM gemini_api_calls").fetchone()[0]
        fake_st = _FakeStreamlit()
        monkeypatch.setattr(collection_history_ui, "st", fake_st)
        collection_history_ui.render_collection_history(con)
        after = con.execute("SELECT COUNT(*) FROM gemini_api_calls").fetchone()[0]

    assert len(fake_st.dataframes) == 2
    assert before == after == 1
    row = fake_st.dataframes[0].iloc[0]
    assert row["보낸 글자"].startswith("전체 ")
    assert "한글 4" in row["보낸 글자"]
    assert row["입력 토큰"] == "12,345"
    assert row["출력 토큰"] == "6,789"
    assert row["사고 토큰"] == "1,111"
    assert row["생성 토큰(출력+사고)"] == "7,900/65,536 · 12.1%"
    assert row["전체 토큰"] == "20,245"
    assert row["실제 요청 수"] == 18
    assert row["설정 상한"] == 25
    assert row["사고 수준"] == "high"
    assert row["제한 시간"] == "600초"
    assert row["종료 사유"] == "STOP"
    assert row["종료 메시지"] == ""
    assert row["오류 요약"] == ""

    usage_metrics = fake_st.metrics[:5]
    assert [args[0] for args, _kwargs in usage_metrics] == [
        "API 요청 · 참고 RPD 20회",
        "1회 최대 입력 · 한도 1,048,576토큰",
        "1회 최대 생성 · 출력 한도 참고 65,536토큰",
        "사고 토큰",
        "전체 토큰",
    ]
    assert usage_metrics[0][0][1] == "1회"
    assert usage_metrics[1][0][1] == "12,345토큰"
    assert usage_metrics[2][0][1] == "7,900토큰"
    assert all(str(kwargs.get("help") or "").strip() for _args, kwargs in usage_metrics)
    assert all(kwargs.get("border") is True for _args, kwargs in usage_metrics)


def test_gemini_usage_warns_and_shows_validation_error_near_output_limit(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from src.config import GeminiConfig
    from src.services.gemini_service import record_gemini_api_call

    db_path = tmp_path / "gemini-near-limit.duckdb"
    init_database(db_path)
    config = GeminiConfig(
        api_key="test-key",
        model="gemini-3.6-flash",
        app_id="content-trend-tracker",
        quota_scope_id="scope",
        timeout_seconds=60,
        retry_wait_seconds=2.0,
        retry_max_wait_seconds=30.0,
    )
    error_message = "cluster_test의 AI 요청서 기본 설정이 올바르지 않습니다."
    with connect_database(db_path) as con:
        record_gemini_api_call(
            con,
            config=config,
            content_pack_id="topic_angle_batch_test",
            request_hash="near_limit_hash",
            feature_id="trend_topic_angle_batch_v1",
            attempt_number=1,
            cache_hit=False,
            status="response_validation_error",
            http_status=200,
            error_type="response_validation_error",
            retry_reason="",
            retry_wait_seconds=0,
            input_tokens=23_212,
            output_tokens=23_308,
            thought_tokens=42_211,
            total_tokens=88_731,
            duration_ms=307_000,
            error_message=error_message,
            request_text="글감 요청",
            response_text='{"clusters":[]}',
            requested_item_count=18,
            configured_items_per_request=25,
            thinking_level="high",
            request_timeout_seconds=600,
            finish_reason="MAX_TOKENS",
            finish_message="Output token limit reached",
        )

        fake_st = _FakeStreamlit()
        monkeypatch.setattr(collection_history_ui, "st", fake_st)
        collection_history_ui._render_gemini_usage(con)

    row = fake_st.dataframes[0].iloc[0]
    assert row["상태"] == "response_validation_error"
    assert row["생성 토큰(출력+사고)"] == (
        "65,519/65,536 · 100.0% · 한도 근접"
    )
    assert row["오류 요약"] == error_message
    assert row["종료 사유"] == "MAX_TOKENS"
    assert row["종료 메시지"] == "Output token limit reached"
    assert any("65,000토큰 이상" in message for message in fake_st.warning_messages)
    assert fake_st.metrics[2][0][1] == "65,519토큰"
