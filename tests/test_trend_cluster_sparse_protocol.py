from __future__ import annotations

import json

from src.services.trend_cluster_sparse_executor import partition_for_view
from src.services.trend_cluster_sparse_protocol import (
    CLUSTERING_FEATURE_VERSION,
    SPARSE_RESPONSE_SCHEMA,
    build_sparse_request_text,
    candidate_topic_sort_key,
    conservative_must_merge_profiles,
    parse_sparse_response,
    select_all_topic_candidates,
)
from src.services.trend_cluster_token_runtime import AdaptiveInputTokenEstimator


def _candidate(
    candidate_id: str,
    title: str,
    *,
    subjects: tuple[str, ...],
    fingerprint: str,
    option: bool = False,
) -> dict:
    row = {
        "candidate_id": candidate_id,
        "title": title,
        "examples": [title],
        "items": [],
        "safety_refined": True,
        "safety_profile": {
            "dates": (),
            "numbered_events": (),
            "products": (),
            "actions": (),
            "directions": (),
            "subjects": subjects,
            "title_fingerprints": (fingerprint,),
        },
        "existing_cluster_candidates": [],
    }
    if option:
        row["existing_cluster_candidates"] = [
            {
                "cluster_id": f"cluster-{candidate_id}",
                "title": title,
                "examples": [title],
            }
        ]
    return row


def test_first_stage_merge_requires_exact_title_fingerprint() -> None:
    exact_left = {
        "title_fingerprints": ("same",),
        "subjects": ("삼성전자",),
        "dates": (),
        "numbered_events": (),
        "products": ("s26",),
        "actions": ("product_release",),
        "directions": (),
    }
    exact_right = dict(exact_left)
    similar_but_not_exact = {**exact_left, "title_fingerprints": ("other",)}

    assert conservative_must_merge_profiles(exact_left, exact_right) == "exact_title"
    assert conservative_must_merge_profiles(exact_left, similar_but_not_exact) == ""


def test_first_stage_merge_respects_hard_direction_conflict() -> None:
    left = {
        "title_fingerprints": ("same",),
        "subjects": ("삼성전자",),
        "dates": (),
        "numbered_events": (),
        "products": (),
        "actions": ("stock_price",),
        "directions": ("up",),
    }
    right = {**left, "directions": ("down",)}

    assert conservative_must_merge_profiles(left, right) == ""


def test_candidates_are_topic_sorted_before_token_partition() -> None:
    candidates = [
        _candidate("b", "KBO 경기 결과", subjects=("KBO",), fingerprint="b"),
        _candidate("c", "삼성 갤럭시 공개", subjects=("삼성",), fingerprint="c"),
        _candidate("a", "삼성 갤럭시 출시", subjects=("삼성",), fingerprint="a"),
    ]

    selected = select_all_topic_candidates(candidates, max_candidates=100)

    assert len(selected) == 3
    assert [candidate_topic_sort_key(row) for row in selected] == sorted(
        candidate_topic_sort_key(row) for row in selected
    )
    samsung_positions = [
        index for index, row in enumerate(selected) if "삼성" in row["title"]
    ]
    assert samsung_positions[1] - samsung_positions[0] == 1


def test_sparse_request_makes_result_categories_mutually_exclusive() -> None:
    candidates = [
        (1, _candidate("long-private-id-1", "제목 1", subjects=("주제",), fingerprint="1")),
        (2, _candidate("long-private-id-2", "제목 2", subjects=("주제",), fingerprint="2")),
    ]

    request_text = build_sparse_request_text("batch", "title", candidates)
    payload = json.loads(request_text.split("\n\n", 1)[1])

    assert CLUSTERING_FEATURE_VERSION == "7"
    assert [row["candidate_no"] for row in payload["candidates"]] == [1, 2]
    assert "long-private-id" not in request_text
    assert "독립 후보는 어떤 목록에도" in request_text
    assert "existing_links, new_groups, uncertain_nos는 서로 배타적" in request_text
    assert "같은 candidate_no를 이 세 범주에 두 번 이상 반환하지 말고" in request_text
    assert "하나의 new_group 안에서도 같은 번호를 반복하지 마세요" in request_text
    assert "conflicts는 병합 차단 근거이므로 이 배타 규칙의 예외" in request_text
    assert "representative_candidate_no" in request_text
    assert "문자열을 응답에 다시 쓰지 마세요" in request_text
    assert set(SPARSE_RESPONSE_SCHEMA["properties"]) == {
        "existing_links",
        "new_groups",
        "uncertain_nos",
        "conflicts",
    }
    new_group_schema = SPARSE_RESPONSE_SCHEMA["properties"]["new_groups"]["items"]
    assert "representative_candidate_no" in new_group_schema["properties"]
    assert "representative_candidate_no" in new_group_schema["required"]
    assert "representative_title" not in new_group_schema["properties"]


