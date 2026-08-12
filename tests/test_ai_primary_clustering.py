from __future__ import annotations

from datetime import datetime, timedelta

from src.services import trend_discovery_service as service


def _item(
    source_id: str,
    title: str,
    *,
    url: str | None = None,
    minutes_ago: int = 0,
    source_type: str = "naver_news",
) -> dict:
    now = datetime.now() - timedelta(minutes=minutes_ago)
    tokens = service.identity_tokens(title)
    return {
        "source_item_id": source_id,
        "source_type": source_type,
        "canonical_title": title,
        "raw_title": title,
        "item_title": title,
        "normalized_title": service.normalize_title(title),
        "compact_title": service.compact_title(title),
        "identity_tokens": tokens,
        "editorial_identity_tokens": service._editorial_identity_tokens(tokens),
        "calendar_identity_tokens": service._calendar_identity_tokens(tokens),
        "normalized_url": service.normalize_url(url or f"https://example.com/{source_id}"),
        "query": title,
        "query_supported": True,
        "published_at": now,
        "observed_at": now,
        "imported_at": now,
        "source_name": f"publisher-{source_id}.example",
        "domain": "example.com",
        "metadata": {},
    }


def test_first_stage_groups_same_normalized_url_before_title() -> None:
    candidates, stats = service._build_first_stage_candidates(
        [
            _item(
                "a",
                "갤럭시 Z 폴드8 사전 판매 시작",
                url="https://news.example.com/article/10?utm_source=feed",
            ),
            _item(
                "b",
                "삼성 폴드8 예약 판매 개시",
                url="https://news.example.com/article/10?fbclid=tracking",
                source_type="daum_web",
            ),
        ]
    )

    assert len(candidates) == 1
    assert candidates[0]["first_stage_kind"] == "same_url"
    assert {item["source_item_id"] for item in candidates[0]["items"]} == {"a", "b"}
    assert stats["url_merged_items"] == 1


def test_same_url_is_split_when_hard_facts_conflict() -> None:
    candidates, stats = service._build_first_stage_candidates(
        [
            _item("up", "삼성전자 주가 상승", url="https://example.com/live/stock"),
            _item("down", "삼성전자 주가 하락", url="https://example.com/live/stock"),
        ]
    )

    assert len(candidates) == 2
    assert stats["url_conflict_splits"] == 1


def test_first_stage_groups_safe_exact_titles_across_urls() -> None:
    title = "제1235회 로또 당첨번호 발표"
    candidates, stats = service._build_first_stage_candidates(
        [
            _item("a", title, url="https://one.example/a"),
            _item("b", title, url="https://two.example/b", source_type="daum_web"),
        ]
    )

    assert len(candidates) == 1
    assert candidates[0]["first_stage_kind"] == "same_title"
    assert stats["title_merged_groups"] == 1


def test_generic_exact_titles_are_not_automatically_grouped() -> None:
    candidates, stats = service._build_first_stage_candidates(
        [
            _item("a", "오늘의 주요 뉴스", url="https://one.example/a"),
            _item("b", "오늘의 주요 뉴스", url="https://two.example/b"),
        ]
    )

    assert len(candidates) == 2
    assert stats["title_merged_groups"] == 0


def test_only_completed_second_stage_clusters_are_existing_candidates() -> None:
    candidate = service._candidate_from_items([_item("new", "삼성전자 D램 신제품 출시")])
    old_unprocessed = {
        "cluster_id": "old-unprocessed",
        "title": "삼성전자 메모리 신제품",
        "items": [_item("old-a", "삼성전자 메모리 신제품 공개")],
        "second_stage_ready": False,
    }
    completed = {
        "cluster_id": "completed",
        "title": "삼성전자 차세대 D램 출시",
        "items": [_item("old-b", "삼성전자 차세대 D램 공개")],
        "second_stage_ready": True,
    }

    attached, reference_count = service._attach_existing_cluster_candidates(
        [candidate],
        [old_unprocessed, completed],
    )

    ids = {
        option["cluster_id"]
        for option in attached[0]["existing_cluster_candidates"]
    }
    assert ids == {"completed"}
    assert reference_count == 1


