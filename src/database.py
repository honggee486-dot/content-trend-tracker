from __future__ import annotations

from datetime import datetime
from pathlib import Path
from time import sleep

import duckdb

from src.config import DEFAULT_DB_PATH, DEFAULT_SETTINGS, ensure_project_directories
from src.services.trend_normalization import normalize_url


DB_CONNECT_MAX_ATTEMPTS = 20
DB_CONNECT_RETRY_SECONDS = 0.25


def connect_database(
    db_path: str | Path = DEFAULT_DB_PATH,
    *,
    read_only: bool = False,
) -> duckdb.DuckDBPyConnection:
    ensure_project_directories()
    path = Path(db_path)
    if not read_only:
        path.parent.mkdir(parents=True, exist_ok=True)
    for attempt_number in range(1, DB_CONNECT_MAX_ATTEMPTS + 1):
        try:
            return duckdb.connect(str(path), read_only=read_only)
        except duckdb.IOException as exc:
            if (
                attempt_number >= DB_CONNECT_MAX_ATTEMPTS
                or not _is_database_lock_error(exc)
            ):
                raise
            # Windows에서 다른 프로세스가 연결을 닫는 짧은 구간만 기다립니다.
            sleep(DB_CONNECT_RETRY_SECONDS)
    raise RuntimeError("DuckDB connection retry loop ended unexpectedly")


def is_database_lock_error(exc: BaseException) -> bool:
    """다른 프로세스의 DuckDB 파일 점유 오류인지 확인합니다."""
    message = str(exc).casefold()
    return any(
        marker in message
        for marker in (
            "file is already open",
            "could not set lock",
            "conflicting lock",
            "used by another process",
            "다른 프로세스가 파일을 사용 중",
        )
    )


def _is_database_lock_error(exc: BaseException) -> bool:
    """기존 내부 호출 호환 래퍼입니다."""
    return is_database_lock_error(exc)


