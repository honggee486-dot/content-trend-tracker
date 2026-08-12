from pathlib import Path

import duckdb
import pytest

from src.adapters.youtube_duckdb_adapter import YouTubeDuckDBAdapter
from src.adapters.youtube_parquet_adapter import (
    YouTubeParquetAdapter,
    YouTubeParquetError,
)
from src.database import connect_database, init_database
from src.services.topic_service import import_youtube_signals


def write_exchange_file(path: Path, *, schema_version: str = "1.0") -> None:
    escaped_path = path.as_posix().replace("'", "''")
    with duckdb.connect(":memory:") as con:
        con.execute(
            """
            CREATE TABLE signals (
                schema_version VARCHAR, source_type VARCHAR, signal_type VARCHAR,
                external_id VARCHAR, topic_title VARCHAR, item_title VARCHAR,
                keyword VARCHAR, source_url VARCHAR, source_name VARCHAR,
                published_at TIMESTAMP, observed_at TIMESTAMP, signal_value DOUBLE,
                view_count BIGINT, view_delta BIGINT, views_per_hour DOUBLE,
                topic_score DOUBLE, metadata_json VARCHAR, exported_at TIMESTAMP
            )
            """
        )
        con.execute(
            """
            INSERT INTO signals VALUES
            (?, 'youtube', 'recent_video', 'video:test', 'AI 검색', 'AI 검색 영상',
             'AI 검색', 'https://youtu.be/test', '테스트 채널',
             '2026-07-14 08:00:00', '2026-07-14 10:00:00', 50.0,
             1000, 100, 50.0, NULL, '{"channel_name":"테스트 채널"}',
             '2026-07-14 10:30:00')
            """,
            [schema_version],
        )
        con.execute(f"COPY signals TO '{escaped_path}' (FORMAT PARQUET)")


def test_parquet_inspection_and_load(tmp_path: Path) -> None:
    path = tmp_path / "signals.parquet"
    write_exchange_file(path)

    adapter = YouTubeParquetAdapter(path)
    info = adapter.inspect()
    signals = adapter.load_signals()

    assert info["schema_version"] == "1.0"
    assert info["row_count"] == 1
    assert signals[0]["external_id"] == "video:test"
    assert signals[0]["title"] == "AI 검색"
    assert signals[0]["metadata"]["view_delta"] == 100


def test_reimport_updates_without_duplicate(tmp_path: Path) -> None:
    parquet_path = tmp_path / "signals.parquet"
    db_path = tmp_path / "main.duckdb"
    write_exchange_file(parquet_path)
    init_database(db_path)

    with connect_database(db_path) as con:
        first = import_youtube_signals(
            con,
            YouTubeParquetAdapter(parquet_path),
            sync_source_type="youtube_parquet",
        )
        second = import_youtube_signals(
            con,
            YouTubeParquetAdapter(parquet_path),
            sync_source_type="youtube_parquet",
        )
        source_count = con.execute("SELECT COUNT(*) FROM source_items").fetchone()[0]
        link_count = con.execute("SELECT COUNT(*) FROM topic_source_links").fetchone()[0]

    assert first["items_added"] == 1
    assert second["items_updated"] == 1
    assert source_count == 1
    assert link_count == 1


def test_schema_version_and_corruption_errors_are_clear(tmp_path: Path) -> None:
    unsupported = tmp_path / "unsupported.parquet"
    write_exchange_file(unsupported, schema_version="2.0")
    with pytest.raises(YouTubeParquetError, match="지원하지 않는"):
        YouTubeParquetAdapter(unsupported).inspect()

    corrupted = tmp_path / "corrupted.parquet"
    corrupted.write_bytes(b"not parquet")
    with pytest.raises(YouTubeParquetError, match="손상"):
        YouTubeParquetAdapter(corrupted).inspect()

    with pytest.raises(YouTubeParquetError, match="아직 생성되지"):
        YouTubeParquetAdapter(tmp_path / "missing.parquet").inspect()


def test_duckdb_fallback_remains_available(tmp_path: Path) -> None:
    db_path = tmp_path / "youtube.duckdb"
    with duckdb.connect(str(db_path)) as con:
        con.execute(
            """
            CREATE TABLE content_ideas (
                idea_id VARCHAR, title VARCHAR, keyword VARCHAR, content_type VARCHAR,
                status VARCHAR, priority VARCHAR, score DOUBLE, view_growth BIGINT,
                views_per_hour_growth DOUBLE, updated_at TIMESTAMP
            );
            INSERT INTO content_ideas VALUES
            ('idea1', 'fallback 아이디어', 'fallback', '정보형', 'saved', '보통',
             3.0, 10, 1.0, '2026-07-14 10:00:00');
            """
        )

    signals = YouTubeDuckDBAdapter(db_path).load_signals()
    assert signals[0]["external_id"] == "idea:idea1"
