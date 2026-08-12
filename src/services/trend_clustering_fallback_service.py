from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime
import re
from typing import Any

_EVENT_CONTEXT_TERMS = {
    "발표", "출시", "공개", "협력", "투자", "인수", "합병", "상승", "하락",
    "급등", "급락", "종영", "결승", "승리", "패배", "논란", "사과", "체포",
    "기소", "지원", "신청", "변경", "인상", "인하", "발령", "복귀", "회동",
    "선언", "계약", "공급", "도입", "확대", "축소", "중단", "재개", "매진",
    "1위", "개편", "전환", "추진", "예정", "확정", "변화", "기능", "동맹",
    "파트너십", "회담", "면담", "도착", "참석", "업데이트", "종료",
}
_EVENT_CONTEXT_TERMS_FOLDED = {term.casefold() for term in _EVENT_CONTEXT_TERMS}
_EVENT_MONEY_PATTERN = re.compile(
    r"^(?P<value>\d+(?:\.\d+)?)(?P<scale>만|억|조)(?:원|달러|불)?$"
)
_NANOMETER_PATTERN = re.compile(r"^(?P<value>\d+)(?:nm|나노)$", re.IGNORECASE)
_COLLABORATION_TERMS = {
    "협력", "동맹", "파트너십", "맞손", "손잡았다", "손잡아", "연합",
}
_EVENT_SUBJECT_TERMS = {
    "반도체", "파운드리", "메모리", "데이터센터", "로봇", "자율주행",
    "공급망", "플랫폼", "인프라", "배터리",
}
_KOREAN_EVENT_ANCHOR_PATTERNS = {
    "police": ("경찰",),
    "arrest": ("체포",),
    "self_surrender": ("자수", "자진출석"),
    "suspect": ("피의자",),
    "document": ("서류",),
    "fabrication": ("조작",),
    "indictment": ("기소", "재판행"),
}


def _event_evidence_signatures(tokens: set[str], service: Any) -> set[str]:
    signatures: set[str] = set()
    for token in tokens:
        folded = token.casefold()
        if folded in _COLLABORATION_TERMS:
            signatures.add("action:collaboration")
        elif folded in _EVENT_CONTEXT_TERMS_FOLDED:
            signatures.add(f"action:{folded}")
        if folded in _EVENT_SUBJECT_TERMS:
            signatures.add(f"subject:{folded}")

        money = _EVENT_MONEY_PATTERN.fullmatch(folded.replace(",", ""))
        if money:
            signatures.add(f"amount:{money.group('value')}{money.group('scale')}")
        nanometer = _NANOMETER_PATTERN.fullmatch(folded)
        if nanometer:
            signatures.add(f"product:{nanometer.group('value')}nm")
        elif service._PRODUCT_IDENTITY_PATTERN.fullmatch(folded):
            signatures.add(f"product:{folded}")
        elif (
            folded.isascii()
            and folded.isalnum()
            and 3 <= len(folded) <= 8
            and folded not in service.GENERIC_IDENTITY_TERMS
        ):
            signatures.add(f"term:{folded}")
    return signatures


def _is_event_evidence_token(token: str, service: Any) -> bool:
    return bool(_event_evidence_signatures({token}, service))


def _korean_event_anchors(value: str, service: Any) -> set[str]:
    """한국어 어미가 달라도 같은 사건의 핵심 표지를 좁게 비교합니다."""
    compact = service.compact_title(value)
    return {
        anchor
        for anchor, patterns in _KOREAN_EVENT_ANCHOR_PATTERNS.items()
        if any(pattern in compact for pattern in patterns)
    }


