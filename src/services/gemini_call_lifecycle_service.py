from __future__ import annotations

from collections import defaultdict, deque
from datetime import datetime
from functools import wraps
from pathlib import Path
import os
import threading
from typing import Any, Callable
from uuid import uuid4

from src.config import DEFAULT_DB_PATH
from src.database import connect_database
from src.services.gemini_usage_service import count_text_characters

_DEFAULT_FEATURE_ID = "blog_draft_generation_v1"
_DEFAULT_FEATURE_VERSION = "4"
_PENDING_LOCK = threading.RLock()
_PENDING_CALLS: dict[tuple[str, str, str, str, str], deque[str]] = defaultdict(deque)


def gemini_call_lifecycle_enabled() -> bool:
    if os.getenv("PYTEST_CURRENT_TEST", "").strip():
        return False
    disabled = os.getenv("CONTENT_TREND_DISABLE_GEMINI_CALL_LIFECYCLE", "").strip().casefold()
    return disabled not in {"1", "true", "yes", "on"}


def ensure_gemini_call_lifecycle_schema(con: Any) -> bool:
    """기존 Gemini 호출 원장을 보존하면서 실제 전송/완료 시각 컬럼만 추가합니다."""
    try:
        columns = {
            str(row[1])
            for row in con.execute("PRAGMA table_info('gemini_api_calls')").fetchall()
        }
    except Exception:
        return False
    if not columns:
        return False
    for column_name, column_sql in (
        ("started_at", "TIMESTAMP"),
        ("finished_at", "TIMESTAMP"),
        ("rate_limit_wait_seconds", "DOUBLE DEFAULT 0"),
    ):
        if column_name not in columns:
            con.execute(
                f"ALTER TABLE gemini_api_calls ADD COLUMN {column_name} {column_sql}"
            )
    return True


def _pending_key(config: Any, feature_id: str, request_hash: str) -> tuple[str, str, str, str, str]:
    return (
        str(getattr(config, "app_id", "") or ""),
        str(getattr(config, "quota_scope_id", "") or ""),
        str(getattr(config, "model", "") or ""),
        str(feature_id or _DEFAULT_FEATURE_ID),
        str(request_hash or ""),
    )


def _remember_pending(config: Any, feature_id: str, request_hash: str, call_id: str) -> None:
    with _PENDING_LOCK:
        _PENDING_CALLS[_pending_key(config, feature_id, request_hash)].append(call_id)


def _claim_pending(config: Any, feature_id: str, request_hash: str) -> str:
    key = _pending_key(config, feature_id, request_hash)
    with _PENDING_LOCK:
        bucket = _PENDING_CALLS.get(key)
        if not bucket:
            return ""
        call_id = bucket.popleft()
        if not bucket:
            _PENDING_CALLS.pop(key, None)
        return call_id


def begin_gemini_api_call(
    config: Any,
    request_text: str,
    request_hash: str,
    *,
    feature_id: str,
    thinking_level: str = "",
    timeout_seconds: int | None = None,
    rate_limit_wait_seconds: float = 0.0,
    db_path: str | Path = DEFAULT_DB_PATH,
) -> str:
    """RPM/TPM 대기 후 실제 HTTP 전송 직전에 `전송 중` 행을 먼저 기록합니다."""
    call_id = f"gemcall_{uuid4().hex}"
    started_at = datetime.now()
    counts = count_text_characters(str(request_text or ""))
    try:
        with connect_database(db_path) as con:
            if not ensure_gemini_call_lifecycle_schema(con):
                return ""
            con.execute(
                """
                INSERT INTO gemini_api_calls(
                    call_id, app_id, quota_scope_id, feature_id, feature_version,
                    content_pack_id, request_hash, model_name, attempt_number,
                    cache_hit, status, http_status, error_type, retry_reason,
                    retry_wait_seconds, input_tokens, output_tokens, thought_tokens,
                    total_tokens, request_char_count,
                    request_non_whitespace_char_count, request_hangul_char_count,
                    response_char_count, response_non_whitespace_char_count,
                    response_hangul_char_count, requested_item_count,
                    configured_items_per_request, thinking_level,
                    request_timeout_seconds, finish_reason, finish_message,
                    duration_ms, error_message, created_at, started_at, finished_at,
                    rate_limit_wait_seconds
                ) VALUES (
                    ?, ?, ?, ?, '', ?, ?, ?, 1,
                    FALSE, 'in_progress', NULL, '', '',
                    0, NULL, NULL, NULL,
                    NULL, ?, ?, ?,
                    NULL, NULL, NULL, NULL,
                    NULL, ?,
                    ?, '', '',
                    0, '', ?, ?, NULL,
                    ?
                )
                """,
                [
                    call_id,
                    str(getattr(config, "app_id", "") or ""),
                    str(getattr(config, "quota_scope_id", "") or ""),
                    str(feature_id or _DEFAULT_FEATURE_ID),
                    f"gemini_pending_{str(request_hash or '')[:20]}",
                    str(request_hash or ""),
                    str(getattr(config, "model", "") or ""),
                    int(counts.total),
                    int(counts.non_whitespace),
                    int(counts.hangul),
                    str(thinking_level or ""),
                    None if timeout_seconds is None else max(0, int(timeout_seconds)),
                    started_at,
                    started_at,
                    max(0.0, float(rate_limit_wait_seconds or 0.0)),
                ],
            )
    except Exception:
        # 로그 실패가 실제 사용자 요청을 막으면 안 됩니다.
        return ""
    _remember_pending(config, feature_id, request_hash, call_id)
    return call_id


