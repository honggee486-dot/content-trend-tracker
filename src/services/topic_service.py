from __future__ import annotations

import hashlib
import json
from datetime import datetime
from typing import Any, Iterable, Protocol
from uuid import uuid4

import duckdb
import pandas as pd

from src.services.trend_normalization import (
    normalize_title as normalize_trend_title,
    normalize_url,
)

SIGNAL_TYPE_LABELS = {
    "emerging_topic": "떠오르는 주제",
    "recent_video": "최근 영상",
    "content_idea": "콘텐츠 아이디어",
    "naver_news": "뉴스",
    "naver_blog": "NAVER 블로그",
    "daum_web": "Daum 웹문서",
    "daum_cafe": "Daum 카페",
    "google_trend": "Google Trends",
    "wikipedia_pageview": "위키백과 조회수",
    "other": "기타 신호",
}


class SignalAdapter(Protocol):
    def load_signals(self, limit: int = 100) -> list[dict[str, Any]]: ...


def normalize_title(value: str) -> str:
    return normalize_trend_title(value)


def _first_not_none(*values: Any) -> Any:
    for value in values:
        if value is not None:
            return value
    return None


def infer_signal_type(source: dict[str, Any]) -> str:
    metadata = source.get("metadata") or {}
    signal_type = str(metadata.get("signal_type") or "").strip()
    if signal_type in SIGNAL_TYPE_LABELS:
        return signal_type

    source_name = str(source.get("source_name") or "")
    if "떠오르는 주제" in source_name:
        return "emerging_topic"
    if "최근 영상" in source_name:
        return "recent_video"
    if "콘텐츠 아이디어" in source_name:
        return "content_idea"
    return "other"


def _source_id(source_type: str, external_id: str) -> str:
    raw = f"{source_type}|{external_id}".casefold()
    return "src_" + hashlib.sha1(raw.encode("utf-8")).hexdigest()[:20]


def add_manual_topic(
    con: duckdb.DuckDBPyConnection,
    *,
    title: str,
    summary: str = "",
    category: str = "",
    memo: str = "",
    priority: int = 2,
) -> tuple[str, bool]:
    clean_title = str(title or "").strip()
    normalized = normalize_title(clean_title)
    if not clean_title or not normalized:
        raise ValueError("주제명을 입력하세요.")

    existing = con.execute(
        """
        SELECT topic_id FROM topics
        WHERE normalized_title = ? AND archived_at IS NULL
        LIMIT 1
        """,
        [normalized],
    ).fetchone()
    now = datetime.now()
    if existing:
        topic_id = str(existing[0])
        con.execute(
            """
            UPDATE topics
            SET is_interested = TRUE,
                summary = CASE WHEN ? <> '' THEN ? ELSE summary END,
                category = CASE WHEN ? <> '' THEN ? ELSE category END,
                memo = CASE WHEN ? <> '' THEN ? ELSE memo END,
                updated_at = ?
            WHERE topic_id = ?
            """,
            [summary, summary, category, category, memo, memo, now, topic_id],
        )
        return topic_id, False

    topic_id = f"topic_{uuid4().hex}"
    con.execute(
        """
        INSERT INTO topics(
            topic_id, title, normalized_title, summary, category, status,
            priority, is_interested, memo, source_count,
            first_seen_at, last_seen_at, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, 'candidate', ?, TRUE, ?, 0, ?, ?, ?, ?)
        """,
        [
            topic_id,
            clean_title,
            normalized,
            summary.strip(),
            category.strip(),
            max(1, min(int(priority), 3)),
            memo.strip(),
            now,
            now,
            now,
            now,
        ],
    )
    return topic_id, True



def _collection_source_name(sync_source_type: str) -> str:
    normalized = str(sync_source_type or "").strip().casefold()
    if normalized == "naver_search":
        return "naver"
    if normalized == "daum_search":
        return "daum"
    return normalized or "external"


