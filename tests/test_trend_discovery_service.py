from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path
from time import perf_counter

import src.services.trend_discovery_service as trend_service
from src.adapters.naver_search_adapter import NaverSearchError
from src.services.api_quota_service import get_naver_search_usage
from src.database import connect_database, init_database
from src.services.topic_service import upsert_source_signal
from src.services.trend_discovery_service import (
    angle_transfer_value,
    build_portal_search_queries,
    get_trend_cluster_items,
    get_trend_inventory_summary,
    get_trend_ranking_refresh_status,
    list_ranked_trends,
    promote_trend_cluster,
    rebuild_trend_rankings,
    recommend_content_angle_details,
    recommend_content_angles,
    refresh_trend_sources,
)


def _signal(source_type: str, external_id: str, title: str, hours_ago: int, **metadata):
    return {
        "source_type": source_type,
        "external_id": external_id,
        "title": title,
        "source_name": metadata.pop("source_name", source_type),
        "source_url": f"https://example.com/{external_id}",
        "published_at": datetime.now() - timedelta(hours=hours_ago),
        "observed_at": datetime.now() - timedelta(hours=hours_ago),
        "signal_value": metadata.get("topic_score"),
        "metadata": metadata,
    }


def test_finalize_portal_collection_forwards_collection_run_id(monkeypatch) -> None:
    captured: dict[str, object] = {}

    def fake_import_preloaded_source_signals(*args, **kwargs):
        captured.update(kwargs)
        return {
            "status": "success",
            "items_read": 1,
            "items_added": 1,
            "items_updated": 0,
            "items_skipped": 0,
        }

    monkeypatch.setattr(
        trend_service,
        "import_preloaded_source_signals",
        fake_import_preloaded_source_signals,
    )
    result = trend_service._finalize_portal_collection(
        object(),
        {
            "provider": "naver",
            "api_name": "search_api",
            "sync_source_type": "naver_search",
        },
        {
            "signals": [_signal("naver_news", "news-1", "AI 검색 변화", 1)],
            "successful_requests": 1,
        },
        collection_run_id="collection_test",
    )

    assert captured["collection_run_id"] == "collection_test"
    assert captured["sync_source_type"] == "naver_search"
    assert captured["create_topics"] is False
    assert result["items_read"] == 1


def test_cross_source_items_are_clustered_and_ranked(tmp_path: Path) -> None:
    db_path = tmp_path / "main.duckdb"
    init_database(db_path)
    with connect_database(db_path) as con:
        upsert_source_signal(
            con,
            _signal(
                "youtube",
                "yt1",
                "AI 검색 변화",
                1,
                signal_type="emerging_topic",
                item_title="AI 검색 변화",
                topic_score=8.5,
                views_per_hour=2500,
                view_delta=14000,
            ),
        )
        upsert_source_signal(
            con,
            _signal(
                "naver_news",
                "news1",
                "AI 검색 변화와 새 기능",
                2,
                item_title="AI 검색 변화와 새 기능",
                discovery_query="AI 검색 변화",
                seed_kind="youtube_topic",
                source_name="테스트뉴스",
            ),
            create_topic=False,
        )
        upsert_source_signal(
            con,
            _signal(
                "naver_blog",
                "blog1",
                "AI 검색 변화 직접 써본 후기",
                3,
                item_title="AI 검색 변화 직접 써본 후기",
                discovery_query="AI 검색 변화",
                seed_kind="youtube_topic",
                source_name="테스트블로그",
            ),
            create_topic=False,
        )

        result = rebuild_trend_rankings(con, lookback_hours=72)
        rankings = list_ranked_trends(con)

        assert result["items"] == 3
        assert result["timings"]["database"] >= 0
        assert result["timings"]["total"] >= result["timings"]["database"]
        assert not rankings.empty
        top = rankings.iloc[0]
        assert int(top["언급수"]) == 3
        assert int(top["출처종류"]) == 3
        assert int(top["naver_count"]) == 2
        assert int(top["daum_count"]) == 0
        assert int(top["youtube_count"]) == 1
        assert int(top["google_count"]) == 0
        assert int(top["wikipedia_count"]) == 0
        assert float(top["트렌드점수"]) > 60
        assert float(top["콘텐츠품질"]) >= 70
        assert str(top["판정"]) in {"추천", "검토"}
        assert str(top["원문"]) == "https://example.com/yt1"

        cluster_id = str(top["cluster_id"])
        items = get_trend_cluster_items(con, cluster_id)
        assert {item["source_type"] for item in items} == {"youtube", "naver_news", "naver_blog"}

        topic_id = promote_trend_cluster(con, cluster_id)
        linked_count = con.execute(
            "SELECT COUNT(*) FROM topic_source_links WHERE topic_id = ?", [topic_id]
        ).fetchone()[0]
        assert linked_count == 3


def test_ranked_trends_include_grouped_source_counts(tmp_path: Path) -> None:
    db_path = tmp_path / "source-counts.duckdb"
    init_database(db_path)
    with connect_database(db_path) as con:
        source_types = [
            "youtube",
            "naver_news",
            "naver_blog",
            "daum_web",
            "daum_cafe",
            "google_trends",
            "wikipedia_pageviews",
        ]
        for index, source_type in enumerate(source_types):
            upsert_source_signal(
                con,
                _signal(
                    source_type,
                    f"source-{index}",
                    "공통 트렌드 주제",
                    index + 1,
                    item_title="공통 트렌드 주제",
                    discovery_query="공통 트렌드 주제",
                    source_name=f"출처 {index}",
                ),
                create_topic=False,
            )

        rebuild_trend_rankings(con, lookback_hours=72)
        ranking = list_ranked_trends(con).iloc[0]

        assert int(ranking["언급수"]) == 7
        assert int(ranking["naver_count"]) == 2
        assert int(ranking["daum_count"]) == 2
        assert int(ranking["youtube_count"]) == 1
        assert int(ranking["google_count"]) == 1
        assert int(ranking["wikipedia_count"]) == 1


def test_content_angle_recommendations_follow_source_evidence() -> None:
    mixed_angles = recommend_content_angles(
        "전기요금 변경",
        [
            {
                "source_type": "naver_news",
                "source_name": "뉴스A",
                "raw_title": "전기요금 인상 발표와 시행 일정",
                "metadata": {"item_title": "전기요금 인상 발표와 시행 일정"},
            },
            {
                "source_type": "naver_blog",
                "source_name": "블로그B",
                "raw_title": "전기요금 절약 방법 직접 써본 후기",
                "metadata": {"item_title": "전기요금 절약 방법 직접 써본 후기"},
            },
        ],
    )
    youtube_angles = recommend_content_angles(
        "신작 게임",
        [
            {
                "source_type": "youtube",
                "source_name": "채널A",
                "raw_title": "신작 공포게임이 갑자기 인기 급상승한 이유",
                "metadata": {"item_title": "신작 공포게임이 갑자기 인기 급상승한 이유"},
            }
        ],
    )

    assert 2 <= len(mixed_angles) <= 5
    assert any("[변경 핵심]" in angle for angle in mixed_angles)
    assert any("[적용 일정]" in angle or "[사용자 대응]" in angle for angle in mixed_angles)
    assert not any("[반응 요약]" in angle or "[발표와 반응]" in angle for angle in mixed_angles)
    assert any("[핵심 정리]" in angle for angle in youtube_angles)
    assert not any("[변경 핵심]" in angle for angle in youtube_angles)
    assert mixed_angles != youtube_angles


def test_quiz_answer_intent_prioritizes_answers_and_suppresses_generic_angles() -> None:
    details = recommend_content_angle_details(
        "7월 15일 캐시워크 정답",
        [
            {
                "source_type": "naver_blog",
                "source_name": "블로그A",
                "raw_title": "7월 15일 캐시워크 정답 후기와 사용자 불편",
                "metadata": {"item_title": "7월 15일 캐시워크 정답 후기와 사용자 불편"},
            },
            {
                "source_type": "daum_web",
                "source_name": "웹문서B",
                "raw_title": "캐시워크 정답 발표와 생활 변화",
                "metadata": {"item_title": "캐시워크 정답 발표와 생활 변화"},
            },
        ],
    )

    assert details[0]["key"] == "quiz_answer"
    assert details[0]["text"].startswith("[정답 정리]")
    assert not any(
        detail["key"] in {"reaction", "official_vs_reaction", "impact", "comparison", "practical"}
        for detail in details
    )


def test_quiz_answer_intent_adds_only_supported_update_and_input_caution() -> None:
    details = recommend_content_angle_details(
        "오퀴즈 정답",
        [
            {
                "source_type": "naver_blog",
                "source_name": "블로그A",
                "raw_title": "오후 새 문제 추가와 정답 변경 업데이트",
                "metadata": {"item_title": "오후 새 문제 추가와 정답 변경 업데이트"},
            },
            {
                "source_type": "daum_cafe",
                "source_name": "카페B",
                "raw_title": "정답 입력 시 띄어쓰기와 오답 주의",
                "metadata": {"item_title": "정답 입력 시 띄어쓰기와 오답 주의"},
            },
        ],
    )

    assert [detail["key"] for detail in details] == [
        "quiz_answer",
        "quiz_answer_update",
        "quiz_answer_caution",
    ]