def test_new_candidate_can_join_existing_cluster_without_merging_existing_clusters() -> None:
    candidate = service._candidate_from_items(
        [_item("new", "삼성전자 차세대 D램 출시")]
    )
    candidate["existing_cluster_candidates"] = (
        {"cluster_id": "memory"},
        {"cluster_id": "factory"},
    )
    existing = [
        {
            "cluster_id": "memory",
            "title": "삼성전자 차세대 메모리 출시",
            "items": [_item("old-memory", "삼성전자 차세대 메모리 공개")],
        },
        {
            "cluster_id": "factory",
            "title": "삼성전자 신규 공장 투자",
            "items": [_item("old-factory", "삼성전자 신규 공장 투자")],
        },
    ]
    assignments = [
        {
            "candidate_id": candidate["candidate_id"],
            "decision": "existing",
            "existing_cluster_id": "memory",
            "new_group_id": "",
            "representative_title": "삼성전자 차세대 D램 출시",
            "confidence": 97,
        }
    ]

    clusters, processed, uncertain, links, new_count, conflicts = (
        service._apply_second_stage_assignments([candidate], assignments, existing)
    )

    assert candidate["candidate_id"] in processed
    assert not uncertain
    assert links == 1
    assert new_count == 0
    assert conflicts == 0
    memory = next(cluster for cluster in clusters if cluster["cluster_id"] == "memory")
    factory = next(cluster for cluster in clusters if cluster["cluster_id"] == "factory")
    assert {item["source_item_id"] for item in memory["items"]} == {
        "old-memory",
        "new",
    }
    assert {item["source_item_id"] for item in factory["items"]} == {"old-factory"}


def test_hard_conflict_blocks_existing_assignment() -> None:
    candidate = service._candidate_from_items([_item("down", "삼성전자 주가 하락")])
    candidate["existing_cluster_candidates"] = ({"cluster_id": "up"},)
    existing = [
        {
            "cluster_id": "up",
            "title": "삼성전자 주가 상승",
            "items": [_item("up-item", "삼성전자 주가 상승")],
        }
    ]
    assignments = [
        {
            "candidate_id": candidate["candidate_id"],
            "decision": "existing",
            "existing_cluster_id": "up",
            "new_group_id": "",
            "representative_title": "삼성전자 주가 변동",
            "confidence": 99,
        }
    ]

    clusters, processed, uncertain, links, _new_count, conflicts = (
        service._apply_second_stage_assignments([candidate], assignments, existing)
    )

    assert not processed
    assert candidate["candidate_id"] in uncertain
    assert links == 0
    assert conflicts == 1
    assert len(clusters) == 2


def test_conflicting_new_candidates_cannot_join_same_existing_cluster() -> None:
    upward = service._candidate_from_items([_item("up", "삼성전자 주가 상승")])
    downward = service._candidate_from_items([_item("down", "삼성전자 주가 하락")])
    for candidate in (upward, downward):
        candidate["existing_cluster_candidates"] = ({"cluster_id": "stock"},)
    existing = [
        {
            "cluster_id": "stock",
            "title": "삼성전자 주가 변동",
            "items": [_item("old", "삼성전자 주가 변동")],
        }
    ]
    assignments = [
        {
            "candidate_id": upward["candidate_id"],
            "decision": "existing",
            "existing_cluster_id": "stock",
            "new_group_id": "",
            "representative_title": "삼성전자 주가 상승",
            "confidence": 99,
        },
        {
            "candidate_id": downward["candidate_id"],
            "decision": "existing",
            "existing_cluster_id": "stock",
            "new_group_id": "",
            "representative_title": "삼성전자 주가 하락",
            "confidence": 99,
        },
    ]

    clusters, processed, uncertain, links, _new_count, conflicts = (
        service._apply_second_stage_assignments(
            [upward, downward], assignments, existing
        )
    )

    assert len(processed) == 1
    assert len(uncertain) == 1
    assert links == 1
    assert conflicts == 1
    stock = next(cluster for cluster in clusters if cluster["cluster_id"] == "stock")
    assert len(stock["items"]) == 2
