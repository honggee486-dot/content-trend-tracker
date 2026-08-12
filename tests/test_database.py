from pathlib import Path

import duckdb
import pytest

from src import database
from src.database import connect_database, init_database


def test_connect_database_retries_windows_file_lock_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    expected_connection = object()
    attempts = 0
    delays: list[float] = []

    def fake_connect(path: str, *, read_only: bool):
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            raise duckdb.IOException(
                "IO Error: Cannot open file. File is already open in Python"
            )
        return expected_connection

    monkeypatch.setattr(database.duckdb, "connect", fake_connect)
    monkeypatch.setattr(database, "sleep", delays.append)

    result = connect_database(tmp_path / "retry.duckdb")

    assert result is expected_connection
    assert attempts == 3
    assert delays == [
        database.DB_CONNECT_RETRY_SECONDS,
        database.DB_CONNECT_RETRY_SECONDS,
    ]


def test_connect_database_does_not_retry_unrelated_io_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    attempts = 0

    def fake_connect(path: str, *, read_only: bool):
        nonlocal attempts
        attempts += 1
        raise duckdb.IOException("IO Error: database file is corrupt")

    monkeypatch.setattr(database.duckdb, "connect", fake_connect)

    with pytest.raises(duckdb.IOException, match="corrupt"):
        connect_database(tmp_path / "broken.duckdb")

    assert attempts == 1


def test_init_database_creates_core_tables(tmp_path: Path) -> None:
    db_path = tmp_path / "main.duckdb"
    init_database(db_path)
    with connect_database(db_path) as con:
        tables = {row[0] for row in con.execute("SHOW TABLES").fetchall()}
        trend_columns = {
            row[1]
            for row in con.execute("PRAGMA table_info('trend_clusters')").fetchall()
        }
        source_columns = {
            row[1]
            for row in con.execute("PRAGMA table_info('source_items')").fetchall()
        }
        publish_columns = {
            row[1]
            for row in con.execute("PRAGMA table_info('publish_records')").fetchall()
        }
        discovery_columns = {
            row[1]
            for row in con.execute("PRAGMA table_info('collection_query_discoveries')").fetchall()
        }
        profile_columns = {
            row[1]
            for row in con.execute("PRAGMA table_info('trend_cluster_ai_profiles')").fetchall()
        }
        preference_columns = {
            row[1]
            for row in con.execute("PRAGMA table_info('topic_content_preferences')").fetchall()
        }
        gemini_call_columns = {
            row[1]
            for row in con.execute("PRAGMA table_info('gemini_api_calls')").fetchall()
        }
    assert {
        "topics",
        "source_items",
        "topic_references",
        "content_packs",
        "generation_sessions",
        "gemini_response_cache",
        "gemini_api_calls",
        "trend_cluster_ai_profiles",
        "trend_cluster_ai_angles",
        "topic_content_preferences",
        "drafts",
        "publish_records",
        "blog_profiles",
        "trend_clusters",
        "trend_cluster_items",
        "trend_feedback",
        "api_usage_counters",
        "collection_query_discoveries",
    }.issubset(tables)

    assert {"quality_score", "recommendation_status", "quality_reasons_json"}.issubset(
        trend_columns
    )
    assert {
        "normalized_url",
        "first_imported_at",
        "previous_imported_at",
        "last_imported_at",
        "observation_count",
    }.issubset(source_columns)
    assert "rediscovery_score" in trend_columns
    assert "blog_profile_id" in publish_columns
    assert {
        "run_id",
        "source_type",
        "discovery_query",
        "source_item_id",
        "result_rank",
    }.issubset(discovery_columns)

    assert "content_plan_json" in profile_columns
    assert {
        "topic_id",
        "source_cluster_id",
        "audience",
        "purpose",
        "angle",
        "category",
        "target_length",
        "title_rules_json",
        "outline_json",
        "forbidden_expressions_json",
        "fact_check_items_json",
    }.issubset(preference_columns)
    assert {
        "requested_item_count",
        "configured_items_per_request",
        "thinking_level",
        "request_timeout_seconds",
        "finish_reason",
        "finish_message",
    }.issubset(gemini_call_columns)