def _provider_result_values(result: Any) -> tuple[int | None, int | None, int | None, int | None, str, str]:
    if not isinstance(result, tuple):
        return None, None, None, None, "", ""
    try:
        if len(result) == 4:
            _, input_tokens, output_tokens, total_tokens = result
            return input_tokens, output_tokens, None, total_tokens, "", ""
        if len(result) == 5:
            _, input_tokens, output_tokens, thought_tokens, total_tokens = result
            return input_tokens, output_tokens, thought_tokens, total_tokens, "", ""
        if len(result) >= 7:
            _, input_tokens, output_tokens, thought_tokens, total_tokens, finish_reason, finish_message = result[:7]
            return (
                input_tokens,
                output_tokens,
                thought_tokens,
                total_tokens,
                str(finish_reason or ""),
                str(finish_message or ""),
            )
    except Exception:
        pass
    return None, None, None, None, "", ""


def mark_gemini_api_provider_complete(
    call_id: str,
    *,
    result: Any = None,
    error: BaseException | None = None,
    db_path: str | Path = DEFAULT_DB_PATH,
) -> None:
    """HTTP 반환/예외 직후 완료 시각을 남겨 후속 파싱·DB 저장 시간과 분리합니다."""
    if not call_id:
        return
    finished_at = datetime.now()
    try:
        with connect_database(db_path) as con:
            if not ensure_gemini_call_lifecycle_schema(con):
                return
            row = con.execute(
                "SELECT started_at FROM gemini_api_calls WHERE call_id = ?",
                [call_id],
            ).fetchone()
            if row is None:
                return
            started_at = row[0] if isinstance(row[0], datetime) else finished_at
            duration_ms = max(0, int((finished_at - started_at).total_seconds() * 1000))
            if error is None:
                input_tokens, output_tokens, thought_tokens, total_tokens, finish_reason, finish_message = _provider_result_values(result)
                con.execute(
                    """
                    UPDATE gemini_api_calls
                    SET status = 'response_received', http_status = 200,
                        input_tokens = ?, output_tokens = ?, thought_tokens = ?, total_tokens = ?,
                        finish_reason = ?, finish_message = ?, duration_ms = ?, finished_at = ?
                    WHERE call_id = ?
                    """,
                    [
                        input_tokens,
                        output_tokens,
                        thought_tokens,
                        total_tokens,
                        finish_reason[:100],
                        finish_message[:1000],
                        duration_ms,
                        finished_at,
                        call_id,
                    ],
                )
                return

            info = getattr(error, "info", None)
            http_status = getattr(info, "http_status", None)
            error_type = str(getattr(info, "error_type", "") or type(error).__name__)
            error_message = str(getattr(info, "message", "") or error)
            finish_reason = str(getattr(info, "finish_reason", "") or "")
            finish_message = str(getattr(info, "finish_message", "") or "")
            con.execute(
                """
                UPDATE gemini_api_calls
                SET status = 'failed', http_status = ?, error_type = ?, error_message = ?,
                    finish_reason = ?, finish_message = ?, duration_ms = ?, finished_at = ?
                WHERE call_id = ?
                """,
                [
                    http_status,
                    error_type[:100],
                    error_message[:1000],
                    finish_reason[:100],
                    finish_message[:1000],
                    duration_ms,
                    finished_at,
                    call_id,
                ],
            )
    except Exception:
        return


