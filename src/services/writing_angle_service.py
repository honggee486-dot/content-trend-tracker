"""주제 의도와 실제 근거를 바탕으로 글쓰기 방향을 보수적으로 추천합니다."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Any, Iterable

from src.services.trend_normalization import (
    clean_text,
    identity_tokens,
    normalize_title,
    normalize_url,
    source_domain,
    tokenize,
)

QUIZ_PLATFORM_TERMS = {
    "캐시워크", "오퀴즈", "타임스프레드", "지니어트", "캐시닥", "쏠퀴즈",
    "리브메이트", "행운퀴즈", "돈버는퀴즈", "돈버는 퀴즈", "초성퀴즈",
}
QUIZ_ANSWER_TERMS = {"정답", "답은", "정답은", "정답 공개", "정답 확인"}
UPDATE_TERMS = {
    "발표", "공개", "출시", "업데이트", "변경", "개편", "도입", "시행", "인상",
    "인하", "종료", "신설", "폐지", "추가", "삭제", "새 기능", "신제품",
}
SCHEDULE_TERMS = {
    "일정", "언제", "날짜", "시간", "출시일", "방송일", "시행일", "마감", "예정",
    "기간", "몇 시", "몇시", "예약일", "오픈일",
}
HOW_TO_TERMS = {
    "방법", "사용법", "신청", "설정", "등록", "설치", "예약", "접수", "준비물",
    "이용법", "절차", "하는 법", "해결법",
}
PROBLEM_TERMS = {
    "오류", "안됨", "안 돼", "안돼", "고장", "끊김", "느림", "문제", "실패", "먹통",
    "충돌", "버그", "튕김", "멈춤", "불량", "복구", "해결", "삽질", "실패기",
}
SOLUTION_TERMS = {
    "해결", "수정", "복구", "재설치", "초기화", "점검", "대처", "조치", "우회", "예방",
    "성공기", "해결기", "트러블슈팅",
}
PROBLEM_EXPERIENCE_TERMS = {
    "삽질", "해결기", "실패기", "성공기", "트러블슈팅", "직접 해결", "직접 수정",
    "해결 과정", "문제 해결", "해결했다", "고쳤다", "재현", "원인 분석",
}
COMPARISON_TERMS = {"비교", "차이", "대안", "대체", "versus", " vs ", "장단점", "뭐가 낫", "어느 것이"}
PRICE_TERMS = {
    "가격", "요금", "비용", "할인", "혜택", "지원금", "보조금", "무료", "특가", "쿠폰",
    "캐시백", "환급", "월정액", "구독료",
}
ELIGIBILITY_TERMS = {"조건", "대상", "자격", "신청 기간", "소득 기준", "지원 대상", "준비 서류", "필요 서류"}
REACTION_TERMS = {"후기", "리뷰", "반응", "평가", "장점", "단점", "불편", "체험", "사용기", "만족", "불만"}
FACT_TERMS = {"논란", "사실", "진짜", "가짜", "과장", "루머", "오해", "검증", "팩트체크", "사실 여부"}
IMPACT_TERMS = {"영향", "변화", "전망", "사용자", "소비자", "업계", "생활", "업무", "시장", "부담"}
TREND_TERMS = {"급상승", "인기", "유행", "화제", "조회", "검색량", "순위", "주목", "확산"}
CAUTION_TERMS = {"주의", "오답", "입력", "띄어쓰기", "실수", "제한", "예외", "유의", "함정"}
PERSON_WORK_TERMS = {
    "프로필", "출연진", "배우", "등장인물", "줄거리", "결말", "몇부작", "몇 부작",
    "인물관계도", "원작", "시즌", "에피소드", "작품 정보",
}
EVENT_TERMS = {"사건", "사고", "속보", "결과", "우승", "경기 결과", "발생", "현황", "상황"}

FEATURE_LABELS = {
    "quiz_answer": "정답 조회",
    "quiz_platform": "퀴즈 서비스",
    "update": "발표·변경",
    "schedule": "일정·날짜",
    "how_to": "방법·절차",
    "problem": "오류·문제",
    "solution": "해결·복구",
    "problem_experience": "문제 해결 경험",
    "comparison": "비교·선택",
    "price": "가격·혜택",
    "eligibility": "대상·조건",
    "reaction": "후기·반응",
    "fact": "논란·사실 확인",
    "impact": "영향·변화",
    "trend": "확산·급상승",
    "caution": "주의·예외",
    "person_work": "인물·작품 정보",
    "event": "사건·결과",
}

INTENT_LABELS = {
    "quiz_answer": "정답 조회형",
    "problem_solving": "문제 해결형",
    "comparison": "비교·선택형",
    "schedule": "일정·날짜 확인형",
    "price_benefit": "가격·혜택형",
    "release_update": "출시·업데이트형",
    "how_to": "사용 방법형",
    "fact_check": "팩트체크형",
    "review_reaction": "후기·반응형",
    "person_work_info": "인물·작품 정보형",
    "event_summary": "사건·결과 요약형",
    "general": "일반 설명형",
}

PURPOSE_LABELS = {
    "problem_solving": "문제 해결·경험",
    "comparison": "비교·선택",
    "release_update": "출시·변경",
    "schedule": "일정·날짜",
    "how_to": "방법·절차",
    "fact_check": "논란·사실 확인",
    "review_reaction": "후기·반응",
    "price_benefit": "가격·혜택",
    "person_work_info": "인물·작품",
    "event_summary": "사건·결과",
    "general": "일반 설명",
}

TIER_LABELS = {
    "primary": "주 추천 방향",
    "secondary": "보조 방향",
    "reference": "단일 근거 참고 방향",
}

_COPY_FILLER_TOKENS = {
    "핵심", "내용", "정리", "요약", "종합", "단독", "속보", "관련", "최신", "기사", "뉴스",
    "알아보기", "총정리", "전문", "기자", "보도",
}
_COMPARISON_GENERIC_TOKENS = {
    "비교", "차이", "대안", "대체", "장단점", "선택", "성능", "가격", "비용", "기준", "사용자",
    "모델", "제품", "서비스", "기능", "실제", "어느", "무엇", "뭐가", "낫다", "카메라",
}
_FACTUAL_SOURCE_TYPES = {"naver_news", "daum_web"}
_COMMUNITY_SOURCE_TYPES = {"naver_blog", "daum_cafe"}


@dataclass(frozen=True)
class WritingIntent:
    key: str
    label: str
    confidence: float
    reason: str


@dataclass(frozen=True)
class EvidenceProfile:
    subject: str
    subject_text: str
    items: tuple[dict[str, Any], ...]
    item_titles: tuple[str, ...]
    item_texts: tuple[str, ...]
    item_features: tuple[frozenset[str], ...]
    item_publishers: tuple[str, ...]
    item_official: tuple[bool, ...]
    source_types: frozenset[str]
    publishers: frozenset[str]
    factual_count: int
    community_publishers: frozenset[str]
    feature_item_counts: dict[str, int]
    feature_publisher_counts: dict[str, int]
    subject_features: frozenset[str]
    unique_item_count: int
    subject_support_count: int
    subject_publisher_count: int
    dominant_detail_support_count: int
    mixed_evidence: bool
    evidence_reason: str
    purpose_item_counts: dict[str, int]
    purpose_labels: tuple[str, ...]

    @property
    def community_publisher_count(self) -> int:
        return len(self.community_publishers)


def _contains_any(text: str, terms: set[str]) -> bool:
    folded = text.casefold()
    return any(term.casefold() in folded for term in terms)


def _item_title(item: dict[str, Any]) -> str:
    metadata = item.get("metadata") or {}
    return clean_text(str(metadata.get("item_title") or item.get("raw_title") or ""))


def _item_text(item: dict[str, Any]) -> str:
    metadata = item.get("metadata") or {}
    chunks = [
        str(metadata.get("item_title") or item.get("raw_title") or ""),
        str(metadata.get("description") or ""),
        str(metadata.get("signal_type") or ""),
    ]
    return " ".join(clean_text(chunk) for chunk in chunks if clean_text(chunk)).casefold()


def _publisher(item: dict[str, Any]) -> str:
    source_name = clean_text(str(item.get("source_name") or "")).casefold()
    if source_name:
        return source_name
    return source_domain(str(item.get("source_url") or "")).casefold()


def _is_official_item(item: dict[str, Any]) -> bool:
    metadata = item.get("metadata") or {}
    for key in ("is_official", "official", "official_source"):
        value = metadata.get(key)
        if value is True or str(value).strip().casefold() in {"1", "true", "yes", "official"}:
            return True
    reference_type = str(metadata.get("reference_type") or "").strip().casefold()
    if reference_type in {"official_agency", "company_official", "official"}:
        return True
    domain = source_domain(str(item.get("source_url") or ""))
    if domain.endswith((".go.kr", ".gov.kr", ".gov")):
        return True
    source_name = clean_text(str(item.get("source_name") or "")).casefold()
    return "공식" in source_name or "official" in source_name


def _feature_terms() -> dict[str, set[str]]:
    return {
        "quiz_answer": QUIZ_ANSWER_TERMS,
        "quiz_platform": QUIZ_PLATFORM_TERMS,
        "update": UPDATE_TERMS,
        "schedule": SCHEDULE_TERMS,
        "how_to": HOW_TO_TERMS,
        "problem": PROBLEM_TERMS,
        "solution": SOLUTION_TERMS,
        "problem_experience": PROBLEM_EXPERIENCE_TERMS,
        "comparison": COMPARISON_TERMS,
        "price": PRICE_TERMS,
        "eligibility": ELIGIBILITY_TERMS,
        "reaction": REACTION_TERMS,
        "fact": FACT_TERMS,
        "impact": IMPACT_TERMS,
        "trend": TREND_TERMS,
        "caution": CAUTION_TERMS,
        "person_work": PERSON_WORK_TERMS,
        "event": EVENT_TERMS,
    }


def _features_for_text(text: str) -> frozenset[str]:
    features = {
        feature for feature, terms in _feature_terms().items() if _contains_any(text, terms)
    }
    if "problem_experience" in features:
        features.add("problem")
    return frozenset(features)


def _copy_tokens(title: str) -> set[str]:
    return {token for token in tokenize(title) if token not in _COPY_FILLER_TOKENS}


def _same_evidence(left: dict[str, Any], right: dict[str, Any]) -> bool:
    left_url = normalize_url(str(left.get("normalized_url") or left.get("source_url") or ""))
    right_url = normalize_url(str(right.get("normalized_url") or right.get("source_url") or ""))
    if left_url and right_url and left_url == right_url:
        return True

    left_title = normalize_title(_item_title(left))
    right_title = normalize_title(_item_title(right))
    if left_title and left_title == right_title:
        return True
    if not left_title or not right_title:
        return False

    if SequenceMatcher(None, left_title, right_title).ratio() >= 0.94:
        return True
    left_tokens = _copy_tokens(left_title)
    right_tokens = _copy_tokens(right_title)
    if min(len(left_tokens), len(right_tokens)) < 3:
        return False
    overlap = len(left_tokens & right_tokens)
    union = len(left_tokens | right_tokens)
    return overlap >= 3 and union > 0 and overlap / union >= 0.88


def _representative_priority(item: dict[str, Any]) -> tuple[int, int, str]:
    source_type = str(item.get("source_type") or "")
    timestamp = str(item.get("published_at") or item.get("observed_at") or "")
    return (
        1 if _is_official_item(item) else 0,
        1 if source_type in _FACTUAL_SOURCE_TYPES else 0,
        timestamp,
    )


def _deduplicated_items(items: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """추적 URL, 동일 제목, 매우 유사한 복제 제목을 방향 근거에서 한 번만 계산합니다."""
    groups: list[list[dict[str, Any]]] = []
    for item in items:
        matched_group = next(
            (group for group in groups if any(_same_evidence(item, existing) for existing in group)),
            None,
        )
        if matched_group is None:
            groups.append([item])
        else:
            matched_group.append(item)
    return [max(group, key=_representative_priority) for group in groups]


def _purpose_key(features: frozenset[str]) -> str:
    if "problem_experience" in features or ("problem" in features and "solution" in features):
        return "problem_solving"
    if "comparison" in features:
        return "comparison"
    if "update" in features:
        return "release_update"
    if "schedule" in features:
        return "schedule"
    if "how_to" in features:
        return "how_to"
    if "fact" in features:
        return "fact_check"
    if "reaction" in features:
        return "review_reaction"
    if "price" in features:
        return "price_benefit"
    if "person_work" in features:
        return "person_work_info"
    if "event" in features:
        return "event_summary"
    return "general"


def build_evidence_profile(subject: str, items: Iterable[dict[str, Any]]) -> EvidenceProfile:
    item_list = _deduplicated_items(items)
    subject_clean = clean_text(subject) or "선택한 주제"
    subject_text = subject_clean.casefold()
    item_titles = tuple(_item_title(item) for item in item_list)
    item_texts = tuple(_item_text(item) for item in item_list)
    item_features = tuple(_features_for_text(text) for text in item_texts)
    item_publishers = tuple(_publisher(item) for item in item_list)
    item_official = tuple(_is_official_item(item) for item in item_list)
    source_types = frozenset(str(item.get("source_type") or "") for item in item_list)
    publishers = frozenset(publisher for publisher in item_publishers if publisher)
    community_publishers = frozenset(
        publisher
        for item, publisher in zip(item_list, item_publishers)
        if item.get("source_type") in _COMMUNITY_SOURCE_TYPES and publisher
    )
    factual_count = sum(
        1 for item in item_list if item.get("source_type") in _FACTUAL_SOURCE_TYPES
    )

    subject_features = _features_for_text(subject_text)
    feature_item_counts: Counter[str] = Counter()
    feature_publishers: dict[str, set[str]] = {feature: set() for feature in _feature_terms()}
    for features, publisher in zip(item_features, item_publishers):
        for feature in features:
            feature_item_counts[feature] += 1
            if publisher:
                feature_publishers[feature].add(publisher)

    subject_tokens = identity_tokens(subject_clean)
    subject_support_count = 0
    subject_publishers: set[str] = set()
    detail_support: Counter[str] = Counter()
    for title, publisher in zip(item_titles, item_publishers):
        item_tokens = identity_tokens(title)
        if subject_tokens:
            required_overlap = max(1, (len(subject_tokens) + 1) // 2)
            if len(subject_tokens & item_tokens) >= required_overlap:
                subject_support_count += 1
                if publisher:
                    subject_publishers.add(publisher)
        for token in item_tokens - subject_tokens:
            detail_support[token] += 1

    purpose_counts = Counter(_purpose_key(features) for features in item_features)
    non_general_purposes = [
        (purpose, count) for purpose, count in purpose_counts.items() if purpose != "general" and count > 0
    ]
    purpose_labels = tuple(
        f"{PURPOSE_LABELS[purpose]} {count}건"
        for purpose, count in sorted(non_general_purposes, key=lambda item: (-item[1], item[0]))
    )

    dominant_detail_support_count = max(detail_support.values(), default=0)
    coherence_threshold = max(2, int(subject_support_count * 0.6 + 0.999))
    dominant_purpose_count = max((count for _, count in non_general_purposes), default=0)
    purpose_mixed = (
        len(item_list) >= 3
        and len(non_general_purposes) >= 2
        and dominant_purpose_count < max(2, int(len(item_list) * 0.6 + 0.999))
    )
    detail_mixed = (
        len(item_list) >= 3
        and subject_support_count >= 3
        and dominant_detail_support_count < coherence_threshold
    )
    mixed_evidence = purpose_mixed or detail_mixed
    if mixed_evidence:
        purpose_text = " · ".join(purpose_labels[:4])
        suffix = f" 하위 작성 목적은 {purpose_text}로 나뉩니다." if purpose_text else ""
        evidence_reason = (
            f"중복을 제외한 원문 {len(item_list)}건은 ‘{subject_clean}’이라는 공통 대상만 공유하고 "
            f"세부 사건이나 작성 목적은 여러 갈래입니다.{suffix} 특정 단일 원문을 대표 방향으로 사용하지 않았습니다."
        )
    else:
        evidence_reason = (
            f"중복을 제외한 원문 {len(item_list)}건 중 {subject_support_count}건, "
            f"독립 발행처 {len(subject_publishers)}곳에서 주제명이 지지됩니다."
        )

    return EvidenceProfile(
        subject=subject_clean,
        subject_text=subject_text,
        items=tuple(item_list),
        item_titles=item_titles,
        item_texts=item_texts,
        item_features=item_features,
        item_publishers=item_publishers,
        item_official=item_official,
        source_types=source_types,
        publishers=publishers,
        factual_count=factual_count,
        community_publishers=community_publishers,
        feature_item_counts=dict(feature_item_counts),
        feature_publisher_counts={
            feature: len(feature_publishers[feature]) for feature in _feature_terms()
        },
        subject_features=subject_features,
        unique_item_count=len(item_list),
        subject_support_count=subject_support_count,
        subject_publisher_count=len(subject_publishers),
        dominant_detail_support_count=dominant_detail_support_count,
        mixed_evidence=mixed_evidence,
        evidence_reason=evidence_reason,
        purpose_item_counts=dict(purpose_counts),
        purpose_labels=purpose_labels,
    )


def classify_writing_intent(profile: EvidenceProfile) -> WritingIntent:
    """제목을 우선하고 여러 독립 원문이 반복 지지할 때만 근거 점수를 보탭니다."""
    features = profile.subject_features
    counts = profile.feature_item_counts

    if "quiz_answer" in features and (
        "quiz_platform" in features or counts.get("quiz_platform", 0) >= 1
    ):
        return WritingIntent(
            "quiz_answer",
            INTENT_LABELS["quiz_answer"],
            0.99,
            "주제명에서 퀴즈 서비스와 현재 정답 조회 표현이 함께 확인됩니다.",
        )

    scores: dict[str, float] = {
        "problem_solving": (13 if "problem" in features else 0) + (5 if "solution" in features else 0),
        "comparison": 15 if "comparison" in features else 0,
        "schedule": 16 if "schedule" in features else 0,
        "price_benefit": (12 if "price" in features else 0) + (5 if "eligibility" in features else 0),
        "release_update": 13 if "update" in features else 0,
        "how_to": (13 if "how_to" in features else 0) + (3 if "eligibility" in features else 0),
        "fact_check": 15 if "fact" in features else 0,
        "review_reaction": 14 if "reaction" in features else 0,
        "person_work_info": 13 if "person_work" in features else 0,
        "event_summary": 12 if "event" in features else 0,
    }

    if (
        max(scores.values(), default=0) == 0
        and len(profile.publishers) >= 2
        and not profile.mixed_evidence
    ):
        evidence_mapping = {
            "problem_solving": "problem_experience",
            "comparison": "comparison",
            "schedule": "schedule",
            "price_benefit": "price",
            "release_update": "update",
            "how_to": "how_to",
            "fact_check": "fact",
            "review_reaction": "reaction",
            "person_work_info": "person_work",
            "event_summary": "event",
        }
        for intent_key, feature in evidence_mapping.items():
            if counts.get(feature, 0) >= 2 and profile.feature_publisher_counts.get(feature, 0) >= 2:
                scores[intent_key] = 7.0

    priority = {
        "problem_solving": 10,
        "comparison": 9,
        "schedule": 8,
        "price_benefit": 7,
        "release_update": 6,
        "how_to": 5,
        "fact_check": 4,
        "review_reaction": 3,
        "person_work_info": 2,
        "event_summary": 1,
    }
    key, score = max(scores.items(), key=lambda item: (item[1], priority[item[0]]))
    if score <= 0:
        return WritingIntent(
            "general",
            INTENT_LABELS["general"],
            0.45,
            "주제명에서 특정 작성 목적이 충분히 확인되지 않아 일반 설명형으로 보수적으로 분류했습니다.",
        )

    direct_features = [feature for feature in features if feature in FEATURE_LABELS]
    if score >= 15:
        confidence = 0.95
    elif score >= 12:
        confidence = 0.9
    else:
        confidence = 0.68
    reason = (
        f"주제명에서 {', '.join(FEATURE_LABELS.get(feature, feature) for feature in sorted(direct_features))} 표현이 확인됩니다."
        if direct_features
        else "서로 다른 원문에서 같은 작성 목적 표현이 반복 확인됩니다."
    )
    return WritingIntent(key, INTENT_LABELS[key], confidence, reason)


def _excluded_labels(profile: EvidenceProfile, intent: WritingIntent) -> list[str]:
    counts = profile.feature_item_counts
    excluded: list[str] = []
    if intent.key != "comparison" and counts.get("comparison", 0) < 2:
        excluded.append("비교")
    if intent.key != "review_reaction" and (
        counts.get("reaction", 0) < 2 or profile.community_publisher_count < 2
    ):
        excluded.append("후기·반응")
    if intent.key != "release_update" and counts.get("update", 0) == 0:
        excluded.append("출시·변경")
    if intent.key != "schedule" and counts.get("schedule", 0) == 0:
        excluded.append("일정")
    if intent.key not in {"how_to", "problem_solving"} and (
        counts.get("how_to", 0) == 0 and counts.get("solution", 0) == 0
    ):
        excluded.append("사용법·해결")
    if intent.key != "fact_check" and (
        counts.get("fact", 0) == 0 or len(profile.publishers) < 2
    ):
        excluded.append("팩트체크")
    return excluded[:4]


def _subject_support_indices(profile: EvidenceProfile) -> list[int]:
    subject_tokens = identity_tokens(profile.subject)
    if not subject_tokens:
        return list(range(profile.unique_item_count))
    required_overlap = max(1, (len(subject_tokens) + 1) // 2)
    return [
        index
        for index, title in enumerate(profile.item_titles)
        if len(subject_tokens & identity_tokens(title)) >= required_overlap
    ]


def _problem_experience_indices(profile: EvidenceProfile) -> list[int]:
    result: list[int] = []
    for index, (text, features) in enumerate(zip(profile.item_texts, profile.item_features)):
        explicit_experience = "problem_experience" in features
        paired_problem_solution = "problem" in features and bool({"solution", "how_to"} & features)
        if explicit_experience or paired_problem_solution or _contains_any(text, PROBLEM_EXPERIENCE_TERMS):
            result.append(index)
    return result


def _support_indices(profile: EvidenceProfile, key: str) -> list[int]:
    if key == "general_issue_map":
        return list(range(profile.unique_item_count))
    if key in {"general_common_facts", "general_core"}:
        return _subject_support_indices(profile) or list(range(profile.unique_item_count))
    if key == "verify_scope":
        return []
    if key.startswith("problem_"):
        return _problem_experience_indices(profile)

    feature_map: dict[str, set[str]] = {
        "quiz_answer": {"quiz_answer"},
        "quiz_answer_update": {"update"},
        "quiz_answer_caution": {"caution"},
        "schedule_summary": {"schedule"},
        "schedule_change": {"update"},
        "schedule_prepare": {"how_to", "eligibility"},
        "comparison_core": {"comparison"},
        "comparison_choice": {"comparison"},
        "comparison_price": {"comparison", "price"},
        "price_summary": {"price"},
        "price_eligibility": {"eligibility"},
        "price_alternative": {"comparison"},
        "update_summary": {"update"},
        "update_timeline": {"schedule"},
        "update_impact": {"impact"},
        "update_action": {"how_to"},
        "official_vs_reaction": {"reaction"},
        "how_to_steps": {"how_to"},
        "how_to_requirements": {"eligibility"},
        "how_to_mistakes": {"problem", "caution"},
        "fact_claims": {"fact"},
        "fact_sources": {"fact"},
        "fact_impact": {"impact"},
        "reaction_summary": {"reaction"},
        "reaction_pros_cons": {"reaction"},
        "reaction_caution": {"problem", "caution"},
        "work_core_info": {"person_work"},
        "work_latest": {"update", "schedule"},
        "work_reaction": {"reaction"},
        "event_key_facts": {"event"},
        "event_timeline": {"schedule"},
        "event_impact": {"impact"},
        "event_factcheck": {"fact"},
        "general_spread": {"trend"},
    }
    required = feature_map.get(key, set())
    return [
        index for index, features in enumerate(profile.item_features) if features & required
    ]


def _comparison_targets(profile: EvidenceProfile, support_indices: list[int]) -> list[str]:
    subject_targets = {
        token for token in identity_tokens(profile.subject) if token not in _COMPARISON_GENERIC_TOKENS
    }
    all_targets = set(subject_targets)
    for index in support_indices:
        all_targets.update(
            token
            for token in identity_tokens(profile.item_titles[index])
            if token not in _COMPARISON_GENERIC_TOKENS
        )
    if len(subject_targets) >= 2 and "comparison" in profile.subject_features:
        return sorted(all_targets)
    if subject_targets and all_targets - subject_targets:
        return sorted(all_targets)
    return []


def _evidence_cards(profile: EvidenceProfile, indices: list[int]) -> list[dict[str, Any]]:
    ordered = sorted(
        indices,
        key=lambda index: _representative_priority(profile.items[index]),
        reverse=True,
    )
    cards: list[dict[str, Any]] = []
    for index in ordered[:3]:
        item = profile.items[index]
        cards.append(
            {
                "title": profile.item_titles[index] or "제목 없음",
                "publisher": profile.item_publishers[index] or str(item.get("source_type") or "출처 미상"),
                "url": normalize_url(str(item.get("normalized_url") or item.get("source_url") or "")),
                "source_type": str(item.get("source_type") or ""),
                "is_official": profile.item_official[index],
            }
        )
    return cards


def _candidate_tier(
    key: str,
    support_count: int,
    publisher_count: int,
    support_ratio: float,
    official_count: int,
) -> tuple[str | None, str]:
    if key == "verify_scope":
        return "primary", "구체적인 방향을 확정할 근거가 없어 먼저 범위와 공식 자료를 확인하는 안전한 방향입니다."
    if support_count <= 0:
        return None, "이 방향을 직접 지원하는 중복 제외 원문이 없습니다."
    if support_count == 1:
        return "reference", "중복 제외 독립 원문 1건만 확인되어 주 추천에서 분리했습니다."
    if key.startswith("problem_"):
        if support_count >= 3 and publisher_count >= 2:
            return "eligible", f"독립된 문제 해결 경험 {support_count}건과 발행처 {publisher_count}곳이 확인됩니다."
        return "secondary", f"문제 해결 경험은 {support_count}건이지만 주 추천 기준인 3건·발행처 2곳에는 부족합니다."
    if key.startswith("comparison_"):
        if support_count >= 2 or publisher_count >= 2:
            return "eligible", f"실제 비교 원문 {support_count}건과 발행처 {publisher_count}곳이 확인됩니다."
        return "reference", "실제 비교 원문이 1건뿐이어서 참고 방향으로 분리했습니다."
    if support_count >= 2 or publisher_count >= 2 or (official_count >= 1 and support_count >= 2):
        return "eligible", (
            f"중복 제외 원문 {support_count}건, 독립 발행처 {publisher_count}곳, "
            f"클러스터 근거의 {support_ratio * 100:.0f}%가 이 방향을 지원합니다."
        )
    return "reference", "독립 근거가 한 방향의 기본 추천 기준에 미치지 못했습니다."


def _finalize_candidates(
    profile: EvidenceProfile,
    intent: WritingIntent,
    candidates: list[dict[str, Any]],
    excluded_labels: list[str],
) -> list[dict[str, Any]]:
    unique_candidates: list[dict[str, Any]] = []
    seen_keys: set[str] = set()
    for candidate in candidates:
        if candidate["key"] in seen_keys:
            continue
        seen_keys.add(candidate["key"])
        unique_candidates.append(candidate)

    evaluated: list[dict[str, Any]] = []
    excluded_directions: list[dict[str, str]] = []
    for candidate in unique_candidates:
        key = str(candidate["key"])
        support_indices = _support_indices(profile, key)
        comparison_targets: list[str] = []
        if key.startswith("comparison_"):
            comparison_targets = _comparison_targets(profile, support_indices)
            if len(comparison_targets) < 2:
                excluded_directions.append(
                    {
                        "label": str(candidate["text"]),
                        "reason": "실제 비교 원문에서 서로 다른 비교 대상 2개를 확인하지 못했습니다.",
                    }
                )
                continue

        support_count = len(support_indices)
        support_publishers = {
            profile.item_publishers[index]
            for index in support_indices
            if profile.item_publishers[index]
        }
        publisher_count = len(support_publishers)
        official_count = sum(1 for index in support_indices if profile.item_official[index])
        support_ratio = support_count / profile.unique_item_count if profile.unique_item_count else 0.0
        tier, tier_reason = _candidate_tier(
            key,
            support_count,
            publisher_count,
            support_ratio,
            official_count,
        )
        if tier is None:
            excluded_directions.append(
                {"label": str(candidate["text"]), "reason": tier_reason}
            )
            continue

        evidence = _evidence_cards(profile, support_indices)
        evaluated.append(
            {
                **candidate,
                "support_item_count": support_count,
                "support_publisher_count": publisher_count,
                "support_ratio": support_ratio,
                "official_support_count": official_count,
                "official_confirmation_required": bool(candidate["needs_verification"] and official_count == 0),
                "representative_evidence": evidence,
                "comparison_targets": comparison_targets,
                "_provisional_tier": tier,
                "_tier_reason": tier_reason,
            }
        )

    eligible = [item for item in evaluated if item["_provisional_tier"] == "eligible"]
    primary_key = ""
    if eligible:
        primary = max(
            eligible,
            key=lambda item: (
                int(item["support_item_count"]),
                float(item["support_ratio"]),
                int(item["support_publisher_count"]),
                float(item["score"]),
            ),
        )
        primary_key = str(primary["key"])

    for item in evaluated:
        provisional = str(item.pop("_provisional_tier"))
        tier_reason = str(item.pop("_tier_reason"))
        if provisional == "eligible":
            tier = "primary" if str(item["key"]) == primary_key else "secondary"
            if tier == "secondary":
                tier_reason = (
                    tier_reason
                    + " 다만 더 많은 클러스터 근거가 일치하는 핵심 방향이 있어 보조 방향으로 배치했습니다."
                )
        else:
            tier = provisional
        item["recommendation_tier"] = tier
        item["recommendation_tier_label"] = TIER_LABELS[tier]
        item["confidence_reason"] = tier_reason
        item["intent_key"] = intent.key
        item["intent_label"] = intent.label
        item["intent_confidence"] = intent.confidence
        item["intent_reason"] = intent.reason
        item["excluded_labels"] = excluded_labels
        item["excluded_directions"] = excluded_directions[:6]
        item["evidence_item_count"] = profile.unique_item_count
        item["subject_support_count"] = profile.subject_support_count
        item["subject_publisher_count"] = profile.subject_publisher_count
        item["dominant_detail_support_count"] = profile.dominant_detail_support_count
        item["mixed_evidence"] = profile.mixed_evidence
        item["evidence_reason"] = profile.evidence_reason
        item["evidence_purpose_labels"] = list(profile.purpose_labels)

    tier_order = {"primary": 0, "secondary": 1, "reference": 2}
    evaluated.sort(
        key=lambda item: (
            tier_order[str(item["recommendation_tier"])],
            -int(item["support_item_count"]),
            -float(item["score"]),
            str(item["key"]),
        )
    )
    return evaluated


def recommend_writing_angle_details(
    subject: str,
    items: Iterable[dict[str, Any]],
) -> list[dict[str, Any]]:
    profile = build_evidence_profile(subject, items)
    intent = classify_writing_intent(profile)
    counts = profile.feature_item_counts
    excluded = _excluded_labels(profile, intent)
    candidates: list[dict[str, Any]] = []

    def add(
        key: str,
        score: float,
        text: str,
        reason: str,
        *,
        needs_verification: bool = False,
    ) -> None:
        candidates.append(
            {
                "key": key,
                "score": score,
                "text": text,
                "reason": reason,
                "needs_verification": needs_verification,
            }
        )

    topic = profile.subject

    if intent.key == "quiz_answer":
        add("quiz_answer", 100, f"[정답 정리] {topic}의 문제별 정답을 빠르게 확인", intent.reason, needs_verification=True)
        if counts.get("update", 0):
            add("quiz_answer_update", 90, f"[정답 업데이트] {topic}에서 새로 추가되거나 변경된 문제와 정답 정리", "원문에 새 문제·추가·변경·시간대 업데이트 표현이 확인됩니다.", needs_verification=True)
        if counts.get("caution", 0):
            add("quiz_answer_caution", 80, f"[입력 주의] {topic}의 문제 문구와 실제 입력할 정답을 구분해 정리", "원문에 오답·입력·띄어쓰기·주의 표현이 확인됩니다.", needs_verification=True)

    elif intent.key == "schedule":
        add("schedule_summary", 100, f"[핵심 일정] {topic}의 날짜·시간·마감 정보를 한눈에 정리", intent.reason, needs_verification=True)
        if counts.get("update", 0):
            add("schedule_change", 90, f"[변경 확인] {topic} 일정의 연기·변경 여부와 최신 공지 확인", "원문에 발표·변경·예정 표현이 확인됩니다.", needs_verification=True)
        if counts.get("how_to", 0) or counts.get("eligibility", 0):
            add("schedule_prepare", 80, f"[준비 사항] {topic} 전에 필요한 신청·예약·준비 절차", "원문에 신청·예약·조건 표현이 확인됩니다.", needs_verification=True)

    elif intent.key == "problem_solving":
        add("problem_diagnosis", 100, f"[원인 진단] {topic}의 증상과 가능한 원인을 먼저 구분", intent.reason, needs_verification=True)
        if counts.get("solution", 0) or counts.get("how_to", 0) or counts.get("problem_experience", 0):
            add("problem_steps", 95, f"[단계별 해결] {topic}을 안전한 순서대로 점검하고 해결", "원문에 독립된 해결·수정·설정 경험이 확인됩니다.", needs_verification=True)
        add("problem_fallback", 75, f"[해결되지 않을 때] {topic}에서 추가로 확인할 항목과 문의 기준", "문제 해결형 주제는 실패 시 다음 점검 기준을 함께 제공하는 것이 유용합니다.", needs_verification=True)

    elif intent.key == "comparison":
        add("comparison_core", 100, f"[핵심 비교] {topic}의 실제 차이를 동일 기준으로 정리", intent.reason, needs_verification=True)
        add("comparison_choice", 90, f"[상황별 선택] {topic}에서 사용자 조건별로 어떤 선택이 맞는지 정리", "비교 목적과 실제 비교 원문이 확인될 때만 선택 기준을 제시합니다.", needs_verification=True)
        if counts.get("price", 0):
            add("comparison_price", 85, f"[가격·조건 비교] {topic}의 비용과 혜택 차이를 함께 확인", "원문에 가격·요금·혜택 표현이 확인됩니다.", needs_verification=True)

    elif intent.key == "price_benefit":
        add("price_summary", 100, f"[가격·혜택 정리] {topic}의 실제 비용과 받을 수 있는 혜택을 구분", intent.reason, needs_verification=True)
        if counts.get("eligibility", 0) or "eligibility" in profile.subject_features:
            add("price_eligibility", 92, f"[대상·조건] {topic}의 신청 대상과 제외 조건을 체크리스트로 정리", "원문에 대상·자격·조건 표현이 확인됩니다.", needs_verification=True)
        if counts.get("comparison", 0):
            add("price_alternative", 80, f"[대안 비교] {topic}과 함께 언급된 다른 선택지의 총비용 비교", "실제 비교 원문이 확인되는 범위에서만 대안을 비교합니다.", needs_verification=True)

    elif intent.key == "release_update":
        add("update_summary", 100, f"[변경 핵심] {topic}에서 새로 발표·변경된 내용만 먼저 정리", intent.reason, needs_verification=True)
        if counts.get("schedule", 0):
            add("update_timeline", 92, f"[적용 일정] {topic}의 발표·시행·출시 시점을 시간순으로 정리", "원문에 일정·날짜·예정 표현이 확인됩니다.", needs_verification=True)
        if counts.get("impact", 0) and profile.factual_count:
            add("update_impact", 85, f"[실제 영향] {topic}이 사용자·비용·업무에 바꾸는 구체적인 부분", "사실성 출처에 영향·사용자·시장 표현이 확인됩니다.", needs_verification=True)
        if counts.get("how_to", 0):
            add("update_action", 80, f"[사용자 대응] {topic} 이후 필요한 설정·신청·준비 절차", "원문에 방법·설정·신청 표현이 확인됩니다.", needs_verification=True)
        if counts.get("reaction", 0) >= 2 and profile.community_publisher_count >= 2 and profile.factual_count:
            add("official_vs_reaction", 78, f"[발표와 반응] {topic}의 공식 변경 내용과 여러 사용자 반응을 분리해 비교", "사실성 출처와 서로 다른 커뮤니티 발행처의 반응 근거가 함께 있습니다.", needs_verification=True)

    elif intent.key == "how_to":
        add("how_to_steps", 100, f"[단계별 방법] {topic}을 처음부터 끝까지 따라 할 수 있게 정리", intent.reason, needs_verification=True)
        if counts.get("eligibility", 0):
            add("how_to_requirements", 90, f"[준비·조건] {topic} 전에 필요한 자격·서류·설정을 확인", "원문에 대상·조건·준비 서류 표현이 확인됩니다.", needs_verification=True)
        if counts.get("caution", 0) or counts.get("problem", 0):
            add("how_to_mistakes", 82, f"[실수 방지] {topic}에서 자주 막히는 부분과 주의사항", "원문에 문제·주의·예외 표현이 확인됩니다.", needs_verification=True)

    elif intent.key == "fact_check":
        add("fact_claims", 100, f"[주장 분리] {topic}에서 확인된 사실·주장·추정을 구분", intent.reason, needs_verification=True)
        add("fact_sources", 92, f"[근거 확인] {topic}을 공식 자료와 독립 출처로 교차 확인", f"서로 다른 발행처가 {len(profile.publishers)}곳 확인됩니다.", needs_verification=True)
        if counts.get("impact", 0):
            add("fact_impact", 78, f"[오해의 영향] {topic}의 잘못된 정보가 실제 판단에 미치는 영향", "원문에 영향·사용자 표현이 확인됩니다.", needs_verification=True)

    elif intent.key == "review_reaction":
        add("reaction_summary", 100, f"[반응 요약] {topic}에서 반복적으로 언급된 평가를 긍정·부정으로 구분", intent.reason, needs_verification=profile.community_publisher_count < 2)
        if counts.get("reaction", 0) >= 2 and profile.community_publisher_count >= 2:
            add("reaction_pros_cons", 92, f"[장단점] {topic}의 여러 후기에서 공통으로 나온 장점과 불편", f"서로 다른 커뮤니티·블로그 발행처 {profile.community_publisher_count}곳에서 반응 표현이 확인됩니다.")
        if counts.get("problem", 0) or counts.get("caution", 0):
            add("reaction_caution", 80, f"[이용 전 주의] {topic} 후기에서 반복되는 문제와 확인할 조건", "후기 원문에 문제·불편·주의 표현이 확인됩니다.", needs_verification=True)

    elif intent.key == "person_work_info":
        add("work_core_info", 100, f"[핵심 정보] {topic}의 인물·작품·등장 관계를 정확히 정리", intent.reason, needs_verification=True)
        if counts.get("schedule", 0) or counts.get("update", 0):
            add("work_latest", 88, f"[공개·방송 정보] {topic}의 최신 공개 일정과 변경 사항", "원문에 공개·방송·일정 표현이 확인됩니다.", needs_verification=True)
        if counts.get("reaction", 0) >= 2 and profile.community_publisher_count >= 2:
            add("work_reaction", 76, f"[시청자 반응] {topic}에 대한 여러 시청자 반응을 쟁점별로 정리", "서로 다른 커뮤니티 발행처에서 반응 표현이 반복 확인됩니다.", needs_verification=True)

    elif intent.key == "event_summary":
        add("event_key_facts", 100, f"[핵심 요약] {topic}의 발생 경위와 현재 확인된 결과를 정리", intent.reason, needs_verification=True)
        if counts.get("schedule", 0):
            add("event_timeline", 90, f"[시간순 정리] {topic}의 주요 전개를 시점별로 정리", "원문에 날짜·시간·일정 표현이 확인됩니다.", needs_verification=True)
        if counts.get("impact", 0):
            add("event_impact", 82, f"[영향] {topic}이 관련 사용자·시장·일정에 미친 영향을 구분", "원문에 영향·시장·사용자 표현이 확인됩니다.", needs_verification=True)
        if counts.get("fact", 0) and len(profile.publishers) >= 2:
            add("event_factcheck", 80, f"[사실 확인] {topic}의 확정 사실과 아직 확인되지 않은 주장을 구분", "논란·검증 표현과 복수 발행처 근거가 있습니다.", needs_verification=True)

    else:
        if profile.mixed_evidence and identity_tokens(topic):
            add(
                "general_issue_map",
                100,
                f"[이슈 구분] {topic} 관련 원문을 발표·논란·활용·문제 해결처럼 하위 목적별로 나눠 정리",
                profile.evidence_reason,
                needs_verification=True,
            )
            add(
                "general_common_facts",
                90,
                f"[공통 사실] {topic} 관련 여러 원문에서 공통으로 확인되는 내용만 정리",
                "특정 사례 하나보다 여러 독립 원문이 함께 지지하는 공통 사실을 우선합니다.",
                needs_verification=True,
            )
        elif identity_tokens(topic):
            add("general_core", 100, f"[핵심 정리] {topic}에서 현재 확인되는 사실과 배경을 간결하게 설명", intent.reason, needs_verification=True)
        else:
            add("verify_scope", 100, "[추가 확인] 구체적인 사건·대상과 공식 근거를 먼저 확인한 뒤 글의 범위를 정리", "현재 묶음에는 구체적인 주제를 식별할 근거가 부족합니다.", needs_verification=True)
        if counts.get("trend", 0) >= 2 and len(profile.source_types) >= 2:
            add("general_spread", 75, f"[주목 이유] {topic}이 여러 출처에서 관심을 받은 계기만 근거 범위 안에서 정리", "검색·영상 확산 표현과 복수 출처 근거가 함께 있습니다.", needs_verification=True)

    # 혼합 클러스터에서는 하위 목적을 숨기지 않되, 방향별 독립 근거 등급으로 분리합니다.
    existing_keys = {str(candidate["key"]) for candidate in candidates}
    if intent.key != "comparison" and counts.get("comparison", 0) > 0 and "comparison_core" not in existing_keys:
        add("comparison_core", 70, f"[비교 참고] {topic}과 함께 비교된 실제 대상과 차이를 근거 범위 안에서 정리", "클러스터 일부 원문에서 비교 목적이 확인됩니다.", needs_verification=True)
    if intent.key != "problem_solving" and _problem_experience_indices(profile) and "problem_case_study" not in existing_keys:
        add("problem_case_study", 72, f"[문제 해결 사례] {topic} 관련 독립된 실패·원인·해결 경험을 과정별로 정리", "클러스터 일부 원문에서 문제 해결 경험이 확인됩니다.", needs_verification=True)
    if profile.mixed_evidence and intent.key != "release_update" and counts.get("update", 0) >= 2 and "update_summary" not in existing_keys:
        add("update_summary", 74, f"[발표·변경] {topic} 관련 발표·출시·변경 내용만 별도로 정리", "여러 원문에서 발표·변경 목적이 반복 확인됩니다.", needs_verification=True)
    if profile.mixed_evidence and intent.key not in {"how_to", "problem_solving"} and counts.get("how_to", 0) >= 2 and "how_to_steps" not in existing_keys:
        add("how_to_steps", 68, f"[사용 방법] {topic} 관련 실제 설정·사용 절차만 별도로 정리", "여러 원문에서 방법·절차 목적이 반복 확인됩니다.", needs_verification=True)

    result = _finalize_candidates(profile, intent, candidates, excluded)
    if result:
        return result

    # 모든 세부 방향이 비교 대상 부족 등으로 제외된 경우에도 안전한 확인 방향은 남깁니다.
    fallback = {
        "key": "verify_scope",
        "score": 100,
        "text": "[추가 확인] 구체적인 사건·대상과 공식 근거를 먼저 확인한 뒤 글의 범위를 정리",
        "reason": "현재 근거로는 안전하게 추천할 글쓰기 방향이 없습니다.",
        "needs_verification": True,
    }
    return _finalize_candidates(profile, intent, [fallback], excluded)