def _item_similarity(
    left: dict[str, Any],
    right: dict[str, Any],
    service: Any,
) -> float:
    if left.get("normalized_url") and left["normalized_url"] == right.get("normalized_url"):
        return 1.0

    left_normalized = str(left.get("normalized_title") or "")
    right_normalized = str(right.get("normalized_title") or "")
    left_ids = set(left.get("identity_tokens") or ())
    right_ids = set(right.get("identity_tokens") or ())
    left_editorial = set(
        left.get("editorial_identity_tokens") or service._editorial_identity_tokens(left_ids)
    )
    right_editorial = set(
        right.get("editorial_identity_tokens") or service._editorial_identity_tokens(right_ids)
    )
    left_calendar = set(
        left.get("calendar_identity_tokens") or service._calendar_identity_tokens(left_ids)
    )
    right_calendar = set(
        right.get("calendar_identity_tokens") or service._calendar_identity_tokens(right_ids)
    )

    if service._has_conflicting_calendar_identity(left_calendar, right_calendar):
        return 0.0
    if service._has_conflicting_numbered_identity(left_ids, right_ids):
        return 0.0
    if service._has_conflicting_event_facts(left_editorial, right_editorial):
        return 0.0

    if left_normalized and left_normalized == right_normalized and left_editorial:
        return 0.98

    left_compact = str(left.get("compact_title") or "")
    right_compact = str(right.get("compact_title") or "")
    if min(len(left_compact), len(right_compact)) >= 5:
        if left_compact == right_compact and left_editorial:
            return 0.96

    left_ids = left_editorial
    right_ids = right_editorial
    shared = left_ids & right_ids
    score = 0.0
    if service._has_shared_numbered_context(left_ids, right_ids):
        score = max(score, 0.86)
    if shared:
        coverage = len(shared) / max(1, min(len(left_ids), len(right_ids)))
        jaccard = service._jaccard(left_ids, right_ids)
        if len(shared) >= 2 and coverage >= 0.70:
            score = max(score, 0.88)
        elif len(shared) >= 2 and jaccard >= 0.45:
            score = max(score, 0.76)
        elif len(shared) == 1 and coverage == 1.0:
            shared_token = next(iter(shared))
            if len(shared_token) >= 4 and any(char.isdigit() for char in shared_token):
                score = max(score, 0.80)

        if min(len(left_compact), len(right_compact)) >= 5 and (
            left_compact in right_compact or right_compact in left_compact
        ):
            score = max(score, 0.86)

        sequence_ratio = service._string_similarity(left_normalized, right_normalized)
        if sequence_ratio >= 0.84:
            score = max(score, sequence_ratio)
        compact_ratio = service._string_similarity(left_compact, right_compact)
        if len(shared) >= 2 and compact_ratio >= 0.55:
            score = max(score, 0.74 + (compact_ratio - 0.55) * 0.5)

    left_query = service.normalize_title(str(left.get("query") or ""))
    right_query = service.normalize_title(str(right.get("query") or ""))
    query_identities = service._editorial_identity_tokens(left_query)
    shared_non_query = shared - query_identities
    entity_only_query = service._is_entity_only_title(left_query, query_identities)
    entity_query_event_support = bool(
        len(shared_non_query) >= 2
        and any(_is_event_evidence_token(token, service) for token in shared_non_query)
    )
    if (
        left_query
        and left_query == right_query
        and left.get("query_supported")
        and right.get("query_supported")
        and shared
        and (
            (len(query_identities) >= 2 and not entity_only_query)
            or entity_query_event_support
            or (bool(shared_non_query) and not entity_only_query)
            or service._has_versioned_identity(query_identities)
        )
    ):
        score = max(score, 0.90)

    left_event = _korean_event_anchors(str(left.get("canonical_title") or ""), service)
    right_event = _korean_event_anchors(str(right.get("canonical_title") or ""), service)
    shared_event = left_event & right_event
    event_age_hours = abs(
        (service._item_time(left) - service._item_time(right)).total_seconds()
    ) / 3600
    if (
        len(shared_event) >= 3
        and {"police", "arrest"} <= shared_event
        and shared_event & {"self_surrender", "document", "fabrication"}
        and event_age_hours <= 72
    ):
        score = max(score, 0.84)
    return score