def test_quiz_app_review_and_policy_change_do_not_use_answer_intent() -> None:
    review_details = recommend_content_angle_details(
        "캐시워크 퀴즈 앱 사용 후기",
        [
            {
                "source_type": "naver_blog",
                "source_name": "블로그A",
                "raw_title": "캐시워크 퀴즈 앱 직접 사용 후기와 불편",
                "metadata": {"item_title": "캐시워크 퀴즈 앱 직접 사용 후기와 불편"},
            }
        ],
    )
    policy_details = recommend_content_angle_details(
        "캐시워크 정책 변경",
        [
            {
                "source_type": "naver_news",
                "source_name": "뉴스A",
                "raw_title": "캐시워크 정책 변경 발표",
                "metadata": {"item_title": "캐시워크 정책 변경 발표"},
            }
        ],
    )

    assert not any(detail["key"].startswith("quiz_answer") for detail in review_details)
    assert any(detail["key"] == "reaction_summary" for detail in review_details)
    assert not any(detail["key"].startswith("quiz_answer") for detail in policy_details)
    assert any(detail["key"] == "update_summary" for detail in policy_details)


def test_generic_popular_video_scope_does_not_merge_unrelated_items(tmp_path: Path) -> None:
    db_path = tmp_path / "main.duckdb"
    init_database(db_path)
    with connect_database(db_path) as con:
        for external_id, item_title in (("yt-cat", "고양이 구조 영상"), ("yt-car", "신형 전기차 공개")):
            upsert_source_signal(
                con,
                _signal(
                    "youtube",
                    external_id,
                    item_title,
                    1,
                    signal_type="recent_video",
                    item_title=item_title,
                    discovery_query="인기영상:KR:전체",
                    topic_score=7.0,
                ),
            )

        result = rebuild_trend_rankings(con, lookback_hours=72)
        rankings = list_ranked_trends(con)

        assert result["clusters"] == 2
        assert len(rankings) == 2
        assert "인기영상:KR:전체" not in set(rankings["주제"].astype(str))


def test_recent_video_prefers_topic_title_over_noisy_video_title(tmp_path: Path) -> None:
    db_path = tmp_path / "main.duckdb"
    init_database(db_path)
    with connect_database(db_path) as con:
        upsert_source_signal(
            con,
            _signal(
                "youtube",
                "yt-noisy",
                "공포 게임 신작",
                1,
                signal_type="recent_video",
                item_title="NO NO NO GRANNY!!! #shorts #live #gaming",
                topic_score=7.2,
                views_per_hour=1800,
                view_delta=9000,
            ),
        )

        rebuild_trend_rankings(con, lookback_hours=72)
        rankings = list_ranked_trends(con)

        assert len(rankings) == 1
        row = rankings.iloc[0]
        assert "확인 필요" in str(row["주제"])
        assert "NO NO NO" not in str(row["주제"])
        assert str(row["판정"]) == "보류"


def test_noisy_single_video_is_held_below_clear_topic(tmp_path: Path) -> None:
    db_path = tmp_path / "main.duckdb"
    init_database(db_path)
    with connect_database(db_path) as con:
        upsert_source_signal(
            con,
            _signal(
                "youtube",
                "clean",
                "전기요금 변경",
                1,
                signal_type="emerging_topic",
                item_title="전기요금 변경",
                topic_score=8.0,
                views_per_hour=1200,
                view_delta=8000,
            ),
        )
        upsert_source_signal(
            con,
            _signal(
                "youtube",
                "noisy",
                "NO NO NO GRANNY!!! #shorts #live #gaming",
                1,
                signal_type="recent_video",
                item_title="NO NO NO GRANNY!!! #shorts #live #gaming",
                topic_score=8.0,
                views_per_hour=1200,
                view_delta=8000,
            ),
        )

        rebuild_trend_rankings(con, lookback_hours=72)
        rankings = list_ranked_trends(con)

        clean_row = rankings[rankings["주제"] == "전기요금 변경"].iloc[0]
        noisy_row = rankings[rankings["주제"].str.contains("확인 필요")].iloc[0]

        assert float(clean_row["콘텐츠품질"]) > float(noisy_row["콘텐츠품질"])
        assert float(clean_row["트렌드점수"]) > float(noisy_row["트렌드점수"])
        assert str(noisy_row["판정"]) == "보류"


class _YouTubeFixtureAdapter:
    def load_signals(self, limit: int = 100):
        return [
            _signal(
                "youtube",
                "yt-partial-success",
                "전기요금 변경",
                1,
                signal_type="emerging_topic",
                item_title="전기요금 변경",
                topic_score=8.2,
                views_per_hour=1500,
                view_delta=7000,
            )
        ][:limit]


class _FailingNaverAdapter:
    def search(self, **kwargs):
        raise NaverSearchError("NAVER API HUB 도메인 오류")


def test_naver_failure_does_not_block_youtube_import_or_ranking(tmp_path: Path) -> None:
    db_path = tmp_path / "main.duckdb"
    init_database(db_path)
    with connect_database(db_path) as con:
        result = refresh_trend_sources(
            con,
            youtube_adapter=_YouTubeFixtureAdapter(),
            naver_adapter=_FailingNaverAdapter(),
            configured_seed_queries=["전기요금"],
            lookback_hours=72,
        )

        assert result["youtube"]["items_read"] == 1
        assert result["naver"]["status"] == "failed"
        assert result["naver"]["failed_requests"] >= 1
        assert "naver" in result["errors"]
        assert result["ranking"]["clusters"] == 1
        rankings = list_ranked_trends(con)
        assert len(rankings) == 1
        assert str(rankings.iloc[0]["주제"]) == "전기요금 변경"


class _CountingNaverAdapter:
    def __init__(self):
        self.calls = 0

    def search(self, **kwargs):
        self.calls += 1
        return []


def test_naver_search_calls_are_counted_and_limited(tmp_path: Path) -> None:
    db_path = tmp_path / "main.duckdb"
    init_database(db_path)
    adapter = _CountingNaverAdapter()
    with connect_database(db_path) as con:
        result = refresh_trend_sources(
            con,
            naver_adapter=adapter,
            configured_seed_queries=["전기요금", "AI 검색"],
            portal_pages_per_query=1,
            naver_daily_safety_limit=100,
            naver_monthly_safety_limit=4,
        )
        assert result["errors"] == {}
        assert adapter.calls == 4
        usage = get_naver_search_usage(con, daily_limit=100, monthly_limit=4)
        assert usage.monthly_used == 4

        second = refresh_trend_sources(
            con,
            naver_adapter=adapter,
            configured_seed_queries=["전기요금"],
            portal_pages_per_query=1,
            naver_daily_safety_limit=100,
            naver_monthly_safety_limit=4,
        )
        assert "naver" in second["errors"]
        assert "월간 안전 한도" in second["errors"]["naver"]
        assert adapter.calls == 4


class _PublicFixtureAdapter:
    def __init__(self, source_type: str, title: str):
        self.source_type = source_type
        self.title = title
        self.request_count = 0

    def load_signals(self, limit: int = 100):
        self.request_count += 1
        signal_type = (
            "google_trend" if self.source_type == "google_trends" else "wikipedia_pageview"
        )
        metadata = {
            "signal_type": signal_type,
            "item_title": self.title,
            "description": "검색 급상승 조회수 관심 증가",
        }
        if self.source_type == "google_trends":
            metadata.update({"traffic_count": 20_000, "approx_traffic": "20K+"})
        else:
            metadata.update({"views": 12_000, "rank": 5})
        return [
            _signal(
                self.source_type,
                f"{self.source_type}-1",
                self.title,
                1,
                **metadata,
            )
        ][:limit]


def test_free_public_sources_are_imported_counted_and_ranked(tmp_path: Path) -> None:
    from src.services.api_quota_service import get_local_api_usage

    db_path = tmp_path / "main.duckdb"
    init_database(db_path)
    google = _PublicFixtureAdapter("google_trends", "AI 검색 변화")
    wikipedia = _PublicFixtureAdapter("wikipedia_pageviews", "AI 검색 변화")
    with connect_database(db_path) as con:
        result = refresh_trend_sources(
            con,
            google_trends_adapter=google,
            wikipedia_adapter=wikipedia,
            lookback_hours=72,
        )

        assert result["google_trends"]["items_read"] == 1
        assert result["wikipedia"]["items_read"] == 1
        assert result["ranking"]["clusters"] == 1
        ranking = list_ranked_trends(con).iloc[0]
        assert int(ranking["언급수"]) == 2
        assert int(ranking["출처종류"]) == 2

        google_usage = get_local_api_usage(
            con,
            provider="google",
            api_name="trends_rss",
        )
        wiki_usage = get_local_api_usage(
            con,
            provider="wikimedia",
            api_name="pageviews_top",
        )
        assert google_usage.daily_used == 1
        assert wiki_usage.daily_used == 1


def test_google_trend_becomes_naver_discovery_query(tmp_path: Path) -> None:
    class _QueryCapturingNaver:
        def __init__(self):
            self.queries = []

        def search(self, **kwargs):
            self.queries.append(kwargs["query"])
            return []

    db_path = tmp_path / "main.duckdb"
    init_database(db_path)
    google = _PublicFixtureAdapter("google_trends", "새로운 AI 검색")
    naver = _QueryCapturingNaver()
    with connect_database(db_path) as con:
        refresh_trend_sources(
            con,
            google_trends_adapter=google,
            naver_adapter=naver,
            configured_seed_queries=[],
            portal_pages_per_query=1,
            naver_daily_safety_limit=100,
            naver_monthly_safety_limit=100,
        )

    assert naver.queries == ["새로운 AI 검색", "새로운 AI 검색"]


class _CountingDaumAdapter:
    def __init__(self):
        self.calls = []

    def search(self, **kwargs):
        self.calls.append((kwargs["search_type"], kwargs["query"]))
        return []


