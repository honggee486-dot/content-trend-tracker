from pathlib import Path

import duckdb

from src.adapters.youtube_duckdb_adapter import YouTubeDuckDBAdapter


def test_loads_latest_emerging_topics(tmp_path: Path) -> None:
    db_path = tmp_path / "youtube.duckdb"
    with duckdb.connect(str(db_path)) as con:
        con.execute(
            """
            CREATE TABLE emerging_topic_snapshots (
                snapshot_at TIMESTAMP,
                topic VARCHAR,
                topic_rank INTEGER,
                topic_score DOUBLE,
                topic_stage VARCHAR,
                video_count INTEGER,
                channel_count INTEGER,
                confirmed_video_count INTEGER,
                total_recent_views_per_hour DOUBLE,
                total_view_growth BIGINT,
                representative_titles VARCHAR,
                representative_video_urls VARCHAR,
                source_labels VARCHAR
            )
            """
        )
        con.execute(
            """
            INSERT INTO emerging_topic_snapshots VALUES
            ('2026-07-13 10:00:00', '이전 주제', 1, 3.0, '관찰', 1, 1, 0, 10, 100, '', '', ''),
            ('2026-07-14 10:00:00', '최신 주제', 1, 9.5, '확산', 5, 4, 3, 200, 5000,
             '대표 영상', 'https://www.youtube.com/watch?v=test', '카테고리')
            """
        )
    signals = YouTubeDuckDBAdapter(db_path).load_signals(limit=10)
    assert len(signals) == 1
    assert signals[0]["title"] == "최신 주제"
    assert signals[0]["signal_value"] == 9.5
