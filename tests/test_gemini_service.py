from __future__ import annotations

import json
from pathlib import Path

from src.config import GeminiConfig
from src.database import connect_database, init_database
from src.services.ai_result_parser import (
    parse_ai_result,
    validate_ai_result_against_references,
)
from src.services import gemini_service
from src.services.gemini_service import (
    GeminiHttpError,
    _ApiErrorInfo,
    _call_interactions_api,
    _parse_api_error,
    _parse_retry_delay,
    build_gemini_request_preview,
    generate_gemini_draft,
    mark_latest_gemini_call_validation_failure,
)


def _config() -> GeminiConfig:
    return GeminiConfig(
        api_key="test-key",
        model="gemini-3.6-flash",
        app_id="content-trend-tracker",
        quota_scope_id="honggee-gemini-main",
        timeout_seconds=60,
        retry_wait_seconds=2.0,
        retry_max_wait_seconds=30.0,
    )


def _pack() -> dict:
    references = [
        {
            "id": "S1",
            "reference_kind": "factual_reference",
            "title": "공식 발표",
            "publisher": "공식 기관",
            "url": "https://example.com/official",
            "published_at": "2026-07-25",
            "memo": "기준일 2026년 7월 25일, 핵심 수치 10건",
        }
    ]
    return {
        "content_pack_id": "pack_test",
        "topic_id": "topic_test",
        "audience": "일반 독자",
        "purpose": "핵심 내용 설명",
        "angle": "사실과 해석을 구분",
        "category": "생활 정보",
        "target_length": 2500,
        "title_rules_json": json.dumps(["과장하지 않는다."], ensure_ascii=False),
        "outline_json": json.dumps(["핵심부터 설명한다."], ensure_ascii=False),
        "forbidden_expressions_json": json.dumps(["무조건"], ensure_ascii=False),
        "fact_check_items_json": json.dumps(["기준일 확인"], ensure_ascii=False),
        "references_json": json.dumps(references, ensure_ascii=False),
    }


def _topic() -> dict:
    return {
        "topic_id": "topic_test",
        "title": "공식 발표 정리",
        "summary": "공개 자료를 바탕으로 핵심을 설명",
        "category": "생활 정보",
        "memo": "비공개 연락처 test@example.com",
    }


def _valid_output() -> str:
    return json.dumps(
        {
            "schema_version": "2.0",
            "title": "공식 발표 핵심 정리",
            "summary": "공식 자료를 바탕으로 핵심 내용을 정리했습니다.",
            "category": "생활 정보",
            "tags": ["공식 발표", "생활 정보"],
            "blocks": [
                {"type": "paragraph", "text": "공식 발표의 핵심 내용입니다."}
            ],
            "fact_checks": [
                {
                    "claim": "핵심 수치는 10건이다.",
                    "status": "needs_verification",
                    "reason": "기준일 재확인 필요",
                    "source_ids": ["S1"],
                }
            ],
            "sources": [{"id": "S1"}],
        },
        ensure_ascii=False,
    )


def test_preview_excludes_user_memo_and_source_urls_by_default() -> None:
    preview = build_gemini_request_preview(_pack(), _topic(), _config())

    assert "test@example.com" not in preview.request_text
    assert "https://example.com/official" not in preview.request_text
    assert "topic_test" not in preview.request_text
    assert "Google Search" in preview.request_text
    assert "R1, R2" in preview.request_text
    assert "requested_at_hour" in preview.request_text
    assert not preview.findings


def test_preview_blocks_sensitive_user_memo_when_enabled() -> None:
    preview = build_gemini_request_preview(
        _pack(),
        _topic(),
        _config(),
        include_user_memo=True,
    )

    assert "test@example.com" in preview.request_text
    assert any(item.kind == "email" for item in preview.findings)



