from pathlib import Path

from src.database import connect_database, init_database
from src.services.collection_history_service import (
    finish_collection_run,
    start_collection_run,
)
from src.services.topic_service import (
    add_manual_topic,
    get_topic_sources,
    import_source_signals,
    list_topics,
    upsert_source_signal,
)


def test_manual_and_source_topic_are_integrated(tmp_path: Path) -> None:
    db_path = tmp_path / "main.duckdb"
    init_database(db_path)
    with connect_database(db_path) as con:
        topic_id, created = add_manual_topic(con, title="AI 검색 변화")
        assert created
        action = upsert_source_signal(
            con,
            {
                "source_type": "youtube",
                "external_id": "emerging:ai-search",
                "title": "AI 검색 변화",
                "source_name": "YouTube",
                "signal_value": 12.3,
                "metadata": {},
            },
        )
        assert action == "added"
        topics = list_topics(con)
        row = topics.loc[topics["topic_id"] == topic_id].iloc[0]
        assert int(row["신호수"]) == 1
        assert bool(row["is_interested"])


def test_topic_sources_expose_signal_details(tmp_path: Path) -> None:
    db_path = tmp_path / "details.duckdb"
    init_database(db_path)
    with connect_database(db_path) as con:
        topic_id, _ = add_manual_topic(con, title="AI 검색 변화")
        upsert_source_signal(
            con,
            {
                "source_type": "youtube",
                "external_id": "video:1",
                "title": "AI 검색 변화",
                "source_name": "기존 YouTube 트렌드 트래커 · 최근 영상",
                "signal_value": 2500,
                "metadata": {
                    "signal_type": "recent_video",
                    "item_title": "검색 방식이 달라지는 이유",
                    "view_count": 2500,
                    "view_delta": 500,
                    "views_per_hour": 120,
                },
            },
        )
        sources = get_topic_sources(con, topic_id)

    assert len(sources) == 1
    assert sources[0]["signal_type_label"] == "최근 영상"
    assert sources[0]["item_title"] == "검색 방식이 달라지는 이유"
    assert sources[0]["view_count"] == 2500
    assert sources[0]["view_delta"] == 500
    assert sources[0]["views_per_hour"] == 120


def test_import_skips_malformed_external_items_without_failing_source(tmp_path: Path) -> None:
    class _MixedAdapter:
        def load_signals(self, limit: int = 100):
            return [
                {
                    "source_type": "wikipedia_pageviews",
                    "external_id": "valid-1",
                    "title": "AI 검색 변화",
                    "source_name": "위키백과",
                    "metadata": {},
                },
                {
                    "source_type": "wikipedia_pageviews",
                    "external_id": "symbol-only",
                    "title": "---",
                    "source_name": "위키백과",
                    "metadata": {},
                },
                {"source_type": "daum_web", "title": "외부 ID 없음"},
                None,
            ][:limit]

    db_path = tmp_path / "skip-invalid.duckdb"
    init_database(db_path)
    with connect_database(db_path) as con:
        result = import_source_signals(
            con,
            _MixedAdapter(),
            sync_source_type="mixed-public",
            create_topics=False,
        )
        stored = con.execute("SELECT COUNT(*) FROM source_items").fetchone()[0]

    assert result == {
        "status": "success",
        "items_read": 4,
        "items_added": 1,
        "items_updated": 0,
        "items_skipped": 3,
    }
    assert stored == 1


def test_tracking_url_variants_update_one_source_item(tmp_path: Path) -> None:
    db_path = tmp_path / "url-dedup.duckdb"
    init_database(db_path)
    with connect_database(db_path) as con:
        first = upsert_source_signal(
            con,
            {
                "source_type": "naver_news",
                "external_id": "first",
                "title": "갤럭시 S26 공개",
                "source_url": "https://news.example/item/1?utm_source=naver&id=7",
                "metadata": {},
            },
            create_topic=False,
        )
        second = upsert_source_signal(
            con,
            {
                "source_type": "naver_news",
                "external_id": "second",
                "title": "갤럭시 S26 공개 사양",
                "source_url": "https://news.example/item/1?id=7&fbclid=tracking",
                "metadata": {},
            },
            create_topic=False,
        )
        rows = con.execute(
            "SELECT raw_title, normalized_url FROM source_items"
        ).fetchall()

    assert first == "added"
    assert second == "updated"
    assert rows == [("갤럭시 S26 공개 사양", "https://news.example/item/1?id=7")]