def init_database(db_path: str | Path = DEFAULT_DB_PATH) -> None:
    with connect_database(db_path) as con:
        con.execute(
            """
            CREATE TABLE IF NOT EXISTS app_settings (
                setting_key VARCHAR PRIMARY KEY,
                setting_value VARCHAR,
                updated_at TIMESTAMP NOT NULL
            )
            """
        )
        con.execute(
            """
            CREATE TABLE IF NOT EXISTS topics (
                topic_id VARCHAR PRIMARY KEY,
                title VARCHAR NOT NULL,
                normalized_title VARCHAR NOT NULL,
                summary VARCHAR,
                category VARCHAR,
                status VARCHAR NOT NULL,
                priority INTEGER NOT NULL,
                is_interested BOOLEAN NOT NULL,
                memo VARCHAR,
                source_count INTEGER NOT NULL DEFAULT 0,
                first_seen_at TIMESTAMP NOT NULL,
                last_seen_at TIMESTAMP NOT NULL,
                created_at TIMESTAMP NOT NULL,
                updated_at TIMESTAMP NOT NULL,
                archived_at TIMESTAMP
            )
            """
        )
        topic_columns = {
            str(row[1])
            for row in con.execute("PRAGMA table_info('topics')").fetchall()
        }
        if "source_count" not in topic_columns:
            con.execute(
                "ALTER TABLE topics ADD COLUMN source_count INTEGER DEFAULT 0"
            )

        con.execute(
            "CREATE INDEX IF NOT EXISTS idx_topics_normalized ON topics(normalized_title)"
        )
        con.execute(
            "CREATE INDEX IF NOT EXISTS idx_topics_status ON topics(status)"
        )
        con.execute(
            """
            CREATE TABLE IF NOT EXISTS source_items (
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
                first_imported_at TIMESTAMP,
                previous_imported_at TIMESTAMP,
                last_imported_at TIMESTAMP,
                observation_count INTEGER NOT NULL DEFAULT 1,
                imported_at TIMESTAMP NOT NULL,
                UNIQUE(source_type, external_id)
            )
            """
        )
        source_item_columns = {
            str(row[1])
            for row in con.execute("PRAGMA table_info('source_items')").fetchall()
        }
        if "normalized_url" not in source_item_columns:
            con.execute("ALTER TABLE source_items ADD COLUMN normalized_url VARCHAR")
        for column_name, column_sql in (
            ("first_imported_at", "TIMESTAMP"),
            ("previous_imported_at", "TIMESTAMP"),
            ("last_imported_at", "TIMESTAMP"),
            ("observation_count", "INTEGER DEFAULT 1"),
        ):
            if column_name not in source_item_columns:
                con.execute(
                    f"ALTER TABLE source_items ADD COLUMN {column_name} {column_sql}"
                )

        # 기존 DB는 원본 행을 보존한 채 현재 imported_at을 최초·최근 포착 시각으로
        # 사용합니다. 이후 수집부터 이전 포착 시각과 누적 포착 횟수가 갱신됩니다.
        con.execute(
            """
            UPDATE source_items
            SET first_imported_at = COALESCE(first_imported_at, imported_at),
                last_imported_at = COALESCE(last_imported_at, imported_at),
                observation_count = GREATEST(COALESCE(observation_count, 1), 1)
            WHERE first_imported_at IS NULL
               OR last_imported_at IS NULL
               OR observation_count IS NULL
               OR observation_count < 1
            """
        )

        # 기존 원문은 삭제하지 않고 정규 URL만 채워 이후 중복 판정에 사용합니다.
        missing_urls = con.execute(
            """
            SELECT source_item_id, source_url
            FROM source_items
            WHERE COALESCE(TRIM(source_url), '') <> ''
              AND COALESCE(TRIM(normalized_url), '') = ''
            """
        ).fetchall()
        if missing_urls:
            con.executemany(
                "UPDATE source_items SET normalized_url = ? WHERE source_item_id = ?",
                [
                    [normalize_url(str(source_url or "")), str(source_item_id)]
                    for source_item_id, source_url in missing_urls
                ],
            )
        con.execute(
            "CREATE INDEX IF NOT EXISTS idx_source_items_normalized_url "
            "ON source_items(normalized_url)"
        )
        con.execute(
            "CREATE INDEX IF NOT EXISTS idx_source_items_source_type "
            "ON source_items(source_type)"
        )
        con.execute(
            "CREATE INDEX IF NOT EXISTS idx_source_items_imported_at "
            "ON source_items(imported_at)"
        )
        con.execute(
            """
            CREATE TABLE IF NOT EXISTS topic_source_links (
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
            UPDATE topics t
            SET source_count = COALESCE((
                SELECT COUNT(*)
                FROM topic_source_links l
                WHERE l.topic_id = t.topic_id
            ), 0)
            """
        )
        con.execute(
            """
            CREATE TABLE IF NOT EXISTS topic_status_history (
                history_id VARCHAR PRIMARY KEY,
                topic_id VARCHAR NOT NULL,
                previous_status VARCHAR,
                new_status VARCHAR NOT NULL,
                note VARCHAR,
                changed_at TIMESTAMP NOT NULL
            )
            """
        )
        con.execute(
            """
            CREATE TABLE IF NOT EXISTS topic_references (
                reference_id VARCHAR PRIMARY KEY,
                topic_id VARCHAR NOT NULL,
                reference_type VARCHAR NOT NULL,
                title VARCHAR NOT NULL,
                publisher VARCHAR,
                url VARCHAR NOT NULL,
                normalized_url VARCHAR NOT NULL,
                published_at VARCHAR,
                memo VARCHAR,
                created_at TIMESTAMP NOT NULL,
                updated_at TIMESTAMP NOT NULL,
                archived_at TIMESTAMP
            )
            """
        )
        con.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_topic_references_topic
            ON topic_references(topic_id, archived_at)
            """
        )
        con.execute(
            """
            CREATE TABLE IF NOT EXISTS content_packs (
                content_pack_id VARCHAR PRIMARY KEY,
                topic_id VARCHAR NOT NULL,
                version INTEGER NOT NULL,
                audience VARCHAR NOT NULL,
                purpose VARCHAR NOT NULL,
                angle VARCHAR NOT NULL,
                category VARCHAR,
                target_length INTEGER,
                title_rules_json VARCHAR NOT NULL,
                outline_json VARCHAR NOT NULL,
                forbidden_expressions_json VARCHAR NOT NULL,
                fact_check_items_json VARCHAR NOT NULL,
                references_json VARCHAR NOT NULL,
                pack_markdown VARCHAR NOT NULL,
                prompt_text VARCHAR NOT NULL,
                created_at TIMESTAMP NOT NULL
            )
            """
        )
        con.execute(
            """
            CREATE TABLE IF NOT EXISTS generation_sessions (
                generation_id VARCHAR PRIMARY KEY,
                topic_id VARCHAR NOT NULL,
                content_pack_id VARCHAR NOT NULL,
                ai_provider VARCHAR NOT NULL,
                prompt_text VARCHAR NOT NULL,
                raw_response VARCHAR NOT NULL,
                parsed_json VARCHAR,
                parse_status VARCHAR NOT NULL,
                validation_errors_json VARCHAR NOT NULL,
                validation_warnings_json VARCHAR NOT NULL,
                schema_version VARCHAR,
                created_at TIMESTAMP NOT NULL
            )
            """
        )
        con.execute(
            """
            CREATE TABLE IF NOT EXISTS gemini_response_cache (
                request_hash VARCHAR PRIMARY KEY,
                app_id VARCHAR NOT NULL,
                quota_scope_id VARCHAR NOT NULL,
                feature_id VARCHAR NOT NULL,
                feature_version VARCHAR NOT NULL,
                model_name VARCHAR NOT NULL,
                schema_version VARCHAR NOT NULL,
                content_pack_id VARCHAR NOT NULL,
                raw_response VARCHAR NOT NULL,
                created_at TIMESTAMP NOT NULL
            )
            """
        )
        con.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_gemini_response_cache_created_at
            ON gemini_response_cache(created_at)
            """
        )
        con.execute(
            """
            CREATE TABLE IF NOT EXISTS gemini_api_calls (
                call_id VARCHAR PRIMARY KEY,
                app_id VARCHAR NOT NULL,
                quota_scope_id VARCHAR NOT NULL,
                feature_id VARCHAR NOT NULL,
                feature_version VARCHAR NOT NULL DEFAULT '',
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
                requested_item_count INTEGER,
                configured_items_per_request INTEGER,
                thinking_level VARCHAR,
                request_timeout_seconds INTEGER,
                finish_reason VARCHAR NOT NULL DEFAULT '',
                finish_message VARCHAR NOT NULL DEFAULT '',
                duration_ms BIGINT NOT NULL DEFAULT 0,
                error_message VARCHAR,
                created_at TIMESTAMP NOT NULL
            )
            """
        )
        gemini_call_columns = {
            str(row[1])
            for row in con.execute("PRAGMA table_info('gemini_api_calls')").fetchall()
        }
        for column_name, column_sql in (
            ("thought_tokens", "BIGINT"),
            ("request_char_count", "BIGINT"),
            ("request_non_whitespace_char_count", "BIGINT"),
            ("request_hangul_char_count", "BIGINT"),
            ("response_char_count", "BIGINT"),
            ("response_non_whitespace_char_count", "BIGINT"),
            ("response_hangul_char_count", "BIGINT"),
            ("feature_version", "VARCHAR DEFAULT ''"),
            ("requested_item_count", "INTEGER"),
            ("configured_items_per_request", "INTEGER"),
            ("thinking_level", "VARCHAR"),
            ("request_timeout_seconds", "INTEGER"),
            ("finish_reason", "VARCHAR DEFAULT ''"),
            ("finish_message", "VARCHAR DEFAULT ''"),
        ):
            if column_name not in gemini_call_columns:
                con.execute(
                    f"ALTER TABLE gemini_api_calls ADD COLUMN {column_name} {column_sql}"
                )

        con.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_gemini_api_calls_app_created
            ON gemini_api_calls(app_id, created_at)
            """
        )
        con.execute(
            """
            CREATE TABLE IF NOT EXISTS trend_cluster_ai_profiles (
                cluster_id VARCHAR PRIMARY KEY,
                canonical_title VARCHAR NOT NULL,
                display_title VARCHAR NOT NULL,
                summary VARCHAR NOT NULL,
                verification_points_json VARCHAR NOT NULL,
                content_plan_json VARCHAR NOT NULL DEFAULT '{}',
                model_name VARCHAR NOT NULL,
                feature_version VARCHAR NOT NULL,
                created_at TIMESTAMP NOT NULL,
                updated_at TIMESTAMP NOT NULL
            )
            """
        )
        profile_columns = {
            str(row[1])
            for row in con.execute("PRAGMA table_info('trend_cluster_ai_profiles')").fetchall()
        }
        if "content_plan_json" not in profile_columns:
            con.execute(
                "ALTER TABLE trend_cluster_ai_profiles "
                "ADD COLUMN content_plan_json VARCHAR DEFAULT '{}'"
            )
        con.execute(
            "UPDATE trend_cluster_ai_profiles SET content_plan_json = '{}' "
            "WHERE content_plan_json IS NULL OR TRIM(content_plan_json) = ''"
        )
        con.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_trend_cluster_ai_profiles_updated
            ON trend_cluster_ai_profiles(updated_at)
            """
        )
        con.execute(
            """
            CREATE TABLE IF NOT EXISTS trend_cluster_ai_angles (
                angle_id VARCHAR PRIMARY KEY,
                cluster_id VARCHAR NOT NULL,
                canonical_title VARCHAR NOT NULL,
                angle_order INTEGER NOT NULL,
                angle_label VARCHAR NOT NULL,
                angle_text VARCHAR NOT NULL,
                rationale VARCHAR NOT NULL,
                search_queries_json VARCHAR NOT NULL,
                search_intent VARCHAR,
                reader_question VARCHAR,
                demand_evidence_json VARCHAR NOT NULL DEFAULT '[]',
                evidence_source_ids_json VARCHAR NOT NULL DEFAULT '[]',
                score_breakdown_json VARCHAR NOT NULL DEFAULT '{}',
                direction_score DOUBLE,
                score_reasons_json VARCHAR NOT NULL DEFAULT '[]',
                model_name VARCHAR NOT NULL,
                feature_version VARCHAR NOT NULL,
                created_at TIMESTAMP NOT NULL,
                updated_at TIMESTAMP NOT NULL,
                UNIQUE(cluster_id, angle_order)
            )
            """
        )
        angle_columns = {
            str(row[1])
            for row in con.execute("PRAGMA table_info('trend_cluster_ai_angles')").fetchall()
        }
        angle_migrations = {
            "search_intent": "VARCHAR",
            "reader_question": "VARCHAR",
            "demand_evidence_json": "VARCHAR DEFAULT '[]'",
            "evidence_source_ids_json": "VARCHAR DEFAULT '[]'",
            "score_breakdown_json": "VARCHAR DEFAULT '{}'",
            "direction_score": "DOUBLE",
            "score_reasons_json": "VARCHAR DEFAULT '[]'",
        }
        for column_name, column_type in angle_migrations.items():
            if column_name not in angle_columns:
                con.execute(
                    f"ALTER TABLE trend_cluster_ai_angles ADD COLUMN {column_name} {column_type}"
                )
        con.execute(
            """
            UPDATE trend_cluster_ai_angles
            SET demand_evidence_json = COALESCE(NULLIF(TRIM(demand_evidence_json), ''), '[]'),
                evidence_source_ids_json = COALESCE(NULLIF(TRIM(evidence_source_ids_json), ''), '[]'),
                score_breakdown_json = COALESCE(NULLIF(TRIM(score_breakdown_json), ''), '{}'),
                score_reasons_json = COALESCE(NULLIF(TRIM(score_reasons_json), ''), '[]')
            """
        )
        con.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_trend_cluster_ai_angles_cluster
            ON trend_cluster_ai_angles(cluster_id, angle_order)
            """
        )
        con.execute(
            """
            CREATE TABLE IF NOT EXISTS topic_content_preferences (
                topic_id VARCHAR PRIMARY KEY,
                source_cluster_id VARCHAR,
                audience VARCHAR,
                purpose VARCHAR,
                angle VARCHAR,
                category VARCHAR,
                target_length INTEGER,
                title_rules_json VARCHAR NOT NULL DEFAULT '[]',
                outline_json VARCHAR NOT NULL DEFAULT '[]',
                forbidden_expressions_json VARCHAR NOT NULL DEFAULT '[]',
                fact_check_items_json VARCHAR NOT NULL DEFAULT '[]',
                created_at TIMESTAMP NOT NULL,
                updated_at TIMESTAMP NOT NULL
            )
            """
        )
        con.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_topic_content_preferences_cluster
            ON topic_content_preferences(source_cluster_id)
            """
        )
        con.execute(
            """
            CREATE TABLE IF NOT EXISTS drafts (
                draft_id VARCHAR PRIMARY KEY,
                topic_id VARCHAR NOT NULL,
                generation_id VARCHAR,
                title VARCHAR NOT NULL,
                summary VARCHAR,
                category VARCHAR,
                tags_json VARCHAR NOT NULL,
                body_markdown VARCHAR NOT NULL,
                body_html VARCHAR,
                sources_json VARCHAR NOT NULL,
                image_prompts_json VARCHAR NOT NULL,
                current_revision INTEGER NOT NULL,
                created_at TIMESTAMP NOT NULL,
                updated_at TIMESTAMP NOT NULL
            )
            """
        )
        con.execute(
            """
            CREATE TABLE IF NOT EXISTS draft_revisions (
                revision_id VARCHAR PRIMARY KEY,
                draft_id VARCHAR NOT NULL,
                revision_number INTEGER NOT NULL,
                title VARCHAR NOT NULL,
                summary VARCHAR,
                category VARCHAR,
                tags_json VARCHAR NOT NULL,
                body_markdown VARCHAR NOT NULL,
                change_note VARCHAR,
                created_at TIMESTAMP NOT NULL,
                UNIQUE(draft_id, revision_number)
            )
            """
        )
        con.execute(
            """
            CREATE TABLE IF NOT EXISTS fact_check_items (
                fact_check_id VARCHAR PRIMARY KEY,
                draft_id VARCHAR NOT NULL,
                claim_text VARCHAR NOT NULL,
                check_status VARCHAR NOT NULL,
                reason VARCHAR,
                evidence VARCHAR,
                source_ids_json VARCHAR NOT NULL,
                source_url VARCHAR,
                checked_at TIMESTAMP
            )
            """
        )
        con.execute(
            """
            CREATE TABLE IF NOT EXISTS publish_records (
                publish_id VARCHAR PRIMARY KEY,
                draft_id VARCHAR NOT NULL,
                platform VARCHAR NOT NULL,
                publish_status VARCHAR NOT NULL,
                write_url VARCHAR,
                published_url VARCHAR,
                memo VARCHAR,
                created_at TIMESTAMP NOT NULL,
                published_at TIMESTAMP
            )
            """
        )
        publish_record_columns = {
            str(row[1])
            for row in con.execute("PRAGMA table_info('publish_records')").fetchall()
        }
        if "blog_profile_id" not in publish_record_columns:
            con.execute("ALTER TABLE publish_records ADD COLUMN blog_profile_id VARCHAR")
        con.execute(
            """
            CREATE TABLE IF NOT EXISTS blog_profiles (
                blog_profile_id VARCHAR PRIMARY KEY,
                profile_name VARCHAR NOT NULL,
                platform VARCHAR NOT NULL,
                login_url VARCHAR,
                write_url VARCHAR NOT NULL,
                output_format VARCHAR NOT NULL DEFAULT 'plain_text',
                default_category VARCHAR,
                default_tags_json VARCHAR NOT NULL DEFAULT '[]',
                is_default BOOLEAN NOT NULL DEFAULT FALSE,
                is_active BOOLEAN NOT NULL DEFAULT TRUE,
                created_at TIMESTAMP NOT NULL,
                updated_at TIMESTAMP NOT NULL
            )
            """
        )
        con.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_blog_profiles_active
            ON blog_profiles(is_active, is_default)
            """
        )
        con.execute(
            """
            CREATE TABLE IF NOT EXISTS trend_clusters (
                cluster_id VARCHAR PRIMARY KEY,
                canonical_title VARCHAR NOT NULL,
                trend_score DOUBLE NOT NULL,
                opportunity_score DOUBLE NOT NULL,
                fact_risk_score DOUBLE NOT NULL,
                quality_score DOUBLE NOT NULL DEFAULT 50,
                rediscovery_score DOUBLE NOT NULL DEFAULT 0,
                recommendation_status VARCHAR NOT NULL DEFAULT 'review',
                item_count INTEGER NOT NULL,
                source_type_count INTEGER NOT NULL,
                publisher_count INTEGER NOT NULL,
                source_types_json VARCHAR NOT NULL,
                score_reasons_json VARCHAR NOT NULL,
                quality_reasons_json VARCHAR NOT NULL DEFAULT '[]',
                first_seen_at TIMESTAMP,
                last_seen_at TIMESTAMP,
                calculated_at TIMESTAMP NOT NULL
            )
            """
        )
        trend_cluster_columns = {
            str(row[1])
            for row in con.execute("PRAGMA table_info('trend_clusters')").fetchall()
        }
        for column_name, column_sql in (
            ("quality_score", "DOUBLE DEFAULT 50"),
            ("rediscovery_score", "DOUBLE DEFAULT 0"),
            ("recommendation_status", "VARCHAR DEFAULT 'review'"),
            ("quality_reasons_json", "VARCHAR DEFAULT '[]'"),
        ):
            if column_name not in trend_cluster_columns:
                con.execute(
                    f"ALTER TABLE trend_clusters ADD COLUMN {column_name} {column_sql}"
                )

        con.execute(
            """
            CREATE TABLE IF NOT EXISTS trend_cluster_items (
                cluster_id VARCHAR NOT NULL,
                source_item_id VARCHAR NOT NULL,
                linked_at TIMESTAMP NOT NULL,
                PRIMARY KEY(cluster_id, source_item_id)
            )
            """
        )
        con.execute(
            """
            CREATE TABLE IF NOT EXISTS trend_cluster_processing (
                source_item_id VARCHAR PRIMARY KEY,
                input_hash VARCHAR NOT NULL,
                feature_id VARCHAR NOT NULL,
                feature_version VARCHAR NOT NULL,
                model_name VARCHAR NOT NULL,
                first_stage_key VARCHAR NOT NULL DEFAULT '',
                cluster_id VARCHAR NOT NULL,
                status VARCHAR NOT NULL DEFAULT 'processed',
                attempt_count INTEGER NOT NULL DEFAULT 0,
                last_error VARCHAR NOT NULL DEFAULT '',
                processed_at TIMESTAMP,
                updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        processing_columns = {
            str(row[1])
            for row in con.execute(
                "PRAGMA table_info('trend_cluster_processing')"
            ).fetchall()
        }
        for column_name, column_sql in (
            ("first_stage_key", "VARCHAR DEFAULT ''"),
            ("status", "VARCHAR DEFAULT 'processed'"),
            ("attempt_count", "INTEGER DEFAULT 0"),
            ("last_error", "VARCHAR DEFAULT ''"),
            ("updated_at", "TIMESTAMP DEFAULT CURRENT_TIMESTAMP"),
        ):
            if column_name not in processing_columns:
                con.execute(
                    f"ALTER TABLE trend_cluster_processing ADD COLUMN {column_name} {column_sql}"
                )
        con.execute(
            """
            UPDATE trend_cluster_processing
            SET status = COALESCE(NULLIF(status, ''), 'processed'),
                attempt_count = COALESCE(attempt_count, 0),
                first_stage_key = COALESCE(first_stage_key, ''),
                last_error = COALESCE(last_error, ''),
                updated_at = COALESCE(updated_at, processed_at, CURRENT_TIMESTAMP)
            WHERE status IS NULL OR status = ''
               OR attempt_count IS NULL
               OR first_stage_key IS NULL
               OR last_error IS NULL
               OR updated_at IS NULL
            """
        )
        con.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_trend_cluster_processing_engine
            ON trend_cluster_processing(
                feature_id, feature_version, model_name, status, updated_at
            )
            """
        )
        con.execute(
            """
            CREATE TABLE IF NOT EXISTS trend_clustering_jobs (
                job_id VARCHAR PRIMARY KEY,
                status VARCHAR NOT NULL,
                launcher VARCHAR NOT NULL,
                model_name VARCHAR NOT NULL,
                scan_limit INTEGER NOT NULL,
                batch_size INTEGER NOT NULL,
                max_batches INTEGER NOT NULL,
                completed_batches INTEGER NOT NULL DEFAULT 0,
                processed_units INTEGER NOT NULL DEFAULT 0,
                processed_source_items INTEGER NOT NULL DEFAULT 0,
                remaining_items INTEGER NOT NULL DEFAULT 0,
                existing_links INTEGER NOT NULL DEFAULT 0,
                new_clusters INTEGER NOT NULL DEFAULT 0,
                uncertain_units INTEGER NOT NULL DEFAULT 0,
                conflict_units INTEGER NOT NULL DEFAULT 0,
                needs_review_items INTEGER NOT NULL DEFAULT 0,
                input_tokens BIGINT NOT NULL DEFAULT 0,
                output_tokens BIGINT NOT NULL DEFAULT 0,
                thought_tokens BIGINT NOT NULL DEFAULT 0,
                total_tokens BIGINT NOT NULL DEFAULT 0,
                error_message VARCHAR NOT NULL DEFAULT '',
                created_at TIMESTAMP NOT NULL,
                started_at TIMESTAMP,
                heartbeat_at TIMESTAMP,
                finished_at TIMESTAMP
            )
            """
        )
        con.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_trend_clustering_jobs_created
            ON trend_clustering_jobs(created_at DESC, status)
            """
        )
        con.execute(
            """
            CREATE TABLE IF NOT EXISTS trend_clustering_job_batches (
                job_id VARCHAR NOT NULL,
                batch_number INTEGER NOT NULL,
                status VARCHAR NOT NULL,
                scanned_pending_items INTEGER NOT NULL DEFAULT 0,
                first_stage_units INTEGER NOT NULL DEFAULT 0,
                all_first_stage_units INTEGER NOT NULL DEFAULT 0,
                source_items INTEGER NOT NULL DEFAULT 0,
                url_merged_items INTEGER NOT NULL DEFAULT 0,
                url_conflict_splits INTEGER NOT NULL DEFAULT 0,
                title_merged_groups INTEGER NOT NULL DEFAULT 0,
                existing_candidate_refs INTEGER NOT NULL DEFAULT 0,
                deferred_units INTEGER NOT NULL DEFAULT 0,
                processed_units INTEGER NOT NULL DEFAULT 0,
                processed_source_items INTEGER NOT NULL DEFAULT 0,
                existing_links INTEGER NOT NULL DEFAULT 0,
                new_clusters INTEGER NOT NULL DEFAULT 0,
                uncertain_units INTEGER NOT NULL DEFAULT 0,
                conflict_units INTEGER NOT NULL DEFAULT 0,
                needs_review_items INTEGER NOT NULL DEFAULT 0,
                input_tokens BIGINT NOT NULL DEFAULT 0,
                output_tokens BIGINT NOT NULL DEFAULT 0,
                thought_tokens BIGINT NOT NULL DEFAULT 0,
                total_tokens BIGINT NOT NULL DEFAULT 0,
                duration_ms INTEGER NOT NULL DEFAULT 0,
                error_message VARCHAR NOT NULL DEFAULT '',
                started_at TIMESTAMP NOT NULL,
                finished_at TIMESTAMP,
                PRIMARY KEY(job_id, batch_number)
            )
            """
        )

        job_batch_columns = {
            str(row[1])
            for row in con.execute(
                "PRAGMA table_info('trend_clustering_job_batches')"
            ).fetchall()
        }
        for column_name in (
            "scanned_pending_items",
            "all_first_stage_units",
            "url_merged_items",
            "url_conflict_splits",
            "title_merged_groups",
            "deferred_units",
        ):
            if column_name not in job_batch_columns:
                con.execute(
                    "ALTER TABLE trend_clustering_job_batches "
                    f"ADD COLUMN {column_name} INTEGER DEFAULT 0"
                )

        con.execute(
            """
            CREATE TABLE IF NOT EXISTS trend_feedback (
                feedback_id VARCHAR PRIMARY KEY,
                cluster_id VARCHAR NOT NULL UNIQUE,
                canonical_title VARCHAR NOT NULL,
                feedback_type VARCHAR NOT NULL,
                note VARCHAR,
                item_count INTEGER NOT NULL DEFAULT 0,
                unique_evidence_count INTEGER NOT NULL DEFAULT 0,
                source_type_count INTEGER NOT NULL DEFAULT 0,
                publisher_count INTEGER NOT NULL DEFAULT 0,
                created_at TIMESTAMP NOT NULL,
                updated_at TIMESTAMP NOT NULL
            )
            """
        )
        con.execute(
            "CREATE INDEX IF NOT EXISTS idx_trend_feedback_type "
            "ON trend_feedback(feedback_type)"
        )

        con.execute(
            """
            CREATE TABLE IF NOT EXISTS api_usage_counters (
                provider VARCHAR NOT NULL,
                api_name VARCHAR NOT NULL,
                period_type VARCHAR NOT NULL,
                period_key VARCHAR NOT NULL,
                call_count BIGINT NOT NULL DEFAULT 0,
                updated_at TIMESTAMP NOT NULL,
                PRIMARY KEY(provider, api_name, period_type, period_key)
            )
            """
        )

        con.execute(
            """
            CREATE TABLE IF NOT EXISTS sync_runs (
                sync_run_id VARCHAR PRIMARY KEY,
                source_type VARCHAR NOT NULL,
                started_at TIMESTAMP NOT NULL,
                finished_at TIMESTAMP,
                status VARCHAR NOT NULL,
                items_read INTEGER NOT NULL DEFAULT 0,
                items_added INTEGER NOT NULL DEFAULT 0,
                items_updated INTEGER NOT NULL DEFAULT 0,
                error_message VARCHAR
            )
            """
        )

        con.execute(
            """
            CREATE TABLE IF NOT EXISTS collection_runs (
                run_id VARCHAR PRIMARY KEY,
                run_type VARCHAR NOT NULL,
                status VARCHAR NOT NULL,
                started_at TIMESTAMP NOT NULL,
                finished_at TIMESTAMP,
                duration_ms BIGINT,
                source_count INTEGER NOT NULL DEFAULT 0,
                succeeded_source_count INTEGER NOT NULL DEFAULT 0,
                failed_source_count INTEGER NOT NULL DEFAULT 0,
                request_count BIGINT NOT NULL DEFAULT 0,
                retry_count BIGINT NOT NULL DEFAULT 0,
                newly_saved_count BIGINT NOT NULL DEFAULT 0,
                updated_count BIGINT NOT NULL DEFAULT 0,
                skipped_count BIGINT NOT NULL DEFAULT 0,
                summary VARCHAR,
                error_message VARCHAR,
                created_at TIMESTAMP NOT NULL
            )
            """
        )
        con.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_collection_runs_started_at
            ON collection_runs(started_at)
            """
        )
        con.execute(
            """
            CREATE TABLE IF NOT EXISTS collection_run_sources (
                run_id VARCHAR NOT NULL,
                source_name VARCHAR NOT NULL,
                status VARCHAR NOT NULL,
                duration_ms BIGINT,
                request_count BIGINT NOT NULL DEFAULT 0,
                retry_count BIGINT NOT NULL DEFAULT 0,
                newly_saved_count BIGINT NOT NULL DEFAULT 0,
                updated_count BIGINT NOT NULL DEFAULT 0,
                skipped_count BIGINT NOT NULL DEFAULT 0,
                error_message VARCHAR,
                PRIMARY KEY(run_id, source_name)
            )
            """
        )

        # 검색 결과 메타데이터가 재수집 때 덮어써져도 실제 발견 이력은 별도 원장에
        # 보존합니다. 기존 source_items를 역산해 과거 행을 만들지는 않습니다.
        con.execute(
            """
            CREATE TABLE IF NOT EXISTS collection_query_discoveries (
                run_id VARCHAR NOT NULL,
                source_name VARCHAR NOT NULL,
                source_type VARCHAR NOT NULL,
                discovery_query VARCHAR NOT NULL,
                source_item_id VARCHAR NOT NULL,
                external_id VARCHAR NOT NULL,
                source_url VARCHAR,
                is_new BOOLEAN NOT NULL,
                result_rank INTEGER,
                discovered_at TIMESTAMP NOT NULL,
                PRIMARY KEY(run_id, source_type, discovery_query, source_item_id)
            )
            """
        )

        now = datetime.now()
        quota_policy_row = con.execute(
            "SELECT setting_value FROM app_settings WHERE setting_key = 'naver_search_quota_policy_version'"
        ).fetchone()
        for key, value in DEFAULT_SETTINGS.items():
            con.execute(
                """
                INSERT INTO app_settings(setting_key, setting_value, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(setting_key) DO NOTHING
                """,
                [key, value, now],
            )

        two_stage_clustering_migration = con.execute(
            "SELECT setting_value FROM app_settings "
            "WHERE setting_key = 'trend_two_stage_clustering_migrated_v1'"
        ).fetchone()
        if two_stage_clustering_migration is None:
            con.execute(
                """
                UPDATE app_settings
                SET setting_value = '4000', updated_at = ?
                WHERE setting_key = 'trend_ai_clustering_max_items'
                  AND setting_value IN ('2400', '300')
                """,
                [now],
            )
            con.execute(
                """
                UPDATE app_settings
                SET setting_value = '200', updated_at = ?
                WHERE setting_key = 'trend_ai_clustering_batch_size'
                  AND setting_value IN ('80', '100')
                """,
                [now],
            )
            con.execute(
                """
                INSERT INTO app_settings(setting_key, setting_value, updated_at)
                VALUES ('trend_two_stage_clustering_migrated_v1', 'true', ?)
                ON CONFLICT(setting_key) DO NOTHING
                """,
                [now],
            )

        blog_profile_migration = con.execute(
            "SELECT setting_value FROM app_settings WHERE setting_key = 'blog_profiles_migrated_v1'"
        ).fetchone()
        if blog_profile_migration is None:
            profile_count = int(
                con.execute("SELECT COUNT(*) FROM blog_profiles").fetchone()[0]
            )
            if profile_count == 0:
                naver_url = con.execute(
                    "SELECT setting_value FROM app_settings WHERE setting_key = 'naver_write_url'"
                ).fetchone()
                tistory_url = con.execute(
                    "SELECT setting_value FROM app_settings WHERE setting_key = 'tistory_write_url'"
                ).fetchone()
                con.execute(
                    """
                    INSERT INTO blog_profiles(
                        blog_profile_id, profile_name, platform, login_url, write_url,
                        output_format, default_category, default_tags_json,
                        is_default, is_active, created_at, updated_at
                    ) VALUES
                        ('blog_naver_default', '네이버 블로그', 'naver_blog',
                         'https://nid.naver.com/nidlogin.login', ?, 'plain_text', '', '[]',
                         TRUE, TRUE, ?, ?),
                        ('blog_tistory_default', '티스토리', 'tistory',
                         'https://www.tistory.com/auth/login', ?, 'markdown', '', '[]',
                         FALSE, TRUE, ?, ?)
                    ON CONFLICT(blog_profile_id) DO NOTHING
                    """,
                    [
                        str(naver_url[0] if naver_url else "https://blog.naver.com/"),
                        now,
                        now,
                        str(tistory_url[0] if tistory_url else "https://www.tistory.com/"),
                        now,
                        now,
                    ],
                )
            con.execute(
                """
                INSERT INTO app_settings(setting_key, setting_value, updated_at)
                VALUES ('blog_profiles_migrated_v1', 'true', ?)
                ON CONFLICT(setting_key) DO UPDATE SET
                    setting_value = EXCLUDED.setting_value,
                    updated_at = EXCLUDED.updated_at
                """,
                [now],
            )

        # 0.8.3의 임시 기본값(월 20,000회)을 사용 중인 기존 DB만
        # 현재 무료 최대치 정책으로 한 번 자동 전환합니다.
        if quota_policy_row is None:
            con.execute(
                """
                UPDATE app_settings
                SET setting_value = '775000', updated_at = ?
                WHERE setting_key = 'naver_search_monthly_safety_limit'
                  AND setting_value = '20000'
                """,
                [now],
            )
            con.execute(
                """
                INSERT INTO app_settings(setting_key, setting_value, updated_at)
                VALUES ('naver_search_quota_policy_version', '2', ?)
                ON CONFLICT(setting_key) DO UPDATE SET
                    setting_value = EXCLUDED.setting_value,
                    updated_at = EXCLUDED.updated_at
                """,
                [now],
            )


def get_setting(
    con: duckdb.DuckDBPyConnection,
    key: str,
    default: str = "",
) -> str:
    row = con.execute(
        "SELECT setting_value FROM app_settings WHERE setting_key = ?",
        [key],
    ).fetchone()
    return default if row is None or row[0] is None else str(row[0])


def set_setting(
    con: duckdb.DuckDBPyConnection,
    key: str,
    value: str,
) -> None:
    con.execute(
        """
        INSERT INTO app_settings(setting_key, setting_value, updated_at)
        VALUES (?, ?, ?)
        ON CONFLICT(setting_key) DO UPDATE SET
            setting_value = EXCLUDED.setting_value,
            updated_at = EXCLUDED.updated_at
        """,
        [key, value, datetime.now()],
    )
