from __future__ import annotations

from datetime import datetime
import hashlib
import re
from typing import Any, Iterable


_NUMBERED_EVENT_PATTERN = re.compile(r"(?<!\d)(\d{1,4})(회|차)(?!\d)")
_DATE_PATTERNS = (
    re.compile(r"(?<!\d)(\d{4})[./-](\d{1,2})[./-](\d{1,2})(?!\d)"),
    re.compile(r"(?:(\d{4})년\s*)?(\d{1,2})월\s*(\d{1,2})일"),
)
_PRODUCT_PATTERN = re.compile(
    r"(?<![A-Za-z0-9])(?=[A-Za-z0-9._+-]*[A-Za-z])(?=[A-Za-z0-9._+-]*\d)"
    r"[A-Za-z0-9]+(?:[._+-][A-Za-z0-9]+)*(?![A-Za-z0-9])",
    re.IGNORECASE,
)
_WORD_PATTERN = re.compile(r"[가-힣A-Za-z0-9][가-힣A-Za-z0-9._+-]*")
_SPACE_PATTERN = re.compile(r"\s+")

_ACTION_PATTERNS: dict[str, tuple[str, ...]] = {
    "stock_price": ("주가", "급등", "급락", "상승", "하락", "강세", "약세"),
    "target_price": ("목표주가", "투자의견"),
    "earnings": ("실적", "매출", "영업이익", "순이익"),
    "product_release": ("제품 출시", "신제품", "출시", "공개"),
    "factory_investment": ("공장", "증설", "생산시설", "설비투자", "공장 투자"),
    "schedule": ("일정", "예정", "개막", "경기 시간", "언제"),
    "result": ("결과", "승리", "패배", "우승", "스코어", "득점"),
    "policy_announcement": ("정책 발표", "발표", "추진", "예고"),
    "policy_effective": ("시행", "적용", "발효"),
    "application": ("신청", "접수", "모집"),
}
_ACTION_CONFLICT_PAIRS = {
    frozenset(("stock_price", "target_price")),
    frozenset(("stock_price", "earnings")),
    frozenset(("target_price", "earnings")),
    frozenset(("product_release", "factory_investment")),
    frozenset(("schedule", "result")),
    frozenset(("policy_announcement", "policy_effective")),
}
_UPWARD_TERMS = ("상승", "급등", "반등", "강세", "오름")
_DOWNWARD_TERMS = ("하락", "급락", "약세", "내림")
_TIME_SCOPED_ACTIONS = {"schedule", "result", "policy_effective", "application"}
_GENERIC_SUBJECT_TERMS = {
    "관련", "소식", "정보", "정리", "오늘", "내일", "어제", "최신", "뉴스",
    "브리핑", "발표", "공개", "출시", "결과", "일정", "예정", "신청", "방법",
    "업데이트", "주가", "상승", "하락", "급등", "급락", "실적", "정책",
}


def _clean_text(value: Any) -> str:
    return _SPACE_PATTERN.sub(" ", str(value or "")).strip()


def _normalized_text(value: Any) -> str:
    return re.sub(r"[^0-9a-z가-힣]+", "", _clean_text(value).casefold())


def _title_fingerprint(value: Any) -> str:
    normalized = _normalized_text(value)
    if not normalized:
        return ""
    return hashlib.sha1(normalized.encode("utf-8")).hexdigest()[:20]


def _item_title(item: dict[str, Any]) -> str:
    for key in ("canonical_title", "item_title", "raw_title", "title"):
        value = _clean_text(item.get(key))
        if value:
            return value
    return ""


def _candidate_item_titles(candidate: dict[str, Any]) -> list[str]:
    return [
        title
        for title in (
            _item_title(item)
            for item in candidate.get("items") or ()
            if isinstance(item, dict)
        )
        if title
    ]


def _candidate_text(candidate: dict[str, Any]) -> str:
    item_titles = _candidate_item_titles(candidate)
    values = item_titles or [
        _clean_text(candidate.get("title")),
        *(_clean_text(value) for value in candidate.get("examples") or ()),
    ]
    return " ".join(value for value in values if value)