def test_daum_web_and_cafe_calls_are_counted_and_share_queries(tmp_path: Path) -> None:
    from src.services.api_quota_service import get_kakao_daum_usage

    db_path = tmp_path / "main.duckdb"
    init_database(db_path)
    adapter = _CountingDaumAdapter()
    with connect_database(db_path) as con:
        result = refresh_trend_sources(
            con,
            daum_adapter=adapter,
            configured_seed_queries=["전기요금", "AI 검색"],
            portal_pages_per_query=1,
            daum_max_workers=1,
            kakao_daum_daily_safety_limit=100,
            kakao_daum_monthly_safety_limit=4,
        )
        assert result["errors"] == {}
        assert adapter.calls == [
            ("web", "전기요금"),
            ("cafe", "전기요금"),
            ("web", "AI 검색"),
            ("cafe", "AI 검색"),
        ]
        usage = get_kakao_daum_usage(
            con,
            daily_limit=100,
            monthly_limit=4,
        )
        assert usage.monthly_used == 4

        second = refresh_trend_sources(
            con,
            daum_adapter=adapter,
            configured_seed_queries=["전기요금"],
            portal_pages_per_query=1,
            kakao_daum_daily_safety_limit=100,
            kakao_daum_monthly_safety_limit=4,
        )
        assert "daum" in second["errors"]
        assert adapter.calls[-1] == ("cafe", "AI 검색")


def test_portal_pages_expand_calls_and_pass_page_numbers(tmp_path: Path) -> None:
    class _PageCapturingAdapter:
        def __init__(self):
            self.calls = []

        def search(self, **kwargs):
            self.calls.append((kwargs["search_type"], kwargs["query"], kwargs["page"]))
            return []

    db_path = tmp_path / "pages.duckdb"
    init_database(db_path)
    naver = _PageCapturingAdapter()
    with connect_database(db_path) as con:
        result = refresh_trend_sources(
            con,
            naver_adapter=naver,
            configured_seed_queries=["전기요금"],
            portal_pages_per_query=3,
            naver_max_workers=1,
            naver_daily_safety_limit=100,
            naver_monthly_safety_limit=100,
        )

    assert result["errors"] == {}
    assert naver.calls == [
        ("news", "전기요금", 1),
        ("news", "전기요금", 2),
        ("news", "전기요금", 3),
        ("blog", "전기요금", 1),
        ("blog", "전기요금", 2),
        ("blog", "전기요금", 3),
    ]


def test_daum_sources_join_cross_source_cluster(tmp_path: Path) -> None:
    db_path = tmp_path / "main.duckdb"
    init_database(db_path)
    with connect_database(db_path) as con:
        upsert_source_signal(
            con,
            _signal(
                "naver_news",
                "news-daum-cluster",
                "전기요금 개편 발표",
                1,
                item_title="전기요금 개편 발표",
                discovery_query="전기요금 개편",
            ),
            create_topic=False,
        )
        upsert_source_signal(
            con,
            _signal(
                "daum_cafe",
                "cafe-daum-cluster",
                "전기요금 개편 실제 반응",
                2,
                item_title="전기요금 개편 실제 반응",
                discovery_query="전기요금 개편",
            ),
            create_topic=False,
        )
        rebuild_trend_rankings(con, lookback_hours=72)
        top = list_ranked_trends(con).iloc[0]
        items = get_trend_cluster_items(con, str(top["cluster_id"]))
        assert {item["source_type"] for item in items} == {"naver_news", "daum_cafe"}
        angles = recommend_content_angles(str(top["주제"]), items)
        assert any("변경 핵심" in angle for angle in angles)
        assert not any("반응 요약" in angle or "발표와 반응" in angle for angle in angles)


def test_generic_portal_query_does_not_merge_unrelated_results(tmp_path: Path) -> None:
    db_path = tmp_path / "generic-query.duckdb"
    init_database(db_path)
    with connect_database(db_path) as con:
        for external_id, title in (
            ("horror-1", "Pixel 11 새 색상과 가격 유출"),
            ("horror-2", "로봇 잔디깎이 할인 행사"),
            ("horror-3", "저작권 소송에 직면한 검색 기업"),
        ):
            upsert_source_signal(
                con,
                _signal(
                    "daum_web",
                    external_id,
                    title,
                    1,
                    item_title=title,
                    discovery_query="horror",
                ),
                create_topic=False,
            )

        result = rebuild_trend_rankings(con, lookback_hours=72)
        rankings = list_ranked_trends(con)

    assert result["clusters"] == 3
    assert len(rankings) == 3
    assert "horror" not in set(rankings["주제"].str.casefold())


def test_near_duplicate_event_titles_merge_without_generic_query(tmp_path: Path) -> None:
    db_path = tmp_path / "same-event.duckdb"
    init_database(db_path)
    with connect_database(db_path) as con:
        upsert_source_signal(
            con,
            _signal(
                "naver_news",
                "galaxy-news",
                "삼성 갤럭시 S26 공개, 카메라 기능 강화",
                1,
                item_title="삼성 갤럭시 S26 공개, 카메라 기능 강화",
                source_name="news.example",
            ),
            create_topic=False,
        )
        upsert_source_signal(
            con,
            _signal(
                "naver_blog",
                "galaxy-blog",
                "삼성 갤럭시 S26 공개 카메라 강화 기능 정리",
                2,
                item_title="삼성 갤럭시 S26 공개 카메라 강화 기능 정리",
                source_name="blog.example",
            ),
            create_topic=False,
        )

        result = rebuild_trend_rankings(con, lookback_hours=72)
        ranking = list_ranked_trends(con).iloc[0]

    assert result["clusters"] == 1
    assert int(ranking["언급수"]) == 2
    assert "갤럭시 S26" in str(ranking["주제"])