def test_init_database_migrates_legacy_gemini_calls_idempotently(tmp_path: Path) -> None:
    db_path = tmp_path / "legacy_gemini_calls.duckdb"
    with connect_database(db_path) as con:
        con.execute(
            """
            CREATE TABLE gemini_api_calls (
                call_id VARCHAR PRIMARY KEY,
                app_id VARCHAR NOT NULL,
                quota_scope_id VARCHAR NOT NULL,
                feature_id VARCHAR NOT NULL,
                content_pack_id VARCHAR NOT NULL,
                request_hash VARCHAR NOT NULL,
                model_name VARCHAR NOT NULL,
                attempt_number INTEGER NOT NULL,
                cache_hit BOOLEAN NOT NULL,
                status VARCHAR NOT NULL,
                http_status INTEGER,
                error_type VARCHAR,
                retry_reason VARCHAR,
                retry_wait_seconds DOUBLE NOT NULL DEFAULT 0,
                input_tokens BIGINT,
                output_tokens BIGINT,
                thought_tokens BIGINT,
                total_tokens BIGINT,
                request_char_count BIGINT,
                request_non_whitespace_char_count BIGINT,
                request_hangul_char_count BIGINT,
                response_char_count BIGINT,
                response_non_whitespace_char_count BIGINT,
                response_hangul_char_count BIGINT,
                duration_ms BIGINT NOT NULL DEFAULT 0,
                error_message VARCHAR,
                created_at TIMESTAMP NOT NULL
            )
            """
        )
        con.execute(
            """
            INSERT INTO gemini_api_calls(
                call_id, app_id, quota_scope_id, feature_id, content_pack_id,
                request_hash, model_name, attempt_number, cache_hit, status,
                created_at
            ) VALUES ('legacy-1', 'app', 'quota', 'feature', 'pack',
                      'hash-1', 'model', 1, FALSE, 'success', CURRENT_TIMESTAMP)
            """
        )

    init_database(db_path)
    init_database(db_path)

    with connect_database(db_path) as con:
        con.execute(
            """
            INSERT INTO gemini_api_calls(
                call_id, app_id, quota_scope_id, feature_id, content_pack_id,
                request_hash, model_name, attempt_number, cache_hit, status,
                created_at
            ) VALUES ('legacy-2', 'app', 'quota', 'feature', 'pack',
                      'hash-2', 'model', 1, FALSE, 'success', CURRENT_TIMESTAMP)
            """
        )
        rows = con.execute(
            """
            SELECT call_id, requested_item_count, configured_items_per_request,
                   thinking_level, request_timeout_seconds,
                   finish_reason, finish_message
            FROM gemini_api_calls
            ORDER BY call_id
            """
        ).fetchall()

    assert rows == [
        ("legacy-1", None, None, None, None, "", ""),
        ("legacy-2", None, None, None, None, "", ""),
    ]


def test_init_database_migrates_existing_trend_cluster_columns(tmp_path: Path) -> None:
    db_path = tmp_path / "legacy.duckdb"
    with connect_database(db_path) as con:
        con.execute(
            """
            CREATE TABLE trend_clusters (
                cluster_id VARCHAR PRIMARY KEY,
                canonical_title VARCHAR NOT NULL,
                trend_score DOUBLE NOT NULL,
                opportunity_score DOUBLE NOT NULL,
                fact_risk_score DOUBLE NOT NULL,
                item_count INTEGER NOT NULL,
                source_type_count INTEGER NOT NULL,
                publisher_count INTEGER NOT NULL,
                source_types_json VARCHAR NOT NULL,
                score_reasons_json VARCHAR NOT NULL,
                first_seen_at TIMESTAMP,
                last_seen_at TIMESTAMP,
                calculated_at TIMESTAMP NOT NULL
            )
            """
        )

    init_database(db_path)

    with connect_database(db_path) as con:
        columns = {
            row[1]
            for row in con.execute("PRAGMA table_info('trend_clusters')").fetchall()
        }
    assert {
        "quality_score",
        "rediscovery_score",
        "recommendation_status",
        "quality_reasons_json",
    }.issubset(columns)