def _token_values(candidate: dict[str, Any]) -> set[str]:
    tokens: set[str] = set()
    for key in ("identity_tokens", "editorial_tokens", "calendar_tokens"):
        tokens.update(
            _clean_text(value).casefold()
            for value in candidate.get(key) or ()
            if _clean_text(value)
        )
    for item in candidate.get("items") or ():
        if not isinstance(item, dict):
            continue
        for key in (
            "identity_tokens",
            "editorial_identity_tokens",
            "calendar_identity_tokens",
        ):
            tokens.update(
                _clean_text(value).casefold()
                for value in item.get(key) or ()
                if _clean_text(value)
            )
    return tokens


def _dates(text: str, tokens: set[str]) -> set[str]:
    values: set[str] = set()
    for pattern in _DATE_PATTERNS:
        for match in pattern.finditer(text):
            year, month, day = match.groups()
            values.add(f"{year or '----'}-{int(month):02d}-{int(day):02d}")
    values.update(
        token
        for token in tokens
        if re.fullmatch(r"(?:\d{4}년)?\d{1,2}월\d{1,2}일", token)
    )
    return values


def _numbered_events(text: str, tokens: set[str]) -> set[str]:
    values = {
        f"{match.group(1)}{match.group(2)}"
        for match in _NUMBERED_EVENT_PATTERN.finditer(text)
    }
    values.update(
        token for token in tokens if re.fullmatch(r"\d{1,4}(?:회|차)", token)
    )
    return values


def _products(text: str, tokens: set[str]) -> set[str]:
    values = {match.group(0).casefold() for match in _PRODUCT_PATTERN.finditer(text)}
    values.update(token for token in tokens if _PRODUCT_PATTERN.fullmatch(token))
    return values


def _actions(text: str) -> set[str]:
    folded = text.casefold()
    return {
        action
        for action, patterns in _ACTION_PATTERNS.items()
        if any(pattern.casefold() in folded for pattern in patterns)
    }


def _directions(text: str) -> set[str]:
    folded = text.casefold()
    values: set[str] = set()
    if any(term in folded for term in _UPWARD_TERMS):
        values.add("up")
    if any(term in folded for term in _DOWNWARD_TERMS):
        values.add("down")
    return values


def _subjects(text: str, tokens: set[str]) -> set[str]:
    values = set(tokens)
    values.update(match.group(0).casefold() for match in _WORD_PATTERN.finditer(text))
    filtered = {
        value
        for value in values
        if 2 <= len(value) <= 64
        and value not in _GENERIC_SUBJECT_TERMS
        and not _NUMBERED_EVENT_PATTERN.fullmatch(value)
        and not any(pattern.fullmatch(value) for pattern in _DATE_PATTERNS)
        and value not in _UPWARD_TERMS
        and value not in _DOWNWARD_TERMS
    }
    return set(sorted(filtered, key=lambda value: (-len(value), value))[:12])


def build_candidate_safety_profile(candidate: dict[str, Any]) -> dict[str, tuple[str, ...]]:
    text = _candidate_text(candidate)
    tokens = _token_values(candidate)
    item_titles = _candidate_item_titles(candidate)
    # 예시는 보조 근거이며 완전 동일 제목의 식별자로 사용하지 않습니다.
    title_values = item_titles or [candidate.get("title")]
    return {
        "dates": tuple(sorted(_dates(text, tokens))),
        "numbered_events": tuple(sorted(_numbered_events(text, tokens))),
        "products": tuple(sorted(_products(text, tokens))),
        "actions": tuple(sorted(_actions(text))),
        "directions": tuple(sorted(_directions(text))),
        "subjects": tuple(sorted(_subjects(text, tokens))),
        "title_fingerprints": tuple(
            sorted(
                {
                    fingerprint
                    for fingerprint in (_title_fingerprint(value) for value in title_values)
                    if fingerprint
                }
            )
        ),
    }


def _profile_sets(profile: dict[str, Iterable[str]]) -> dict[str, set[str]]:
    keys = (
        "dates",
        "numbered_events",
        "products",
        "actions",
        "directions",
        "subjects",
        "title_fingerprints",
    )
    return {key: set(profile.get(key) or ()) for key in keys}


def _subject_overlap(left: set[str], right: set[str]) -> int:
    matched: set[tuple[str, str]] = set()
    for left_value in left:
        for right_value in right:
            if left_value == right_value or (
                min(len(left_value), len(right_value)) >= 2
                and (left_value in right_value or right_value in left_value)
            ):
                matched.add((left_value, right_value))
    return len(matched)


