"""NAVER·Daum 검색 요청을 메모리에 모아 수집 종료 시 DuckDB에 저장합니다."""

from __future__ import annotations

import json
import socket
from collections import defaultdict
from datetime import datetime
from threading import RLock
from typing import Any
from urllib.error import HTTPError, URLError

import duckdb


_CAPTURE_LOCK = RLock()
_ACTIVE_RUN_ID: str | None = None
_CAPTURED_ATTEMPTS: dict[str, list[dict[str, Any]]] = defaultdict(list)
_NEXT_SEQUENCE = 0
_MAX_ERROR_MESSAGE_LENGTH = 1000


def activate_portal_request_capture(run_id: str) -> None:
    """다음 포털 요청을 지정한 수집 실행에 연결합니다."""
    normalized = str(run_id or "").strip()
    if not normalized:
        return
    global _ACTIVE_RUN_ID
    with _CAPTURE_LOCK:
        _ACTIVE_RUN_ID = normalized
        _CAPTURED_ATTEMPTS.setdefault(normalized, [])


def discard_portal_request_capture(run_id: str) -> None:
    """완료되었거나 더 이상 기록할 수 없는 실행의 메모리 기록을 정리합니다."""
    normalized = str(run_id or "").strip()
    if not normalized:
        return
    global _ACTIVE_RUN_ID
    with _CAPTURE_LOCK:
        _CAPTURED_ATTEMPTS.pop(normalized, None)
        if _ACTIVE_RUN_ID == normalized:
            _ACTIVE_RUN_ID = None


def _exception_chain(exc: BaseException) -> list[BaseException]:
    chain: list[BaseException] = []
    current: BaseException | None = exc
    seen: set[int] = set()
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        chain.append(current)
        current = current.__cause__ or current.__context__
    return chain


def _error_details(exc: BaseException | None) -> tuple[int | None, str | None, str | None]:
    if exc is None:
        return None, None, None

    chain = _exception_chain(exc)
    http_status = next(
        (int(item.code) for item in chain if isinstance(item, HTTPError)),
        None,
    )
    if http_status is not None:
        error_type = f"http_{http_status}"
    elif any(isinstance(item, socket.gaierror) for item in chain):
        error_type = "dns_error"
    elif any(isinstance(item, (TimeoutError, socket.timeout)) for item in chain):
        error_type = "timeout"
    elif any(isinstance(item, (URLError, ConnectionError)) for item in chain):
        error_type = "network_error"
    elif any(isinstance(item, json.JSONDecodeError) for item in chain):
        error_type = "response_json_error"
    else:
        error_type = type(exc).__name__ or "request_error"
    return http_status, error_type, str(exc)[:_MAX_ERROR_MESSAGE_LENGTH]


def record_portal_request_attempt(
    *,
    source_name: str,
    source_type: str,
    discovery_query: str,
    request_page: int,
    requested_result_count: int,
    result_count: int,
    duration_ms: int,
    started_at: datetime,
    finished_at: datetime,
    error: BaseException | None = None,
) -> bool:
    """실제 adapter.search 한 번을 물리 요청 시도로 기록합니다.

    재시도는 같은 논리 요청에 여러 시도로 쌓이며, 수집 종료 때 한 행으로 합쳐집니다.
    활성 수집 실행이 없으면 기존 단독 adapter 호출과 테스트를 방해하지 않고 생략합니다.
    """
    normalized_source = str(source_name or "").strip().casefold()
    normalized_type = str(source_type or "").strip().casefold()
    normalized_query = str(discovery_query or "").strip()
    if normalized_source not in {"naver", "daum"} or not normalized_type or not normalized_query:
        return False

    http_status, error_type, error_message = _error_details(error)
    global _NEXT_SEQUENCE
    with _CAPTURE_LOCK:
        run_id = _ACTIVE_RUN_ID
        if not run_id:
            return False
        _NEXT_SEQUENCE += 1
        _CAPTURED_ATTEMPTS[run_id].append(
            {
                "sequence": _NEXT_SEQUENCE,
                "run_id": run_id,
                "source_name": normalized_source,
                "source_type": normalized_type,
                "discovery_query": normalized_query,
                "request_page": max(1, int(request_page or 1)),
                "requested_result_count": max(0, int(requested_result_count or 0)),
                "status": "failure" if error is not None else "success",
                "result_count": max(0, int(result_count or 0)),
                "duration_ms": max(0, int(duration_ms or 0)),
                "http_status": http_status,
                "error_type": error_type,
                "error_message": error_message,
                "started_at": started_at,
                "finished_at": finished_at,
            }
        )
    return True


def _captured_snapshot(run_id: str) -> list[dict[str, Any]]:
    with _CAPTURE_LOCK:
        return [dict(row) for row in _CAPTURED_ATTEMPTS.get(run_id, ())]


