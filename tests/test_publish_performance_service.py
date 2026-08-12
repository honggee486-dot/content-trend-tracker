from __future__ import annotations

from datetime import datetime, timedelta

import duckdb
import pytest

from src.services.publish_performance_service import (
    build_publish_performance_comparison,
    list_latest_publish_performance,
    list_publish_performance_snapshots,
    save_publish_performance_snapshot,
)


def _connection() -> duckdb.DuckDBPyConnection:
    con = duckdb.connect(":memory:")
    con.execute(
        """
        CREATE TABLE blog_profiles(
            blog_profile_id VARCHAR PRIMARY KEY,
            profile_name VARCHAR NOT NULL
        )
        """
    )
    con.execute(
        """
        CREATE TABLE drafts(
            draft_id VARCHAR PRIMARY KEY,
            title VARCHAR NOT NULL
        )
        """
    )
    con.execute(
        """
        CREATE TABLE publish_records(
            publish_id VARCHAR PRIMARY KEY,
            draft_id VARCHAR NOT NULL,
            platform VARCHAR NOT NULL,
            publish_status VARCHAR NOT NULL,
            blog_profile_id VARCHAR,
            published_at TIMESTAMP,
            created_at TIMESTAMP NOT NULL,
            archived_at TIMESTAMP
        )
        """
    )
    con.execute(
        "INSERT INTO blog_profiles VALUES ('profile_a', '티스토리 정책'), ('profile_b', '네이버 생활')"
    )
    return con


def _seed_publish_record(
    con: duckdb.DuckDBPyConnection,
    *,
    index: int,
    profile_id: str,
    platform: str,
    published_at: datetime,
    archived: bool = False,
) -> str:
    draft_id = f"draft_{index}"
    publish_id = f"publish_{index}"
    con.execute("INSERT INTO drafts VALUES (?, ?)", [draft_id, f"글 {index}"])
    con.execute(
        """
        INSERT INTO publish_records(
            publish_id, draft_id, platform, publish_status, blog_profile_id,
            published_at, created_at, archived_at
        ) VALUES (?, ?, ?, 'published', ?, ?, ?, ?)
        """,
        [
            publish_id,
            draft_id,
            platform,
            profile_id,
            published_at,
            published_at,
            published_at if archived else None,
        ],
    )
    return publish_id


def test_snapshots_are_append_only_and_latest_value_is_used() -> None:
    con = _connection()
    published_at = datetime(2026, 8, 1, 9, 0, 0)
    publish_id = _seed_publish_record(
        con,
        index=1,
        profile_id="profile_a",
        platform="tistory",
        published_at=published_at,
    )

    first_id = save_publish_performance_snapshot(
        con,
        publish_id=publish_id,
        observation_window_days=7,
        observed_at=published_at + timedelta(days=7),
        views=100,
        search_visits=40,
        likes=5,
        comments=2,
        shares=1,
        memo="첫 확인",
    )
    second_id = save_publish_performance_snapshot(
        con,
        publish_id=publish_id,
        observation_window_days=7,
        observed_at=published_at + timedelta(days=8),
        views=130,
        search_visits=50,
        likes=7,
        comments=3,
        shares=2,
        memo="정정 확인",
    )

    assert first_id != second_id
    history = list_publish_performance_snapshots(con, publish_id=publish_id)
    assert len(history) == 2
    assert history[0]["views"] == 130
    assert history[0]["interactions"] == 12
    assert history[0]["search_share"] == pytest.approx(50 / 130)

    latest = list_latest_publish_performance(con, observation_window_days=7)
    assert len(latest) == 1
    assert latest[0]["snapshot_id"] == second_id
    assert latest[0]["views"] == 130


def test_snapshot_validation_rejects_invalid_or_archived_records() -> None:
    con = _connection()
    published_at = datetime(2026, 8, 1, 9, 0, 0)
    active_id = _seed_publish_record(
        con,
        index=1,
        profile_id="profile_a",
        platform="tistory",
        published_at=published_at,
    )
    archived_id = _seed_publish_record(
        con,
        index=2,
        profile_id="profile_b",
        platform="naver_blog",
        published_at=published_at,
        archived=True,
    )

    with pytest.raises(ValueError, match="0 이상의 정수"):
        save_publish_performance_snapshot(
            con,
            publish_id=active_id,
            observation_window_days=7,
            observed_at=published_at + timedelta(days=7),
            views=-1,
        )
    with pytest.raises(ValueError, match="발행 시각보다 빠를 수 없습니다"):
        save_publish_performance_snapshot(
            con,
            publish_id=active_id,
            observation_window_days=7,
            observed_at=published_at - timedelta(minutes=1),
            views=1,
        )
    with pytest.raises(ValueError, match="보관된 발행 기록"):
        save_publish_performance_snapshot(
            con,
            publish_id=archived_id,
            observation_window_days=7,
            observed_at=published_at + timedelta(days=7),
            views=1,
        )


def test_comparison_uses_same_window_and_requires_two_sufficient_profiles() -> None:
    con = _connection()
    published_at = datetime(2026, 7, 1, 9, 0, 0)
    profile_a_views = [100, 120, 80]
    profile_b_views = [200, 220, 180]

    for offset, views in enumerate(profile_a_views, start=1):
        publish_id = _seed_publish_record(
            con,
            index=offset,
            profile_id="profile_a",
            platform="tistory",
            published_at=published_at,
        )
        save_publish_performance_snapshot(
            con,
            publish_id=publish_id,
            observation_window_days=7,
            observed_at=published_at + timedelta(days=7),
            views=views,
            search_visits=views // 2,
            likes=10,
            comments=2,
            shares=1,
        )

    for offset, views in enumerate(profile_b_views, start=4):
        publish_id = _seed_publish_record(
            con,
            index=offset,
            profile_id="profile_b",
            platform="naver_blog",
            published_at=published_at,
        )
        save_publish_performance_snapshot(
            con,
            publish_id=publish_id,
            observation_window_days=7,
            observed_at=published_at + timedelta(days=7),
            views=views,
            search_visits=views // 4,
            likes=8,
            comments=1,
            shares=0,
        )

    save_publish_performance_snapshot(
        con,
        publish_id="publish_1",
        observation_window_days=30,
        observed_at=published_at + timedelta(days=30),
        views=500,
    )

    seven_day = build_publish_performance_comparison(
        con,
        observation_window_days=7,
    )
    assert seven_day.status == "비교 가능"
    assert seven_day.comparison_ready is True
    assert seven_day.recommendation_action == "keep_current_rules"
    assert seven_day.view_leader == "네이버 생활"
    assert len(seven_day.profile_rows) == 2

    thirty_day = build_publish_performance_comparison(
        con,
        observation_window_days=30,
    )
    assert thirty_day.status == "표본 부족"
    assert thirty_day.comparison_ready is False
    assert len(thirty_day.profile_rows) == 1