def test_direct_draft_api_uses_google_search_and_structured_output(monkeypatch) -> None:
    captured: dict = {}

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, traceback):
            return False

        def read(self):
            return json.dumps(
                {
                    "output_text": _valid_output(),
                    "usage": {
                        "total_input_tokens": 100,
                        "total_output_tokens": 200,
                        "total_thought_tokens": 50,
                        "total_tokens": 350,
                    },
                    "candidates": [{"finishReason": "STOP"}],
                },
                ensure_ascii=False,
            ).encode("utf-8")

    def fake_urlopen(request, timeout):
        captured["payload"] = json.loads(request.data.decode("utf-8"))
        captured["url"] = request.full_url
        captured["timeout"] = timeout
        return FakeResponse()

    monkeypatch.setattr(gemini_service.urllib.request, "urlopen", fake_urlopen)
    (
        output,
        input_tokens,
        output_tokens,
        thought_tokens,
        total_tokens,
        finish_reason,
        finish_message,
    ) = _call_interactions_api(
        _config(),
        "현재 자료를 검색해 초안을 작성하세요.",
        "request-hash",
    )

    assert json.loads(output)["schema_version"] == "2.0"
    assert captured["payload"]["tools"] == [{"type": "google_search"}]
    assert captured["payload"]["response_format"]["mime_type"] == "application/json"
    assert captured["payload"]["generation_config"] == {"thinking_level": "high"}
    assert captured["payload"]["store"] is False
    assert "labels" not in captured["payload"]
    assert captured["url"] == "https://generativelanguage.googleapis.com/v1/interactions"
    assert captured["timeout"] == 60
    assert (input_tokens, output_tokens, thought_tokens, total_tokens) == (100, 200, 50, 350)
    assert finish_reason == "STOP"
    assert finish_message == ""


def test_api_finish_metadata_handles_max_tokens_and_missing_candidates(monkeypatch) -> None:
    payloads = [
        {
            "output_text": _valid_output(),
            "candidates": [
                {
                    "finishReason": "MAX_TOKENS",
                    "finishMessage": "Output token limit reached",
                }
            ],
        },
        {"output_text": _valid_output()},
    ]

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, traceback):
            return False

        def read(self):
            return json.dumps(payloads.pop(0), ensure_ascii=False).encode("utf-8")

    monkeypatch.setattr(
        gemini_service.urllib.request,
        "urlopen",
        lambda *_args, **_kwargs: FakeResponse(),
    )

    first = _call_interactions_api(_config(), "request", "hash-1")
    second = _call_interactions_api(_config(), "request", "hash-2")

    assert first[-2:] == ("MAX_TOKENS", "Output token limit reached")
    assert second[-2:] == ("", "")

