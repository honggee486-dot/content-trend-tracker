from __future__ import annotations

import json
import re
from collections import defaultdict
from dataclasses import dataclass
from typing import Any, Iterable, Sequence

from src.services.trend_cluster_safety_service import (
    build_candidate_safety_profile,
    build_existing_option_payload,
    must_split_profiles,
    refine_first_stage_candidates,
    resolve_existing_option_id,
)

CLUSTERING_SCAN_CANDIDATE_LIMIT = 50_000
CLUSTERING_ACTIVE_VIEWS = ("title", "event", "identity", "existing")
CLUSTERING_REQUIRED_BASE_VIEWS = frozenset({"title", "event", "identity"})
CLUSTERING_MAX_SPARSE_GROUP_SIZE = 200
CLUSTERING_FEATURE_ID = "trend_cluster_grouping_v3"
CLUSTERING_FEATURE_VERSION = "7"

SPARSE_RESPONSE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "existing_links": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "candidate_no": {"type": "integer", "minimum": 1},
                    "option_id": {"type": "integer", "minimum": 1, "maximum": 5},
                    "confidence": {"type": "integer", "minimum": 0, "maximum": 100},
                },
                "required": ["candidate_no", "option_id", "confidence"],
                "additionalProperties": False,
            },
        },
        "new_groups": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "candidate_nos": {
                        "type": "array",
                        "items": {"type": "integer", "minimum": 1},
                        "minItems": 2,
                    },
                    "representative_candidate_no": {
                        "type": "integer",
                        "minimum": 1,
                    },
                    "confidence": {"type": "integer", "minimum": 0, "maximum": 100},
                },
                "required": [
                    "candidate_nos",
                    "representative_candidate_no",
                    "confidence",
                ],
                "additionalProperties": False,
            },
        },
        "uncertain_nos": {
            "type": "array",
            "items": {"type": "integer", "minimum": 1},
        },
        "conflicts": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "candidate_nos": {
                        "type": "array",
                        "items": {"type": "integer", "minimum": 1},
                        "minItems": 2,
                        "maxItems": 2,
                    },
                    "reason": {
                        "type": "string",
                        "enum": [
                            "date",
                            "numbered_event",
                            "product",
                            "action",
                            "direction",
                            "other",
                        ],
                    },
                },
                "required": ["candidate_nos", "reason"],
                "additionalProperties": False,
            },
        },
    },
    "required": [
        "existing_links",
        "new_groups",
        "uncertain_nos",
        "conflicts",
    ],
    "additionalProperties": False,
}

_SPACE_PATTERN = re.compile(r"\s+")
_NON_TOPIC_PATTERN = re.compile(r"[^0-9a-z가-힣._+-]+", re.IGNORECASE)


@dataclass(frozen=True)
class SparseParseResult:
    existing_links: tuple[dict[str, Any], ...]
    new_groups: tuple[dict[str, Any], ...]
    uncertain_nos: tuple[int, ...]
    conflicts: tuple[dict[str, Any], ...]
    invalid_nos: tuple[int, ...]
    diagnostics: dict[str, int]
    valid: bool
    error_message: str = ""


def clean_text(value: Any) -> str:
    return _SPACE_PATTERN.sub(" ", str(value or "")).strip()


def normalized_topic(value: Any) -> str:
    return _NON_TOPIC_PATTERN.sub(" ", clean_text(value).casefold()).strip()


def conservative_must_merge_profiles(
    left_profile: dict[str, Iterable[str]],
    right_profile: dict[str, Iterable[str]],
) -> str:
    """1차 자동 병합은 충돌 없는 완전 동일 제목만 허용합니다."""
    if must_split_profiles(left_profile, right_profile):
        return ""
    left_titles = set(left_profile.get("title_fingerprints") or ())
    right_titles = set(right_profile.get("title_fingerprints") or ())
    if not left_titles or not right_titles or left_titles.isdisjoint(right_titles):
        return ""
    left_subjects = {
        normalized_topic(value)
        for value in left_profile.get("subjects") or ()
        if normalized_topic(value)
    }
    right_subjects = {
        normalized_topic(value)
        for value in right_profile.get("subjects") or ()
        if normalized_topic(value)
    }
    for left in left_subjects:
        for right in right_subjects:
            if left == right or (
                min(len(left), len(right)) >= 2 and (left in right or right in left)
            ):
                return "exact_title"
    return ""


