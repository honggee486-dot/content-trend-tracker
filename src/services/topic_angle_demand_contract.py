from __future__ import annotations

from collections import Counter
from typing import Any, Callable


DIRECTION_SCORE_LIMITS: dict[str, int] = {
    "search_intent_fit": 35,
    "demand_signal_support": 30,
    "evidence_availability": 20,
    "differentiation": 10,
    "timeliness_practicality": 5,
}


def _clean_text(value: Any) -> str:
    return str(value or "").strip()


def _clean_unique_texts(
    value: Any,
    *,
    min_items: int,
    max_items: int,
    max_length: int,
) -> list[str] | None:
    if not isinstance(value, list):
        return None
    result: list[str] = []
    seen: set[str] = set()
    for item in value:
        text = _clean_text(item)
        key = text.casefold()
        if text and key not in seen:
            seen.add(key)
            result.append(text[:max_length])
    result = result[:max_items]
    return result if min_items <= len(result) <= max_items else None


def _optional_number(value: Any) -> int | float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return int(number) if number.is_integer() else number


def build_evidence_contract(
    items: list[dict[str, Any]],
    *,
    safe_public_text: Callable[[Any, str], str],
    maximum: int = 8,
) -> tuple[list[dict[str, Any]], dict[str, str]]:
    """Build public E1..E8 evidence and an internal ID map.

    URLs and database identifiers never enter the public payload. Numeric fields are
    included only when the source already provided them; no search volume is inferred.
    """
    prepared: list[tuple[dict[str, Any], dict[str, Any], str]] = []
    for item in items:
        metadata = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
        title = safe_public_text(
            metadata.get("item_title") or item.get("raw_title"),
            "근거 제목",
        )
        source_item_id = _clean_text(item.get("source_item_id"))
        if title and source_item_id:
            prepared.append((item, metadata, title))
        if len(prepared) >= max(1, int(maximum)):
            break

    publisher_counts = Counter(
        safe_public_text(item.get("source_name"), "발행처").casefold()
        for item, _metadata, _title in prepared
        if safe_public_text(item.get("source_name"), "발행처")
    )
    source_type_counts = Counter(
        _clean_text(item.get("source_type")).casefold()
        for item, _metadata, _title in prepared
        if _clean_text(item.get("source_type"))
    )

    public_rows: list[dict[str, Any]] = []
    evidence_map: dict[str, str] = {}
    for index, (item, metadata, title) in enumerate(prepared, start=1):
        evidence_id = f"E{index}"
        publisher = safe_public_text(item.get("source_name"), "발행처")
        source_type = _clean_text(item.get("source_type"))
        description = safe_public_text(metadata.get("description"), "근거 설명")[:240]
        discovery_query = safe_public_text(
            metadata.get("discovery_query"),
            "발견 검색어",
        )[:160]
        keyword = safe_public_text(metadata.get("keyword"), "키워드")[:160]

        row: dict[str, Any] = {
            "evidence_id": evidence_id,
            "title": title[:300],
            "publisher": publisher[:160],
            "source_type": source_type[:80],
            "published_at": _clean_text(
                item.get("published_at") or item.get("observed_at")
            ),
            "observation_count": max(1, int(item.get("observation_count") or 1)),
            "publisher_repeat_count": publisher_counts.get(publisher.casefold(), 0),
            "source_type_repeat_count": source_type_counts.get(source_type.casefold(), 0),
        }
        optional_text = {
            "description_snippet": description,
            "discovery_query": discovery_query,
            "keyword": keyword,
            "approximate_interest": _clean_text(metadata.get("approx_traffic"))[:80],
        }
        for key, value in optional_text.items():
            if value:
                row[key] = value

        numeric_candidates = {
            "signal_value": item.get("signal_value"),
            "view_count": metadata.get("view_count", item.get("view_count")),
            "view_delta": metadata.get("view_delta", item.get("view_delta")),
            "views_per_hour": metadata.get(
                "views_per_hour", item.get("views_per_hour")
            ),
            "topic_score": metadata.get("topic_score", item.get("topic_score")),
            "result_rank": metadata.get("result_rank"),
        }
        for key, raw_value in numeric_candidates.items():
            number = _optional_number(raw_value)
            if number is not None:
                row[key] = number

        public_rows.append(row)
        evidence_map[evidence_id] = _clean_text(item.get("source_item_id"))
    return public_rows, evidence_map