def test_init_database_migrates_old_naver_quota_defaults_to_free_maximum(tmp_path: Path) -> None:
    db_path = tmp_path / "legacy_quota.duckdb"
    with connect_database(db_path) as con:
        con.execute(
            """
            CREATE TABLE app_settings (
                setting_key VARCHAR PRIMARY KEY,
                setting_value VARCHAR NOT NULL,
                updated_at TIMESTAMP NOT NULL
            )
            """
        )
        con.execute(
            """
            INSERT INTO app_settings(setting_key, setting_value, updated_at)
            VALUES ('naver_search_monthly_safety_limit', '20000', CURRENT_TIMESTAMP)
            """
        )

    init_database(db_path)

    with connect_database(db_path) as con:
        values = dict(
            con.execute(
                """
                SELECT setting_key, setting_value
                FROM app_settings
                WHERE setting_key IN (
                    'naver_search_daily_safety_limit',
                    'naver_search_monthly_safety_limit',
                    'naver_search_quota_policy_version'
                )
                """
            ).fetchall()
        )
    assert values["naver_search_daily_safety_limit"] == "25000"
    assert values["naver_search_monthly_safety_limit"] == "775000"
    assert values["naver_search_quota_policy_version"] == "2"


def test_init_database_backfills_normalized_url_without_deleting_rows(tmp_path: Path) -> None:
    db_path = tmp_path / "legacy_source_items.duckdb"
    with connect_database(db_path) as con:
        con.execute(
            """
            CREATE TABLE source_items (
                source_item_id VARCHAR PRIMARY KEY,
                source_type VARCHAR NOT NULL,
                external_id VARCHAR NOT NULL,
                raw_title VARCHAR NOT NULL,
                normalized_title VARCHAR NOT NULL,
                source_url VARCHAR,
                source_name VARCHAR,
                published_at TIMESTAMP,
                observed_at TIMESTAMP,
                signal_value DOUBLE,
                metadata_json VARCHAR,
                imported_at TIMESTAMP NOT NULL,
                UNIQUE(source_type, external_id)
            )
            """
        )
        con.execute(
            """
            INSERT INTO source_items VALUES (
                'src_legacy', 'naver_news', 'legacy', '테스트 기사', '테스트 기사',
                'https://example.com/item?utm_source=test&id=1', '테스트뉴스',
                NULL, NULL, NULL, '{}', CURRENT_TIMESTAMP
            )
            """
        )

    init_database(db_path)

    with connect_database(db_path) as con:
        row = con.execute(
            "SELECT source_item_id, normalized_url FROM source_items"
        ).fetchone()
        discovery_count = con.execute(
            "SELECT COUNT(*) FROM collection_query_discoveries"
        ).fetchone()[0]
    assert row == ("src_legacy", "https://example.com/item?id=1")
    assert discovery_count == 0


def test_init_database_backfills_source_observation_columns(tmp_path: Path) -> None:
    db_path = tmp_path / "legacy_observations.duckdb"
    with connect_database(db_path) as con:
        con.execute(
            """
            CREATE TABLE source_items (
                source_item_id VARCHAR PRIMARY KEY,
                source_type VARCHAR NOT NULL,
                external_id VARCHAR NOT NULL,
                raw_title VARCHAR NOT NULL,
                normalized_title VARCHAR NOT NULL,
                source_url VARCHAR,
                normalized_url VARCHAR,
                source_name VARCHAR,
                published_at TIMESTAMP,
                observed_at TIMESTAMP,
                signal_value DOUBLE,
                metadata_json VARCHAR,
                imported_at TIMESTAMP NOT NULL,
                UNIQUE(source_type, external_id)
            )
            """
        )
        con.execute(
            """
            INSERT INTO source_items VALUES (
                'src_old', 'naver_news', 'old', '기존 기사', '기존 기사',
                'https://example.com/old', 'https://example.com/old', '뉴스',
                NULL, NULL, NULL, '{}', TIMESTAMP '2026-07-01 12:00:00'
            )
            """
        )

    init_database(db_path)

    with connect_database(db_path) as con:
        row = con.execute(
            """
            SELECT first_imported_at, previous_imported_at, last_imported_at,
                   observation_count, imported_at
            FROM source_items
            """
        ).fetchone()

    assert row[0] == row[4]
    assert row[1] is None
    assert row[2] == row[4]
    assert row[3] == 1