def test_bridge_article_conservatively_merges_same_semiconductor_event(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "same-semiconductor-event.duckdb"
    init_database(db_path)
    titles = (
        "삼성전자, 브로드컴과 5년간 290조원 규모 AI 반도체 동맹",
        "삼성전자, 브로드컴에 2nm AI 반도체 파운드리·HBM 공급",
        "삼성전자·브로드컴, 290조 AI 반도체 동맹...HBM·2나노 원팀",
    )
    with connect_database(db_path) as con:
        for index, title in enumerate(titles):
            upsert_source_signal(
                con,
                _signal(
                    ("naver_news", "daum_web", "naver_blog")[index],
                    f"broadcom-{index}",
                    title,
                    index + 1,
                    item_title=title,
                    source_name=f"publisher-{index}.example",
                ),
                create_topic=False,
            )

        result = rebuild_trend_rankings(con, lookback_hours=72)
        ranking = list_ranked_trends(con).iloc[0]

    assert result["clusters"] == 1
    assert int(ranking["언급수"]) == 3
    assert "브로드컴" in str(ranking["주제"])


def test_entity_only_query_separates_different_events_and_keeps_specific_titles(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "entity-query-events.duckdb"
    init_database(db_path)
    titles = (
        "이재명 대통령, 샌프란시스코 도착",
        "이재명 대통령 미국 도착...AI 정상회의 참석",
        "이재명 대통령, 주택 세제 개편 발표",
        "이재명 대통령 부동산 세제 변경 발표",
    )
    with connect_database(db_path) as con:
        for index, title in enumerate(titles):
            upsert_source_signal(
                con,
                _signal(
                    ("naver_news", "daum_web")[index % 2],
                    f"president-event-{index}",
                    title,
                    index + 1,
                    item_title=title,
                    discovery_query="이재명 대통령",
                    source_name=f"publisher-{index}.example",
                ),
                create_topic=False,
            )

        result = rebuild_trend_rankings(con, lookback_hours=72)
        cluster_rows = con.execute(
            "SELECT canonical_title, item_count FROM trend_clusters ORDER BY item_count"
        ).fetchall()

    assert result["clusters"] == 2, cluster_rows
    assert sorted(row[1] for row in cluster_rows) == [2, 2]
    assert all(row[0] != "이재명 대통령" for row in cluster_rows)


def test_different_amounts_and_products_do_not_merge_under_entity_query(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "different-event-facts.duckdb"
    init_database(db_path)
    titles = (
        "아크전자 A1 칩 100억원 공급 계약",
        "아크전자 B2 칩 200억원 공급 계약",
    )
    with connect_database(db_path) as con:
        for index, title in enumerate(titles):
            upsert_source_signal(
                con,
                _signal(
                    ("naver_news", "daum_web")[index],
                    f"arc-contract-{index}",
                    title,
                    index + 1,
                    item_title=title,
                    discovery_query="아크전자",
                    source_name=f"publisher-{index}.example",
                ),
                create_topic=False,
            )

        result = rebuild_trend_rankings(con, lookback_hours=72)

    assert result["clusters"] == 2


def test_daily_digest_dates_do_not_merge_unrelated_documents(tmp_path: Path) -> None:
    db_path = tmp_path / "daily-digest.duckdb"
    init_database(db_path)
    with connect_database(db_path) as con:
        for index, title in enumerate(
            (
                "2026년 7월 15일 수요일 오늘의 뉴스",
                "2026년 7월 15일 수요일 정기 점검 안내",
                "2026년 7월 15일 수요일 오늘의 운세",
            )
        ):
            upsert_source_signal(
                con,
                _signal(
                    ("naver_news", "naver_blog", "daum_cafe")[index],
                    f"daily-{index}",
                    title,
                    index + 1,
                    item_title=title,
                    source_name=f"publisher-{index}.example",
                ),
                create_topic=False,
            )

        result = rebuild_trend_rankings(con, lookback_hours=72)
        rankings = list_ranked_trends(con)

    assert result["clusters"] == 3
    assert set(rankings["판정"]) == {"보류"}
    assert all("확인 필요" in title for title in rankings["주제"])


def test_generic_maintenance_notices_do_not_merge_by_date_alone(tmp_path: Path) -> None:
    db_path = tmp_path / "maintenance-notices.duckdb"
    init_database(db_path)
    with connect_database(db_path) as con:
        titles = (
            "2026년 7월 15일 정기 점검 안내",
            "2026년 7월 15일 점검 안내",
        )
        for index, title in enumerate(titles):
            upsert_source_signal(
                con,
                _signal(
                    ("naver_news", "daum_web")[index],
                    f"maintenance-{index}",
                    title,
                    index + 1,
                    item_title=title,
                    source_name=f"publisher-{index}.example",
                ),
                create_topic=False,
            )

        result = rebuild_trend_rankings(con, lookback_hours=72)

    assert result["clusters"] == 2


def test_episode_round_and_duration_identifiers_prevent_false_merges(tmp_path: Path) -> None:
    db_path = tmp_path / "numbered-identifiers.duckdb"
    init_database(db_path)
    titles = (
        "프로그램 최신",
        "프로그램 1회",
        "프로그램 2회",
        "대회 최신",
        "대회 1차",
        "대회 2차",
        "계획 안내",
        "3주 계획",
        "4주 계획",
    )
    with connect_database(db_path) as con:
        for index, title in enumerate(titles):
            upsert_source_signal(
                con,
                _signal(
                    ("naver_news", "naver_blog", "daum_web")[index % 3],
                    f"numbered-{index}",
                    title,
                    index + 1,
                    item_title=title,
                    source_name=f"publisher-{index}.example",
                ),
                create_topic=False,
            )

        result = rebuild_trend_rankings(con, lookback_hours=72)
        cluster_by_title = dict(
            con.execute(
                """
                SELECT s.raw_title, i.cluster_id
                FROM trend_cluster_items i
                JOIN source_items s ON s.source_item_id = i.source_item_id
                """
            ).fetchall()
        )

    assert result["clusters"] >= 6
    assert cluster_by_title["프로그램 1회"] != cluster_by_title["프로그램 2회"]
    assert cluster_by_title["대회 1차"] != cluster_by_title["대회 2차"]
    assert cluster_by_title["3주 계획"] != cluster_by_title["4주 계획"]


def test_same_lotto_round_variants_merge_without_absorbing_other_round_or_topic(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "same-lotto-round.duckdb"
    init_database(db_path)
    titles = (
        "로또 1235회 당첨번호 1등 당첨지역",
        "제1235회 로또 1등 번호와 당첨금",
        "로또복권 1235회 당첨 번호 확인",
        "로또 1236회 당첨번호",
        "장수 프로그램 1235회 방송 예고",
    )
    with connect_database(db_path) as con:
        for index, title in enumerate(titles):
            upsert_source_signal(
                con,
                _signal(
                    ("naver_news", "naver_blog", "daum_web")[index % 3],
                    f"lotto-round-{index}",
                    title,
                    index + 1,
                    item_title=title,
                    discovery_query=(
                        "로또 1235회 당첨번호"
                        if index < 3
                        else title
                    ),
                    source_name=f"publisher-{index}.example",
                ),
                create_topic=False,
            )

        result = rebuild_trend_rankings(con, lookback_hours=72)
        cluster_by_title = dict(
            con.execute(
                """
                SELECT s.raw_title, i.cluster_id
                FROM trend_cluster_items i
                JOIN source_items s ON s.source_item_id = i.source_item_id
                """
            ).fetchall()
        )

    same_round_ids = {cluster_by_title[title] for title in titles[:3]}
    assert len(same_round_ids) == 1
    assert cluster_by_title[titles[3]] not in same_round_ids
    assert cluster_by_title[titles[4]] not in same_round_ids
    assert result["clusters"] == 3


def test_shared_version_query_merges_source_specific_titles(tmp_path: Path) -> None:
    db_path = tmp_path / "version-query.duckdb"
    init_database(db_path)
    with connect_database(db_path) as con:
        for index, title in enumerate(
            (
                "ZX-4.2 파일 안전성 문제와 대응",
                "ZX-4.2 모델별 성능과 가격 비교",
                "ZX-4.2 공개 뒤 사용자 반응 정리",
            )
        ):
            upsert_source_signal(
                con,
                _signal(
                    ("naver_news", "naver_blog", "daum_web")[index],
                    f"version-{index}",
                    title,
                    index + 1,
                    item_title=title,
                    discovery_query="ZX-4.2",
                    source_name=f"publisher-{index}.example",
                ),
                create_topic=False,
            )

        result = rebuild_trend_rankings(con, lookback_hours=72)
        ranking = list_ranked_trends(con).iloc[0]

    assert result["clusters"] == 1
    assert int(ranking["언급수"]) == 3


def test_repeated_specific_terms_merge_event_variants(tmp_path: Path) -> None:
    db_path = tmp_path / "specific-phrase.duckdb"
    init_database(db_path)
    titles = (
        "아크전자 차세대 폴더블 핵심 기술 플렉스 티타늄 발표",
        "아크전자 플렉스 티타늄 발표 폴더블 디스플레이 핵심 기술 정리",
    )
    with connect_database(db_path) as con:
        for index, title in enumerate(titles):
            upsert_source_signal(
                con,
                _signal(
                    ("naver_news", "naver_blog")[index % 2],
                    f"phrase-{index}",
                    title,
                    index + 1,
                    item_title=title,
                    source_name=f"publisher-{index}.example",
                ),
                create_topic=False,
            )

        result = rebuild_trend_rankings(con, lookback_hours=72)
        ranking = list_ranked_trends(con).iloc[0]

    assert result["clusters"] == 1
    assert int(ranking["언급수"]) == len(titles)


def test_ranking_performance_stays_bounded_on_large_synthetic_fixture(tmp_path: Path) -> None:
    db_path = tmp_path / "large-ranking.duckdb"
    init_database(db_path)
    with connect_database(db_path) as con:
        for index in range(1000):
            title = f"테스트제품 모델-X-{index:04d} 기능 변경"
            upsert_source_signal(
                con,
                _signal(
                    "naver_news",
                    f"large-{index}",
                    title,
                    index % 48,
                    item_title=title,
                    source_name=f"publisher-{index % 20}.example",
                ),
                create_topic=False,
            )

        started = perf_counter()
        result = rebuild_trend_rankings(con, lookback_hours=72)
        elapsed = perf_counter() - started

    assert result["items"] == 1000
    assert result["clusters"] == 1000
    assert elapsed < 8.0


def test_korean_paraphrases_of_same_arrest_event_merge(tmp_path: Path) -> None:
    db_path = tmp_path / "korean-paraphrase.duckdb"
    init_database(db_path)
    with connect_database(db_path) as con:
        for source_type, external_id, title in (
            (
                "naver_news",
                "arrest-news",
                "자진 출석 피의자 긴급체포한 경찰, 수사서류 조작",
            ),
            (
                "daum_web",
                "arrest-web",
                "자진출석 피의자 불법 긴급체포한 경찰...서류 조작까지",
            ),
            (
                "daum_cafe",
                "arrest-cafe",
                "피의자 자진 출석 뒤 불법 체포한 경찰관 서류 조작 논란",
            ),
        ):
            upsert_source_signal(
                con,
                _signal(
                    source_type,
                    external_id,
                    title,
                    1,
                    item_title=title,
                    discovery_query="긴급 체포",
                ),
                create_topic=False,
            )

        result = rebuild_trend_rankings(con, lookback_hours=72)
        ranking = list_ranked_trends(con).iloc[0]

    assert result["clusters"] == 1
    assert int(ranking["언급수"]) == 3


def test_actual_arrest_event_variants_merge_without_absorbing_another_case(tmp_path: Path) -> None:
    db_path = tmp_path / "actual-arrest-variants.duckdb"
    init_database(db_path)
    with connect_database(db_path) as con:
        titles = (
            "자수자 경찰서 밖 유인 체포...현직 경찰관 기소",
            "자진 출석 피의자 긴급체포한 경찰...서류 조작까지",
            "자수하러 경찰서 갔더니 갑자기 밖으로 불러내 긴급체포한 경찰관",
            "막가는 경찰...피의자 불법 체포하고 수사서류까지 조작",
            "투자 유튜버 흉기로 찌른 20대 긴급체포",
        )
        for index, title in enumerate(titles):
            source_type = ("naver_news", "daum_web", "daum_cafe")[index % 3]
            upsert_source_signal(
                con,
                _signal(
                    source_type,
                    f"actual-arrest-{index}",
                    title,
                    index + 1,
                    item_title=title,
                    source_name=f"publisher-{index}.example",
                ),
                create_topic=False,
            )

        result = rebuild_trend_rankings(con, lookback_hours=72)
        cluster_rows = con.execute(
            "SELECT canonical_title, item_count FROM trend_clusters ORDER BY item_count"
        ).fetchall()
        cluster_sizes = sorted(row[1] for row in cluster_rows)

    assert result["clusters"] == 2, cluster_rows
    assert cluster_sizes == [1, 4]


def test_ambiguous_single_word_does_not_merge_unrelated_titles(tmp_path: Path) -> None:
    db_path = tmp_path / "ambiguous-single-word.duckdb"
    init_database(db_path)
    with connect_database(db_path) as con:
        for index, title in enumerate(
            (
                "Apple News",
                "OpenAI 하드웨어 소송과 Apple의 대응",
                "Apple Pie 바삭한 사과 파이 만드는 법",
            )
        ):
            upsert_source_signal(
                con,
                _signal(
                    "daum_web",
                    f"apple-{index}",
                    title,
                    index + 1,
                    item_title=title,
                    discovery_query="Apple",
                    source_name=f"publisher-{index}.example",
                ),
                create_topic=False,
            )

        result = rebuild_trend_rankings(con, lookback_hours=72)

    assert result["clusters"] == 3


def test_generic_youtube_categories_are_held_and_never_used_as_bare_titles(tmp_path: Path) -> None:
    db_path = tmp_path / "generic-youtube.duckdb"
    init_database(db_path)
    with connect_database(db_path) as con:
        for external_id, title in (
            ("generic-horror", "horror"),
            ("generic-vtuber", "VTuber"),
            ("scope", "카테고리: Travel & Events / fresh"),
        ):
            upsert_source_signal(
                con,
                _signal(
                    "youtube",
                    external_id,
                    title,
                    1,
                    signal_type="recent_video",
                    item_title=title,
                    topic_score=9.0,
                    views_per_hour=5000,
                ),
            )

        rebuild_trend_rankings(con, lookback_hours=72)
        rankings = list_ranked_trends(con)

    assert set(rankings["판정"]) == {"보류"}
    bare_titles = {"horror", "vtuber", "travel & events", "fresh"}
    assert not (set(rankings["주제"].str.casefold()) & bare_titles)
    assert all("확인 필요" in title for title in rankings["주제"])


def test_independent_sources_outrank_repeated_single_source_copies(tmp_path: Path) -> None:
    db_path = tmp_path / "source-volume.duckdb"
    init_database(db_path)
    with connect_database(db_path) as con:
        for index in range(12):
            upsert_source_signal(
                con,
                _signal(
                    "naver_news",
                    f"copy-{index}",
                    "자동차 배터리 할인 행사 안내",
                    1,
                    item_title="자동차 배터리 할인 행사 안내",
                    source_name="copy.example",
                ),
                create_topic=False,
            )
        for source_type, external_id, title, publisher in (
            ("naver_news", "s26-news", "갤럭시 S26 카메라 사양 공개", "news.example"),
            ("naver_blog", "s26-blog", "갤럭시 S26 공개 카메라 사양 정리", "blog.example"),
        ):
            upsert_source_signal(
                con,
                _signal(
                    source_type,
                    external_id,
                    title,
                    1,
                    item_title=title,
                    source_name=publisher,
                ),
                create_topic=False,
            )

        rebuild_trend_rankings(con, lookback_hours=72)
        rankings = list_ranked_trends(con)
        multi = rankings[rankings["주제"].str.contains("갤럭시 S26")].iloc[0]
        copies = rankings[rankings["주제"].str.contains("자동차 배터리")].iloc[0]

    assert float(multi["트렌드점수"]) > float(copies["트렌드점수"])
    assert int(copies["언급수"]) == 12


def test_stale_topic_decays_below_recent_topic(tmp_path: Path) -> None:
    db_path = tmp_path / "recency.duckdb"
    init_database(db_path)
    with connect_database(db_path) as con:
        upsert_source_signal(
            con,
            _signal("naver_news", "recent", "갤럭시 S26 공개", 2, item_title="갤럭시 S26 공개"),
            create_topic=False,
        )
        upsert_source_signal(
            con,
            _signal("naver_news", "stale", "아이폰 19 공개", 120, item_title="아이폰 19 공개"),
            create_topic=False,
        )
        rebuild_trend_rankings(con, lookback_hours=168)
        rankings = list_ranked_trends(con)

    recent_score = float(rankings[rankings["주제"].str.contains("갤럭시")]["트렌드점수"].iloc[0])
    stale_score = float(rankings[rankings["주제"].str.contains("아이폰")]["트렌드점수"].iloc[0])
    assert recent_score > stale_score


def test_google_and_wikipedia_are_supporting_signals_not_sole_proof(tmp_path: Path) -> None:
    db_path = tmp_path / "public-only.duckdb"
    init_database(db_path)
    with connect_database(db_path) as con:
        upsert_source_signal(
            con,
            _signal(
                "google_trends",
                "google-s26",
                "갤럭시 S26",
                1,
                item_title="갤럭시 S26",
                traffic_count=100_000,
            ),
            create_topic=False,
        )
        upsert_source_signal(
            con,
            _signal(
                "wikipedia_pageviews",
                "wiki-s26",
                "갤럭시 S26",
                1,
                item_title="갤럭시 S26",
                views=200_000,
                rank=1,
            ),
            create_topic=False,
        )
        rebuild_trend_rankings(con, lookback_hours=72)
        ranking = list_ranked_trends(con).iloc[0]

    assert ranking["판정"] == "보류"


def test_angle_details_include_reason_and_transfer_exact_text() -> None:
    details = recommend_content_angle_details(
        "전기요금 개편",
        [
            {
                "source_type": "naver_news",
                "source_name": "뉴스A",
                "raw_title": "전기요금 개편 발표와 시행 일정",
                "metadata": {"item_title": "전기요금 개편 발표와 시행 일정"},
            },
            {
                "source_type": "daum_cafe",
                "source_name": "카페B",
                "raw_title": "전기요금 개편 후 사용자 불편 반응",
                "metadata": {"item_title": "전기요금 개편 후 사용자 불편 반응"},
            },
        ],
    )

    assert details
    assert all(detail["reason"] for detail in details)
    selected = details[0]
    assert angle_transfer_value(selected) == selected["text"]


def test_ranking_refresh_status_reports_current_without_rebuilding(tmp_path: Path) -> None:
    db_path = tmp_path / "ranking-status-current.duckdb"
    init_database(db_path)
    with connect_database(db_path) as con:
        upsert_source_signal(
            con,
            _signal(
                "naver_news",
                "status-current",
                "갤럭시 S26 공개",
                1,
                item_title="갤럭시 S26 공개",
            ),
            create_topic=False,
        )
        rebuild_trend_rankings(con, lookback_hours=72)
        calculated_at = con.execute(
            "SELECT MAX(calculated_at) FROM trend_clusters"
        ).fetchone()[0]

        status = get_trend_ranking_refresh_status(con, lookback_hours=72)
        calculated_after = con.execute(
            "SELECT MAX(calculated_at) FROM trend_clusters"
        ).fetchone()[0]

    assert status == {
        "needs_rebuild": False,
        "reason": "current",
        "has_rankings": True,
        "items": 1,
        "clusters": 1,
        "pending_items": 0,
    }
    assert calculated_after == calculated_at


def test_ranking_refresh_status_detects_next_day_without_rebuilding(
    tmp_path: Path,
    monkeypatch,
) -> None:
    db_path = tmp_path / "ranking-status-stale.duckdb"
    init_database(db_path)
    ranking_day = {"value": "2026-07-16"}
    monkeypatch.setattr(trend_service, "_ranking_day", lambda: ranking_day["value"])
    with connect_database(db_path) as con:
        upsert_source_signal(
            con,
            _signal(
                "naver_news",
                "status-stale",
                "갤럭시 S26 공개",
                1,
                item_title="갤럭시 S26 공개",
            ),
            create_topic=False,
        )
        rebuild_trend_rankings(con, lookback_hours=72)
        calculated_at = con.execute(
            "SELECT MAX(calculated_at) FROM trend_clusters"
        ).fetchone()[0]

        ranking_day["value"] = "2026-07-17"
        status = get_trend_ranking_refresh_status(con, lookback_hours=72)
        calculated_after = con.execute(
            "SELECT MAX(calculated_at) FROM trend_clusters"
        ).fetchone()[0]

    assert status["needs_rebuild"] is True
    assert status["reason"] == "stale"
    assert status["has_rankings"] is True
    assert calculated_after == calculated_at


def test_ranking_refresh_status_reports_missing_rankings(tmp_path: Path) -> None:
    db_path = tmp_path / "ranking-status-empty.duckdb"
    init_database(db_path)
    with connect_database(db_path) as con:
        status = get_trend_ranking_refresh_status(con, lookback_hours=72)

    assert status["needs_rebuild"] is True
    assert status["reason"] == "missing_rankings"
    assert status["has_rankings"] is False
    assert status["clusters"] == 0
    assert status["pending_items"] == 0


def test_unchanged_rankings_reuse_previous_calculation(tmp_path: Path) -> None:
    db_path = tmp_path / "ranking-cache.duckdb"
    init_database(db_path)
    with connect_database(db_path) as con:
        upsert_source_signal(
            con,
            _signal(
                "naver_news",
                "cache-news",
                "갤럭시 S26 공개",
                1,
                item_title="갤럭시 S26 공개",
            ),
            create_topic=False,
        )
        first = rebuild_trend_rankings(con, lookback_hours=72)
        first_calculated_at = con.execute(
            "SELECT MAX(calculated_at) FROM trend_clusters"
        ).fetchone()[0]
        second = rebuild_trend_rankings(con, lookback_hours=72)
        second_calculated_at = con.execute(
            "SELECT MAX(calculated_at) FROM trend_clusters"
        ).fetchone()[0]

    assert first["reused"] is False
    assert second["reused"] is True
    assert first_calculated_at == second_calculated_at


def test_rankings_recalculate_on_next_day_for_time_decay(
    tmp_path: Path,
    monkeypatch,
) -> None:
    db_path = tmp_path / "ranking-day.duckdb"
    init_database(db_path)
    ranking_day = {"value": "2026-07-16"}
    monkeypatch.setattr(trend_service, "_ranking_day", lambda: ranking_day["value"])
    with connect_database(db_path) as con:
        upsert_source_signal(
            con,
            {
                "source_type": "naver_news",
                "external_id": "daily-decay",
                "title": "갤럭시 S26 공개",
                "source_name": "테스트뉴스",
                "source_url": "https://example.com/daily-decay",
                "published_at": datetime.now() - timedelta(hours=1),
                "observed_at": datetime.now() - timedelta(hours=1),
                "metadata": {"item_title": "갤럭시 S26 공개"},
            },
            create_topic=False,
        )
        first = rebuild_trend_rankings(con, lookback_hours=72)
        same_day = rebuild_trend_rankings(con, lookback_hours=72)

        ranking_day["value"] = "2026-07-17"
        next_day = rebuild_trend_rankings(con, lookback_hours=72)

    assert first["reused"] is False
    assert same_day["reused"] is True
    assert next_day["reused"] is False


def test_frequent_rediscovery_adds_more_score_than_first_observation(tmp_path: Path) -> None:
    db_path = tmp_path / "rediscovery.duckdb"
    init_database(db_path)
    with connect_database(db_path) as con:
        repeated_signal = _signal(
            "naver_news",
            "repeat-s26",
            "갤럭시 S26 공개",
            1,
            item_title="갤럭시 S26 공개",
            source_name="반복뉴스",
        )
        upsert_source_signal(con, repeated_signal, create_topic=False)
        upsert_source_signal(con, repeated_signal, create_topic=False)
        upsert_source_signal(
            con,
            _signal(
                "naver_news",
                "first-iphone",
                "아이폰 19 공개",
                1,
                item_title="아이폰 19 공개",
                source_name="신규뉴스",
            ),
            create_topic=False,
        )

        rebuild_trend_rankings(con, lookback_hours=72)
        rankings = list_ranked_trends(con)

    repeated = rankings[rankings["주제"].str.contains("갤럭시 S26")].iloc[0]
    first = rankings[rankings["주제"].str.contains("아이폰 19")].iloc[0]
    assert float(repeated["재포착점수"]) > 0
    assert float(first["재포착점수"]) == 0
    assert float(repeated["트렌드점수"]) > float(first["트렌드점수"])


def test_short_rediscovery_gap_scores_above_long_gap(tmp_path: Path) -> None:
    db_path = tmp_path / "rediscovery-gap.duckdb"
    init_database(db_path)
    now = datetime.now()
    with connect_database(db_path) as con:
        for external_id, title in (
            ("short-gap", "갤럭시 S26 공개"),
            ("long-gap", "아이폰 19 공개"),
        ):
            upsert_source_signal(
                con,
                _signal(
                    "naver_news",
                    external_id,
                    title,
                    1,
                    item_title=title,
                    source_name=external_id,
                ),
                create_topic=False,
            )

        con.execute(
            """
            UPDATE source_items
            SET observation_count = 8,
                previous_imported_at = ?,
                last_imported_at = ?
            WHERE external_id = 'short-gap'
            """,
            [now - timedelta(hours=6), now],
        )
        con.execute(
            """
            UPDATE source_items
            SET observation_count = 8,
                previous_imported_at = ?,
                last_imported_at = ?
            WHERE external_id = 'long-gap'
            """,
            [now - timedelta(days=10), now],
        )

        rebuild_trend_rankings(con, lookback_hours=72)
        rankings = list_ranked_trends(con)

    short_gap = rankings[rankings["주제"].str.contains("갤럭시 S26")].iloc[0]
    long_gap = rankings[rankings["주제"].str.contains("아이폰 19")].iloc[0]
    assert float(short_gap["재포착점수"]) > float(long_gap["재포착점수"])


def test_negative_signal_values_do_not_raise_math_domain_error(tmp_path: Path) -> None:
    db_path = tmp_path / "main.duckdb"
    init_database(db_path)
    with connect_database(db_path) as con:
        upsert_source_signal(
            con,
            _signal(
                "youtube",
                "yt-negative",
                "전기요금 변경",
                1,
                signal_type="emerging_topic",
                item_title="전기요금 변경",
                topic_score=8.0,
                views_per_hour=-10,
                view_delta=-25,
            ),
        )
        upsert_source_signal(
            con,
            _signal(
                "google_trends",
                "google-negative",
                "전기요금 변경",
                1,
                signal_type="google_trend",
                item_title="전기요금 변경",
                traffic_count=-100,
            ),
            create_topic=False,
        )
        upsert_source_signal(
            con,
            _signal(
                "wikipedia_pageviews",
                "wiki-negative",
                "전기요금 변경",
                1,
                signal_type="wikipedia_pageview",
                item_title="전기요금 변경",
                views=-50,
                rank=3,
            ),
            create_topic=False,
        )

        result = rebuild_trend_rankings(con, lookback_hours=72)
        rankings = list_ranked_trends(con)

        assert result["clusters"] == 1
        assert len(rankings) == 1
        assert str(rankings.iloc[0]["주제"]) == "전기요금 변경"


def test_non_finite_signal_values_are_safely_ignored(tmp_path: Path) -> None:
    db_path = tmp_path / "non-finite.duckdb"
    init_database(db_path)
    with connect_database(db_path) as con:
        upsert_source_signal(
            con,
            _signal(
                "youtube",
                "yt-nan",
                "전기요금 변경",
                1,
                signal_type="emerging_topic",
                item_title="전기요금 변경",
                topic_score=float("nan"),
                views_per_hour=float("inf"),
                view_delta="not-a-number",
            ),
        )
        upsert_source_signal(
            con,
            _signal(
                "google_trends",
                "google-inf",
                "전기요금 변경",
                1,
                signal_type="google_trend",
                item_title="전기요금 변경",
                traffic_count=float("-inf"),
            ),
            create_topic=False,
        )

        result = rebuild_trend_rankings(con, lookback_hours=72)
        rankings = list_ranked_trends(con)

        assert result["clusters"] == 1
        assert len(rankings) == 1
        assert float(rankings.iloc[0]["트렌드점수"]) >= 0


def test_refresh_reports_progress_and_elapsed_timings(tmp_path: Path) -> None:
    db_path = tmp_path / "timings.duckdb"
    init_database(db_path)
    progress_updates: list[tuple[float, str]] = []
    with connect_database(db_path) as con:
        result = refresh_trend_sources(
            con,
            youtube_adapter=_YouTubeFixtureAdapter(),
            configured_seed_queries=[],
            lookback_hours=72,
            progress_callback=lambda value, message: progress_updates.append((value, message)),
        )

    assert result["total_elapsed_seconds"] >= 0
    assert result["timings"]["youtube"] >= 0
    assert result["timings"]["ranking"] >= 0
    assert progress_updates[0][0] > 0
    assert progress_updates[-1][0] == 1.0
    assert any("YouTube" in message for _, message in progress_updates)
    assert any("통합" in message for _, message in progress_updates)


def test_source_specific_analysis_limits_prevent_portal_volume_domination(tmp_path: Path) -> None:
    db_path = tmp_path / "source-limits.duckdb"
    init_database(db_path)
    with connect_database(db_path) as con:
        for index in range(12):
            upsert_source_signal(
                con,
                _signal(
                    "youtube",
                    f"yt-limit-{index}",
                    f"유튜브 주제 {index}",
                    1,
                    signal_type="emerging_topic",
                    item_title=f"유튜브 주제 {index}",
                    topic_score=5.0,
                ),
                create_topic=False,
            )
            upsert_source_signal(
                con,
                _signal(
                    "naver_news",
                    f"news-limit-{index}",
                    f"뉴스 주제 {index}",
                    1,
                    item_title=f"뉴스 주제 {index}",
                    source_name="테스트뉴스",
                ),
                create_topic=False,
            )

        result = rebuild_trend_rankings(
            con,
            lookback_hours=72,
            source_limits={
                "youtube": 10,
                "naver": 10,
                "daum": 10,
                "google_trends": 10,
                "wikipedia": 10,
            },
        )

    assert result["items"] == 20


def test_portal_analysis_sampling_preserves_each_subtype(tmp_path: Path) -> None:
    db_path = tmp_path / "balanced-subtypes.duckdb"
    init_database(db_path)
    with connect_database(db_path) as con:
        for index in range(20):
            upsert_source_signal(
                con,
                _signal(
                    "naver_blog",
                    f"blog-balance-{index}",
                    f"최신 블로그 주제 {index}",
                    1,
                    item_title=f"최신 블로그 주제 {index}",
                    discovery_query=f"블로그 검색어 {index}",
                ),
                create_topic=False,
            )
        for index in range(8):
            upsert_source_signal(
                con,
                _signal(
                    "naver_news",
                    f"news-balance-{index}",
                    f"조금 오래된 뉴스 주제 {index}",
                    20,
                    item_title=f"조금 오래된 뉴스 주제 {index}",
                    discovery_query=f"뉴스 검색어 {index}",
                ),
                create_topic=False,
            )

        items = trend_service._parse_source_rows(
            con,
            72,
            source_limits={"naver": 10},
        )

    naver_items = [
        item for item in items if item["source_type"] in {"naver_news", "naver_blog"}
    ]
    counts = Counter(item["source_type"] for item in naver_items)

    assert len(naver_items) == 10
    assert counts["naver_news"] >= 3
    assert counts["naver_blog"] >= 3


def test_portal_analysis_sampling_limits_query_concentration(tmp_path: Path) -> None:
    db_path = tmp_path / "balanced-queries.duckdb"
    init_database(db_path)
    with connect_database(db_path) as con:
        for index in range(30):
            upsert_source_signal(
                con,
                _signal(
                    "naver_news",
                    f"travel-dominant-{index}",
                    f"여행 일반 기사 {index}",
                    1,
                    item_title=f"여행 일반 기사 {index}",
                    discovery_query="여행",
                ),
                create_topic=False,
            )
        for index in range(12):
            upsert_source_signal(
                con,
                _signal(
                    "naver_news",
                    f"specific-query-{index}",
                    f"구체 주제 기사 {index}",
                    2,
                    item_title=f"구체 주제 기사 {index}",
                    discovery_query=f"구체 검색어 {index}",
                ),
                create_topic=False,
            )

        items = trend_service._parse_source_rows(
            con,
            72,
            source_limits={"naver": 10},
        )

    naver_items = [
        item for item in items if item["source_type"] in {"naver_news", "naver_blog"}
    ]
    query_counts = Counter(item["query"] for item in naver_items)

    assert len(naver_items) == 10
    assert query_counts["여행"] <= 3
    assert len(query_counts) >= 8



def test_portal_requests_use_bounded_parallelism(tmp_path: Path) -> None:
    import threading
    import time

    class _ConcurrentAdapter:
        def __init__(self):
            self.active = 0
            self.max_active = 0
            self.lock = threading.Lock()

        def search(self, **kwargs):
            with self.lock:
                self.active += 1
                self.max_active = max(self.max_active, self.active)
            time.sleep(0.02)
            with self.lock:
                self.active -= 1
            return []

    db_path = tmp_path / "parallel.duckdb"
    init_database(db_path)
    adapter = _ConcurrentAdapter()
    with connect_database(db_path) as con:
        result = refresh_trend_sources(
            con,
            naver_adapter=adapter,
            configured_seed_queries=["전기요금", "인공지능", "자동차"],
            portal_pages_per_query=2,
            naver_max_workers=4,
            naver_daily_safety_limit=100,
            naver_monthly_safety_limit=100,
        )

    assert result["errors"] == {}
    assert adapter.max_active >= 2
    assert adapter.max_active <= 4



def test_transient_portal_failure_retries_and_counts_actual_calls(tmp_path: Path) -> None:
    import threading

    class _RetryingNaverAdapter:
        def __init__(self):
            self.calls: dict[str, int] = {}
            self.lock = threading.Lock()

        def search(self, **kwargs):
            search_type = str(kwargs["search_type"])
            with self.lock:
                self.calls[search_type] = self.calls.get(search_type, 0) + 1
                call_number = self.calls[search_type]
            if search_type == "news" and call_number == 1:
                raise NaverSearchError("NAVER 검색이 HTTP 503으로 일시 실패했습니다.")
            return []

    db_path = tmp_path / "retry.duckdb"
    init_database(db_path)
    adapter = _RetryingNaverAdapter()
    with connect_database(db_path) as con:
        result = refresh_trend_sources(
            con,
            naver_adapter=adapter,
            configured_seed_queries=["전기요금"],
            portal_pages_per_query=1,
            naver_max_workers=1,
            naver_daily_safety_limit=100,
            naver_monthly_safety_limit=100,
        )
        usage = get_naver_search_usage(con, daily_limit=100, monthly_limit=100)

    assert result["naver"]["status"] == "success"
    assert result["naver"]["planned_request_count"] == 2
    assert result["naver"]["request_count"] == 3
    assert result["naver"]["retry_count"] == 1
    assert result["naver"]["successful_requests"] == 2
    assert usage.daily_used == 3


def test_partial_portal_failure_preserves_successful_results(tmp_path: Path) -> None:
    class _PartiallyFailingNaverAdapter:
        def search(self, **kwargs):
            if kwargs["search_type"] == "news":
                raise NaverSearchError("NAVER news 검색이 HTTP 404로 실패했습니다.")
            return [
                _signal(
                    "naver_blog",
                    "partial-blog",
                    "전기요금 절약 후기",
                    1,
                    item_title="전기요금 절약 후기",
                    source_name="테스트블로그",
                )
            ]

    db_path = tmp_path / "partial.duckdb"
    init_database(db_path)
    with connect_database(db_path) as con:
        result = refresh_trend_sources(
            con,
            naver_adapter=_PartiallyFailingNaverAdapter(),
            configured_seed_queries=["전기요금"],
            portal_pages_per_query=1,
            naver_max_workers=1,
            naver_daily_safety_limit=100,
            naver_monthly_safety_limit=100,
        )
        saved = con.execute(
            "SELECT COUNT(*) FROM source_items WHERE external_id = 'partial-blog'"
        ).fetchone()[0]

    assert result["naver"]["status"] == "partial"
    assert result["naver"]["items_read"] == 1
    assert result["naver"]["successful_requests"] == 1
    assert result["naver"]["failed_requests"] == 1
    assert "naver" in result["warnings"]
    assert saved == 1


def test_naver_and_daum_network_fetches_overlap(tmp_path: Path) -> None:
    import threading
    import time

    class _SharedConcurrency:
        def __init__(self):
            self.active = 0
            self.max_active = 0
            self.lock = threading.Lock()

        def enter(self):
            with self.lock:
                self.active += 1
                self.max_active = max(self.max_active, self.active)

        def leave(self):
            with self.lock:
                self.active -= 1

    shared = _SharedConcurrency()

    class _SlowPortalAdapter:
        def search(self, **kwargs):
            shared.enter()
            try:
                time.sleep(0.03)
                return []
            finally:
                shared.leave()

    db_path = tmp_path / "provider-parallel.duckdb"
    init_database(db_path)
    with connect_database(db_path) as con:
        result = refresh_trend_sources(
            con,
            naver_adapter=_SlowPortalAdapter(),
            daum_adapter=_SlowPortalAdapter(),
            configured_seed_queries=["전기요금"],
            portal_pages_per_query=1,
            naver_max_workers=1,
            daum_max_workers=1,
            naver_daily_safety_limit=100,
            naver_monthly_safety_limit=100,
            kakao_daum_daily_safety_limit=100,
            kakao_daum_monthly_safety_limit=100,
        )

    assert result["errors"] == {}
    assert shared.max_active >= 2


def test_unchanged_youtube_parquet_is_not_reimported(tmp_path: Path) -> None:
    class _FileBackedYouTubeAdapter:
        def __init__(self, parquet_path: Path):
            self.parquet_path = parquet_path
            self.calls = 0

        def load_signals(self, limit: int = 100):
            self.calls += 1
            return [
                _signal(
                    "youtube",
                    "yt-file-signature",
                    "전기요금 변경",
                    1,
                    signal_type="emerging_topic",
                    item_title="전기요금 변경",
                    topic_score=8.0,
                )
            ]

    parquet_path = tmp_path / "signals.parquet"
    parquet_path.write_bytes(b"stable-file")
    adapter = _FileBackedYouTubeAdapter(parquet_path)
    db_path = tmp_path / "youtube-signature.duckdb"
    init_database(db_path)
    with connect_database(db_path) as con:
        first = refresh_trend_sources(con, youtube_adapter=adapter)
        second = refresh_trend_sources(con, youtube_adapter=adapter)

    assert first["youtube"]["items_read"] == 1
    assert second["youtube"]["status"] == "skipped"
    assert second["youtube"]["unchanged"] is True
    assert adapter.calls == 1


def test_calendar_only_and_generic_update_topics_are_forced_to_hold(tmp_path: Path) -> None:
    db_path = tmp_path / "main.duckdb"
    init_database(db_path)
    with connect_database(db_path) as con:
        for source_type, external_id, title in (
            ("naver_news", "date-news", "7월 15일 수요일"),
            ("naver_blog", "date-blog", "7월 15일 수요일"),
            ("daum_web", "date-update", "7월 15일 업데이트 안내"),
        ):
            upsert_source_signal(
                con,
                _signal(
                    source_type,
                    external_id,
                    title,
                    1,
                    item_title=title,
                    discovery_query=title,
                    source_name=f"{source_type}-publisher",
                ),
                create_topic=False,
            )

        result = rebuild_trend_rankings(con, lookback_hours=72)
        rankings = list_ranked_trends(con, limit=20)

        assert result["items"] == 3
        assert result["clusters"] == 3
        assert set(rankings["recommendation_status"].astype(str)) == {"hold"}
        assert float(rankings["콘텐츠품질"].max()) <= 22.0
        assert not any(
            title in {"7월 15일 수요일", "7월 15일 업데이트 안내"}
            for title in rankings["주제"].astype(str)
        )


def test_concrete_subject_with_generic_update_words_remains_usable(tmp_path: Path) -> None:
    db_path = tmp_path / "main.duckdb"
    init_database(db_path)
    with connect_database(db_path) as con:
        for source_type, external_id, title in (
            ("naver_news", "gpt-news", "GPT-5.6 7월 15일 업데이트 안내"),
            ("naver_blog", "gpt-blog", "GPT-5.6 기능 업데이트 정리"),
        ):
            upsert_source_signal(
                con,
                _signal(
                    source_type,
                    external_id,
                    title,
                    1,
                    item_title=title,
                    discovery_query="GPT-5.6 업데이트",
                    source_name=f"{source_type}-publisher",
                ),
                create_topic=False,
            )

        result = rebuild_trend_rankings(con, lookback_hours=72)
        rankings = list_ranked_trends(con, limit=20)

        assert result["clusters"] == 1
        assert len(rankings) == 1
        assert "GPT-5.6" in str(rankings.iloc[0]["주제"])
        assert str(rankings.iloc[0]["recommendation_status"]) in {"recommended", "review"}


def test_same_subject_with_different_calendar_dates_does_not_merge(tmp_path: Path) -> None:
    db_path = tmp_path / "main.duckdb"
    init_database(db_path)
    with connect_database(db_path) as con:
        for external_id, title in (
            ("quiz-15", "캐시워크 7월 15일 정답"),
            ("quiz-16", "캐시워크 7월 16일 정답"),
        ):
            upsert_source_signal(
                con,
                _signal(
                    "naver_blog",
                    external_id,
                    title,
                    1,
                    item_title=title,
                    discovery_query=title,
                    source_name=external_id,
                ),
                create_topic=False,
            )

        result = rebuild_trend_rankings(con, lookback_hours=72)

        assert result["items"] == 2
        assert result["clusters"] == 2


def test_vague_blog_idea_question_is_not_promoted_as_a_trend_topic(tmp_path: Path) -> None:
    db_path = tmp_path / "main.duckdb"
    init_database(db_path)
    with connect_database(db_path) as con:
        title = "블로그에 뭘 올릴까"
        upsert_source_signal(
            con,
            _signal(
                "naver_blog",
                "vague-blog-question",
                title,
                1,
                item_title=title,
                discovery_query=title,
                source_name="개인블로그",
            ),
            create_topic=False,
        )

        rebuild_trend_rankings(con, lookback_hours=72)
        rankings = list_ranked_trends(con, limit=20)

        assert len(rankings) == 1
        assert str(rankings.iloc[0]["recommendation_status"]) == "hold"
        assert str(rankings.iloc[0]["주제"]).startswith("구체적 주제 확인 필요")


def test_daily_fortune_and_compact_calendar_only_topics_are_forced_to_hold(tmp_path: Path) -> None:
    db_path = tmp_path / "generic-recurring.duckdb"
    init_database(db_path)
    with connect_database(db_path) as con:
        for source_type, external_id, title in (
            ("naver_blog", "fortune-compact", "오늘의 운세 2026년7월16일"),
            ("daum_web", "fortune-spaced", "오늘의 운세 | 2026년 7월 16일"),
            ("naver_news", "calendar-only", "7월 15일 수요일"),
        ):
            upsert_source_signal(
                con,
                _signal(
                    source_type,
                    external_id,
                    title,
                    1,
                    item_title=title,
                    discovery_query=title,
                    source_name=f"{source_type}-publisher",
                ),
                create_topic=False,
            )

        result = rebuild_trend_rankings(con, lookback_hours=72)
        rankings = list_ranked_trends(con, limit=20)

        assert result["items"] == 3
        assert set(rankings["recommendation_status"].astype(str)) == {"hold"}
        assert float(rankings["콘텐츠품질"].max()) <= 38.0
        assert not any(
            title in {
                "오늘의 운세 2026년7월16일",
                "오늘의 운세 | 2026년 7월 16일",
                "7월 15일 수요일",
            }
            for title in rankings["주제"].astype(str)
        )


def test_concrete_event_about_fortune_service_remains_usable(tmp_path: Path) -> None:
    db_path = tmp_path / "fortune-service-event.duckdb"
    init_database(db_path)
    with connect_database(db_path) as con:
        for source_type, external_id, title in (
            ("naver_news", "fortune-service-news", "삼성생명 오늘의 운세 서비스 종료"),
            ("naver_blog", "fortune-service-blog", "삼성생명 운세 서비스 종료 안내"),
        ):
            upsert_source_signal(
                con,
                _signal(
                    source_type,
                    external_id,
                    title,
                    1,
                    item_title=title,
                    discovery_query="삼성생명 운세 서비스 종료",
                    source_name=f"{source_type}-publisher",
                ),
                create_topic=False,
            )

        rebuild_trend_rankings(con, lookback_hours=72)
        rankings = list_ranked_trends(con, limit=20)

        assert len(rankings) == 1
        assert "삼성생명" in str(rankings.iloc[0]["주제"])
        assert str(rankings.iloc[0]["recommendation_status"]) in {"recommended", "review"}


def test_portal_queries_ignore_stale_dynamic_signals(tmp_path: Path) -> None:
    db_path = tmp_path / "dynamic-query-window.duckdb"
    init_database(db_path)
    with connect_database(db_path) as con:
        upsert_source_signal(
            con,
            _signal(
                "google_trends",
                "google-old",
                "오래된 인기 검색어",
                240,
                item_title="오래된 인기 검색어",
                topic_score=999999,
            ),
            create_topic=False,
        )
        upsert_source_signal(
            con,
            _signal(
                "google_trends",
                "google-new",
                "최근 인기 검색어",
                2,
                item_title="최근 인기 검색어",
                topic_score=100,
            ),
            create_topic=False,
        )

        queries = build_portal_search_queries(
            con,
            ["기본 탐색어"],
            limit=10,
            lookback_hours=72,
        )

    assert "최근 인기 검색어" in queries
    assert "오래된 인기 검색어" not in queries
    assert "기본 탐색어" in queries


def test_trend_inventory_summary_distinguishes_storage_and_window(tmp_path: Path) -> None:
    db_path = tmp_path / "inventory-summary.duckdb"
    init_database(db_path)
    with connect_database(db_path) as con:
        upsert_source_signal(
            con,
            _signal(
                "youtube",
                "old-video",
                "오래된 영상 주제",
                240,
                item_title="오래된 영상 주제",
            ),
            create_topic=False,
        )
        upsert_source_signal(
            con,
            _signal(
                "naver_news",
                "recent-news",
                "최근 뉴스 주제",
                2,
                item_title="최근 뉴스 주제",
                source_name="뉴스A",
            ),
            create_topic=False,
        )
        rebuild_trend_rankings(con, lookback_hours=72)
        summary = get_trend_inventory_summary(con, lookback_hours=72)

    assert summary["stored_items"] == 2
    assert summary["window_items"] == 1
    assert summary["cluster_count"] == 1
    groups = {item["source_group"]: item for item in summary["sources"]}
    assert groups["youtube"]["stored_items"] == 1
    assert groups["youtube"]["window_items"] == 0
    assert groups["naver"]["window_items"] == 1


def test_portal_queries_use_youtube_item_titles_instead_of_collection_scopes(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "youtube-query-title.duckdb"
    init_database(db_path)
    with connect_database(db_path) as con:
        upsert_source_signal(
            con,
            _signal(
                "youtube",
                "youtube-scope",
                "지역 인기: KR / Gaming",
                1,
                signal_type="recent_video",
                item_title="갤럭시 S26 게임 성능 비교",
                keyword="지역 인기: KR / Gaming",
                topic_score=900_000,
            ),
            create_topic=False,
        )

        queries = build_portal_search_queries(
            con,
            [],
            limit=10,
            lookback_hours=72,
        )

    assert "갤럭시 S26 게임 성능 비교" in queries
    assert all("지역 인기:" not in query for query in queries)


def test_portal_queries_skip_generic_youtube_titles(tmp_path: Path) -> None:
    db_path = tmp_path / "youtube-query-generic.duckdb"
    init_database(db_path)
    with connect_database(db_path) as con:
        for external_id, title in (
            ("youtube-daily", "daily"),
            ("youtube-viral", "viral"),
            ("youtube-how", "how"),
            ("youtube-highlights", "highlights"),
        ):
            upsert_source_signal(
                con,
                _signal(
                    "youtube",
                    external_id,
                    title,
                    1,
                    signal_type="recent_video",
                    item_title=title,
                    keyword=title,
                    topic_score=900_000,
                ),
                create_topic=False,
            )

        queries = build_portal_search_queries(
            con,
            ["기본 탐색어"],
            limit=10,
            lookback_hours=72,
        )

    assert queries == ["기본 탐색어"]




def test_ai_primary_candidates_only_exact_dedupe_same_url() -> None:
    from src.services import trend_discovery_service as service

    first = _signal("naver_news", "a", "삼성전자 D램 출시", 1, item_title="삼성전자 D램 출시")
    second = _signal("daum_web", "b", "삼성 새 RAM 공개", 1, item_title="삼성 새 RAM 공개")
    first["source_item_id"] = "a"
    second["source_item_id"] = "b"
    first["normalized_url"] = "https://example.com/same"
    second["normalized_url"] = "https://example.com/same"
    for item in (first, second):
        title = str(item["title"])
        item["canonical_title"] = title
        item["raw_title"] = title
        item["normalized_title"] = service.normalize_title(title)
        item["compact_title"] = service.compact_title(title)
        item["identity_tokens"] = service.identity_tokens(title)
        item["editorial_identity_tokens"] = service._editorial_identity_tokens(title)
        item["calendar_identity_tokens"] = set()
        item["query"] = ""
        item["query_supported"] = False
        item["imported_at"] = item["observed_at"]

    candidates, stats = service._build_first_stage_candidates([first, second])

    assert len(candidates) == 1
    assert {item["source_item_id"] for item in candidates[0]["items"]} == {"a", "b"}
    assert stats["url_merged_items"] == 1


def test_legacy_similarity_cluster_algorithm_is_removed() -> None:
    from src.services import trend_discovery_service as service

    assert not hasattr(service, "_cluster_items")
    assert not hasattr(service, "_merge_fragmented_event_clusters")