def test_generation_retries_temporary_limit_then_caches_valid_response(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "gemini.duckdb"
    init_database(db_path)
    pack = _pack()
    preview = build_gemini_request_preview(pack, _topic(), _config())
    references = json.loads(pack["references_json"])
    attempts = 0
    delays: list[float] = []

    def fake_api_call(config, request_text, request_hash):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise GeminiHttpError(
                _ApiErrorInfo(
                    http_status=429,
                    error_type="rate_limited",
                    message="temporary limit",
                    retryable=True,
                    retry_delay_seconds=2.0,
                )
            )
        return _valid_output(), 100, 200, None, 300, "STOP", ""

    with connect_database(db_path) as con:
        first = generate_gemini_draft(
            con,
            config=_config(),
            content_pack_id="pack_test",
            preview=preview,
            references=references,
            api_call=fake_api_call,
            sleep_func=delays.append,
        )
        parsed = validate_ai_result_against_references(
            parse_ai_result(first.raw_response),
            references,
        )
        second = generate_gemini_draft(
            con,
            config=_config(),
            content_pack_id="pack_test",
            preview=preview,
            references=references,
            api_call=lambda *_: (_ for _ in ()).throw(AssertionError("cache miss")),
        )
        logs = con.execute(
            """
            SELECT attempt_number, status, feature_version, requested_item_count,
                   configured_items_per_request, thinking_level,
                   request_timeout_seconds, finish_reason
            FROM gemini_api_calls
            ORDER BY created_at
            """
        ).fetchall()
        usage_row = con.execute(
            """
            SELECT request_char_count, request_non_whitespace_char_count,
                   request_hangul_char_count, response_char_count
            FROM gemini_api_calls
            WHERE status = 'success_after_retry'
            """
        ).fetchone()

    assert first.success
    assert first.status == "success_after_retry"
    assert first.attempts == 2
    assert delays == [2.0]
    assert parsed.is_valid
    assert parsed.data is not None
    assert parsed.data["sources"][0]["url"] == "https://example.com/official"
    assert second.success and second.cache_hit
    assert logs == [
        (1, "retrying", "4", None, None, "high", 60, ""),
        (2, "success_after_retry", "4", None, None, "high", 60, "STOP"),
        (0, "cache_hit", "4", None, None, "", None, ""),
    ]
    assert usage_row[0] == len(preview.request_text)
    assert usage_row[1] < usage_row[0]
    assert usage_row[2] > 0
    assert usage_row[3] > 0


def test_daily_quota_error_does_not_retry(tmp_path: Path) -> None:
    db_path = tmp_path / "daily.duckdb"
    init_database(db_path)
    pack = _pack()
    preview = build_gemini_request_preview(pack, _topic(), _config())
    calls = 0

    def fake_api_call(config, request_text, request_hash):
        nonlocal calls
        calls += 1
        raise GeminiHttpError(
            _ApiErrorInfo(
                http_status=429,
                error_type="daily_quota_exhausted",
                message="daily limit",
                retryable=False,
                retry_delay_seconds=2.0,
            )
        )

    with connect_database(db_path) as con:
        result = generate_gemini_draft(
            con,
            config=_config(),
            content_pack_id="pack_test",
            preview=preview,
            references=json.loads(pack["references_json"]),
            api_call=fake_api_call,
            sleep_func=lambda _: (_ for _ in ()).throw(AssertionError("unexpected sleep")),
        )

    assert not result.success
    assert result.status == "daily_quota_exhausted"
    assert calls == 1


def test_retry_delay_parses_header_and_retry_info_formats() -> None:
    assert _parse_retry_delay("2") == 2.0
    assert _parse_retry_delay("2.5s") == 2.5
    assert _parse_retry_delay("") is None


def test_api_error_distinguishes_daily_and_temporary_quota() -> None:
    daily = _parse_api_error(
        429,
        json.dumps(
            {
                "error": {
                    "status": "RESOURCE_EXHAUSTED",
                    "message": "Requests per day limit reached",
                }
            }
        ),
    )
    temporary = _parse_api_error(
        429,
        json.dumps(
            {
                "error": {
                    "status": "RESOURCE_EXHAUSTED",
                    "message": "Requests per minute limit reached",
                    "details": [
                        {
                            "@type": "type.googleapis.com/google.rpc.RetryInfo",
                            "retryDelay": "2s",
                        }
                    ],
                }
            }
        ),
    )

    assert daily.error_type == "daily_quota_exhausted"
    assert not daily.retryable
    assert temporary.error_type == "rate_limited"
    assert temporary.retryable
    assert temporary.retry_delay_seconds == 2.0


def test_validation_failure_removes_cached_response(tmp_path: Path) -> None:
    db_path = tmp_path / "invalid-cache.duckdb"
    init_database(db_path)
    pack = _pack()
    preview = build_gemini_request_preview(pack, _topic(), _config())

    with connect_database(db_path) as con:
        result = generate_gemini_draft(
            con,
            config=_config(),
            content_pack_id="pack_test",
            preview=preview,
            references=json.loads(pack["references_json"]),
            api_call=lambda *_: (_valid_output(), 100, 200, 300),
        )
        assert result.success
        assert con.execute(
            "SELECT COUNT(*) FROM gemini_response_cache WHERE request_hash = ?",
            [preview.request_hash],
        ).fetchone()[0] == 1

        mark_latest_gemini_call_validation_failure(
            con,
            app_id=_config().app_id,
            request_hash=preview.request_hash,
            errors=["invalid response"],
        )
        cached_count = con.execute(
            "SELECT COUNT(*) FROM gemini_response_cache WHERE request_hash = ?",
            [preview.request_hash],
        ).fetchone()[0]
        latest_status = con.execute(
            "SELECT status FROM gemini_api_calls ORDER BY created_at DESC LIMIT 1"
        ).fetchone()[0]

    assert cached_count == 0
    assert latest_status == "response_validation_error"


def test_499_cancelled_is_reported_as_connection_termination() -> None:
    info = _parse_api_error(
        499,
        json.dumps(
            {
                "error": {
                    "code": 499,
                    "message": "The operation was cancelled.",
                    "status": "CANCELLED",
                }
            }
        ),
    )

    assert info.error_type == "request_cancelled"
    assert info.retryable is False
    assert "연결이 종료" in info.message
