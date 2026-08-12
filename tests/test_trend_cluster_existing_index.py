from __future__ import annotations

from types import SimpleNamespace

from src.services.trend_cluster_existing_index import install_existing_cluster_index


def _module(scored_ids: list[str]) -> SimpleNamespace:
    def score(candidate: dict, descriptor: dict) -> float:
        scored_ids.append(str(descriptor["cluster_id"]))
        shared = set(candidate.get("editorial_tokens") or ()) & set(
            descriptor.get("editorial_tokens") or ()
        )
        return 3.0 if shared else -1.0

    def legacy(*_args, **_kwargs):
        raise AssertionError("indexed implementation should replace legacy full scan")

    module = SimpleNamespace(
        AI_EXISTING_CLUSTER_CANDIDATE_LIMIT=5,
        _existing_cluster_descriptor=lambda cluster: dict(cluster),
        _existing_cluster_match_score=score,
        _attach_existing_cluster_candidates=legacy,
    )
    install_existing_cluster_index(module)
    return module


def test_existing_cluster_index_scores_only_shared_editorial_tokens() -> None:
    scored_ids: list[str] = []
    module = _module(scored_ids)

    candidates = [
        {
            "candidate_id": "candidate",
            "editorial_tokens": {"삼성", "출시"},
        }
    ]
    existing = [
        {
            "cluster_id": "cluster-shared",
            "title": "삼성 신제품 출시",
            "editorial_tokens": {"삼성", "제품"},
            "items": [{"source_item_id": "1"}],
            "second_stage_ready": True,
        },
        {
            "cluster_id": "cluster-unrelated",
            "title": "야구 경기 결과",
            "editorial_tokens": {"야구", "경기"},
            "items": [{"source_item_id": "2"}],
            "second_stage_ready": True,
        },
    ]

    attached, reference_count = module._attach_existing_cluster_candidates(
        candidates,
        existing,
    )

    assert scored_ids == ["cluster-shared"]
    assert reference_count == 1
    assert attached[0]["existing_cluster_candidates"][0]["cluster_id"] == "cluster-shared"


def test_existing_cluster_index_ignores_generic_product_review_template_overlap() -> None:
    scored_ids: list[str] = []
    module = _module(scored_ids)

    candidates = [
        {
            "candidate_id": "parasol",
            "title": "여바라 360도 원형파라솔 세트 특징과 선택 포인트 총정리",
            "editorial_tokens": {"여바라", "원형파라솔", "세트", "특징과", "선택", "포인트", "총정리"},
        }
    ]
    existing = [
        {
            "cluster_id": "monitor",
            "title": "인터픽셀 IP2726 화이트 FHD IPS 100Hz 모니터 특징과 선택 포인트",
            "editorial_tokens": {"인터픽셀", "ip2726", "모니터", "특징과", "선택", "포인트"},
            "items": [{"source_item_id": "monitor-item"}],
            "second_stage_ready": True,
        }
    ]

    attached, reference_count = module._attach_existing_cluster_candidates(
        candidates,
        existing,
    )

    assert scored_ids == []
    assert reference_count == 0
    assert attached[0]["existing_cluster_candidates"] == ()


def test_existing_cluster_index_keeps_same_product_identity_despite_generic_template() -> None:
    scored_ids: list[str] = []
    module = _module(scored_ids)

    candidates = [
        {
            "candidate_id": "monitor-new",
            "title": "인터픽셀 IP2726 특징과 선택 포인트 총정리",
            "editorial_tokens": {"인터픽셀", "ip2726", "특징과", "선택", "포인트", "총정리"},
        }
    ]
    existing = [
        {
            "cluster_id": "monitor-existing",
            "title": "인터픽셀 IP2726 모니터 특징과 선택 포인트",
            "editorial_tokens": {"인터픽셀", "ip2726", "모니터", "특징과", "선택", "포인트"},
            "items": [{"source_item_id": "monitor-item"}],
            "second_stage_ready": True,
        }
    ]

    attached, reference_count = module._attach_existing_cluster_candidates(
        candidates,
        existing,
    )

    assert scored_ids == ["monitor-existing"]
    assert reference_count == 1
    assert attached[0]["existing_cluster_candidates"][0]["cluster_id"] == "monitor-existing"