def public_cluster_payload(cluster: dict[str, Any]) -> dict[str, Any]:
    return {
        "cluster_id": _clean_text(cluster.get("cluster_id")),
        "topic": _clean_text(cluster.get("topic")),
        "trend_score": float(cluster.get("trend_score") or 0),
        "opportunity_score": float(cluster.get("opportunity_score") or 0),
        "signals": list(cluster.get("signals") or []),
    }


def validate_direction_contract(
    direction: Any,
    *,
    evidence_map: dict[str, str],
) -> tuple[dict[str, Any] | None, str]:
    if not isinstance(direction, dict):
        return None, "방향이 객체가 아닙니다."

    label = _clean_text(direction.get("label"))
    angle = _clean_text(direction.get("angle"))
    rationale = _clean_text(direction.get("rationale"))
    search_intent = _clean_text(direction.get("search_intent"))
    reader_question = _clean_text(direction.get("reader_question"))
    queries = _clean_unique_texts(
        direction.get("search_queries"),
        min_items=1,
        max_items=3,
        max_length=200,
    )
    demand_evidence = _clean_unique_texts(
        direction.get("demand_evidence"),
        min_items=1,
        max_items=3,
        max_length=400,
    )
    evidence_ids = _clean_unique_texts(
        direction.get("evidence_source_ids"),
        min_items=1,
        max_items=3,
        max_length=20,
    )
    score_reasons = _clean_unique_texts(
        direction.get("score_reasons"),
        min_items=1,
        max_items=5,
        max_length=300,
    )
    if not all([label, angle, rationale, search_intent, reader_question]):
        return None, "방향의 필수 설명이 비어 있습니다."
    if queries is None or demand_evidence is None or evidence_ids is None:
        return None, "방향의 검색어·수요 근거·근거 ID가 올바르지 않습니다."
    if score_reasons is None:
        return None, "방향의 점수 이유가 올바르지 않습니다."

    unknown_ids = [item for item in evidence_ids if item not in evidence_map]
    if unknown_ids:
        return None, "요청에 없는 근거 ID가 포함됐습니다: " + ", ".join(unknown_ids)

    raw_breakdown = direction.get("score_breakdown")
    if not isinstance(raw_breakdown, dict):
        return None, "방향 점수 항목이 없습니다."
    breakdown: dict[str, int] = {}
    for key, maximum in DIRECTION_SCORE_LIMITS.items():
        value = raw_breakdown.get(key)
        if isinstance(value, bool):
            return None, f"{key} 점수가 정수가 아닙니다."
        try:
            score = int(value)
        except (TypeError, ValueError):
            return None, f"{key} 점수가 정수가 아닙니다."
        if score < 0 or score > maximum:
            return None, f"{key} 점수는 0~{maximum} 범위여야 합니다."
        breakdown[key] = score

    direction_score = sum(breakdown.values())
    return {
        "label": label[:80],
        "angle": angle[:500],
        "rationale": rationale[:1000],
        "search_queries": queries,
        "search_intent": search_intent[:300],
        "reader_question": reader_question[:500],
        "demand_evidence": demand_evidence,
        "evidence_source_ids": [evidence_map[item] for item in evidence_ids],
        "evidence_request_ids": evidence_ids,
        "score_breakdown": breakdown,
        "direction_score": direction_score,
        "score_reasons": score_reasons,
    }, ""


def stable_score_sort(directions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    indexed = list(enumerate(directions))
    indexed.sort(
        key=lambda item: (-int(item[1].get("direction_score") or 0), item[0])
    )
    return [direction for _index, direction in indexed]


def format_direction_for_request(direction: dict[str, Any]) -> str:
    parts = [_clean_text(direction.get("angle_text") or direction.get("angle"))]
    labeled = [
        ("검색 의도", direction.get("search_intent")),
        ("독자 질문", direction.get("reader_question")),
    ]
    for label, value in labeled:
        text = _clean_text(value)
        if text:
            parts.append(f"{label}: {text}")
    evidence = direction.get("demand_evidence") or []
    clean_evidence = [
        _clean_text(item) for item in evidence if _clean_text(item)
    ]
    if clean_evidence:
        parts.append("수요 근거: " + " / ".join(clean_evidence))
    queries = direction.get("search_queries") or []
    clean_queries = [_clean_text(item) for item in queries if _clean_text(item)]
    if clean_queries:
        parts.append("조사 초점: " + ", ".join(clean_queries))
    return " / ".join(part for part in parts if part)