def _action_conflict(left: set[str], right: set[str]) -> str:
    for pair in _ACTION_CONFLICT_PAIRS:
        first, second = tuple(pair)
        if (
            first in left
            and second in right
            and not ({first, second} <= left or {first, second} <= right)
        ) or (
            second in left
            and first in right
            and not ({first, second} <= left or {first, second} <= right)
        ):
            return "action"
    return ""


def must_split_profiles(
    left_profile: dict[str, Iterable[str]],
    right_profile: dict[str, Iterable[str]],
) -> str:
    left = _profile_sets(left_profile)
    right = _profile_sets(right_profile)
    shared_subjects = _subject_overlap(left["subjects"], right["subjects"])

    if left["directions"] and right["directions"] and left["directions"].isdisjoint(
        right["directions"]
    ):
        return "direction"
    action_conflict = _action_conflict(left["actions"], right["actions"])
    if action_conflict:
        return action_conflict
    if (
        left["numbered_events"]
        and right["numbered_events"]
        and left["numbered_events"].isdisjoint(right["numbered_events"])
        and shared_subjects >= 1
    ):
        return "numbered_event"
    if (
        left["products"]
        and right["products"]
        and left["products"].isdisjoint(right["products"])
        and shared_subjects >= 1
    ):
        return "product"
    time_scoped = bool(
        (left["actions"] | right["actions"]) & _TIME_SCOPED_ACTIONS
        or left["numbered_events"]
        or right["numbered_events"]
    )
    if (
        time_scoped
        and left["dates"]
        and right["dates"]
        and left["dates"].isdisjoint(right["dates"])
        and shared_subjects >= 1
    ):
        return "date"
    return ""


def must_merge_profiles(
    left_profile: dict[str, Iterable[str]],
    right_profile: dict[str, Iterable[str]],
) -> str:
    if must_split_profiles(left_profile, right_profile):
        return ""
    left = _profile_sets(left_profile)
    right = _profile_sets(right_profile)
    shared_subjects = _subject_overlap(left["subjects"], right["subjects"])
    if (
        left["title_fingerprints"]
        and right["title_fingerprints"]
        and left["title_fingerprints"] & right["title_fingerprints"]
        and shared_subjects >= 1
    ):
        return "exact_title"
    if left["numbered_events"] & right["numbered_events"] and shared_subjects >= 1:
        return "numbered_event"
    if (
        left["products"] & right["products"]
        and left["actions"] & right["actions"]
        and shared_subjects >= 1
    ):
        return "product_action"
    if (
        left["dates"] & right["dates"]
        and left["actions"] & right["actions"]
        and shared_subjects >= 2
    ):
        return "dated_action"
    return ""


def _source_ids(items: Iterable[dict[str, Any]]) -> list[str]:
    return sorted(
        {
            _clean_text(item.get("source_item_id"))
            for item in items
            if _clean_text(item.get("source_item_id"))
        }
    )


def _stable_candidate_id(
    parents: Iterable[dict[str, Any]],
    items: Iterable[dict[str, Any]],
) -> str:
    item_ids = _source_ids(items)
    parent_ids = sorted(
        {
            _clean_text(parent.get("candidate_id"))
            for parent in parents
            if _clean_text(parent.get("candidate_id"))
        }
    )
    key = "|".join(item_ids or parent_ids)
    return "stage1s_" + hashlib.sha1(key.encode("utf-8")).hexdigest()[:18]


def _item_datetime(item: dict[str, Any]) -> Any:
    for key in ("published_at", "observed_at", "imported_at"):
        value = item.get(key)
        if value is not None:
            return value
    return datetime.min


def _unique(values: Iterable[Any], *, limit: int | None = None) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        clean = _clean_text(value)
        if not clean or clean in seen:
            continue
        seen.add(clean)
        result.append(clean)
        if limit is not None and len(result) >= limit:
            break
    return result


def _filter_existing_options(
    profile: dict[str, Iterable[str]],
    options: Iterable[dict[str, Any]],
) -> tuple[dict[str, Any], ...]:
    filtered: list[dict[str, Any]] = []
    seen: set[str] = set()
    for option in options:
        cluster_id = _clean_text(option.get("cluster_id"))
        if not cluster_id or cluster_id in seen:
            continue
        option_profile = build_candidate_safety_profile(
            {
                "title": option.get("title"),
                "examples": option.get("examples") or (),
            }
        )
        if must_split_profiles(profile, option_profile):
            continue
        seen.add(cluster_id)
        filtered.append(dict(option))
        if len(filtered) >= 5:
            break
    return tuple(filtered)