def _positive_int(value: Any) -> int | None:
    try:
        parsed = int(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return parsed if parsed > 0 else None


def _record_query_discoveries(
    con: duckdb.DuckDBPyConnection,
    *,
    collection_run_id: str | None,
    source_name: str,
    prepared_rows: list[dict[str, Any]],
    existing_source_ids: set[str],
) -> int:
    if not collection_run_id:
        return 0

    first_new_position_by_source_id: dict[str, int] = {}
    for row in prepared_rows:
        source_item_id = str(row.get("source_item_id") or "").strip()
        if source_item_id and source_item_id not in existing_source_ids:
            first_new_position_by_source_id.setdefault(
                source_item_id, int(row.get("position", 0) or 0)
            )

    deduplicated: dict[tuple[str, str, str], dict[str, Any]] = {}
    for row in prepared_rows:
        query = str(row.get("discovery_query") or "").strip()
        source_type = str(row.get("source_type") or "").strip()
        source_item_id = str(row.get("source_item_id") or "").strip()
        if not query or not source_type or not source_item_id:
            continue
        key = (source_type, query, source_item_id)
        incoming_rank = _positive_int(row.get("result_rank"))
        incoming = {
            "run_id": collection_run_id,
            "source_name": source_name,
            "source_type": source_type,
            "discovery_query": query,
            "source_item_id": source_item_id,
            # source_items가 URL 기준으로 기존 행에 합쳐져도 이번 검색 응답의
            # 외부 ID와 URL을 발견 증거로 보존합니다.
            "external_id": str(row.get("external_id") or "").strip(),
            "source_url": str(row.get("source_url") or "").strip(),
            # 배치 내 같은 원문이 여러 검색어에서 발견되면 실제 최초 저장을
            # 일으킨 첫 결과만 신규로 기록하고 나머지는 기존 갱신으로 봅니다.
            "is_new": (
                source_item_id not in existing_source_ids
                and int(row.get("position", 0) or 0)
                == first_new_position_by_source_id.get(source_item_id)
            ),
            "result_rank": incoming_rank,
            "discovered_at": row.get("discovered_at") or datetime.now(),
        }
        current = deduplicated.get(key)
        if current is None:
            deduplicated[key] = incoming
            continue
        current["is_new"] = bool(current["is_new"] or incoming["is_new"])
        if incoming["source_url"]:
            current["source_url"] = incoming["source_url"]
        if incoming["external_id"]:
            current["external_id"] = incoming["external_id"]
        current_rank = _positive_int(current.get("result_rank"))
        if current_rank is None or (incoming_rank is not None and incoming_rank < current_rank):
            current["result_rank"] = incoming_rank
        if incoming["discovered_at"] < current["discovered_at"]:
            current["discovered_at"] = incoming["discovered_at"]

    if not deduplicated:
        return 0

    frame = pd.DataFrame(list(deduplicated.values()))
    frame["result_rank"] = pd.array(frame["result_rank"], dtype="Int64")
    con.register("_incoming_query_discoveries", frame)
    try:
        con.execute(
            """
            INSERT INTO collection_query_discoveries(
                run_id, source_name, source_type, discovery_query, source_item_id,
                external_id, source_url, is_new, result_rank, discovered_at
            )
            SELECT run_id, source_name, source_type, discovery_query, source_item_id,
                   external_id, source_url, is_new, result_rank, discovered_at
            FROM _incoming_query_discoveries
            ON CONFLICT(run_id, source_type, discovery_query, source_item_id) DO UPDATE SET
                external_id = EXCLUDED.external_id,
                source_url = CASE
                    WHEN COALESCE(TRIM(EXCLUDED.source_url), '') <> '' THEN EXCLUDED.source_url
                    ELSE collection_query_discoveries.source_url
                END,
                is_new = collection_query_discoveries.is_new OR EXCLUDED.is_new,
                result_rank = CASE
                    WHEN collection_query_discoveries.result_rank IS NULL THEN EXCLUDED.result_rank
                    WHEN EXCLUDED.result_rank IS NULL THEN collection_query_discoveries.result_rank
                    ELSE LEAST(collection_query_discoveries.result_rank, EXCLUDED.result_rank)
                END,
                discovered_at = LEAST(
                    collection_query_discoveries.discovered_at,
                    EXCLUDED.discovered_at
                )
            """
        )
    finally:
        con.unregister("_incoming_query_discoveries")
    return len(deduplicated)


def _batch_upsert_source_signals(
    con: duckdb.DuckDBPyConnection,
    signals: list[dict[str, Any]],
    *,
    collection_run_id: str | None = None,
    collection_source_name: str = "external",
) -> tuple[int, int, int]:
    """주제 생성이 필요 없는 외부 신호를 DuckDB에 한 번에 반영합니다.

    포털 검색 결과처럼 수천 건이 들어오는 경우 행마다 SELECT/INSERT를 반복하지 않고
    기존 URL·외부 ID를 한 번에 조회한 뒤 staging DataFrame으로 UPSERT합니다.
    """
    now = datetime.now()
    prepared: list[dict[str, Any]] = []
    skipped = 0
    for position, signal in enumerate(signals):
        if not isinstance(signal, dict):
            skipped += 1
            continue
        source_type = str(signal.get("source_type") or "unknown").strip()
        external_id = str(signal.get("external_id") or "").strip()
        title = str(signal.get("title") or "").strip()
        normalized = normalize_title(title)
        if not external_id or not normalized:
            skipped += 1
            continue
        metadata_value = signal.get("metadata") or {}
        metadata = metadata_value if isinstance(metadata_value, dict) else {}
        source_url = str(signal.get("source_url") or "").strip()
        normalized_url = normalize_url(source_url)
        observed_at = signal.get("observed_at")
        prepared.append(
            {
                "row_key": f"{position}:{source_type}:{external_id}",
                "position": position,
                "source_type": source_type,
                "external_id": external_id,
                "raw_title": title,
                "normalized_title": normalized,
                "source_url": source_url,
                "normalized_url": normalized_url,
                "source_name": signal.get("source_name"),
                "published_at": signal.get("published_at"),
                "observed_at": observed_at,
                "signal_value": signal.get("signal_value"),
                "metadata_json": json.dumps(
                    metadata_value, ensure_ascii=False, default=str
                ),
                "discovery_query": str(metadata.get("discovery_query") or "").strip(),
                "result_rank": _positive_int(metadata.get("result_rank")),
                "discovered_at": observed_at if isinstance(observed_at, datetime) else now,
                "first_imported_at": now,
                "previous_imported_at": None,
                "last_imported_at": now,
                "observation_count": 1,
                "imported_at": now,
            }
        )

    if not prepared:
        return 0, 0, skipped

    keys_frame = pd.DataFrame(
        [
            {
                "row_key": row["row_key"],
                "source_type": row["source_type"],
                "external_id": row["external_id"],
                "normalized_url": row["normalized_url"],
            }
            for row in prepared
        ]
    )
    con.register("_incoming_signal_keys", keys_frame)
    try:
        existing_rows = con.execute(
            """
            SELECT row_key, source_item_id, stored_external_id
            FROM (
                SELECT k.row_key,
                       s.source_item_id,
                       s.external_id AS stored_external_id,
                       ROW_NUMBER() OVER (
                           PARTITION BY k.row_key
                           ORDER BY
                               CASE
                                   WHEN COALESCE(k.normalized_url, '') <> ''
                                    AND s.normalized_url = k.normalized_url THEN 0
                                   ELSE 1
                               END,
                               s.imported_at DESC
                       ) AS row_num
                FROM _incoming_signal_keys k
                LEFT JOIN source_items s
                  ON s.source_type = k.source_type
                 AND (
                       s.external_id = k.external_id
                       OR (
                           COALESCE(k.normalized_url, '') <> ''
                           AND s.normalized_url = k.normalized_url
                       )
                 )
            ) matched
            WHERE row_num = 1
            """
        ).fetchall()
    finally:
        con.unregister("_incoming_signal_keys")

    existing_by_row = {
        str(row_key): (str(source_item_id), str(stored_external_id))
        for row_key, source_item_id, stored_external_id in existing_rows
        if source_item_id is not None
    }
    existing_source_ids = {item[0] for item in existing_by_row.values()}

    final_by_id: dict[str, dict[str, Any]] = {}
    occurrence_count: dict[str, int] = {}
    for row in prepared:
        existing = existing_by_row.get(str(row["row_key"]))
        if existing:
            source_item_id, stored_external_id = existing
        else:
            source_item_id = _source_id(str(row["source_type"]), str(row["external_id"]))
            stored_external_id = str(row["external_id"])
        row["source_item_id"] = source_item_id
        row["stored_external_id"] = stored_external_id
        occurrence_count[source_item_id] = occurrence_count.get(source_item_id, 0) + 1
        final_by_id[source_item_id] = row

    stage_frame = pd.DataFrame(
        [
            {
                "source_item_id": row["source_item_id"],
                "source_type": row["source_type"],
                "external_id": row["stored_external_id"],
                "raw_title": row["raw_title"],
                "normalized_title": row["normalized_title"],
                "source_url": row["source_url"],
                "normalized_url": row["normalized_url"],
                "source_name": row["source_name"],
                "published_at": row["published_at"],
                "observed_at": row["observed_at"],
                "signal_value": row["signal_value"],
                "metadata_json": row["metadata_json"],
                "first_imported_at": row["first_imported_at"],
                "previous_imported_at": row["previous_imported_at"],
                "last_imported_at": row["last_imported_at"],
                "observation_count": row["observation_count"],
                "imported_at": row["imported_at"],
            }
            for row in final_by_id.values()
        ]
    )
    con.execute("BEGIN TRANSACTION")
    con.register("_incoming_source_items", stage_frame)
    try:
        con.execute(
            """
            INSERT INTO source_items(
                source_item_id, source_type, external_id, raw_title, normalized_title,
                source_url, normalized_url, source_name, published_at, observed_at,
                signal_value, metadata_json, first_imported_at, previous_imported_at,
                last_imported_at, observation_count, imported_at
            )
            SELECT source_item_id, source_type, external_id, raw_title, normalized_title,
                   source_url, normalized_url, source_name, published_at, observed_at,
                   signal_value, metadata_json, first_imported_at, previous_imported_at,
                   last_imported_at, observation_count, imported_at
            FROM _incoming_source_items
            ON CONFLICT(source_item_id) DO UPDATE SET
                raw_title = EXCLUDED.raw_title,
                normalized_title = EXCLUDED.normalized_title,
                source_url = EXCLUDED.source_url,
                normalized_url = EXCLUDED.normalized_url,
                source_name = EXCLUDED.source_name,
                published_at = EXCLUDED.published_at,
                observed_at = EXCLUDED.observed_at,
                signal_value = EXCLUDED.signal_value,
                metadata_json = EXCLUDED.metadata_json,
                first_imported_at = COALESCE(
                    source_items.first_imported_at,
                    source_items.imported_at,
                    EXCLUDED.first_imported_at
                ),
                previous_imported_at = COALESCE(
                    source_items.last_imported_at,
                    source_items.imported_at
                ),
                last_imported_at = EXCLUDED.last_imported_at,
                observation_count = GREATEST(
                    COALESCE(source_items.observation_count, 1) + 1,
                    2
                ),
                imported_at = EXCLUDED.imported_at
            """
        )
        _record_query_discoveries(
            con,
            collection_run_id=collection_run_id,
            source_name=collection_source_name,
            prepared_rows=prepared,
            existing_source_ids=existing_source_ids,
        )
        con.execute("COMMIT")
    except Exception:
        try:
            con.execute("ROLLBACK")
        except Exception:
            pass
        raise
    finally:
        con.unregister("_incoming_source_items")

    added = sum(1 for source_id in final_by_id if source_id not in existing_source_ids)
    valid_count = len(prepared)
    updated = max(0, valid_count - added)
    return added, updated, skipped


def _save_signal_batch(
    con: duckdb.DuckDBPyConnection,
    signals: list[dict[str, Any]],
    *,
    sync_source_type: str,
    create_topics: bool,
    collection_run_id: str | None,
) -> tuple[int, int, int]:
    added = 0
    updated = 0
    skipped = 0
    if not create_topics:
        return _batch_upsert_source_signals(
            con,
            signals,
            collection_run_id=collection_run_id,
            collection_source_name=_collection_source_name(sync_source_type),
        )

    for signal in signals:
        if not isinstance(signal, dict):
            skipped += 1
            continue
        external_id = str(signal.get("external_id") or "").strip()
        title = str(signal.get("title") or "").strip()
        if not external_id or not normalize_title(title):
            skipped += 1
            continue
        action = upsert_source_signal(con, signal, create_topic=True)
        if action == "added":
            added += 1
        else:
            updated += 1
    return added, updated, skipped


def _start_sync_run(
    con: duckdb.DuckDBPyConnection,
    *,
    sync_source_type: str,
) -> str:
    run_id = f"sync_{uuid4().hex}"
    con.execute(
        """
        INSERT INTO sync_runs(
            sync_run_id, source_type, started_at, status,
            items_read, items_added, items_updated
        ) VALUES (?, ?, ?, 'running', 0, 0, 0)
        """,
        [run_id, sync_source_type, datetime.now()],
    )
    return run_id


def _finish_sync_run_success(
    con: duckdb.DuckDBPyConnection,
    *,
    run_id: str,
    items_read: int,
    added: int,
    updated: int,
) -> None:
    con.execute(
        """
        UPDATE sync_runs
        SET finished_at = ?, status = 'success', items_read = ?,
            items_added = ?, items_updated = ?
        WHERE sync_run_id = ?
        """,
        [datetime.now(), items_read, added, updated, run_id],
    )


def _finish_sync_run_failure(
    con: duckdb.DuckDBPyConnection,
    *,
    run_id: str,
    error: BaseException,
) -> None:
    con.execute(
        """
        UPDATE sync_runs
        SET finished_at = ?, status = 'failed', error_message = ?
        WHERE sync_run_id = ?
        """,
        [datetime.now(), str(error)[:2000], run_id],
    )


def record_source_import_failure(
    con: duckdb.DuckDBPyConnection,
    *,
    sync_source_type: str,
    error: BaseException,
) -> None:
    """DB 밖에서 발생한 수집 실패를 짧은 연결로 sync_runs에 남깁니다."""
    run_id = _start_sync_run(con, sync_source_type=sync_source_type)
    _finish_sync_run_failure(con, run_id=run_id, error=error)


def import_preloaded_source_signals(
    con: duckdb.DuckDBPyConnection,
    signals: list[dict[str, Any]],
    *,
    sync_source_type: str = "external",
    create_topics: bool = True,
    collection_run_id: str | None = None,
) -> dict[str, Any]:
    """이미 메모리에 수집된 신호를 짧은 DB 구간에서 저장합니다."""
    run_id = _start_sync_run(con, sync_source_type=sync_source_type)
    clean_signals = list(signals or [])
    try:
        added, updated, skipped = _save_signal_batch(
            con,
            clean_signals,
            sync_source_type=sync_source_type,
            create_topics=create_topics,
            collection_run_id=collection_run_id,
        )
        _finish_sync_run_success(
            con,
            run_id=run_id,
            items_read=len(clean_signals),
            added=added,
            updated=updated,
        )
        return {
            "status": "success",
            "items_read": len(clean_signals),
            "items_added": added,
            "items_updated": updated,
            "items_skipped": skipped,
        }
    except Exception as exc:
        _finish_sync_run_failure(con, run_id=run_id, error=exc)
        raise


def import_source_signals(
    con: duckdb.DuckDBPyConnection,
    adapter: SignalAdapter,
    *,
    limit: int = 100,
    sync_source_type: str = "external",
    create_topics: bool = True,
    collection_run_id: str | None = None,
) -> dict[str, Any]:
    """기존 단일 연결 호출의 실행 이력과 실패 기록을 그대로 유지합니다."""
    run_id = _start_sync_run(con, sync_source_type=sync_source_type)
    try:
        signals = list(adapter.load_signals(limit=limit) or [])
        added, updated, skipped = _save_signal_batch(
            con,
            signals,
            sync_source_type=sync_source_type,
            create_topics=create_topics,
            collection_run_id=collection_run_id,
        )
        _finish_sync_run_success(
            con,
            run_id=run_id,
            items_read=len(signals),
            added=added,
            updated=updated,
        )
        return {
            "status": "success",
            "items_read": len(signals),
            "items_added": added,
            "items_updated": updated,
            "items_skipped": skipped,
        }
    except Exception as exc:
        _finish_sync_run_failure(con, run_id=run_id, error=exc)
        raise


def import_youtube_signals(
    con: duckdb.DuckDBPyConnection,
    adapter: SignalAdapter,
    *,
    limit: int = 100,
    sync_source_type: str = "youtube",
) -> dict[str, Any]:
    """기존 호출 호환성을 유지하는 YouTube 신호 가져오기 래퍼입니다."""
    return import_source_signals(
        con,
        adapter,
        limit=limit,
        sync_source_type=sync_source_type,
        create_topics=True,
    )


def get_last_successful_import(
    con: duckdb.DuckDBPyConnection,
    source_type: str,
) -> datetime | None:
    row = con.execute(
        """
        SELECT MAX(finished_at)
        FROM sync_runs
        WHERE source_type = ? AND status = 'success'
        """,
        [source_type],
    ).fetchone()
    return row[0] if row else None


def upsert_source_signal(
    con: duckdb.DuckDBPyConnection,
    signal: dict[str, Any],
    *,
    create_topic: bool = True,
) -> str:
    source_type = str(signal.get("source_type") or "unknown").strip()
    external_id = str(signal.get("external_id") or "").strip()
    title = str(signal.get("title") or "").strip()
    normalized = normalize_title(title)
    if not external_id or not normalized:
        raise ValueError("외부 신호에는 external_id와 title이 필요합니다.")

    source_url = str(signal.get("source_url") or "").strip()
    normalized_url = normalize_url(source_url)
    existing_by_url = None
    if normalized_url:
        existing_by_url = con.execute(
            """
            SELECT source_item_id, external_id
            FROM source_items
            WHERE source_type = ? AND normalized_url = ?
            ORDER BY imported_at DESC
            LIMIT 1
            """,
            [source_type, normalized_url],
        ).fetchone()
    if existing_by_url:
        source_item_id = str(existing_by_url[0])
        stored_external_id = str(existing_by_url[1])
    else:
        source_item_id = _source_id(source_type, external_id)
        stored_external_id = external_id
    now = datetime.now()
    exists = con.execute(
        "SELECT 1 FROM source_items WHERE source_item_id = ?",
        [source_item_id],
    ).fetchone()
    metadata = json.dumps(signal.get("metadata") or {}, ensure_ascii=False, default=str)
    con.execute(
        """
        INSERT INTO source_items(
            source_item_id, source_type, external_id, raw_title, normalized_title,
            source_url, normalized_url, source_name, published_at, observed_at, signal_value,
            metadata_json, first_imported_at, previous_imported_at, last_imported_at,
            observation_count, imported_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(source_item_id) DO UPDATE SET
            raw_title = EXCLUDED.raw_title,
            normalized_title = EXCLUDED.normalized_title,
            source_url = EXCLUDED.source_url,
            normalized_url = EXCLUDED.normalized_url,
            source_name = EXCLUDED.source_name,
            published_at = EXCLUDED.published_at,
            observed_at = EXCLUDED.observed_at,
            signal_value = EXCLUDED.signal_value,
            metadata_json = EXCLUDED.metadata_json,
            first_imported_at = COALESCE(
                source_items.first_imported_at,
                source_items.imported_at,
                EXCLUDED.first_imported_at
            ),
            previous_imported_at = COALESCE(
                source_items.last_imported_at,
                source_items.imported_at
            ),
            last_imported_at = EXCLUDED.last_imported_at,
            observation_count = GREATEST(
                COALESCE(source_items.observation_count, 1) + 1,
                2
            ),
            imported_at = EXCLUDED.imported_at
        """,
        [
            source_item_id,
            source_type,
            stored_external_id,
            title,
            normalized,
            source_url,
            normalized_url,
            signal.get("source_name"),
            signal.get("published_at"),
            signal.get("observed_at"),
            signal.get("signal_value"),
            metadata,
            now,
            None,
            now,
            1,
            now,
        ],
    )

    if not create_topic:
        return "updated" if exists else "added"

    topic_row = con.execute(
        """
        SELECT topic_id FROM topics
        WHERE normalized_title = ? AND archived_at IS NULL
        ORDER BY is_interested DESC, updated_at DESC
        LIMIT 1
        """,
        [normalized],
    ).fetchone()
    if topic_row:
        topic_id = str(topic_row[0])
    else:
        topic_id = f"topic_{uuid4().hex}"
        con.execute(
            """
            INSERT INTO topics(
                topic_id, title, normalized_title, summary, category, status,
                priority, is_interested, memo, source_count,
                first_seen_at, last_seen_at, created_at, updated_at
            ) VALUES (?, ?, ?, '', '', 'candidate', 2, FALSE, '', 0, ?, ?, ?, ?)
            """,
            [topic_id, title, normalized, now, now, now, now],
        )

    con.execute(
        """
        INSERT INTO topic_source_links(
            topic_id, source_item_id, match_type, match_score, linked_at
        ) VALUES (?, ?, 'normalized', 1.0, ?)
        ON CONFLICT(topic_id, source_item_id) DO NOTHING
        """,
        [topic_id, source_item_id, now],
    )
    con.execute(
        """
        UPDATE topics
        SET source_count = (
                SELECT COUNT(*) FROM topic_source_links WHERE topic_id = ?
            ),
            last_seen_at = ?,
            updated_at = ?
        WHERE topic_id = ?
        """,
        [topic_id, now, now, topic_id],
    )
    return "updated" if exists else "added"


def list_topics(
    con: duckdb.DuckDBPyConnection,
    *,
    interested_only: bool = False,
) -> pd.DataFrame:
    where = "WHERE t.archived_at IS NULL"
    if interested_only:
        where += " AND t.is_interested = TRUE"
    return con.execute(
        f"""
        SELECT
            t.topic_id,
            t.title AS 주제,
            t.category AS 카테고리,
            t.status,
            t.priority,
            t.is_interested,
            t.source_count AS 신호수,
            string_agg(DISTINCT s.source_type, ', ') AS 출처,
            MAX(s.signal_value) AS 최고신호,
            t.last_seen_at AS 최근확인,
            t.memo AS 메모
        FROM topics t
        LEFT JOIN topic_source_links l ON l.topic_id = t.topic_id
        LEFT JOIN source_items s ON s.source_item_id = l.source_item_id
        {where}
        GROUP BY ALL
        ORDER BY t.is_interested DESC, t.priority DESC, MAX(t.updated_at) DESC
        """
    ).fetchdf()


def get_topic(con: duckdb.DuckDBPyConnection, topic_id: str) -> dict[str, Any] | None:
    row = con.execute(
        "SELECT * FROM topics WHERE topic_id = ?",
        [topic_id],
    ).fetchone()
    if row is None:
        return None
    columns = [item[0] for item in con.description]
    return dict(zip(columns, row))


def get_topic_sources(
    con: duckdb.DuckDBPyConnection,
    topic_id: str,
) -> list[dict[str, Any]]:
    rows = con.execute(
        """
        SELECT s.source_item_id, s.source_type, s.raw_title, s.source_url,
               s.source_name, s.published_at, s.observed_at, s.signal_value,
               s.metadata_json
        FROM topic_source_links l
        JOIN source_items s ON s.source_item_id = l.source_item_id
        WHERE l.topic_id = ?
        ORDER BY s.signal_value DESC NULLS LAST, s.observed_at DESC NULLS LAST
        """,
        [topic_id],
    ).fetchall()
    columns = [item[0] for item in con.description]
    result = []
    for row in rows:
        item = dict(zip(columns, row))
        try:
            item["metadata"] = json.loads(item.pop("metadata_json") or "{}")
        except json.JSONDecodeError:
            item["metadata"] = {}

        metadata = item["metadata"]
        signal_type = infer_signal_type(item)
        item["signal_type"] = signal_type
        item["signal_type_label"] = SIGNAL_TYPE_LABELS[signal_type]
        item["item_title"] = str(metadata.get("item_title") or item.get("raw_title") or "").strip()
        item["keyword"] = str(metadata.get("keyword") or "").strip()
        item["view_count"] = _first_not_none(
            metadata.get("view_count"),
            metadata.get("current_view_count"),
        )
        item["view_delta"] = _first_not_none(
            metadata.get("view_delta"),
            metadata.get("view_growth"),
        )
        item["views_per_hour"] = _first_not_none(
            metadata.get("views_per_hour"),
            metadata.get("views_per_hour_growth"),
        )
        item["topic_score"] = _first_not_none(
            metadata.get("topic_score"),
            item.get("signal_value") if signal_type == "emerging_topic" else None,
        )
        result.append(item)
    return result


def update_topic(
    con: duckdb.DuckDBPyConnection,
    topic_id: str,
    *,
    title: str,
    summary: str,
    category: str,
    status: str,
    priority: int,
    is_interested: bool,
    memo: str,
) -> None:
    current = get_topic(con, topic_id)
    if current is None:
        raise ValueError("주제를 찾을 수 없습니다.")
    clean_title = str(title or "").strip()
    if not clean_title:
        raise ValueError("주제명은 비워둘 수 없습니다.")
    now = datetime.now()
    con.execute(
        """
        UPDATE topics
        SET title = ?, normalized_title = ?, summary = ?, category = ?,
            status = ?, priority = ?, is_interested = ?, memo = ?, updated_at = ?
        WHERE topic_id = ?
        """,
        [
            clean_title,
            normalize_title(clean_title),
            summary.strip(),
            category.strip(),
            status,
            max(1, min(int(priority), 3)),
            bool(is_interested),
            memo.strip(),
            now,
            topic_id,
        ],
    )
    if current.get("status") != status:
        con.execute(
            """
            INSERT INTO topic_status_history(
                history_id, topic_id, previous_status, new_status, note, changed_at
            ) VALUES (?, ?, ?, ?, '', ?)
            """,
            [f"hist_{uuid4().hex}", topic_id, current.get("status"), status, now],
        )


def archive_topic(con: duckdb.DuckDBPyConnection, topic_id: str) -> None:
    con.execute(
        "UPDATE topics SET archived_at = ?, updated_at = ? WHERE topic_id = ?",
        [datetime.now(), datetime.now(), topic_id],
    )