def _merge_deterministic_fragments(
    clusters: list[dict[str, Any]],
    service: Any,
) -> list[dict[str, Any]]:
    """강한 사건 공통 근거가 있는 폴백 조각만 보수적으로 합칩니다."""
    if len(clusters) < 2:
        return clusters

    cluster_tokens = [
        set().union(
            *(set(item.get("editorial_identity_tokens") or ()) for item in cluster["items"])
        )
        for cluster in clusters
    ]
    cluster_event_signatures = [
        _event_evidence_signatures(tokens, service) for tokens in cluster_tokens
    ]
    token_clusters: dict[str, list[int]] = defaultdict(list)
    for index, tokens in enumerate(cluster_tokens):
        for token in tokens:
            token_clusters[token].append(index)

    pair_shared_counts: Counter[tuple[int, int]] = Counter()
    for indexes in token_clusters.values():
        if len(indexes) > 80:
            continue
        ordered = sorted(indexes)
        for position, left in enumerate(ordered):
            for right in ordered[position + 1 :]:
                pair_shared_counts[(left, right)] += 1

    merge_candidates: list[tuple[float, int, int]] = []
    for (left, right), shared_count in pair_shared_counts.items():
        if shared_count < 3:
            continue
        common_event_signatures = (
            cluster_event_signatures[left] & cluster_event_signatures[right]
        )
        if len(common_event_signatures) < 2:
            continue
        strong_fact_overlap = bool(
            any(
                signature.startswith(("amount:", "product:"))
                for signature in common_event_signatures
            )
            or sum(
                signature.startswith("subject:")
                for signature in common_event_signatures
            )
            >= 2
        )
        if (
            min(len(clusters[left]["items"]), len(clusters[right]["items"])) > 3
            and not strong_fact_overlap
        ):
            continue
        if service._has_conflicting_event_facts(
            cluster_tokens[left], cluster_tokens[right]
        ):
            continue
        left_items = clusters[left]["items"]
        right_items = clusters[right]["items"]
        left_sample = left_items if len(left_items) <= 12 else left_items[:6] + left_items[-6:]
        right_sample = right_items if len(right_items) <= 12 else right_items[:6] + right_items[-6:]
        similarity = max(
            (
                _item_similarity(left_item, right_item, service)
                for left_item in left_sample
                for right_item in right_sample
            ),
            default=0.0,
        )
        if similarity >= 0.76:
            merge_candidates.append((similarity, left, right))

    parents = list(range(len(clusters)))
    root_items = {index: list(cluster["items"]) for index, cluster in enumerate(clusters)}
    root_tokens = {index: set(tokens) for index, tokens in enumerate(cluster_tokens)}

    def find(index: int) -> int:
        while parents[index] != index:
            parents[index] = parents[parents[index]]
            index = parents[index]
        return index

    for _, left, right in sorted(merge_candidates, reverse=True):
        left_root = find(left)
        right_root = find(right)
        if left_root == right_root:
            continue
        if service._has_conflicting_event_facts(
            root_tokens[left_root], root_tokens[right_root]
        ):
            continue
        parents[right_root] = left_root
        root_items[left_root].extend(root_items.pop(right_root))
        root_tokens[left_root].update(root_tokens.pop(right_root))

    merged: list[dict[str, Any]] = []
    emitted: set[int] = set()
    for index in range(len(clusters)):
        root = find(index)
        if root in emitted:
            continue
        emitted.add(root)
        items = sorted(root_items[root], key=service._item_time, reverse=True)
        merged.append(
            {
                "items": items,
                "latest_time": max(
                    (service._item_time(item) for item in items),
                    default=datetime.min,
                ),
            }
        )
    return merged