def _compose_candidate(
    parents: list[dict[str, Any]],
    items: list[dict[str, Any]],
    *,
    rule_ids: Iterable[str],
    first_stage_kind: str,
    preserve_parent_id: bool = False,
) -> dict[str, Any]:
    base = dict(parents[0]) if parents else {}
    ordered_items = sorted(items, key=lambda item: str(_item_datetime(item)), reverse=True)
    base["items"] = ordered_items
    base["item_count"] = len(ordered_items) or int(base.get("item_count") or 0)
    if not preserve_parent_id:
        base["candidate_id"] = _stable_candidate_id(parents, ordered_items)
    base["first_stage_kind"] = first_stage_kind
    if first_stage_kind == "rule_split" and ordered_items:
        base["title"] = _item_title(ordered_items[0])
        example_values: list[Any] = [_item_title(item) for item in ordered_items]
    else:
        base["title"] = next(
            (
                _clean_text(parent.get("title"))
                for parent in parents
                if _clean_text(parent.get("title"))
            ),
            _item_title(ordered_items[0]) if ordered_items else "",
        )
        example_values = [
            *(value for parent in parents for value in parent.get("examples") or ()),
            *(_item_title(item) for item in ordered_items),
        ]
    base["examples"] = tuple(_unique(example_values, limit=3))
    base["source_types"] = tuple(
        sorted(
            {
                _clean_text(value)
                for parent in parents
                for value in parent.get("source_types") or ()
                if _clean_text(value)
            }
            | {
                _clean_text(item.get("source_type"))
                for item in ordered_items
                if _clean_text(item.get("source_type"))
            }
        )
    )
    base["publishers"] = tuple(
        sorted(
            {
                _clean_text(value)
                for parent in parents
                for value in parent.get("publishers") or ()
                if _clean_text(value)
            }
            | {
                _clean_text(item.get("source_name") or item.get("domain"))
                for item in ordered_items
                if _clean_text(item.get("source_name") or item.get("domain"))
            }
        )
    )
    inherit_parent_tokens = first_stage_kind != "rule_split"
    for target_key, source_keys in (
        ("identity_tokens", ("identity_tokens",)),
        ("editorial_tokens", ("editorial_tokens", "editorial_identity_tokens")),
        ("calendar_tokens", ("calendar_tokens", "calendar_identity_tokens")),
    ):
        values: set[str] = set()
        if inherit_parent_tokens:
            for parent in parents:
                values.update(
                    _clean_text(value).casefold()
                    for value in parent.get(target_key) or ()
                    if _clean_text(value)
                )
        for item in ordered_items:
            for source_key in source_keys:
                values.update(
                    _clean_text(value).casefold()
                    for value in item.get(source_key) or ()
                    if _clean_text(value)
                )
        base[target_key] = values
    if ordered_items:
        times = [_item_datetime(item) for item in ordered_items]
        base["first_seen_at"] = min(times, key=str)
        base["last_seen_at"] = max(times, key=str)

    inherited_rules = {
        _clean_text(value)
        for parent in parents
        for value in parent.get("first_stage_rule_ids") or ()
        if _clean_text(value) and _clean_text(value) != "undetermined"
    }
    inherited_rules.update(_clean_text(value) for value in rule_ids if _clean_text(value))
    base["first_stage_rule_ids"] = tuple(sorted(inherited_rules or {"undetermined"}))
    profile = build_candidate_safety_profile(base)
    base["safety_profile"] = profile
    options = [
        dict(option)
        for parent in parents
        for option in parent.get("existing_cluster_candidates") or ()
    ]
    base["existing_cluster_candidates"] = _filter_existing_options(profile, options)
    base["safety_refined"] = True
    return base