def test_sparse_parser_accepts_valid_sparse_categories() -> None:
    candidates = {
        1: _candidate("a", "기존 연결", subjects=("가",), fingerprint="a", option=True),
        2: _candidate("b", "신규 묶음 1", subjects=("나",), fingerprint="b"),
        3: _candidate("c", "신규 묶음 2", subjects=("나",), fingerprint="c"),
        4: _candidate("d", "불확실", subjects=("다",), fingerprint="d"),
    }
    response = json.dumps(
        {
            "existing_links": [
                {"candidate_no": 1, "option_id": 1, "confidence": 96}
            ],
            "new_groups": [
                {
                    "candidate_nos": [2, 3],
                    "representative_candidate_no": 3,
                    "confidence": 93,
                }
            ],
            "uncertain_nos": [4],
            "conflicts": [],
        },
        ensure_ascii=False,
    )

    parsed = parse_sparse_response(
        response,
        candidate_by_no=candidates,
        finish_reason="STOP",
    )

    assert parsed.valid is True
    assert parsed.existing_links[0]["cluster_id"] == "cluster-a"
    assert parsed.new_groups[0]["candidate_nos"] == (2, 3)
    assert parsed.new_groups[0]["representative_candidate_no"] == 3
    assert parsed.new_groups[0]["representative_title"] == "신규 묶음 2"
    assert parsed.uncertain_nos == (4,)
    assert not parsed.invalid_nos


def test_sparse_parser_rejects_representative_candidate_outside_group() -> None:
    candidates = {
        1: _candidate("a", "후보 1", subjects=("가",), fingerprint="a"),
        2: _candidate("b", "후보 2", subjects=("가",), fingerprint="b"),
        3: _candidate("c", "후보 3", subjects=("다",), fingerprint="c"),
    }
    response = json.dumps(
        {
            "existing_links": [],
            "new_groups": [
                {
                    "candidate_nos": [1, 2],
                    "representative_candidate_no": 3,
                    "confidence": 90,
                }
            ],
            "uncertain_nos": [],
            "conflicts": [],
        }
    )

    parsed = parse_sparse_response(response, candidate_by_no=candidates)

    assert parsed.valid is True
    assert not parsed.new_groups
    assert parsed.diagnostics["invalid_new_group"] == 1
    assert parsed.invalid_nos == (1, 2)
    assert parsed.uncertain_nos == (1, 2)


def test_sparse_parser_rejects_candidate_repeated_across_categories() -> None:
    candidates = {
        1: _candidate("a", "후보 1", subjects=("가",), fingerprint="a", option=True),
        2: _candidate("b", "후보 2", subjects=("나",), fingerprint="b", option=True),
    }
    response = json.dumps(
        {
            "existing_links": [
                {"candidate_no": 1, "option_id": 1, "confidence": 90},
                {"candidate_no": 2, "option_id": 5, "confidence": 90},
            ],
            "new_groups": [],
            "uncertain_nos": [1],
            "conflicts": [],
        }
    )

    parsed = parse_sparse_response(response, candidate_by_no=candidates)

    assert parsed.valid is True
    assert parsed.diagnostics["duplicate_candidate_no"] == 1
    assert parsed.diagnostics["invalid_existing_option"] == 1
    assert parsed.invalid_nos == (1, 2)
    assert parsed.uncertain_nos == (1, 2)


def test_sparse_parser_rejects_duplicate_candidate_inside_new_group() -> None:
    candidates = {
        1: _candidate("a", "후보 1", subjects=("가",), fingerprint="a"),
        2: _candidate("b", "후보 2", subjects=("가",), fingerprint="b"),
    }
    response = json.dumps(
        {
            "existing_links": [],
            "new_groups": [
                {
                    "candidate_nos": [1, 1, 2],
                    "representative_candidate_no": 1,
                    "confidence": 90,
                }
            ],
            "uncertain_nos": [],
            "conflicts": [],
        }
    )

    parsed = parse_sparse_response(response, candidate_by_no=candidates)

    assert parsed.valid is True
    assert not parsed.new_groups
    assert parsed.diagnostics["duplicate_candidate_no"] == 1
    assert parsed.diagnostics["invalid_new_group"] == 1
    assert parsed.invalid_nos == (1, 2)
    assert parsed.uncertain_nos == (1, 2)


def test_sparse_parser_rejects_abnormal_finish_reason() -> None:
    parsed = parse_sparse_response(
        json.dumps(
            {
                "existing_links": [],
                "new_groups": [],
                "uncertain_nos": [],
                "conflicts": [],
            }
        ),
        candidate_by_no={},
        finish_reason="MAX_TOKENS",
    )

    assert parsed.valid is False
    assert parsed.diagnostics["abnormal_finish"] == 1


def test_partition_is_driven_by_estimated_tokens_not_candidate_count() -> None:
    estimator = AdaptiveInputTokenEstimator(tokens_per_character=1.0)
    candidates = [
        (
            index,
            _candidate(
                str(index),
                f"긴 후보 {index} " + ("가" * 400),
                subjects=("같은주제",),
                fingerprint=str(index),
            ),
        )
        for index in range(1, 21)
    ]

    chunks, oversized = partition_for_view(
        candidates,
        view="title",
        batch_id="batch",
        estimator=estimator,
        target_tokens=3_000,
    )

    flattened = [number for chunk in chunks for number, _ in chunk.candidates]
    assert not oversized
    assert sorted(flattened) == list(range(1, 21))
    assert len(chunks) > 1
    assert all(chunk.estimated_tokens <= 3_500 for chunk in chunks)