def _finalize_pending_row(con: Any, call_id: str, kwargs: dict[str, Any]) -> bool:
    if not ensure_gemini_call_lifecycle_schema(con):
        return False
    row = con.execute(
        "SELECT started_at, finished_at, duration_ms FROM gemini_api_calls WHERE call_id = ?",
        [call_id],
    ).fetchone()
    if row is None:
        return False
    now = datetime.now()
    started_at = row[0] if isinstance(row[0], datetime) else now
    finished_at = row[1] if isinstance(row[1], datetime) else now
    duration_ms = max(0, int(row[2] or 0))
    if row[1] is None:
        duration_ms = max(0, int((finished_at - started_at).total_seconds() * 1000))
    response_counts = count_text_characters(str(kwargs.get("response_text") or ""))
    feature_id = str(kwargs.get("feature_id") or _DEFAULT_FEATURE_ID)
    feature_version = str(
        kwargs.get("feature_version")
        or (_DEFAULT_FEATURE_VERSION if feature_id == _DEFAULT_FEATURE_ID else "")
    )
    con.execute(
        """
        UPDATE gemini_api_calls
        SET feature_version = ?, content_pack_id = ?, attempt_number = ?,
            cache_hit = ?, status = ?, http_status = ?, error_type = ?,
            retry_reason = ?, retry_wait_seconds = ?, input_tokens = ?,
            output_tokens = ?, thought_tokens = ?, total_tokens = ?,
            response_char_count = ?, response_non_whitespace_char_count = ?,
            response_hangul_char_count = ?, requested_item_count = ?,
            configured_items_per_request = ?, thinking_level = ?,
            request_timeout_seconds = ?, finish_reason = ?, finish_message = ?,
            duration_ms = ?, error_message = ?, finished_at = ?
        WHERE call_id = ?
        """,
        [
            feature_version[:40],
            str(kwargs.get("content_pack_id") or f"gemini_pending_{str(kwargs.get('request_hash') or '')[:20]}"),
            max(0, int(kwargs.get("attempt_number") or 0)),
            bool(kwargs.get("cache_hit")),
            str(kwargs.get("status") or ""),
            kwargs.get("http_status"),
            str(kwargs.get("error_type") or "")[:100],
            str(kwargs.get("retry_reason") or "")[:200],
            max(0.0, float(kwargs.get("retry_wait_seconds") or 0.0)),
            kwargs.get("input_tokens"),
            kwargs.get("output_tokens"),
            kwargs.get("thought_tokens"),
            kwargs.get("total_tokens"),
            int(response_counts.total),
            int(response_counts.non_whitespace),
            int(response_counts.hangul),
            None if kwargs.get("requested_item_count") is None else max(0, int(kwargs.get("requested_item_count") or 0)),
            None if kwargs.get("configured_items_per_request") is None else max(0, int(kwargs.get("configured_items_per_request") or 0)),
            str(kwargs.get("thinking_level") or ""),
            None if kwargs.get("request_timeout_seconds") is None else max(0, int(kwargs.get("request_timeout_seconds") or 0)),
            str(kwargs.get("finish_reason") or "")[:100],
            str(kwargs.get("finish_message") or "")[:1000],
            duration_ms,
            str(kwargs.get("error_message") or "")[:1000],
            finished_at,
            call_id,
        ],
    )
    return True


def build_lifecycle_record_call(original: Callable[..., None]) -> Callable[..., None]:
    """기존 사후 INSERT를 실제 전송 직전에 만든 같은 행의 UPDATE로 바꿉니다."""
    @wraps(original)
    def wrapped(*args: Any, **kwargs: Any) -> None:
        config = kwargs.get("config")
        request_hash = str(kwargs.get("request_hash") or "")
        feature_id = str(kwargs.get("feature_id") or _DEFAULT_FEATURE_ID)
        if config is None or not request_hash:
            return original(*args, **kwargs)
        call_id = _claim_pending(config, feature_id, request_hash)
        if not call_id:
            return original(*args, **kwargs)
        con = args[0] if args else kwargs.get("con")
        if con is None:
            return original(*args, **kwargs)
        try:
            if _finalize_pending_row(con, call_id, kwargs):
                return None
        except Exception:
            pass
        return original(*args, **kwargs)

    setattr(wrapped, "_gemini_call_lifecycle_record", True)
    setattr(wrapped, "_gemini_call_lifecycle_original", original)
    return wrapped