def _split_candidate(candidate: dict[str, Any]) -> list[dict[str, Any]]:
    items = [item for item in candidate.get("items") or () if isinstance(item, dict)]
    if len(items) <= 1:
        return [
            _compose_candidate(
                [candidate],
                items,
                rule_ids=(),
                first_stage_kind=str(candidate.get("first_stage_kind") or "single"),
                preserve_parent_id=True,
            )
        ]

    safe_parent_group = str(candidate.get("first_stage_kind") or "") in {
        "same_url",
        "same_title",
    }
    groups: list[list[dict[str, Any]]] = []
    profiles: list[dict[str, tuple[str, ...]]] = []
    split_reasons: set[str] = set()
    for item in items:
        piece_profile = build_candidate_safety_profile({"items": [item]})
        selected_index: int | None = None
        for index, profile in enumerate(profiles):
            conflict = must_split_profiles(profile, piece_profile)
            if conflict:
                split_reasons.add(conflict)
                continue
            if safe_parent_group or must_merge_profiles(profile, piece_profile):
                selected_index = index
                break
        if selected_index is None:
            groups.append([item])
            profiles.append(piece_profile)
        else:
            groups[selected_index].append(item)
            profiles[selected_index] = build_candidate_safety_profile(
                {"items": groups[selected_index]}
            )

    if len(groups) == 1:
        return [
            _compose_candidate(
                [candidate],
                groups[0],
                rule_ids=("must_merge:original_safe_group",),
                first_stage_kind=str(candidate.get("first_stage_kind") or "single"),
                preserve_parent_id=True,
            )
        ]

    rules = tuple(f"must_split:{reason}" for reason in sorted(split_reasons)) or (
        "must_split:hard_conflict",
    )
    return [
        _compose_candidate(
            [candidate],
            group,
            rule_ids=rules,
            first_stage_kind="rule_split",
        )
        for group in groups
    ]


def refine_first_stage_candidates(
    candidates: Iterable[dict[str, Any]],
) -> list[dict[str, Any]]:
    source = [dict(candidate) for candidate in candidates]
    if not source:
        return []
    if all(bool(candidate.get("safety_refined")) for candidate in source):
        return source

    split_candidates = [
        refined
        for candidate in source
        for refined in _split_candidate(candidate)
    ]
    merged_groups: list[list[dict[str, Any]]] = []
    merged_profiles: list[dict[str, tuple[str, ...]]] = []
    merge_rules: list[set[str]] = []
    for candidate in split_candidates:
        profile = build_candidate_safety_profile(candidate)
        selected_index: int | None = None
        selected_rule = ""
        for index, group_profile in enumerate(merged_profiles):
            rule = must_merge_profiles(group_profile, profile)
            if rule and not must_split_profiles(group_profile, profile):
                selected_index = index
                selected_rule = rule
                break
        if selected_index is None:
            merged_groups.append([candidate])
            merged_profiles.append(profile)
            merge_rules.append(set())
            continue
        merged_groups[selected_index].append(candidate)
        merge_rules[selected_index].add(selected_rule)
        combined = _compose_candidate(
            merged_groups[selected_index],
            [
                item
                for member in merged_groups[selected_index]
                for item in member.get("items") or ()
            ],
            rule_ids=(f"must_merge:{value}" for value in merge_rules[selected_index]),
            first_stage_kind="rule_merge",
        )
        merged_profiles[selected_index] = build_candidate_safety_profile(combined)

    result: list[dict[str, Any]] = []
    for group, rules in zip(merged_groups, merge_rules, strict=True):
        if len(group) == 1:
            result.append(group[0])
            continue
        result.append(
            _compose_candidate(
                group,
                [item for member in group for item in member.get("items") or ()],
                rule_ids=(f"must_merge:{value}" for value in sorted(rules)),
                first_stage_kind="rule_merge",
            )
        )
    return result


def build_existing_option_payload(candidate: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        {
            "option_id": index,
            "title": _clean_text(option.get("title")),
            "item_count": int(option.get("item_count") or 0),
            "first_seen_at": str(option.get("first_seen_at") or ""),
            "last_seen_at": str(option.get("last_seen_at") or ""),
            "examples": list(option.get("examples") or ())[:2],
        }
        for index, option in enumerate(
            candidate.get("existing_cluster_candidates") or (),
            start=1,
        )
    ]


def resolve_existing_option_id(candidate: dict[str, Any], value: Any) -> str:
    try:
        option_id = int(value)
    except (TypeError, ValueError, OverflowError):
        return ""
    options = list(candidate.get("existing_cluster_candidates") or ())
    if option_id <= 0 or option_id > len(options):
        return ""
    return _clean_text(options[option_id - 1].get("cluster_id"))