def test_repeated_import_tracks_previous_seen_time_and_count(tmp_path: Path) -> None:
    db_path = tmp_path / "observations.duckdb"
    init_database(db_path)
    signal = {
        "source_type": "naver_news",
        "external_id": "repeat-news",
        "title": "갤럭시 S26 공개",
        "source_url": "https://news.example/repeat",
        "metadata": {},
    }
    with connect_database(db_path) as con:
        assert upsert_source_signal(con, signal, create_topic=False) == "added"
        first = con.execute(
            """
            SELECT first_imported_at, previous_imported_at, last_imported_at,
                   observation_count
            FROM source_items
            """
        ).fetchone()
        assert upsert_source_signal(con, signal, create_topic=False) == "updated"
        second = con.execute(
            """
            SELECT first_imported_at, previous_imported_at, last_imported_at,
                   observation_count
            FROM source_items
            """
        ).fetchone()

    assert first[1] is None
    assert first[3] == 1
    assert second[0] == first[0]
    assert second[1] == first[2]
    assert second[2] >= second[1]
    assert second[3] == 2


def test_query_discovery_ledger_preserves_queries_actions_and_best_rank(
    tmp_path: Path,
) -> None:
    class _Adapter:
        def __init__(self, signals):
            self.signals = signals

        def load_signals(self, limit: int = 100):
            return self.signals[:limit]

    def signal(query: str, rank: int) -> dict:
        return {
            "source_type": "naver_news",
            "external_id": "same-news",
            "title": "AI 검색 서비스 개편",
            "source_url": "https://news.example/ai-search",
            "source_name": "news.example",
            "metadata": {
                "discovery_query": query,
                "result_rank": rank,
            },
        }

    db_path = tmp_path / "query-discoveries.duckdb"
    init_database(db_path)
    with connect_database(db_path) as con:
        first_run_id = start_collection_run(con, "manual_refresh")
        first_result = import_source_signals(
            con,
            _Adapter(
                [
                    signal("AI 검색", 7),
                    signal("AI 검색", 2),
                    signal("생성형 AI", 5),
                ]
            ),
            sync_source_type="naver_search",
            create_topics=False,
            collection_run_id=first_run_id,
        )
        first_rows = con.execute(
            """
            SELECT source_name, source_type, discovery_query, external_id,
                   source_url, is_new, result_rank
            FROM collection_query_discoveries
            WHERE run_id = ?
            ORDER BY discovery_query
            """,
            [first_run_id],
        ).fetchall()
        finish_collection_run(
            con,
            first_run_id,
            result={"naver": {**first_result, "status": "success"}},
        )

        second_run_id = start_collection_run(con, "background_refresh")
        second_result = import_source_signals(
            con,
            _Adapter([signal("AI 검색", 1)]),
            sync_source_type="naver_search",
            create_topics=False,
            collection_run_id=second_run_id,
        )
        second_row = con.execute(
            """
            SELECT is_new, result_rank
            FROM collection_query_discoveries
            WHERE run_id = ?
            """,
            [second_run_id],
        ).fetchone()

    assert first_result == {
        "status": "success",
        "items_read": 3,
        "items_added": 1,
        "items_updated": 2,
        "items_skipped": 0,
    }
    assert first_rows == [
        (
            "naver",
            "naver_news",
            "AI 검색",
            "same-news",
            "https://news.example/ai-search",
            True,
            2,
        ),
        (
            "naver",
            "naver_news",
            "생성형 AI",
            "same-news",
            "https://news.example/ai-search",
            False,
            5,
        ),
    ]
    assert second_result["items_added"] == 0
    assert second_result["items_updated"] == 1
    assert second_row == (False, 1)