def _discovery_save_counts(
    con: duckdb.DuckDBPyConnection,
    *,
    run_id: str,
    requested_by_key: dict[tuple[str, str, int], int],
) -> dict[tuple[str, str, int], dict[str, int]]:
    rows = con.execute(
        """
        SELECT source_type, discovery_query, result_rank, is_new
        FROM collection_query_discoveries
        WHERE run_id = ?
        """,
        [run_id],
    ).fetchall()
    counts: dict[tuple[str, str, int], dict[str, int]] = defaultdict(
        lambda: {"newly_saved_count": 0, "updated_count": 0}
    )
    page_sizes: dict[tuple[str, str], int] = {}
    for (source_type, query, _page), size in requested_by_key.items():
        page_sizes[(source_type, query)] = max(page_sizes.get((source_type, query), 0), size)

    for source_type, query, result_rank, is_new in rows:
        type_key = str(source_type or "")
        query_key = str(query or "")
        page_size = page_sizes.get((type_key, query_key), 0)
        rank = int(result_rank or 0)
        request_page = ((rank - 1) // page_size) + 1 if rank > 0 and page_size > 0 else 1
        key = (type_key, query_key, request_page)
        if key not in requested_by_key:
            matching_pages = [
                candidate
                for candidate in requested_by_key
                if candidate[0] == type_key and candidate[1] == query_key
            ]
            if len(matching_pages) != 1:
                continue
            key = matching_pages[0]
        field = "newly_saved_count" if bool(is_new) else "updated_count"
        counts[key][field] += 1
    return counts


def flush_portal_request_capture(
    con: duckdb.DuckDBPyConnection,
    run_id: str,
) -> int:
    """메모리의 물리 시도를 논리 요청별로 합쳐 추가형 원장에 저장합니다."""
    normalized_run_id = str(run_id or "").strip()
    if not normalized_run_id:
        return 0
    attempts = _captured_snapshot(normalized_run_id)
    if not attempts:
        discard_portal_request_capture(normalized_run_id)
        return 0

    grouped: dict[tuple[str, str, str, int], list[dict[str, Any]]] = defaultdict(list)
    for attempt in attempts:
        key = (
            str(attempt["source_name"]),
            str(attempt["source_type"]),
            str(attempt["discovery_query"]),
            int(attempt["request_page"]),
        )
        grouped[key].append(attempt)

    requested_by_key = {
        (source_type, query, page): max(
            int(row.get("requested_result_count") or 0) for row in rows
        )
        for (_source, source_type, query, page), rows in grouped.items()
    }
    save_counts = _discovery_save_counts(
        con,
        run_id=normalized_run_id,
        requested_by_key=requested_by_key,
    )

    created_at = datetime.now()
    ledger_rows: list[list[Any]] = []
    for (source_name, source_type, query, page), rows in grouped.items():
        ordered = sorted(rows, key=lambda row: int(row.get("sequence") or 0))
        final = ordered[-1]
        failed_attempts = [row for row in ordered if row.get("status") == "failure"]
        last_failure = failed_attempts[-1] if failed_attempts else {}
        requested_count = requested_by_key[(source_type, query, page)]
        saved = save_counts.get(
            (source_type, query, page),
            {"newly_saved_count": 0, "updated_count": 0},
        )
        result_count = int(final.get("result_count") or 0) if final.get("status") == "success" else 0
        newly_saved = int(saved["newly_saved_count"])
        updated = int(saved["updated_count"])
        ledger_rows.append(
            [
                normalized_run_id,
                source_name,
                source_type,
                query,
                page,
                requested_count,
                str(final.get("status") or "failure"),
                len(ordered),
                max(0, len(ordered) - 1),
                result_count,
                newly_saved,
                updated,
                max(0, result_count - newly_saved - updated),
                last_failure.get("http_status"),
                last_failure.get("error_type"),
                last_failure.get("error_message"),
                sum(int(row.get("duration_ms") or 0) for row in ordered),
                min(row["started_at"] for row in ordered),
                max(row["finished_at"] for row in ordered),
                created_at,
            ]
        )

    con.executemany(
        """
        INSERT INTO collection_query_requests(
            run_id, source_name, source_type, discovery_query, request_page,
            requested_result_count, status, attempt_count, retry_count, result_count,
            newly_saved_count, updated_count, skipped_count, http_status, error_type,
            error_message, duration_ms, started_at, finished_at, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(run_id, source_name, source_type, discovery_query, request_page)
        DO UPDATE SET
            requested_result_count = EXCLUDED.requested_result_count,
            status = EXCLUDED.status,
            attempt_count = EXCLUDED.attempt_count,
            retry_count = EXCLUDED.retry_count,
            result_count = EXCLUDED.result_count,
            newly_saved_count = EXCLUDED.newly_saved_count,
            updated_count = EXCLUDED.updated_count,
            skipped_count = EXCLUDED.skipped_count,
            http_status = EXCLUDED.http_status,
            error_type = EXCLUDED.error_type,
            error_message = EXCLUDED.error_message,
            duration_ms = EXCLUDED.duration_ms,
            started_at = EXCLUDED.started_at,
            finished_at = EXCLUDED.finished_at,
            created_at = EXCLUDED.created_at
        """,
        ledger_rows,
    )
    discard_portal_request_capture(normalized_run_id)
    return len(ledger_rows)