def candidate_profile(candidate: dict[str, Any]) -> dict[str, Any]:
    profile = dict(candidate.get("safety_profile") or {})
    return profile or dict(build_candidate_safety_profile(candidate))


def _values(profile: dict[str, Any], field: str) -> tuple[str, ...]:
    return tuple(
        sorted(
            {
                normalized_topic(value)
                for value in profile.get(field) or ()
                if normalized_topic(value)
            }
        )
    )


def candidate_topic_sort_key(
    candidate: dict[str, Any],
    *,
    view: str = "title",
) -> tuple[str, ...]:
    profile = candidate_profile(candidate)
    subjects = _values(profile, "subjects")
    products = _values(profile, "products")
    actions = _values(profile, "actions")
    dates = _values(profile, "dates")
    numbered = _values(profile, "numbered_events")
    directions = _values(profile, "directions")
    title = normalized_topic(candidate.get("title"))
    if view == "identity":
        terms = numbered + dates + products + actions + directions + subjects
    elif view == "existing":
        options = tuple(
            normalized_topic(option.get("title"))
            for option in build_existing_option_payload(candidate)
            if normalized_topic(option.get("title"))
        )
        terms = options + products + subjects + actions + dates
    elif view == "event":
        terms = subjects + products + actions + dates + numbered
    else:
        terms = subjects + products + actions + numbered + dates
    return tuple(value for value in terms + (title,) if value)


def select_all_topic_candidates(
    candidates: Iterable[dict[str, Any]],
    *,
    batch_id: str = "cluster_batch_0001",
    max_candidates: int = CLUSTERING_SCAN_CANDIDATE_LIMIT,
    max_request_characters: int | None = None,
) -> list[dict[str, Any]]:
    """요청 크기는 후보 수가 아니라 후속 토큰 분할에서 결정합니다."""
    del batch_id, max_request_characters
    refined = refine_first_stage_candidates(candidates)
    limit = max(
        1,
        min(
            int(max_candidates or CLUSTERING_SCAN_CANDIDATE_LIMIT),
            CLUSTERING_SCAN_CANDIDATE_LIMIT,
        ),
    )
    seen: set[str] = set()
    result: list[dict[str, Any]] = []
    for candidate in refined:
        candidate_id = clean_text(candidate.get("candidate_id"))
        if not candidate_id or candidate_id in seen:
            continue
        seen.add(candidate_id)
        row = dict(candidate)
        row["safety_profile"] = candidate_profile(row)
        result.append(row)
        if len(result) >= limit:
            break
    result.sort(key=lambda row: candidate_topic_sort_key(row, view="title"))
    return result


def _compact_safety(
    candidate: dict[str, Any],
    fields: Sequence[str],
) -> dict[str, list[str]]:
    profile = candidate_profile(candidate)
    result: dict[str, list[str]] = {}
    for field in fields:
        values = [
            clean_text(value)
            for value in profile.get(field) or ()
            if clean_text(value)
        ]
        if values:
            result[field] = values
    return result


def _compact_options(candidate: dict[str, Any], example_limit: int) -> list[dict[str, Any]]:
    return [
        {
            "option_id": int(option.get("option_id") or 0),
            "title": clean_text(option.get("title")),
            "examples": [
                clean_text(value)
                for value in option.get("examples") or ()
                if clean_text(value)
            ][:example_limit],
        }
        for option in build_existing_option_payload(candidate)
    ]


def candidate_payload(
    candidate_no: int,
    candidate: dict[str, Any],
    *,
    view: str,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "candidate_no": int(candidate_no),
        "title": clean_text(candidate.get("title")),
    }
    if view == "title":
        payload["identity"] = _compact_safety(
            candidate,
            ("dates", "numbered_events", "products", "actions", "directions"),
        )
    elif view == "event":
        payload["examples"] = [
            clean_text(value)
            for value in candidate.get("examples") or ()
            if clean_text(value)
        ][:2]
        payload["event"] = _compact_safety(
            candidate,
            ("subjects", "products", "actions", "dates", "numbered_events"),
        )
        options = _compact_options(candidate, 1)
        if options:
            payload["existing_options"] = options
    elif view == "identity":
        payload["identity"] = _compact_safety(
            candidate,
            (
                "dates",
                "numbered_events",
                "products",
                "actions",
                "directions",
                "subjects",
            ),
        )
    else:
        payload["identity"] = _compact_safety(
            candidate,
            (
                "dates",
                "numbered_events",
                "products",
                "actions",
                "directions",
                "subjects",
            ),
        )
        payload["existing_options"] = _compact_options(candidate, 2)
    return payload