def cluster_items_deterministically(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Gemini를 실행할 수 없을 때 기존 순위 품질을 보존하는 비상 폴백입니다."""
    from src.services import trend_discovery_service as service

    clusters: list[dict[str, Any]] = []
    token_index: dict[str, set[int]] = defaultdict(set)
    event_index: dict[str, set[int]] = defaultdict(set)
    exact_title_index: dict[str, set[int]] = defaultdict(set)
    query_index: dict[str, set[int]] = defaultdict(set)
    url_index: dict[str, set[int]] = defaultdict(set)
    strong_numbered_index: dict[str, set[int]] = defaultdict(set)

    ordered_items = sorted(
        items,
        key=lambda item: (service._item_time(item), str(item.get("source_item_id") or "")),
        reverse=True,
    )
    token_frequency: Counter[str] = Counter(
        token
        for item in ordered_items
        for token in set(item.get("editorial_identity_tokens") or ())
    )
    max_block_frequency = max(20, min(150, int(len(ordered_items) * 0.015) or 20))

    def blocking_tokens(item: dict[str, Any]) -> list[str]:
        tokens = set(item.get("editorial_identity_tokens") or ())
        selective = [token for token in tokens if token_frequency[token] <= max_block_frequency]
        selective.sort(key=lambda token: (token_frequency[token], -len(token), token))
        return selective[:5]

    for item in ordered_items:
        candidates: set[int] = set()
        priority_candidates: set[int] = set()
        item_blocking_tokens = blocking_tokens(item)
        for token in item_blocking_tokens:
            candidates.update(token_index[token])
        item_event_anchors = _korean_event_anchors(
            str(item.get("canonical_title") or ""), service
        )
        for anchor in item_event_anchors:
            candidates.update(event_index[anchor])
        normalized = str(item.get("normalized_title") or "")
        item_identities = set(item.get("editorial_identity_tokens") or ())
        item_strong_numbers = service._strong_numbered_identity_tokens(item_identities)
        for numbered_token in item_strong_numbers:
            priority_candidates.update(strong_numbered_index[numbered_token])
        if normalized and item_identities:
            priority_candidates.update(exact_title_index[normalized])
        normalized_url = str(item.get("normalized_url") or "")
        if normalized_url:
            priority_candidates.update(url_index[normalized_url])
        candidates.update(priority_candidates)
        if item.get("query_supported"):
            candidates.update(
                query_index[service.normalize_title(str(item.get("query") or ""))]
            )

        if len(candidates) > 180:
            item_time = service._item_time(item)
            ordered_priority = sorted(
                priority_candidates,
                key=lambda index: abs(
                    (item_time - clusters[index]["latest_time"]).total_seconds()
                ),
            )[:180]
            remaining_limit = max(0, 180 - len(ordered_priority))
            ordered_remaining = sorted(
                candidates - set(ordered_priority),
                key=lambda index: abs(
                    (item_time - clusters[index]["latest_time"]).total_seconds()
                ),
            )[:remaining_limit]
            candidates = set(ordered_priority) | set(ordered_remaining)

        best_index: int | None = None
        best_score = 0.0
        for index in sorted(candidates):
            cluster_items = clusters[index]["items"]
            comparison_items = (
                cluster_items
                if len(cluster_items) <= 12
                else cluster_items[:6] + cluster_items[-6:]
            )
            similarity = max(
                (_item_similarity(item, other, service) for other in comparison_items),
                default=0.0,
            )
            if similarity > best_score:
                best_index, best_score = index, similarity

        if best_index is None or best_score < 0.72:
            best_index = len(clusters)
            clusters.append({"items": [item], "latest_time": service._item_time(item)})
        else:
            clusters[best_index]["items"].append(item)
            if service._item_time(item) > clusters[best_index]["latest_time"]:
                clusters[best_index]["latest_time"] = service._item_time(item)

        for token in item_blocking_tokens:
            token_index[token].add(best_index)
        for anchor in item_event_anchors:
            event_index[anchor].add(best_index)
        if normalized and item_identities:
            exact_title_index[normalized].add(best_index)
        if normalized_url:
            url_index[normalized_url].add(best_index)
        for numbered_token in item_strong_numbers:
            strong_numbered_index[numbered_token].add(best_index)
        if item.get("query_supported"):
            query_index[service.normalize_title(str(item.get("query") or ""))].add(best_index)

    clusters = _merge_deterministic_fragments(clusters, service)
    for cluster in clusters:
        cluster["title"] = service._generate_topic_title(cluster["items"])
        cluster.pop("latest_time", None)
    return clusters


def calculate_item_similarity(
    left: dict[str, Any],
    right: dict[str, Any],
) -> float:
    """읽기 전용 군집 진단에서 사용하는 비상 유사도 계산입니다."""
    from src.services import trend_discovery_service as service

    return _item_similarity(left, right, service)