def test_init_database_adds_and_backfills_legacy_topic_source_count(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "legacy_topics_without_source_count.duckdb"
    with connect_database(db_path) as con:
        con.execute(
            """
            CREATE TABLE topics (
                topic_id VARCHAR PRIMARY KEY,
                title VARCHAR NOT NULL,
                normalized_title VARCHAR NOT NULL,
                summary VARCHAR,
                category VARCHAR,
                status VARCHAR NOT NULL,
                priority INTEGER NOT NULL,
                is_interested BOOLEAN NOT NULL,
                memo VARCHAR,
                first_seen_at TIMESTAMP NOT NULL,
                last_seen_at TIMESTAMP NOT NULL,
                created_at TIMESTAMP NOT NULL,
                updated_at TIMESTAMP NOT NULL,
                archived_at TIMESTAMP
            )
            """
        )
        con.execute(
            """
            CREATE TABLE source_items (
                source_item_id VARCHAR PRIMARY KEY,
                source_type VARCHAR NOT NULL,
                external_id VARCHAR NOT NULL,
                raw_title VARCHAR NOT NULL,
                normalized_title VARCHAR NOT NULL,
                source_url VARCHAR,
                source_name VARCHAR,
                published_at TIMESTAMP,
                observed_at TIMESTAMP,
                signal_value DOUBLE,
                metadata_json VARCHAR,
                imported_at TIMESTAMP NOT NULL,
                UNIQUE(source_type, external_id)
            )
            """
        )
        con.execute(
            """
            CREATE TABLE topic_source_links (
                topic_id VARCHAR NOT NULL,
                source_item_id VARCHAR NOT NULL,
                match_type VARCHAR NOT NULL,
                match_score DOUBLE,
                linked_at TIMESTAMP NOT NULL,
                PRIMARY KEY(topic_id, source_item_id)
            )
            """
        )
        con.execute(
            """
            INSERT INTO topics(
                topic_id, title, normalized_title, summary, category, status,
                priority, is_interested, memo, first_seen_at, last_seen_at,
                created_at, updated_at, archived_at
            ) VALUES (
                'topic_legacy', '레거시 글감', '레거시 글감', '', '', 'candidate',
                2, FALSE, '', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP,
                CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, NULL
            )
            """
        )
        con.execute(
            """
            INSERT INTO source_items(
                source_item_id, source_type, external_id, raw_title,
                normalized_title, source_url, source_name, published_at,
                observed_at, signal_value, metadata_json, imported_at
            ) VALUES (
                'source_legacy', 'naver_news', 'legacy-1', '레거시 기사',
                '레거시 기사', 'https://example.com/legacy', '예시',
                CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, 1.0, '{}', CURRENT_TIMESTAMP
            )
            """
        )
        con.execute(
            """
            INSERT INTO topic_source_links(
                topic_id, source_item_id, match_type, match_score, linked_at
            ) VALUES (
                'topic_legacy', 'source_legacy', 'normalized', 1.0, CURRENT_TIMESTAMP
            )
            """
        )

    init_database(db_path)

    with connect_database(db_path) as con:
        columns = {
            row[1]
            for row in con.execute("PRAGMA table_info('topics')").fetchall()
        }
        source_count = con.execute(
            "SELECT source_count FROM topics WHERE topic_id = 'topic_legacy'"
        ).fetchone()[0]

    assert "source_count" in columns
    assert source_count == 1