def view_instructions(view: str) -> str:
    common = (
        "후보 번호는 이번 요청에서만 유효합니다. 같은 구체적 사건 또는 같은 정보성 주제만 "
        "new_groups에 2개 이상 묶어 반환하세요. 신규 그룹은 candidate_nos 안에서 가장 대표적인 원본 제목의 "
        "candidate_no 하나를 representative_candidate_no로 선택하세요. 입력 title·examples·existing_options의 "
        "문자열을 응답에 다시 쓰지 마세요. 기존 군집과 같을 때만 existing_links에 반환하세요. "
        "existing_links, new_groups, uncertain_nos는 서로 배타적입니다. 같은 candidate_no를 이 세 범주에 "
        "두 번 이상 반환하지 말고 하나의 new_group 안에서도 같은 번호를 반복하지 마세요. conflicts는 병합 차단 "
        "근거이므로 이 배타 규칙의 예외입니다. 불확실한 후보만 uncertain_nos에 반환하고 독립 후보는 어떤 목록에도 "
        "반환하지 마세요. 다른 회차·날짜·제품·행동·방향, 일정과 결과, 정책 발표와 시행, 주가와 목표주가는 합치지 마세요. "
        "실제 병합을 막아야 하는 두 후보만 conflicts에 반환하고 설명 없이 지정 JSON만 반환하세요. "
    )
    suffix = {
        "title": "제목 표현이 달라도 같은 사건을 찾되 단어만 비슷한 경우는 묶지 마세요.",
        "event": "주체·대상·제품·행동의 일치를 중심으로 판단하세요.",
        "identity": "날짜·회차·제품 세대·행동·방향 충돌을 우선 검사하세요.",
        "existing": "제공된 existing_options 안의 option_id만 선택하세요.",
    }[view]
    return common + suffix


def build_sparse_request_text(
    batch_id: str,
    view: str,
    numbered_candidates: Sequence[tuple[int, dict[str, Any]]],
) -> str:
    payload = {
        "batch_id": str(batch_id or "cluster_batch_0001"),
        "view": str(view),
        "candidates": [
            candidate_payload(candidate_no, candidate, view=view)
            for candidate_no, candidate in numbered_candidates
        ],
    }
    return view_instructions(view) + "\n\n" + json.dumps(
        payload,
        ensure_ascii=False,
        separators=(",", ":"),
    )


def _normal_finish_reason(value: str) -> bool:
    return clean_text(value).casefold() in {
        "",
        "stop",
        "completed",
        "success",
        "finish_reason_stop",
    }


def _integer(value: Any, minimum: int, maximum: int) -> int | None:
    try:
        number = int(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return number if minimum <= number <= maximum else None


def parse_sparse_response(
    output_text: str,
    *,
    candidate_by_no: dict[int, dict[str, Any]],
    finish_reason: str = "",
) -> SparseParseResult:
    diagnostics = {
        "invalid_candidate_no": 0,
        "duplicate_candidate_no": 0,
        "invalid_existing_option": 0,
        "invalid_confidence": 0,
        "invalid_new_group": 0,
        "invalid_conflict": 0,
        "response_format_error": 0,
        "abnormal_finish": 0,
    }
    if not _normal_finish_reason(finish_reason):
        diagnostics["abnormal_finish"] = 1
        return SparseParseResult(
            (), (), (), (), (), diagnostics, False, f"비정상 종료: {finish_reason}"
        )
    try:
        parsed = json.loads(output_text)
    except json.JSONDecodeError as exc:
        diagnostics["response_format_error"] = 1
        return SparseParseResult(
            (), (), (), (), (), diagnostics, False, f"희소 응답 JSON 오류: {exc.msg}"
        )
    arrays = (
        parsed.get("existing_links") if isinstance(parsed, dict) else None,
        parsed.get("new_groups") if isinstance(parsed, dict) else None,
        parsed.get("uncertain_nos") if isinstance(parsed, dict) else None,
        parsed.get("conflicts") if isinstance(parsed, dict) else None,
    )
    if not all(isinstance(value, list) for value in arrays):
        diagnostics["response_format_error"] = 1
        return SparseParseResult(
            (), (), (), (), (), diagnostics, False, "희소 응답 필수 배열이 없습니다."
        )
    raw_existing, raw_groups, raw_uncertain, raw_conflicts = arrays

    occurrences: defaultdict[int, int] = defaultdict(int)
    for row in raw_existing:
        if isinstance(row, dict):
            number = _integer(row.get("candidate_no"), 1, 10**9)
            if number is not None:
                occurrences[number] += 1
    for row in raw_groups:
        if isinstance(row, dict):
            for value in row.get("candidate_nos") or ():
                number = _integer(value, 1, 10**9)
                if number is not None:
                    occurrences[number] += 1
    for value in raw_uncertain:
        number = _integer(value, 1, 10**9)
        if number is not None:
            occurrences[number] += 1
    invalid_nos = {number for number, count in occurrences.items() if count > 1}
    diagnostics["duplicate_candidate_no"] = len(invalid_nos)

    existing_links: list[dict[str, Any]] = []
    for row in raw_existing:
        if not isinstance(row, dict):
            diagnostics["response_format_error"] += 1
            continue
        number = _integer(row.get("candidate_no"), 1, 10**9)
        option_id = _integer(row.get("option_id"), 1, 5)
        confidence = _integer(row.get("confidence"), 0, 100)
        if number is None or number not in candidate_by_no:
            diagnostics["invalid_candidate_no"] += 1
            continue
        if number in invalid_nos:
            continue
        if option_id is None or confidence is None:
            diagnostics[
                "invalid_existing_option" if option_id is None else "invalid_confidence"
            ] += 1
            invalid_nos.add(number)
            continue
        cluster_id = resolve_existing_option_id(candidate_by_no[number], option_id)
        if not cluster_id:
            diagnostics["invalid_existing_option"] += 1
            invalid_nos.add(number)
            continue
        existing_links.append(
            {
                "candidate_no": number,
                "option_id": option_id,
                "cluster_id": cluster_id,
                "confidence": confidence,
            }
        )

    new_groups: list[dict[str, Any]] = []
    for row in raw_groups:
        if not isinstance(row, dict):
            diagnostics["invalid_new_group"] += 1
            continue
        numbers = [_integer(value, 1, 10**9) for value in row.get("candidate_nos") or ()]
        valid_numbers = [number for number in numbers if number in candidate_by_no]
        unique_numbers = list(dict.fromkeys(valid_numbers))
        representative_candidate_no = _integer(
            row.get("representative_candidate_no"),
            1,
            10**9,
        )
        representative_title = clean_text(
            candidate_by_no.get(int(representative_candidate_no or 0), {}).get("title")
        )
        confidence = _integer(row.get("confidence"), 0, 100)
        invalid = (
            len(valid_numbers) != len(numbers)
            or len(unique_numbers) != len(valid_numbers)
            or not 2 <= len(unique_numbers) <= CLUSTERING_MAX_SPARSE_GROUP_SIZE
            or any(number in invalid_nos for number in unique_numbers)
            or representative_candidate_no not in unique_numbers
            or not representative_title
            or confidence is None
        )
        if invalid:
            diagnostics["invalid_new_group"] += 1
            invalid_nos.update(unique_numbers)
            continue
        new_groups.append(
            {
                "candidate_nos": tuple(unique_numbers),
                "representative_candidate_no": representative_candidate_no,
                "representative_title": representative_title,
                "confidence": confidence,
            }
        )

    uncertain_nos: set[int] = set(invalid_nos)
    for value in raw_uncertain:
        number = _integer(value, 1, 10**9)
        if number is None or number not in candidate_by_no:
            diagnostics["invalid_candidate_no"] += 1
        else:
            uncertain_nos.add(number)

    conflicts: list[dict[str, Any]] = []
    for row in raw_conflicts:
        if not isinstance(row, dict):
            diagnostics["invalid_conflict"] += 1
            continue
        numbers = [_integer(value, 1, 10**9) for value in row.get("candidate_nos") or ()]
        if (
            len(numbers) != 2
            or any(number not in candidate_by_no for number in numbers)
            or numbers[0] == numbers[1]
        ):
            diagnostics["invalid_conflict"] += 1
            continue
        conflicts.append(
            {
                "candidate_nos": (int(numbers[0]), int(numbers[1])),
                "reason": clean_text(row.get("reason")) or "other",
            }
        )

    return SparseParseResult(
        tuple(existing_links),
        tuple(new_groups),
        tuple(sorted(uncertain_nos)),
        tuple(conflicts),
        tuple(sorted(invalid_nos)),
        diagnostics,
        True,
    )
