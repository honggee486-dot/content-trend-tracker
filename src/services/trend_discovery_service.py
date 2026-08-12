"""여러 출처의 최근 신호를 묶어 오늘의 트렌드 순위를 계산합니다."""

from __future__ import annotations

import hashlib
import json
import math
import re
import socket
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from collections import Counter, defaultdict
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta
from difflib import SequenceMatcher
from pathlib import Path

try:
    from rapidfuzz.fuzz import ratio as rapidfuzz_ratio
except ImportError:  # requirements 설치 전에도 기존 방식으로 동작
    rapidfuzz_ratio = None
from time import perf_counter, sleep
from typing import Any, Callable, Iterable
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit

import duckdb
import pandas as pd

from src.adapters.daum_search_adapter import DaumSearchAdapter
from src.adapters.naver_search_adapter import NaverSearchAdapter
from src.config import get_gemini_config
from src.database import connect_database, get_setting, set_setting
from src.services.gemini_model_service import (
    MODEL_PURPOSE_DATA_REVIEW,
    get_selected_gemini_model,
)
from src.services.gemini_service import record_gemini_api_call
from src.services.trend_cluster_ai_review_service import (
    FEATURE_ID as AI_CLUSTERING_FEATURE_ID,
    FEATURE_VERSION as AI_CLUSTERING_FEATURE_VERSION,
    classify_cluster_batch,
    select_cluster_batch_candidates,
)
from src.services.content_pack_service import link_topic_to_trend_cluster
from src.services.api_quota_service import (
    GOOGLE_TRENDS_API,
    GOOGLE_TRENDS_PROVIDER,
    KAKAO_DAUM_API,
    KAKAO_DAUM_PROVIDER,
    WIKIMEDIA_API,
    WIKIMEDIA_PROVIDER,
    ensure_kakao_daum_capacity,
    ensure_naver_search_capacity,
    record_local_api_calls,
)
from src.services.topic_service import (
    add_manual_topic,
    import_preloaded_source_signals,
    import_source_signals,
    record_source_import_failure,
    import_youtube_signals,
)
from src.services.writing_angle_service import recommend_writing_angle_details
from src.services.trend_clustering_fallback_service import cluster_items_deterministically
from src.services.trend_clustering_lock_service import acquire_trend_clustering_lock
from src.services.trend_normalization import (
    GENERIC_IDENTITY_TERMS,
    clean_text,
    compact_title,
    identity_tokens,
    is_specific_topic,
    normalize_title,
    normalize_url,
    source_domain,
    strip_collection_scope,
    tokenize,
)

_REPEAT_PUNCT_PATTERN = re.compile(r"([!?~])\1{2,}")
_GENERIC_SEEDS = {
    "인공지능", "IT 신제품", "정부 정책", "생활 정보", "경제 이슈", "건강 정보", "자동차",
    "게임", "여행", "취업 채용",
}
_GENERIC_QUERY_TERMS = {
    "인기영상", "인기 동영상", "전체", "트렌딩", "trending", "popular", "youtube popular",
    "fresh", "latest", "recent", "daily", "highlights",
}
_INFORMATIONAL_TERMS = {
    "방법", "신청", "변경", "비교", "가격", "요금", "지원", "조건", "일정", "사용법",
    "원인", "영향", "정책", "출시", "업데이트", "주의", "절약", "혜택", "기준",
}
_RISK_TERMS = {
    "투자", "주식", "코인", "대출", "세금", "법률", "소송", "건강", "질병", "약", "의료",
    "선거", "정치", "사망", "범죄", "사고",
}
_TIME_SENSITIVE_TERMS = {
    "순위", "일정", "결과", "경기", "스코어", "현재", "오늘", "내일", "이번주",
    "최신", "실시간", "속보", "날씨", "예보", "기온", "태풍", "환율", "금리",
    "주가", "가격", "요금", "지원금", "신청", "마감", "정책", "개정", "시행",
    "투표", "확정", "예정",
}
_FACT_CLAIM_PATTERNS = (
    ("날짜·기간", re.compile(r"(?:\d{2,4}년|\d{1,2}월|\d{1,2}일|\d{1,2}[./-]\d{1,2})")),
    ("비율·금액·순위", re.compile(r"\d+(?:\.\d+)?\s*(?:%|퍼센트|원|만원|억원|달러|위|점|명|건|회)")),
)
_NOISE_TERMS = {
    "shorts", "short", "live", "livestream", "mukbang", "asmr", "gameplay",
    "직캠", "먹방", "라이브", "생방송", "몰아보기",
}
_EVENT_CONTEXT_TERMS = {
    "발표", "출시", "공개", "협력", "투자", "인수", "합병", "상승", "하락",
    "급등", "급락", "종영", "결승", "승리", "패배", "논란", "사과", "체포",
    "기소", "지원", "신청", "변경", "인상", "인하", "발령", "복귀", "회동",
    "선언", "계약", "공급", "도입", "확대", "축소", "중단", "재개", "매진",
    "1위", "개편", "전환", "추진", "예정", "확정", "변화", "기능", "동맹",
    "파트너십", "회담", "면담", "도착", "참석", "업데이트", "종료",
}
_NAVIGATION_TITLE_MARKERS = {
    "카테고리", "홈페이지", "메인 페이지", "business post", "accessories news",
    "날씨누리", "개인정보취급방침", "개인정보처리방침", "이용약관",
    "section", "homepage", "archives", "privacy policy",
}
_STATIC_POLICY_TITLE_MARKERS = {
    "개인정보취급방침", "개인정보처리방침", "이용약관", "privacy policy",
}
_NAVIGATION_PATH_MARKERS = {
    "industry", "category", "categories", "section", "sections", "archive",
    "archives", "home", "index.do",
}
_ARTICLE_PATH_MARKERS = {
    "article", "articleview", "view", "read", "newsview", "board", "post",
}

SOURCE_LABELS = {
    "youtube": "YouTube",
    "naver_news": "뉴스",
    "naver_blog": "NAVER 블로그",
    "daum_web": "Daum 웹문서",
    "daum_cafe": "Daum 카페",
    "google_trends": "Google Trends",
    "wikipedia_pageviews": "위키백과 조회수",
}

RECOMMENDATION_LABELS = {
    "recommended": "추천",
    "review": "검토",
    "hold": "보류",
}

_RANKING_ALGORITHM_VERSION = "20"
_NUMBERED_IDENTITY_PATTERN = re.compile(r"^\d+(?:주|회|차)$")
_STRONG_NUMBERED_IDENTITY_PATTERN = re.compile(
    r"^(?P<number>\d{3,})(?P<unit>회|차)$"
)
_PRODUCT_IDENTITY_PATTERN = re.compile(
    r"^(?=[a-z0-9._+-]*[a-z])(?=[a-z0-9._+-]*\d)[a-z0-9]+(?:[._+-][a-z0-9]+)*$",
    re.IGNORECASE,
)
_MONEY_IDENTITY_PATTERN = re.compile(
    r"^(?P<value>\d+(?:\.\d+)?)(?P<scale>만|억|조)?(?P<currency>원|달러|불)$"
)
_MONEY_SCALE = {"": 1.0, "만": 1e4, "억": 1e8, "조": 1e12}
_CALENDAR_IDENTITY_PATTERN = re.compile(
    r"^(?:\d{1,4}(?:년|월|일|시|분|초)|"
    r"(?:\d{4}년)?\d{1,2}월\d{1,2}일(?:(?:월|화|수|목|금|토|일)요일)?|"
    r"(?:월|화|수|목|금|토|일)요일|"
    r"\d{1,4}(?:[./-]\d{1,2}){1,2})$"
)
_GENERIC_EDITORIAL_IDENTITY_TERMS = {
    "업데이트", "안내", "공지", "소식", "정보", "정리", "관련", "주요",
    "오늘", "오늘의", "내일", "어제", "요일", "뉴스", "브리핑", "헤드라인",
    "운세", "날씨", "명언", "띠별", "별자리", "오늘의운세", "오늘운세",
    "일일운세", "띠별운세", "별자리운세", "주간운세", "월간운세",
    "블로그", "콘텐츠", "글감", "주제", "아이디어", "추천", "방법",
    "후기", "반응", "비교", "결과", "발표", "일정", "정답", "사용법",
    "가격", "혜택", "뭘", "무엇", "어떻게", "왜", "할까", "올릴까",
    "써볼까", "좋을까",
    "latest", "recent", "update", "notice", "news", "headline", "blog",
    "content", "topic", "idea", "ideas", "recommendation", "review",
}
_GENERIC_SOURCE_NAMES = {
    "네이버 뉴스 검색", "네이버 블로그 검색", "daum 웹문서 검색", "daum 카페 검색",
    "google trends 한국", "위키백과 pageviews",
}
_SOURCE_ROLES = {
    "youtube": "video",
    "naver_news": "factual",
    "naver_blog": "community",
    "daum_web": "web",
    "daum_cafe": "community",
    "google_trends": "discovery",
    "wikipedia_pageviews": "public_interest",
}

DEFAULT_ANALYSIS_SOURCE_LIMITS = {
    "youtube": 2000,
    "naver": 4000,
    "daum": 4000,
    "google_trends": 500,
    "wikipedia": 500,
}

AI_CLUSTERING_ENABLED_SETTING = "trend_ai_clustering_enabled"
AI_CLUSTERING_MAX_ITEMS_SETTING = "trend_ai_clustering_max_items"
AI_CLUSTERING_BATCH_SIZE_SETTING = "trend_ai_clustering_batch_size"
AI_CLUSTERING_MAX_BATCHES_SETTING = "trend_ai_clustering_max_batches"
LEGACY_AI_CLUSTER_REVIEW_ENABLED_SETTING = "trend_ai_cluster_review_enabled"
LEGACY_AI_CLUSTER_REVIEW_BATCH_SIZE_SETTING = "trend_ai_cluster_review_batch_size"
DEFAULT_AI_CLUSTERING_MAX_ITEMS = 4000
DEFAULT_AI_CLUSTERING_BATCH_SIZE = 200
DEFAULT_AI_CLUSTERING_MAX_BATCHES = 5
AI_CLUSTERING_MAX_ATTEMPTS = 3
AI_EXISTING_CLUSTER_CANDIDATE_LIMIT = 5

_ANALYSIS_SOURCE_GROUPS = {
    "youtube": ("youtube",),
    "naver": ("naver_news", "naver_blog"),
    "daum": ("daum_web", "daum_cafe"),
    "google_trends": ("google_trends",),
    "wikipedia": ("wikipedia_pageviews",),
}

_BALANCED_ANALYSIS_GROUPS = {"naver", "daum"}
_BALANCED_SUBTYPE_MIN_SHARE = 0.30
_BALANCED_QUERY_MAX_SHARE = 0.10
_BALANCED_CANDIDATE_MULTIPLIER = 2
_BALANCED_CANDIDATE_MIN_EXTRA = 100


class ListSignalAdapter:
    def __init__(self, signals: list[dict[str, Any]]):
        self.signals = signals

    def load_signals(self, limit: int = 100) -> list[dict[str, Any]]:
        return self.signals[: max(1, int(limit))]


@dataclass(frozen=True)
class TrendRankingPreparation:
    status: str
    items: tuple[dict[str, Any], ...]
    signature: str
    source_item_count: int
    existing_cluster_count: int
    started_at: float
    ai_clustering_enabled: bool = False
    ai_clustering_model: str = ""
    ai_clustering_max_items: int = DEFAULT_AI_CLUSTERING_MAX_ITEMS
    ai_clustering_batch_size: int = DEFAULT_AI_CLUSTERING_BATCH_SIZE
    ai_clustering_max_batches: int = DEFAULT_AI_CLUSTERING_MAX_BATCHES
    ai_clustering_api_key_configured: bool = False
    existing_clusters: tuple[dict[str, Any], ...] = ()
    selected_source_item_ids: tuple[str, ...] = ()
    pending_item_count: int = 0
    processed_item_count: int = 0
    needs_review_item_count: int = 0
    processing_attempts: tuple[tuple[str, int], ...] = ()
    processing_feature_id: str = ""
    processing_feature_version: str = ""
    processing_model: str = ""
    processing_hash_prefix: str = ""


@dataclass(frozen=True)
class TrendRankingCalculation:
    preparation: TrendRankingPreparation
    cluster_rows: tuple[dict[str, Any], ...]
    cluster_item_rows: tuple[dict[str, Any], ...]
    analysis_seconds: float
    ai_clustering: dict[str, Any] = field(default_factory=dict)
    ai_clustering_calls: tuple[dict[str, Any], ...] = ()
    processing_rows: tuple[dict[str, Any], ...] = ()
    batch_log: dict[str, Any] = field(default_factory=dict)


def _clean_title(value: str) -> str:
    return clean_text(value)


def _tokens(value: str) -> list[str]:
    return [token for token in tokenize(value) if len(token) >= 2 and not token.isdigit()]


def _to_non_negative_float(value: Any) -> float:
    """점수 계산에 안전한 0 이상의 유한 실수만 반환합니다."""
    try:
        numeric = float(value or 0)
    except (TypeError, ValueError, OverflowError):
        return 0.0
    if not math.isfinite(numeric) or numeric <= 0:
        return 0.0
    return numeric


def _safe_log10p(value: Any) -> float:
    return math.log10(_to_non_negative_float(value) + 1.0)


def _ranking_day() -> str:
    """시간 경과 감쇠가 데이터 변경 없이도 하루 한 번 재계산되게 합니다."""
    return datetime.now().date().isoformat()


def _jaccard(left: set[str], right: set[str]) -> float:
    if not left or not right:
        return 0.0
    return len(left & right) / len(left | right)


def _string_similarity(left: str, right: str) -> float:
    if not left or not right:
        return 0.0
    if rapidfuzz_ratio is not None:
        return float(rapidfuzz_ratio(left, right)) / 100.0
    return SequenceMatcher(None, left, right).ratio()


def _item_time(item: dict[str, Any]) -> datetime:
    for key in ("published_at", "observed_at", "imported_at"):
        value = item.get(key)
        if isinstance(value, datetime):
            return value
    return datetime.min


def _is_specific_query(value: str) -> bool:
    clean = strip_collection_scope(value)
    if not clean or clean in _GENERIC_SEEDS:
        return False
    folded = clean.casefold()
    if folded in {item.casefold() for item in _GENERIC_QUERY_TERMS}:
        return False
    # YouTube 교환 데이터의 "인기영상:KR:전체" 같은 수집 범위 표시는
    # 실제 주제가 아니므로 서로 무관한 영상들을 하나로 묶는 기준으로 쓰지 않습니다.
    if ":" in clean and any(term.casefold() in folded for term in _GENERIC_QUERY_TERMS):
        return False
    return is_specific_topic(clean) and bool(_editorial_identity_tokens(clean))


def _canonical_title(item: dict[str, Any]) -> str:
    """영상 제목보다 정제된 주제명·검색어를 우선해 대표 주제명을 만듭니다."""
    metadata = item.get("metadata") or {}
    raw_title = strip_collection_scope(str(item.get("raw_title") or ""))
    item_title = strip_collection_scope(
        str(metadata.get("item_title") or item.get("item_title") or raw_title)
    )
    query = _clean_title(str(metadata.get("discovery_query") or metadata.get("keyword") or ""))
    source_type = str(item.get("source_type") or "")
    signal_type = str(metadata.get("signal_type") or "")

    if source_type == "youtube":
        if signal_type == "emerging_topic" and raw_title:
            return raw_title
        if raw_title and raw_title.casefold() != item_title.casefold():
            return raw_title
        if _is_specific_query(query):
            return query

    # 포털 검색어는 수집 범위일 뿐입니다. 실제 제목이 검색어를 뒷받침하는지는
    # 별도 비교 단계에서 확인하고, 여기서는 원문 제목을 우선합니다.
    return item_title or raw_title or (query if _is_specific_query(query) else "")


def _title_quality(title: str) -> tuple[float, list[str]]:
    clean = _clean_title(title)
    score = 72.0
    reasons: list[str] = []

    if not clean:
        return 0.0, ["대표 제목이 비어 있음"]

    tokens = _tokens(clean)
    identities = identity_tokens(clean)
    editorial_identities = (
        set() if clean.startswith("구체적 주제 확인 필요")
        else _editorial_identity_tokens(identities)
    )
    if 6 <= len(clean) <= 42:
        score += 8
    elif len(clean) > 80:
        score -= 20
        reasons.append("제목이 지나치게 김")
    elif len(clean) < 4:
        score -= 20
        reasons.append("제목이 지나치게 짧음")

    hashtag_count = clean.count("#")
    if hashtag_count >= 4:
        score -= min(30.0, hashtag_count * 4.0)
        reasons.append(f"해시태그가 많음 ({hashtag_count}개)")

    if _REPEAT_PUNCT_PATTERN.search(clean):
        score -= 12
        reasons.append("반복 특수문자가 많음")

    alpha_chars = [char for char in clean if char.isalpha() and char.isascii()]
    uppercase_chars = [char for char in alpha_chars if char.isupper()]
    if len(alpha_chars) >= 8 and len(uppercase_chars) / len(alpha_chars) >= 0.75:
        score -= 14
        reasons.append("영문 대문자 비율이 높음")

    noise_hits = sorted(set(token.casefold() for token in tokens) & _NOISE_TERMS)
    if noise_hits:
        score -= min(20.0, len(noise_hits) * 7.0)
        reasons.append("라이브·쇼츠성 표현 포함: " + ", ".join(noise_hits))

    korean_count = sum("가" <= char <= "힣" for char in clean)
    if korean_count == 0 and len(clean) >= 10:
        score -= 10
        reasons.append("한국어 설명형 글감으로 바로 쓰기 어려움")

    if len(tokens) <= 1:
        score -= 8
        reasons.append("주제를 설명할 핵심 단어가 부족함")

    if not identities:
        score -= 42
        reasons.append("일반 범주 표현만 있어 구체적인 대상 확인이 필요함")
    elif not editorial_identities:
        score -= 58
        reasons.append("날짜·요일·업데이트·안내 표현만 있어 실제 글감 대상이 없음")
    if clean.startswith("구체적 주제 확인 필요"):
        score = min(score, 32.0)
        reasons.append("근거가 부족해 투명한 확인 필요 제목을 사용함")

    if not reasons:
        reasons.append("대표 제목이 비교적 명확함")
    return max(0.0, min(100.0, score)), reasons


def _is_entity_only_title(
    title: str,
    editorial_identities: set[str] | None = None,
) -> bool:
    # 사건 설명 없이 이름·브랜드·프로그램명만 남은 짧은 제목인지 확인합니다.
    clean = _clean_title(title)
    identities = set(
        editorial_identities
        if editorial_identities is not None
        else _editorial_identity_tokens(clean)
    )
    if not clean or not identities:
        return False
    if len(clean) > 28 or len(identities) > 4:
        return False
    folded = clean.casefold()
    return not any(term.casefold() in folded for term in _EVENT_CONTEXT_TERMS)


def _normalized_analysis_limits(source_limits: dict[str, int] | None = None) -> dict[str, int]:
    limits = dict(DEFAULT_ANALYSIS_SOURCE_LIMITS)
    if source_limits:
        for key, value in source_limits.items():
            if key in limits:
                limits[key] = max(10, min(int(value), 20000))
    return limits


def _analysis_query_key(item: dict[str, Any]) -> str:
    query = strip_collection_scope(str(item.get("query") or ""))
    if not query:
        return "__missing_query__"
    return normalize_title(query).casefold() or "__missing_query__"


def _balanced_candidate_limit(limit: int) -> int:
    """검색어 균형 선택 전에 충분한 후보를 읽되 조회량은 보수적으로 제한합니다."""
    normalized_limit = max(1, int(limit))
    return min(
        20000,
        max(
            normalized_limit * _BALANCED_CANDIDATE_MULTIPLIER,
            normalized_limit + _BALANCED_CANDIDATE_MIN_EXTRA,
        ),
    )


def _select_balanced_analysis_items(
    items: list[dict[str, Any]],
    *,
    source_types: tuple[str, ...],
    limit: int,
) -> list[dict[str, Any]]:
    """포털 유형과 검색어가 최근 표본을 독점하지 않도록 균형 있게 고릅니다."""
    normalized_limit = max(1, int(limit))
    ordered = sorted(items, key=_item_time, reverse=True)
    if len(ordered) <= normalized_limit:
        return ordered
    if len(source_types) <= 1:
        return ordered[:normalized_limit]

    subtype_share = min(_BALANCED_SUBTYPE_MIN_SHARE, 1.0 / len(source_types))
    subtype_floor = max(1, int(normalized_limit * subtype_share))
    query_cap = max(1, math.ceil(normalized_limit * _BALANCED_QUERY_MAX_SHARE))

    selected: list[dict[str, Any]] = []
    selected_ids: set[str] = set()
    query_counts: Counter[str] = Counter()
    subtype_counts: Counter[str] = Counter()

    def item_key(item: dict[str, Any]) -> str:
        return str(item.get("source_item_id") or item.get("normalized_url") or id(item))

    def append_item(item: dict[str, Any], *, enforce_query_cap: bool) -> bool:
        key = item_key(item)
        if key in selected_ids or len(selected) >= normalized_limit:
            return False
        query_key = _analysis_query_key(item)
        if enforce_query_cap and query_key and query_counts[query_key] >= query_cap:
            return False
        selected.append(item)
        selected_ids.add(key)
        subtype_counts[str(item.get("source_type") or "")] += 1
        if query_key:
            query_counts[query_key] += 1
        return True

    # 1단계: 각 포털 세부 유형에 최소 몫을 보장합니다.
    for source_type in source_types:
        pool = [item for item in ordered if item.get("source_type") == source_type]
        target = min(subtype_floor, len(pool))
        for item in pool:
            if subtype_counts[source_type] >= target:
                break
            append_item(item, enforce_query_cap=True)
        if subtype_counts[source_type] < target:
            for item in pool:
                if subtype_counts[source_type] >= target:
                    break
                append_item(item, enforce_query_cap=False)

    # 2단계: 남는 자리는 최신순으로 채우되 검색어별 상한을 우선 적용합니다.
    for item in ordered:
        if len(selected) >= normalized_limit:
            break
        append_item(item, enforce_query_cap=True)

    # 3단계: 검색어 종류가 부족해 표본이 비면 최신 자료로 끝까지 채웁니다.
    for item in ordered:
        if len(selected) >= normalized_limit:
            break
        append_item(item, enforce_query_cap=False)

    selected.sort(key=_item_time, reverse=True)
    return selected


def _parse_source_rows(
    con: duckdb.DuckDBPyConnection,
    lookback_hours: int,
    *,
    source_limits: dict[str, int] | None = None,
) -> list[dict[str, Any]]:
    cutoff = datetime.now() - timedelta(hours=max(6, int(lookback_hours)))
    limits = _normalized_analysis_limits(source_limits)
    grouped_rows: list[tuple[str, tuple[str, ...], list[tuple[Any, ...]]]] = []
    columns: list[str] | None = None

    for group_name, source_types in _ANALYSIS_SOURCE_GROUPS.items():
        placeholders = ", ".join("?" for _ in source_types)
        fields = """
            source_item_id, source_type, raw_title, source_url, normalized_url, source_name,
            published_at, observed_at, signal_value, metadata_json,
            first_imported_at, previous_imported_at, last_imported_at,
            observation_count, imported_at
        """
        if group_name in _BALANCED_ANALYSIS_GROUPS and len(source_types) > 1:
            # 최종 한도만큼만 먼저 읽으면 최신 검색어 하나가 후보 전체를
            # 독점할 수 있으므로, 검색어 균형 선택 전에 제한된 여유 후보를 읽습니다.
            candidate_limit = _balanced_candidate_limit(limits[group_name])
            group_rows = con.execute(
                f"""
                SELECT {fields}
                FROM (
                    SELECT {fields},
                           ROW_NUMBER() OVER (
                               PARTITION BY source_type
                               ORDER BY COALESCE(published_at, observed_at, imported_at) DESC
                           ) AS source_rank
                    FROM source_items
                    WHERE source_type IN ({placeholders})
                      AND COALESCE(published_at, observed_at, imported_at) >= ?
                ) AS ranked_source_items
                WHERE source_rank <= ?
                ORDER BY COALESCE(published_at, observed_at, imported_at) DESC
                """,
                [*source_types, cutoff, candidate_limit],
            ).fetchall()
        else:
            group_rows = con.execute(
                f"""
                SELECT {fields}
                FROM source_items
                WHERE source_type IN ({placeholders})
                  AND COALESCE(published_at, observed_at, imported_at) >= ?
                ORDER BY COALESCE(published_at, observed_at, imported_at) DESC
                LIMIT ?
                """,
                [*source_types, cutoff, limits[group_name]],
            ).fetchall()
        if columns is None:
            columns = [item[0] for item in con.description]
        grouped_rows.append((group_name, source_types, group_rows))

    result: list[dict[str, Any]] = []
    for group_name, source_types, rows in grouped_rows:
        group_items: list[dict[str, Any]] = []
        for row in rows:
            item = dict(zip(columns or [], row))
            try:
                item["metadata"] = json.loads(item.pop("metadata_json") or "{}")
            except json.JSONDecodeError:
                item["metadata"] = {}
            item["item_title"] = strip_collection_scope(
                str(item["metadata"].get("item_title") or item["raw_title"] or "")
            )
            item["canonical_title"] = _canonical_title(item)
            comparison_title = item["canonical_title"] or item["item_title"] or item["raw_title"]
            item["normalized_title"] = normalize_title(comparison_title)
            item["compact_title"] = compact_title(comparison_title)
            item["identity_tokens"] = identity_tokens(comparison_title)
            item["editorial_identity_tokens"] = _editorial_identity_tokens(item["identity_tokens"])
            item["calendar_identity_tokens"] = _calendar_identity_tokens(item["identity_tokens"])
            item["tokens"] = set(_tokens(comparison_title))
            item["normalized_url"] = str(item.get("normalized_url") or "") or normalize_url(
                str(item.get("source_url") or "")
            )
            item["domain"] = source_domain(str(item.get("source_url") or ""))
            query = str(
                item["metadata"].get("discovery_query")
                or item["metadata"].get("keyword")
                or ""
            )
            item["query"] = strip_collection_scope(query)
            item["query_identity_tokens"] = identity_tokens(item["query"])
            item["query_supported"] = _query_is_supported_by_item(item)
            group_items.append(item)

        if group_name in _BALANCED_ANALYSIS_GROUPS:
            result.extend(
                _select_balanced_analysis_items(
                    group_items,
                    source_types=source_types,
                    limit=limits[group_name],
                )
            )
        else:
            group_items.sort(key=_item_time, reverse=True)
            result.extend(group_items[: limits[group_name]])

    result.sort(key=_item_time, reverse=True)
    return result


def _query_is_supported_by_item(item: dict[str, Any]) -> bool:
    query = str(item.get("query") or "")
    if not _is_specific_query(query):
        return False
    query_tokens = _editorial_identity_tokens(
        set(item.get("query_identity_tokens") or identity_tokens(query))
    )
    title_tokens = set(item.get("editorial_identity_tokens") or ())
    query_compact = compact_title(query)
    title_compact = str(item.get("compact_title") or "")
    return bool(
        query_tokens
        and (
            query_tokens <= title_tokens
            or (len(query_compact) >= 5 and query_compact in title_compact)
            or normalize_title(query) == str(item.get("normalized_title") or "")
        )
    )


def _calendar_identity_tokens(value: str | set[str]) -> set[str]:
    tokens = set(value) if isinstance(value, set) else identity_tokens(value)
    return {token for token in tokens if _CALENDAR_IDENTITY_PATTERN.fullmatch(token)}


_EDITORIAL_GENERIC_PARTICLES = (
    "에서부터", "으로부터", "에게서", "까지", "부터", "에게", "에서",
    "으로", "처럼", "보다", "에는", "은", "는", "이", "가", "을", "를",
    "의", "에", "로", "와", "과", "도", "만",
)


def _is_generic_editorial_identity(token: str) -> bool:
    folded = token.casefold()
    if folded in _GENERIC_EDITORIAL_IDENTITY_TERMS:
        return True
    if token and all("가" <= char <= "힣" for char in token):
        for particle in _EDITORIAL_GENERIC_PARTICLES:
            if folded.endswith(particle) and len(folded) - len(particle) >= 2:
                if folded[: -len(particle)] in _GENERIC_EDITORIAL_IDENTITY_TERMS:
                    return True
    return False


def _editorial_identity_tokens(value: str | set[str]) -> set[str]:
    """날짜·요일·일반 안내 표현을 제외한 실제 글감 식별 토큰입니다."""
    if isinstance(value, str) and strip_collection_scope(value).startswith("구체적 주제 확인 필요"):
        return set()
    tokens = set(value) if isinstance(value, set) else identity_tokens(value)
    return {
        token
        for token in tokens
        if not _CALENDAR_IDENTITY_PATTERN.fullmatch(token)
        and not _is_generic_editorial_identity(token)
    }


def _has_versioned_identity(tokens: set[str]) -> bool:
    """GPT-5.6처럼 문자와 숫자가 함께 있는 식별자는 단독이어도 구체적입니다."""
    return any(
        any(char.isalpha() for char in token)
        and any(char.isdigit() for char in token)
        for token in tokens
    )



def _calendar_identity_parts(tokens: set[str]) -> dict[str, set[str]]:
    parts: dict[str, set[str]] = defaultdict(set)
    for token in tokens:
        if re.fullmatch(r"\d{1,4}년", token):
            parts["year"].add(token)
        elif re.fullmatch(r"\d{1,2}월", token):
            parts["month"].add(token)
        elif re.fullmatch(r"\d{1,2}일", token):
            parts["day"].add(token)
        elif re.fullmatch(r"(?:월|화|수|목|금|토|일)요일", token):
            parts["weekday"].add(token)
        elif re.fullmatch(
            r"(?:\d{4}년)?\d{1,2}월\d{1,2}일(?:(?:월|화|수|목|금|토|일)요일)?",
            token,
        ):
            parts["date"].add(token)
        elif re.fullmatch(r"\d{1,4}(?:[./-]\d{1,2}){1,2}", token):
            parts["date"].add(token)
    return parts


def _has_conflicting_calendar_identity(left: set[str], right: set[str]) -> bool:
    left_parts = _calendar_identity_parts(left)
    right_parts = _calendar_identity_parts(right)
    for key in set(left_parts) & set(right_parts):
        if left_parts[key].isdisjoint(right_parts[key]):
            return True
    return False


def _has_conflicting_numbered_identity(left: set[str], right: set[str]) -> bool:
    """회차·차수·기간 번호가 다르면 제목 유사도만으로 합치지 않습니다."""
    left_numbers = {token for token in left if _NUMBERED_IDENTITY_PATTERN.fullmatch(token)}
    right_numbers = {token for token in right if _NUMBERED_IDENTITY_PATTERN.fullmatch(token)}
    return bool(left_numbers and right_numbers and left_numbers.isdisjoint(right_numbers))


def _strong_numbered_identity_tokens(tokens: set[str]) -> set[str]:
    """1235회처럼 단독으로도 특정 회차를 가리키는 긴 번호만 반환합니다."""
    return {
        token
        for token in tokens
        if _STRONG_NUMBERED_IDENTITY_PATTERN.fullmatch(token)
    }


def _has_shared_numbered_context(
    left_tokens: set[str],
    right_tokens: set[str],
) -> bool:
    """같은 긴 회차 외에도 로또/로또복권 같은 대상 표현이 겹치는지 확인합니다."""
    left_numbers = _strong_numbered_identity_tokens(left_tokens)
    right_numbers = _strong_numbered_identity_tokens(right_tokens)
    if not (left_numbers & right_numbers):
        return False
    left_context = left_tokens - left_numbers
    right_context = right_tokens - right_numbers
    return any(
        left == right
        or (
            min(len(left), len(right)) >= 2
            and (left in right or right in left)
        )
        for left in left_context
        for right in right_context
    )


def _product_identity_tokens(tokens: set[str]) -> set[str]:
    return {
        token.casefold()
        for token in tokens
        if _PRODUCT_IDENTITY_PATTERN.fullmatch(token)
    }


def _money_identity_values(tokens: set[str]) -> dict[str, set[float]]:
    values: dict[str, set[float]] = defaultdict(set)
    for token in tokens:
        match = _MONEY_IDENTITY_PATTERN.fullmatch(token.replace(",", ""))
        if not match:
            continue
        currency = "usd" if match.group("currency") in {"달러", "불"} else "krw"
        scale = _MONEY_SCALE[match.group("scale") or ""]
        values[currency].add(float(match.group("value")) * scale)
    return values


def _has_conflicting_event_facts(left: set[str], right: set[str]) -> bool:
    """서로 다른 제품명이나 크게 다른 동일 통화 금액이면 병합하지 않습니다."""
    left_products = _product_identity_tokens(left)
    right_products = _product_identity_tokens(right)
    if left_products and right_products and left_products.isdisjoint(right_products):
        return True

    left_money = _money_identity_values(left)
    right_money = _money_identity_values(right)
    for currency in set(left_money) & set(right_money):
        # 290조/292조처럼 반올림 차이는 허용하되 100억/200억처럼
        # 핵심 규모가 다른 계약은 같은 사건으로 보지 않습니다.
        if all(
            max(left_value, right_value) / max(1.0, min(left_value, right_value)) > 1.25
            for left_value in left_money[currency]
            for right_value in right_money[currency]
        ):
            return True
    return False


def _candidate_support(
    candidate: str,
    items: list[dict[str, Any]],
) -> tuple[int, int, int]:
    candidate_ids = _editorial_identity_tokens(candidate)
    candidate_compact = compact_title(candidate)
    supported: list[dict[str, Any]] = []
    for item in items:
        item_ids = set(item.get("editorial_identity_tokens") or ())
        item_compact = str(item.get("compact_title") or "")
        shared_ids = candidate_ids & item_ids
        shared_event_description = bool(
            len(shared_ids) >= 3
            and len(shared_ids) / max(1, len(candidate_ids)) >= 0.6
        )
        if candidate_ids and (
            candidate_ids <= item_ids
            or shared_event_description
            or (
                len(candidate_compact) >= 5
                and (candidate_compact in item_compact or item_compact in candidate_compact)
            )
        ):
            supported.append(item)
    return (
        len(supported),
        len({str(item.get("source_type") or "") for item in supported}),
        len(_evidence_groups(supported)),
    )



def _generate_topic_title(items: list[dict[str, Any]]) -> str:
    candidates: list[tuple[str, bool]] = []
    for item in items:
        query = strip_collection_scope(str(item.get("query") or ""))
        if item.get("query_supported") and query:
            candidates.append((query, True))
        for title_value in (
            item.get("canonical_title"),
            item.get("raw_title"),
            item.get("item_title"),
        ):
            title = strip_collection_scope(str(title_value or ""))
            if title:
                candidates.append((title, False))

    evaluated_candidates: list[tuple[str, bool, int, int, int, float]] = []
    for candidate, is_query in dict.fromkeys(candidates):
        if not _editorial_identity_tokens(candidate):
            continue
        support_count, source_count, evidence_count = _candidate_support(candidate, items)
        if support_count == 0:
            continue
        title_quality, _ = _title_quality(candidate)
        if support_count == 1 and source_count == 1 and title_quality < 65:
            continue
        evaluated_candidates.append(
            (
                candidate,
                is_query,
                support_count,
                source_count,
                evidence_count,
                title_quality,
            )
        )

    has_repeated_specific_candidate = any(
        evidence_count >= 2 and not _is_entity_only_title(candidate)
        for (
            candidate,
            _,
            _,
            _,
            evidence_count,
            _,
        ) in evaluated_candidates
    )
    best_title = ""
    best_score = -1.0
    for (
        candidate,
        is_query,
        support_count,
        source_count,
        evidence_count,
        title_quality,
    ) in evaluated_candidates:
        entity_only = _is_entity_only_title(candidate)
        if has_repeated_specific_candidate and entity_only:
            continue
        korean_count = sum("가" <= char <= "힣" for char in candidate)
        score = (
            support_count * 7
            + source_count * 5
            + min(5, evidence_count) * 4
            + title_quality * 0.16
            + (8 if is_query and support_count >= 1 else 0)
            + (5 if korean_count >= 2 and len(candidate) <= 55 else 0)
            - (24 if entity_only else 0)
            - max(0, len(candidate) - 60) * 0.35
        )
        if score > best_score or (score == best_score and len(candidate) < len(best_title)):
            best_score, best_title = score, candidate

    if best_title:
        return best_title

    if all(
        not strip_collection_scope(
            str(item.get("item_title") or item.get("raw_title") or "")
        )
        for item in items
    ):
        return "구체적 주제 확인 필요 · 수집 범위 신호"

    identity_counts: Counter[str] = Counter()
    for item in items:
        identity_counts.update(
            _editorial_identity_tokens(str(item.get("item_title") or item.get("raw_title") or ""))
        )
    if identity_counts:
        label = identity_counts.most_common(1)[0][0]
        return f"구체적 주제 확인 필요 · {label.upper() if label.isascii() else label} 관련 신호"

    generic_tokens = []
    for item in items:
        for token in _tokens(str(item.get("item_title") or item.get("raw_title") or "")):
            if token.casefold() in GENERIC_IDENTITY_TERMS:
                generic_tokens.append(token)
    if generic_tokens:
        label = Counter(generic_tokens).most_common(1)[0][0]
        return f"구체적 주제 확인 필요 · {label.upper() if label.isascii() else label} 관련 신호"
    return "구체적 주제 확인 필요 · 수집 근거 부족"


def _youtube_momentum(items: list[dict[str, Any]]) -> float:
    best = 0.0
    now = datetime.now()
    for item in items:
        if item.get("source_type") != "youtube":
            continue
        meta = item.get("metadata") or {}
        topic_score = _to_non_negative_float(meta.get("topic_score") or item.get("signal_value") or 0)
        views_per_hour = _to_non_negative_float(meta.get("views_per_hour") or 0)
        view_delta = _to_non_negative_float(meta.get("view_delta") or 0)
        age_hours = max(0.0, (now - _item_time(item)).total_seconds() / 3600)
        decay = math.pow(0.5, age_hours / 24.0)
        score = (
            min(4.0, topic_score * 0.45)
            + min(3.5, _safe_log10p(views_per_hour))
            + min(2.5, _safe_log10p(view_delta) * 0.55)
        ) * decay
        best = max(best, score)
    return min(10.0, best)


def _external_interest_momentum(items: list[dict[str, Any]]) -> float:
    """Google 검색 급상승과 위키백과 조회수 신호를 작은 검증 가산점으로 반영합니다."""
    google_best = 0.0
    wikipedia_best = 0.0
    for item in items:
        source_type = str(item.get("source_type") or "")
        metadata = item.get("metadata") or {}
        if source_type == "google_trends":
            traffic = _to_non_negative_float(metadata.get("traffic_count") or item.get("signal_value") or 0)
            google_best = max(google_best, min(6.0, _safe_log10p(traffic) * 1.2))
        elif source_type == "wikipedia_pageviews":
            views = _to_non_negative_float(metadata.get("views") or item.get("signal_value") or 0)
            rank = max(1.0, _to_non_negative_float(metadata.get("rank") or 1000) or 1000.0)
            rank_score = max(0.0, 4.0 * (1.0 - min(rank, 100.0) / 100.0))
            view_score = min(3.0, _safe_log10p(views) * 0.65)
            wikipedia_best = max(wikipedia_best, min(4.0, rank_score * 0.55 + view_score * 0.6))
    return min(10.0, google_best + wikipedia_best)


def _publisher_identity(item: dict[str, Any]) -> str:
    domain = str(item.get("domain") or source_domain(str(item.get("source_url") or "")))
    source_name = str(item.get("source_name") or "").strip()
    if domain and (not source_name or source_name.casefold() in _GENERIC_SOURCE_NAMES):
        return domain
    return source_name.casefold() or domain or str(item.get("source_type") or "unknown")


def _looks_like_navigation_page(item: dict[str, Any]) -> bool:
    # 기사·게시글이 아니라 섹션·홈·목록 페이지에 가까운 원문을 찾습니다.
    source_type = str(item.get("source_type") or "")
    if source_type not in {"daum_web", "naver_news"}:
        return False

    title = _clean_title(
        str(item.get("canonical_title") or item.get("raw_title") or "")
    )
    source_url = str(item.get("source_url") or "")
    if not title or not source_url:
        return False

    try:
        path = urlsplit(source_url).path.casefold()
    except ValueError:
        return False

    segments = [segment for segment in path.strip("/").split("/") if segment]
    if any(any(char.isdigit() for char in segment) for segment in segments):
        return False
    if any(marker in path for marker in _ARTICLE_PATH_MARKERS):
        return False

    folded_title = title.casefold()
    if any(marker in folded_title for marker in _STATIC_POLICY_TITLE_MARKERS):
        return True
    title_marker = (
        ">" in title
        or " - " in title
        or any(marker in folded_title for marker in _NAVIGATION_TITLE_MARKERS)
    )
    navigation_path = any(
        segment in _NAVIGATION_PATH_MARKERS for segment in segments
    )
    shallow_path = len(segments) <= 1
    identities = _editorial_identity_tokens(title)

    return bool(
        (navigation_path or shallow_path)
        and (title_marker or _is_entity_only_title(title, identities))
    )


def _navigation_page_ratio(
    evidence_groups: list[list[dict[str, Any]]],
) -> float:
    if not evidence_groups:
        return 0.0
    navigation_groups = sum(
        1
        for group in evidence_groups
        if any(_looks_like_navigation_page(item) for item in group)
    )
    return navigation_groups / len(evidence_groups)


def _evidence_groups(items: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    """같은 URL과 복제 수준의 제목은 점수 계산에서 한 근거 묶음으로 셉니다."""
    groups: list[list[dict[str, Any]]] = []
    for item in sorted(items, key=_item_time, reverse=True):
        matched = None
        for index, group in enumerate(groups):
            representative = group[0]
            source_type = str(item.get("source_type") or "")
            same_url = bool(
                source_type != "google_trends"
                and item.get("normalized_url")
                and item.get("normalized_url") == representative.get("normalized_url")
            )
            title_ratio = _string_similarity(
                str(item.get("normalized_title") or ""),
                str(representative.get("normalized_title") or ""),
            )
            shared_identity = bool(
                set(item.get("identity_tokens") or ())
                & set(representative.get("identity_tokens") or ())
            )
            if same_url or (title_ratio >= 0.93 and shared_identity):
                matched = index
                break
        if matched is None:
            groups.append([item])
        else:
            groups[matched].append(item)
    return groups


def _rediscovery_score(
    evidence_groups: list[list[dict[str, Any]]],
) -> tuple[float, int, float | None]:
    """같은 근거가 여러 수집 실행에서 반복 포착된 정도를 0~10점으로 환산합니다."""
    group_strengths: list[float] = []
    repeated_groups = 0
    observed_gaps: list[float] = []

    for group in evidence_groups:
        best_strength = 0.0
        for item in group:
            try:
                observation_count = max(
                    1,
                    int(item.get("observation_count") or 1),
                )
            except (TypeError, ValueError, OverflowError):
                observation_count = 1

            previous_at = item.get("previous_imported_at")
            last_at = item.get("last_imported_at") or item.get("imported_at")
            if (
                observation_count < 2
                or not isinstance(previous_at, datetime)
                or not isinstance(last_at, datetime)
            ):
                continue

            gap_hours = max(
                0.0,
                (last_at - previous_at).total_seconds() / 3600.0,
            )
            observed_gaps.append(gap_hours)
            gap_strength = max(
                0.12,
                1.0 - min(gap_hours, 168.0) / 192.0,
            )
            count_strength = min(
                1.0,
                math.log2(observation_count) / 4.0,
            )
            best_strength = max(
                best_strength,
                gap_strength * (0.45 + 0.55 * count_strength),
            )

        if best_strength > 0:
            repeated_groups += 1
            group_strengths.append(best_strength)

    if not group_strengths:
        return 0.0, 0, None

    strongest = sorted(group_strengths, reverse=True)[:5]
    average_strength = sum(strongest) / len(strongest)
    coverage = min(
        1.0,
        repeated_groups / max(2.0, len(evidence_groups) * 0.6),
    )
    points = min(
        10.0,
        7.0 * average_strength + 3.0 * coverage,
    )
    if repeated_groups == 1:
        points = min(4.0, points)
    median_gap = (
        sorted(observed_gaps)[len(observed_gaps) // 2]
        if observed_gaps
        else None
    )
    return round(points, 1), repeated_groups, median_gap


def _editorial_subject_support(
    title: str,
    evidence_groups: list[list[dict[str, Any]]],
) -> int:
    """대표 제목의 구체적 대상이 독립 근거 몇 묶음에서 확인되는지 계산합니다."""
    subject_tokens = _editorial_identity_tokens(title)
    subject_compact = compact_title(title)
    if not subject_tokens:
        return 0

    support = 0
    for group in evidence_groups:
        supported = False
        for item in group:
            item_title = str(item.get("canonical_title") or item.get("raw_title") or "")
            fallback_tokens = _editorial_identity_tokens(item_title)
            item_tokens = set(item.get("editorial_identity_tokens") or fallback_tokens)
            item_compact = str(item.get("compact_title") or compact_title(item_title))
            if subject_tokens <= item_tokens or (
                len(subject_compact) >= 5
                and (subject_compact in item_compact or item_compact in subject_compact)
            ):
                supported = True
                break
        if supported:
            support += 1
    return support


def _content_quality(
    title: str,
    items: list[dict[str, Any]],
    source_types: set[str],
    evidence_groups: list[list[dict[str, Any]]],
) -> tuple[float, str, list[str]]:
    score, reasons = _title_quality(title)
    editorial_identities = _editorial_identity_tokens(title)
    entity_only_title = _is_entity_only_title(
        title,
        editorial_identities,
    )
    navigation_page_cluster = _navigation_page_ratio(evidence_groups) >= 0.5
    signal_types = {
        str((item.get("metadata") or {}).get("signal_type") or "")
        for item in items
    }

    substantive_roles = {
        _SOURCE_ROLES.get(source_type, source_type)
        for source_type in source_types
        if _SOURCE_ROLES.get(source_type) not in {"discovery", "public_interest"}
    }
    if len(substantive_roles) >= 2:
        score += 14
        reasons.append("서로 다른 성격의 출처에서 반복 확인됨")
    if len(evidence_groups) >= 3:
        score += min(10.0, (len(evidence_groups) - 1) * 2.0)
        reasons.append(f"중복을 제외한 독립 근거가 {len(evidence_groups)}개임")
    if "emerging_topic" in signal_types:
        score += 10
        reasons.append("YouTube 떠오르는 주제 신호가 포함됨")
    if len(items) == 1 and source_types == {"youtube"}:
        score -= 16
        reasons.append("YouTube 단일 영상 신호만 존재함")
    public_only = source_types <= {"wikipedia_pageviews", "google_trends"}
    if public_only:
        score -= 24
        reasons.append(
            "검색·위키 관심 신호는 다른 원문을 뒷받침할 때만 추천 근거로 사용함"
        )

    duplicate_count = len(items) - len(evidence_groups)
    if duplicate_count > 0:
        score -= min(18.0, duplicate_count * 2.0)
        reasons.append(
            f"동일 URL·복제 제목 {duplicate_count}건은 중복 점수에서 제외함"
        )

    subject_support = _editorial_subject_support(title, evidence_groups)
    weak_subject_support = (
        len(evidence_groups) >= 2
        and subject_support < 2
    )
    if weak_subject_support:
        score -= 36
        reasons.append(
            "대표 주제의 구체적 대상이 독립 원문 2곳 이상에서 반복 확인되지 않음"
        )

    if entity_only_title:
        score = min(score, 68.0)
        reasons.append(
            "이름·브랜드·프로그램명만 남아 사건·변화 맥락 확인이 필요함"
        )

    if navigation_page_cluster:
        score = min(score, 32.0)
        reasons.append(
            "기사·글이 아닌 섹션·홈페이지형 원문 비중이 높아 글감에서 보류함"
        )

    if not editorial_identities:
        score = min(score, 22.0)
        reasons.append(
            "상세 원문을 확인해도 반복되는 구체적 대상이 없어 글감에서 보류함"
        )

    score = max(0.0, min(100.0, score))
    if (
        public_only
        or not editorial_identities
        or weak_subject_support
        or navigation_page_cluster
    ):
        status = "hold"
    elif entity_only_title:
        status = "review"
    elif score >= 70 and len(substantive_roles) >= 2:
        status = "recommended"
    elif score >= 45:
        status = "review"
    else:
        status = "hold"
    return round(score, 1), status, reasons


def _fact_risk_score(
    title: str,
    evidence_groups: list[list[dict[str, Any]]],
    publishers: set[str],
) -> tuple[float, list[str]]:
    """민감 분야·시점 의존·수치 주장과 근거 부족을 0~30점으로 계산합니다."""
    title_tokens = set(_tokens(title))
    sensitive_hits = sorted(title_tokens & _RISK_TERMS)
    time_sensitive_hits = sorted(title_tokens & _TIME_SENSITIVE_TERMS)
    claim_hits = [
        label
        for label, pattern in _FACT_CLAIM_PATTERNS
        if pattern.search(title)
    ]

    score = min(20.0, len(sensitive_hits) * 10.0)
    reasons: list[str] = []
    if sensitive_hits:
        reasons.append("민감 분야: " + ", ".join(sensitive_hits))
    if time_sensitive_hits:
        score += min(8.0, 4.0 + max(0, len(time_sensitive_hits) - 1) * 2.0)
        reasons.append("시점 의존: " + ", ".join(time_sensitive_hits))
    if claim_hits:
        score += min(6.0, len(claim_hits) * 3.0)
        reasons.append("수치 주장: " + ", ".join(claim_hits))

    factual_group_count = sum(
        1
        for group in evidence_groups
        if any(
            str(item.get("source_type") or "") in {"naver_news", "daum_web"}
            for item in group
        )
    )
    if reasons:
        if factual_group_count == 0:
            score += 6.0
            reasons.append("뉴스·웹 사실 근거 없음")
        elif len(evidence_groups) < 2 or len(publishers) < 2:
            score += 4.0
            reasons.append("독립 근거·발행처 부족")

    bounded = round(max(0.0, min(30.0, score)), 1)
    return bounded, reasons


def _score_cluster(cluster: dict[str, Any]) -> dict[str, Any]:
    items = cluster["items"]
    now = datetime.now()
    latest = max((_item_time(item) for item in items), default=now)
    age_values = [max(0.0, (now - _item_time(item)).total_seconds() / 3600) for item in items]
    decays = [math.pow(0.5, age / 24.0) for age in age_values]
    recency = 22.0 * (0.65 * max(decays, default=0.0) + 0.35 * (sum(decays) / max(1, len(decays))))
    evidence_groups = _evidence_groups(items)
    rediscovery, repeated_groups, median_rediscovery_gap = _rediscovery_score(evidence_groups)
    source_types = {str(item.get("source_type") or "") for item in items}
    roles = {_SOURCE_ROLES.get(source_type, source_type) for source_type in source_types}
    substantive_roles = roles - {"discovery", "public_interest"}
    publishers = {_publisher_identity(item) for item in items if _publisher_identity(item)}

    type_counts: Counter[str] = Counter()
    for group in evidence_groups:
        for source_type in {str(item.get("source_type") or "") for item in group}:
            type_counts[source_type] += 1
    volume = min(14.0, sum(min(3, count) ** 0.5 * 2.7 for count in type_counts.values()))
    confirmation = min(
        24.0,
        max(0, len(substantive_roles) - 1) * 7.0
        + max(0, len(source_types) - 1) * 2.5
        + min(7.0, max(0, len(publishers) - 1) * 1.4),
    )
    factual = min(12.0, (type_counts["naver_news"] + type_counts["daum_web"]) * 3.0)
    community = min(8.0, (type_counts["naver_blog"] + type_counts["daum_cafe"]) * 2.0)
    youtube = _youtube_momentum(items)
    external_interest = _external_interest_momentum(items)
    if not substantive_roles:
        external_interest *= 0.45

    same_domain_excess = sum(max(0, count - 2) for count in Counter(_publisher_identity(item) for item in items).values())
    duplicate_count = len(items) - len(evidence_groups)
    duplicate_penalty = min(12.0, duplicate_count * 1.8 + same_domain_excess * 0.6)

    title = str(cluster.get("title") or _generate_topic_title(items))
    quality, recommendation_status, quality_reasons = _content_quality(
        title,
        items,
        source_types,
        evidence_groups,
    )
    navigation_page_cluster = _navigation_page_ratio(evidence_groups) >= 0.5
    entity_only_title = _is_entity_only_title(title)
    if navigation_page_cluster:
        rediscovery = 0.0
    raw_total = (
        recency
        + confirmation
        + volume
        + factual
        + community
        + youtube
        + external_interest
        + rediscovery
        - duplicate_penalty
    )
    quality_multiplier = 0.58 + (quality / 100.0) * 0.42
    total = round(max(0.0, min(100.0, raw_total * quality_multiplier)), 1)

    title_tokens = set(_tokens(title))
    informative = 25.0 if title_tokens & _INFORMATIONAL_TERMS else 14.0
    reference_depth = min(25.0, len(evidence_groups) * 4 + len(substantive_roles) * 5)
    risk, risk_reasons = _fact_risk_score(title, evidence_groups, publishers)
    raw_opportunity = (
        recency * 0.75 + confirmation * 0.7 + informative + reference_depth
        + external_interest * 0.55 + rediscovery * 0.35
        - risk * 0.5 - duplicate_penalty * 0.6
    )
    opportunity = round(max(0.0, min(100.0, raw_opportunity * (0.65 + quality / 285.0))), 1)

    factual_only = source_types <= {"naver_news", "daum_web"}
    single_role_factual_review = bool(
        "naver_news" in source_types
        or (
            source_types == {"daum_web"}
            and len(publishers) >= 2
            and len(evidence_groups) >= 2
            and total >= 30
        )
    )
    if (
        recommendation_status == "recommended"
        and opportunity >= 45
        and total >= 52
        and len(substantive_roles) >= 2
    ):
        recommendation_status = "recommended"
    elif total >= 35 and len(substantive_roles) >= 2 and _editorial_identity_tokens(title):
        recommendation_status = "review"
    elif (
        factual_only
        and single_role_factual_review
        and total >= 24
        and quality >= 68
        and _editorial_identity_tokens(title)
    ):
        recommendation_status = "review"
    else:
        recommendation_status = "hold"
    if (
        source_types <= {"google_trends", "wikipedia_pageviews"}
        or not _editorial_identity_tokens(title)
        or navigation_page_cluster
    ):
        recommendation_status = "hold"
    elif entity_only_title and recommendation_status == "recommended":
        recommendation_status = "review"

    reasons = [
        f"최근성 감쇠 {recency:.1f}/22",
        f"독립 출처 교차 확인 {confirmation:.1f}/24 ({len(substantive_roles)}역할)",
        f"출처별 균형 근거량 {volume:.1f}/14 (중복 제외 {len(evidence_groups)}건)",
        f"뉴스·웹 사실 근거 {factual:.1f}/12",
        f"블로그·카페 반응 {community:.1f}/8",
        f"YouTube 확산 신호 {youtube:.1f}/10",
        f"검색·위키 보조 신호 {external_interest:.1f}/10",
        (
            f"반복 포착 보너스 +{rediscovery:.1f}/10 "
            f"({repeated_groups}개 독립 근거, 중앙 간격 {median_rediscovery_gap:.1f}시간)"
            if rediscovery > 0 and median_rediscovery_gap is not None
            else (
                "반복 포착 보너스 +0.0/10 "
                "(독립 반복 근거 2개 미만 또는 반복 근거 없음)"
            )
        ),
        f"중복·동일 도메인 감점 -{duplicate_penalty:.1f}",
        (
            f"사실 확인 위험 {risk:.1f}/30 (" + "; ".join(risk_reasons) + ")"
            if risk_reasons
            else "사실 확인 위험 0.0/30 (특별 위험 신호 없음)"
        ),
        f"콘텐츠 품질 보정 {quality:.1f}/100",
    ]
    return {
        "title": title,
        "latest": latest,
        "first": min((_item_time(item) for item in items), default=latest),
        "score": total,
        "opportunity": opportunity,
        "risk": risk,
        "quality": quality,
        "rediscovery": rediscovery,
        "recommendation_status": recommendation_status,
        "quality_reasons": quality_reasons,
        "source_types": sorted(source_types),
        "publisher_count": len(publishers),
        "effective_item_count": len(evidence_groups),
        "reasons": reasons,
    }



def _enabled_setting(value: Any, *, default: bool = True) -> bool:
    normalized = str(value or "").strip().casefold()
    if not normalized:
        return default
    return normalized in {"1", "true", "yes", "on", "enabled"}


def _bounded_setting_int(value: Any, *, default: int, minimum: int, maximum: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError, OverflowError):
        parsed = default
    return max(minimum, min(parsed, maximum))


def _ai_clustering_settings(con: duckdb.DuckDBPyConnection) -> dict[str, Any]:
    base = get_gemini_config()
    enabled_raw = get_setting(con, AI_CLUSTERING_ENABLED_SETTING, "")
    if not str(enabled_raw or "").strip():
        enabled_raw = get_setting(
            con,
            LEGACY_AI_CLUSTER_REVIEW_ENABLED_SETTING,
            "true",
        )
    batch_raw = get_setting(con, AI_CLUSTERING_BATCH_SIZE_SETTING, "")
    if not str(batch_raw or "").strip():
        batch_raw = get_setting(
            con,
            LEGACY_AI_CLUSTER_REVIEW_BATCH_SIZE_SETTING,
            str(DEFAULT_AI_CLUSTERING_BATCH_SIZE),
        )
    return {
        "enabled": _enabled_setting(enabled_raw),
        "model": get_selected_gemini_model(
            con,
            MODEL_PURPOSE_DATA_REVIEW,
            base_config=base,
        ),
        "max_items": _bounded_setting_int(
            get_setting(
                con,
                AI_CLUSTERING_MAX_ITEMS_SETTING,
                str(DEFAULT_AI_CLUSTERING_MAX_ITEMS),
            ),
            default=DEFAULT_AI_CLUSTERING_MAX_ITEMS,
            minimum=200,
            maximum=10000,
        ),
        "batch_size": _bounded_setting_int(
            batch_raw,
            default=DEFAULT_AI_CLUSTERING_BATCH_SIZE,
            minimum=20,
            maximum=200,
        ),
        "max_batches": _bounded_setting_int(
            get_setting(
                con,
                AI_CLUSTERING_MAX_BATCHES_SETTING,
                str(DEFAULT_AI_CLUSTERING_MAX_BATCHES),
            ),
            default=DEFAULT_AI_CLUSTERING_MAX_BATCHES,
            minimum=1,
            maximum=20,
        ),
        "api_key_configured": bool(base.api_key),
    }


def _candidate_item_title(item: dict[str, Any]) -> str:
    for field_name in ("canonical_title", "raw_title", "item_title"):
        value = strip_collection_scope(str(item.get(field_name) or ""))
        if value:
            return value
    return "구체적 주제 확인 필요 · 수집 근거 부족"


def _candidate_title_examples(items: Iterable[dict[str, Any]]) -> tuple[str, ...]:
    values: list[str] = []
    for item in sorted(items, key=_item_time, reverse=True):
        for field_name in ("canonical_title", "raw_title", "item_title"):
            value = strip_collection_scope(str(item.get(field_name) or ""))
            if value and value not in values:
                values.append(value)
        if len(values) >= 4:
            break
    return tuple(values[:4])


def _fallback_candidate_title(items: Iterable[dict[str, Any]]) -> str:
    ordered = sorted(items, key=_item_time, reverse=True)
    for item in ordered:
        title = _candidate_item_title(item)
        if _editorial_identity_tokens(title):
            return title
    return _candidate_item_title(ordered[0]) if ordered else "구체적 주제 확인 필요 · 수집 근거 부족"


def _safe_exact_title_key(items: Iterable[dict[str, Any]]) -> str:
    ordered = list(items)
    normalized_titles = {
        str(item.get("normalized_title") or "").strip()
        for item in ordered
        if str(item.get("normalized_title") or "").strip()
    }
    if len(normalized_titles) != 1:
        return ""
    title = next(iter(normalized_titles))
    identities = set().union(
        *(set(item.get("editorial_identity_tokens") or ()) for item in ordered)
    )
    if len(identities) < 2 and not _strong_numbered_identity_tokens(identities):
        return ""
    if _is_entity_only_title(title, identities):
        return ""
    return title


def _candidate_from_items(
    items: Iterable[dict[str, Any]],
    *,
    candidate_id: str = "",
    first_stage_kind: str = "single",
) -> dict[str, Any]:
    group_items = sorted(list(items), key=_item_time, reverse=True)
    source_ids = sorted(
        str(item.get("source_item_id") or "")
        for item in group_items
        if str(item.get("source_item_id") or "")
    )
    resolved_id = candidate_id or "stage1_" + hashlib.sha1(
        "|".join(source_ids).encode("utf-8")
    ).hexdigest()[:18]
    identity_tokens_value = set().union(
        *(set(item.get("identity_tokens") or ()) for item in group_items)
    )
    editorial_tokens = set().union(
        *(set(item.get("editorial_identity_tokens") or ()) for item in group_items)
    )
    calendar_tokens = set().union(
        *(set(item.get("calendar_identity_tokens") or ()) for item in group_items)
    )
    publishers = tuple(
        sorted(
            {
                str(item.get("source_name") or item.get("domain") or "").strip()
                for item in group_items
                if str(item.get("source_name") or item.get("domain") or "").strip()
            }
        )
    )
    return {
        "candidate_id": resolved_id,
        "title": _fallback_candidate_title(group_items),
        "examples": _candidate_title_examples(group_items)[:3],
        "source_types": tuple(
            sorted({str(item.get("source_type") or "") for item in group_items})
        ),
        "publishers": publishers,
        "first_seen_at": min((_item_time(item) for item in group_items), default=datetime.min),
        "last_seen_at": max((_item_time(item) for item in group_items), default=datetime.min),
        "identity_tokens": identity_tokens_value,
        "editorial_tokens": editorial_tokens,
        "calendar_tokens": calendar_tokens,
        "items": group_items,
        "item_count": len(group_items),
        "first_stage_kind": first_stage_kind,
    }


def _build_first_stage_candidates(
    items: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """같은 URL을 먼저 묶고 안전한 완전 동일 제목을 두 번째로 묶습니다."""
    ordered = sorted(
        items,
        key=lambda item: (_item_time(item), str(item.get("source_item_id") or "")),
        reverse=True,
    )
    url_groups: list[list[dict[str, Any]]] = []
    url_index: dict[str, list[int]] = defaultdict(list)
    url_merged_items = 0
    url_conflict_splits = 0
    for item in ordered:
        normalized_url = str(item.get("normalized_url") or "").strip()
        source_type = str(item.get("source_type") or "")
        use_url = bool(normalized_url and source_type != "google_trends")
        matched_index = None
        if use_url:
            item_candidate = _candidate_from_items([item])
            for candidate_index in url_index.get(normalized_url, []):
                grouped_candidate = _candidate_from_items(url_groups[candidate_index])
                if not _ai_group_has_hard_conflict([grouped_candidate, item_candidate]):
                    matched_index = candidate_index
                    break
            if matched_index is None and url_index.get(normalized_url):
                url_conflict_splits += 1
        if matched_index is not None:
            url_groups[matched_index].append(item)
            url_merged_items += 1
            continue
        group_index = len(url_groups)
        url_groups.append([item])
        if use_url:
            url_index[normalized_url].append(group_index)

    final_groups: list[list[dict[str, Any]]] = []
    title_index: dict[str, list[int]] = defaultdict(list)
    title_merged_groups = 0
    for url_group in url_groups:
        title_key = _safe_exact_title_key(url_group)
        matched_index = None
        if title_key:
            for candidate_index in title_index.get(title_key, []):
                left = _candidate_from_items(final_groups[candidate_index])
                right = _candidate_from_items(url_group)
                if not _ai_group_has_hard_conflict([left, right]):
                    matched_index = candidate_index
                    break
        if matched_index is None:
            matched_index = len(final_groups)
            final_groups.append(list(url_group))
            if title_key:
                title_index[title_key].append(matched_index)
        else:
            final_groups[matched_index].extend(url_group)
            title_merged_groups += 1

    candidates: list[dict[str, Any]] = []
    for group_items in final_groups:
        normalized_urls = {
            str(item.get("normalized_url") or "")
            for item in group_items
            if str(item.get("normalized_url") or "")
        }
        exact_title = _safe_exact_title_key(group_items)
        if len(normalized_urls) == 1 and len(group_items) >= 2:
            kind = "same_url"
        elif exact_title and len(group_items) >= 2:
            kind = "same_title"
        else:
            kind = "single"
        candidates.append(
            _candidate_from_items(group_items, first_stage_kind=kind)
        )
    candidates.sort(
        key=lambda candidate: (
            int(candidate.get("item_count") or 0) > 1,
            int(candidate.get("item_count") or 0),
            candidate["last_seen_at"],
            str(candidate["candidate_id"]),
        ),
        reverse=True,
    )
    return candidates, {
        "raw_items": len(items),
        "url_groups": len(url_groups),
        "first_stage_units": len(candidates),
        "url_merged_items": url_merged_items,
        "url_conflict_splits": url_conflict_splits,
        "title_merged_groups": title_merged_groups,
    }


_UPWARD_EVENT_PATTERN = re.compile(r"(?:주가)?(?:상승|급등|반등|강세|오름)")
_DOWNWARD_EVENT_PATTERN = re.compile(r"(?:주가)?(?:하락|급락|약세|내림)")


def _candidate_direction_text(candidate: dict[str, Any]) -> str:
    return " ".join(
        [
            str(candidate.get("title") or ""),
            *(str(value) for value in candidate.get("examples") or ()),
        ]
    )


def _has_conflicting_directional_candidates(
    left: dict[str, Any],
    right: dict[str, Any],
) -> bool:
    left_text = _candidate_direction_text(left)
    right_text = _candidate_direction_text(right)
    return bool(
        (_UPWARD_EVENT_PATTERN.search(left_text) and _DOWNWARD_EVENT_PATTERN.search(right_text))
        or (_DOWNWARD_EVENT_PATTERN.search(left_text) and _UPWARD_EVENT_PATTERN.search(right_text))
    )


def _ai_group_has_hard_conflict(group_candidates: list[dict[str, Any]]) -> bool:
    for position, left in enumerate(group_candidates):
        for right in group_candidates[position + 1 :]:
            if _has_conflicting_calendar_identity(
                set(left.get("calendar_tokens") or ()),
                set(right.get("calendar_tokens") or ()),
            ):
                return True
            if _has_conflicting_numbered_identity(
                set(left.get("identity_tokens") or ()),
                set(right.get("identity_tokens") or ()),
            ):
                return True
            if _has_conflicting_event_facts(
                set(left.get("editorial_tokens") or ()),
                set(right.get("editorial_tokens") or ()),
            ):
                return True
            if _has_conflicting_directional_candidates(left, right):
                return True
    return False


def _existing_cluster_descriptor(cluster: dict[str, Any]) -> dict[str, Any]:
    descriptor = _candidate_from_items(
        cluster.get("items") or (),
        candidate_id=str(cluster.get("cluster_id") or ""),
        first_stage_kind="existing_cluster",
    )
    descriptor["cluster_id"] = str(cluster.get("cluster_id") or "")
    descriptor["title"] = str(cluster.get("title") or descriptor.get("title") or "")
    return descriptor


def _existing_cluster_match_score(
    candidate: dict[str, Any],
    existing: dict[str, Any],
) -> float:
    if _ai_group_has_hard_conflict([candidate, existing]):
        return -1.0
    left_tokens = set(candidate.get("editorial_tokens") or ())
    right_tokens = set(existing.get("editorial_tokens") or ())
    shared = left_tokens & right_tokens
    if not shared:
        return -1.0
    strong_left = _strong_numbered_identity_tokens(left_tokens) | _product_identity_tokens(left_tokens)
    strong_right = _strong_numbered_identity_tokens(right_tokens) | _product_identity_tokens(right_tokens)
    strong_overlap = strong_left & strong_right
    title_similarity = _string_similarity(
        normalize_title(str(candidate.get("title") or "")),
        normalize_title(str(existing.get("title") or "")),
    )
    candidate_time = candidate.get("last_seen_at") or datetime.min
    existing_time = existing.get("last_seen_at") or datetime.min
    gap_hours = abs((candidate_time - existing_time).total_seconds()) / 3600.0
    recency_bonus = max(0.0, 1.0 - min(gap_hours, 168.0) / 168.0)
    return (
        len(shared) * 2.0
        + len(strong_overlap) * 8.0
        + title_similarity * 5.0
        + recency_bonus
    )


def _attach_existing_cluster_candidates(
    candidates: list[dict[str, Any]],
    existing_clusters: Iterable[dict[str, Any]],
) -> tuple[list[dict[str, Any]], int]:
    descriptors = [
        _existing_cluster_descriptor(cluster)
        for cluster in existing_clusters
        if cluster.get("items") and bool(cluster.get("second_stage_ready"))
    ]
    attached: list[dict[str, Any]] = []
    total_references = 0
    for candidate in candidates:
        scored = [
            (_existing_cluster_match_score(candidate, descriptor), descriptor)
            for descriptor in descriptors
        ]
        scored = [item for item in scored if item[0] >= 2.5]
        scored.sort(
            key=lambda item: (
                item[0],
                item[1].get("last_seen_at") or datetime.min,
                str(item[1].get("cluster_id") or ""),
            ),
            reverse=True,
        )
        existing_options = []
        for _score, descriptor in scored[:AI_EXISTING_CLUSTER_CANDIDATE_LIMIT]:
            existing_options.append(
                {
                    "cluster_id": str(descriptor.get("cluster_id") or ""),
                    "title": str(descriptor.get("title") or ""),
                    "item_count": int(descriptor.get("item_count") or 0),
                    "first_seen_at": descriptor.get("first_seen_at"),
                    "last_seen_at": descriptor.get("last_seen_at"),
                    "examples": tuple(descriptor.get("examples") or ())[:2],
                }
            )
        copied = dict(candidate)
        copied["existing_cluster_candidates"] = tuple(existing_options)
        attached.append(copied)
        total_references += len(existing_options)
    return attached, total_references


def _apply_second_stage_assignments(
    candidates: list[dict[str, Any]],
    assignments: Iterable[dict[str, Any]],
    existing_clusters: Iterable[dict[str, Any]],
    *,
    confidence_threshold: int = 85,
) -> tuple[list[dict[str, Any]], set[str], set[str], int, int, int]:
    candidate_map = {
        str(candidate.get("candidate_id") or ""): candidate
        for candidate in candidates
    }
    assignment_map = {
        str(row.get("candidate_id") or ""): dict(row)
        for row in assignments
        if str(row.get("candidate_id") or "") in candidate_map
    }
    existing_map = {
        str(cluster.get("cluster_id") or ""): {
            "cluster_id": str(cluster.get("cluster_id") or ""),
            "title": str(cluster.get("title") or ""),
            "items": sorted(cluster.get("items") or (), key=_item_time, reverse=True),
            "second_stage_ready": bool(cluster.get("second_stage_ready")),
        }
        for cluster in existing_clusters
        if str(cluster.get("cluster_id") or "")
    }
    existing_descriptors = {
        cluster_id: _existing_cluster_descriptor(cluster)
        for cluster_id, cluster in existing_map.items()
    }

    accepted_existing: dict[str, list[dict[str, Any]]] = defaultdict(list)
    new_groups: dict[str, list[tuple[dict[str, Any], dict[str, Any]]]] = defaultdict(list)
    processed_candidate_ids: set[str] = set()
    uncertain_candidate_ids: set[str] = set()
    conflict_count = 0

    for candidate_id, candidate in candidate_map.items():
        row = assignment_map.get(candidate_id)
        if row is None:
            uncertain_candidate_ids.add(candidate_id)
            continue
        decision = str(row.get("decision") or "")
        confidence = int(row.get("confidence") or 0)
        if decision == "existing" and confidence >= confidence_threshold:
            cluster_id = str(row.get("existing_cluster_id") or "")
            allowed = {
                str(option.get("cluster_id") or "")
                for option in candidate.get("existing_cluster_candidates") or ()
            }
            descriptor = existing_descriptors.get(cluster_id)
            if cluster_id not in allowed or descriptor is None:
                uncertain_candidate_ids.add(candidate_id)
                continue
            existing_group = [
                descriptor,
                *accepted_existing.get(cluster_id, ()),
                candidate,
            ]
            if _ai_group_has_hard_conflict(existing_group):
                conflict_count += 1
                uncertain_candidate_ids.add(candidate_id)
                continue
            accepted_existing[cluster_id].append(candidate)
            processed_candidate_ids.add(candidate_id)
            continue
        if decision == "new" and confidence >= confidence_threshold:
            group_id = str(row.get("new_group_id") or "")
            if group_id:
                new_groups[group_id].append((candidate, row))
                continue
        uncertain_candidate_ids.add(candidate_id)

    accepted_new: list[dict[str, Any]] = []
    for group_id, pairs in new_groups.items():
        group_candidates = [candidate for candidate, _row in pairs]
        if len(group_candidates) >= 2 and _ai_group_has_hard_conflict(group_candidates):
            conflict_count += len(group_candidates)
            uncertain_candidate_ids.update(
                str(candidate.get("candidate_id") or "") for candidate in group_candidates
            )
            continue
        items = sorted(
            [
                item
                for candidate in group_candidates
                for item in candidate.get("items") or ()
            ],
            key=_item_time,
            reverse=True,
        )
        if not items:
            continue
        best_row = max(pairs, key=lambda pair: int(pair[1].get("confidence") or 0))[1]
        title = " ".join(str(best_row.get("representative_title") or "").split()).strip()
        accepted_new.append(
            {
                "cluster_id": _cluster_id_for_items(items),
                "title": title or _fallback_candidate_title(items),
                "items": items,
            }
        )
        for candidate in group_candidates:
            processed_candidate_ids.add(str(candidate.get("candidate_id") or ""))

    processed_source_ids = {
        str(item.get("source_item_id") or "")
        for candidate_id in processed_candidate_ids
        for item in candidate_map[candidate_id].get("items") or ()
        if str(item.get("source_item_id") or "")
    }
    preserved: list[dict[str, Any]] = []
    for cluster_id, cluster in existing_map.items():
        remaining = [
            item
            for item in cluster.get("items") or ()
            if str(item.get("source_item_id") or "") not in processed_source_ids
        ]
        additions = [
            item
            for candidate in accepted_existing.get(cluster_id, ())
            for item in candidate.get("items") or ()
        ]
        combined = sorted(remaining + additions, key=_item_time, reverse=True)
        if combined:
            preserved.append(
                {
                    "cluster_id": cluster_id,
                    "title": str(cluster.get("title") or _fallback_candidate_title(combined)),
                    "items": combined,
                }
            )

    represented = {
        str(item.get("source_item_id") or "")
        for cluster in preserved + accepted_new
        for item in cluster.get("items") or ()
        if str(item.get("source_item_id") or "")
    }
    all_items = {
        str(item.get("source_item_id") or ""): item
        for cluster in existing_clusters
        for item in cluster.get("items") or ()
        if str(item.get("source_item_id") or "")
    }
    for candidate in candidates:
        for item in candidate.get("items") or ():
            source_id = str(item.get("source_item_id") or "")
            if source_id:
                all_items[source_id] = item
    provisional = [
        {
            "cluster_id": _cluster_id_for_items([item]),
            "title": _fallback_candidate_title([item]),
            "items": [item],
        }
        for source_id, item in all_items.items()
        if source_id not in represented
    ]
    combined_clusters = preserved + accepted_new + provisional
    combined_clusters.sort(
        key=lambda cluster: max(
            (_item_time(item) for item in cluster.get("items") or ()),
            default=datetime.min,
        ),
        reverse=True,
    )
    existing_link_count = sum(len(values) for values in accepted_existing.values())
    return (
        combined_clusters,
        processed_candidate_ids,
        uncertain_candidate_ids,
        existing_link_count,
        len(accepted_new),
        conflict_count,
    )



def _clustering_input_hash(item: dict[str, Any]) -> str:
    payload = {
        "source_item_id": str(item.get("source_item_id") or ""),
        "title": _candidate_item_title(item),
        "raw_title": str(item.get("raw_title") or ""),
        "item_title": str(item.get("item_title") or ""),
        "normalized_url": str(item.get("normalized_url") or ""),
    }
    return hashlib.sha1(
        json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()


def _processing_identity(ai_clustering: dict[str, Any]) -> tuple[str, str, str, str]:
    return (
        AI_CLUSTERING_FEATURE_ID,
        AI_CLUSTERING_FEATURE_VERSION,
        str(ai_clustering.get("model") or ""),
        "",
    )


def _load_existing_cluster_snapshots(
    con: duckdb.DuckDBPyConnection,
    item_map: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    rows = con.execute(
        """
        SELECT tc.cluster_id, tc.canonical_title, tc.calculated_at, tci.source_item_id
        FROM trend_clusters tc
        JOIN trend_cluster_items tci ON tci.cluster_id = tc.cluster_id
        ORDER BY tc.calculated_at DESC, tc.cluster_id, tci.source_item_id
        """
    ).fetchall()
    source_owner: dict[str, str] = {}
    grouped: dict[str, dict[str, Any]] = {}
    for cluster_id, title, _calculated_at, source_item_id in rows:
        source_id = str(source_item_id or "")
        if source_id not in item_map or source_id in source_owner:
            continue
        normalized_cluster_id = str(cluster_id or "")
        source_owner[source_id] = normalized_cluster_id
        cluster = grouped.setdefault(
            normalized_cluster_id,
            {
                "cluster_id": normalized_cluster_id,
                "title": str(title or ""),
                "items": [],
            },
        )
        cluster["items"].append(item_map[source_id])
    snapshots = list(grouped.values())
    for cluster in snapshots:
        cluster["items"].sort(key=_item_time, reverse=True)
    snapshots.sort(
        key=lambda cluster: max(
            (_item_time(item) for item in cluster.get("items") or ()),
            default=datetime.min,
        ),
        reverse=True,
    )
    return snapshots


def _trend_ranking_signature_context(
    con: duckdb.DuckDBPyConnection,
    *,
    lookback_hours: int,
    source_limits: dict[str, int] | None,
) -> dict[str, Any]:
    lookback_hours = max(6, int(lookback_hours))
    cutoff = datetime.now() - timedelta(hours=lookback_hours)
    state = con.execute(
        """
        SELECT COUNT(*), MAX(imported_at), MAX(COALESCE(published_at, observed_at, imported_at))
        FROM source_items
        WHERE source_type IN ('youtube', 'naver_news', 'naver_blog', 'daum_web', 'daum_cafe', 'google_trends', 'wikipedia_pageviews')
          AND COALESCE(published_at, observed_at, imported_at) >= ?
        """,
        [cutoff],
    ).fetchone()
    normalized_limits = _normalized_analysis_limits(source_limits)
    ai_clustering = _ai_clustering_settings(con)
    feature_id, feature_version, model_name, _hash_prefix = _processing_identity(
        ai_clustering
    )
    pending_count = int(
        con.execute(
            """
            SELECT COUNT(*)
            FROM source_items s
            LEFT JOIN trend_cluster_processing p
              ON p.source_item_id = s.source_item_id
             AND p.feature_id = ?
             AND p.feature_version = ?
             AND p.model_name = ?
            WHERE s.source_type IN ('youtube', 'naver_news', 'naver_blog', 'daum_web', 'daum_cafe', 'google_trends', 'wikipedia_pageviews')
              AND COALESCE(s.published_at, s.observed_at, s.imported_at) >= ?
              AND (
                    p.source_item_id IS NULL
                    OR COALESCE(p.status, 'processed') = 'retry'
                  )
            """,
            [feature_id, feature_version, model_name, cutoff],
        ).fetchone()[0]
        or 0
    )
    needs_review_count = int(
        con.execute(
            """
            SELECT COUNT(*)
            FROM trend_cluster_processing p
            JOIN source_items s ON s.source_item_id = p.source_item_id
            WHERE p.feature_id = ?
              AND p.feature_version = ?
              AND p.model_name = ?
              AND COALESCE(p.status, '') = 'needs_review'
              AND COALESCE(s.published_at, s.observed_at, s.imported_at) >= ?
            """,
            [feature_id, feature_version, model_name, cutoff],
        ).fetchone()[0]
        or 0
    )
    signature_raw = "|".join(
        [
            _RANKING_ALGORITHM_VERSION,
            AI_CLUSTERING_FEATURE_ID,
            AI_CLUSTERING_FEATURE_VERSION,
            _ranking_day(),
            str(lookback_hours),
            json.dumps(normalized_limits, sort_keys=True),
            json.dumps(ai_clustering, sort_keys=True),
            str(state[0] or 0),
            str(state[1] or ""),
            str(state[2] or ""),
        ]
    )
    signature = hashlib.sha1(signature_raw.encode("utf-8")).hexdigest()
    cached = con.execute(
        "SELECT setting_value FROM app_settings WHERE setting_key = 'trend_ranking_signature'"
    ).fetchone()
    existing_count = int(
        con.execute("SELECT COUNT(*) FROM trend_clusters").fetchone()[0] or 0
    )
    return {
        "lookback_hours": lookback_hours,
        "state": state,
        "normalized_limits": normalized_limits,
        "ai_clustering": ai_clustering,
        "signature": signature,
        "cached_signature": str(cached[0]) if cached else "",
        "existing_count": existing_count,
        "pending_count": pending_count,
        "needs_review_count": needs_review_count,
    }


def get_trend_ranking_refresh_status(
    con: duckdb.DuckDBPyConnection,
    *,
    lookback_hours: int = 72,
    source_limits: dict[str, int] | None = None,
) -> dict[str, Any]:
    """순위를 다시 계산하지 않고 현재 저장 결과와 2차 군집 대기를 확인합니다."""
    context = _trend_ranking_signature_context(
        con,
        lookback_hours=lookback_hours,
        source_limits=source_limits,
    )
    existing_count = int(context["existing_count"] or 0)
    pending_count = int(context.get("pending_count") or 0)
    cached_signature = str(context["cached_signature"] or "")
    current_signature = str(context["signature"] or "")
    ai_clustering = dict(context.get("ai_clustering") or {})
    ai_can_run = bool(
        ai_clustering.get("enabled") and ai_clustering.get("api_key_configured")
    )
    if pending_count > 0 and ai_can_run:
        reason = "pending_clustering"
    elif existing_count <= 0:
        reason = "missing_rankings"
    elif not cached_signature:
        reason = "missing_signature"
    elif cached_signature != current_signature:
        reason = "stale"
    else:
        reason = "current"
    result = {
        "needs_rebuild": reason != "current",
        "reason": reason,
        "has_rankings": existing_count > 0,
        "items": int(context["state"][0] or 0),
        "clusters": existing_count,
        "pending_items": pending_count if ai_can_run else 0,
    }
    if ai_can_run:
        result["needs_review_items"] = int(context.get("needs_review_count") or 0)
    return result

def prepare_trend_ranking_rebuild(
    con: duckdb.DuckDBPyConnection,
    *,
    lookback_hours: int = 72,
    source_limits: dict[str, int] | None = None,
) -> TrendRankingPreparation:
    """최근 미처리 원문 최대 4,000개와 기존 2차 군집을 짧게 읽습니다."""
    started_at = perf_counter()
    context = _trend_ranking_signature_context(
        con,
        lookback_hours=lookback_hours,
        source_limits=source_limits,
    )
    signature = str(context["signature"])
    ai_clustering = dict(context.get("ai_clustering") or {})
    items = _parse_source_rows(
        con,
        int(context["lookback_hours"]),
        source_limits=context["normalized_limits"],
    )
    item_map = {
        str(item.get("source_item_id") or ""): item
        for item in items
        if str(item.get("source_item_id") or "")
    }
    existing_clusters = _load_existing_cluster_snapshots(con, item_map)
    feature_id, feature_version, model_name, hash_prefix = _processing_identity(
        ai_clustering
    )
    processing_rows = con.execute(
        """
        SELECT source_item_id, input_hash, feature_id, feature_version, model_name,
               COALESCE(status, 'processed'), COALESCE(attempt_count, 0),
               COALESCE(cluster_id, '')
        FROM trend_cluster_processing
        """
    ).fetchall()
    processing_map = {
        str(source_item_id or ""): {
            "input_hash": str(input_hash or ""),
            "feature_id": str(stored_feature_id or ""),
            "feature_version": str(stored_feature_version or ""),
            "model_name": str(stored_model_name or ""),
            "status": str(status or "processed"),
            "attempt_count": int(attempt_count or 0),
            "cluster_id": str(cluster_id or ""),
        }
        for (
            source_item_id,
            input_hash,
            stored_feature_id,
            stored_feature_version,
            stored_model_name,
            status,
            attempt_count,
            cluster_id,
        ) in processing_rows
    }
    ready_cluster_ids = {
        str(row.get("cluster_id") or "")
        for row in processing_map.values()
        if row.get("feature_id") == feature_id
        and row.get("feature_version") == feature_version
        and row.get("model_name") == model_name
        and row.get("status") == "processed"
        and str(row.get("cluster_id") or "")
    }
    for cluster in existing_clusters:
        cluster["second_stage_ready"] = (
            str(cluster.get("cluster_id") or "") in ready_cluster_ids
        )
    pending_items: list[dict[str, Any]] = []
    processed_count = 0
    needs_review_count = 0
    processing_attempts: dict[str, int] = {}
    for item in sorted(
        items,
        key=lambda value: (
            _item_time(value),
            str(value.get("source_item_id") or ""),
        ),
        reverse=True,
    ):
        source_id = str(item.get("source_item_id") or "")
        expected_hash = hash_prefix + _clustering_input_hash(item)
        stored = processing_map.get(source_id)
        same_identity = bool(
            stored
            and stored["input_hash"] == expected_hash
            and stored["feature_id"] == feature_id
            and stored["feature_version"] == feature_version
            and stored["model_name"] == model_name
        )
        if same_identity and stored["status"] == "processed":
            processed_count += 1
            continue
        if same_identity and stored["status"] == "needs_review":
            needs_review_count += 1
            continue
        attempts = stored["attempt_count"] if same_identity and stored else 0
        processing_attempts[source_id] = attempts
        pending_items.append(item)

    scan_limit = int(ai_clustering.get("max_items") or DEFAULT_AI_CLUSTERING_MAX_ITEMS)
    selected_items = pending_items[: max(0, scan_limit)]
    common = {
        "ai_clustering_enabled": bool(ai_clustering.get("enabled")),
        "ai_clustering_model": str(ai_clustering.get("model") or ""),
        "ai_clustering_max_items": scan_limit,
        "ai_clustering_batch_size": int(
            ai_clustering.get("batch_size") or DEFAULT_AI_CLUSTERING_BATCH_SIZE
        ),
        "ai_clustering_max_batches": int(
            ai_clustering.get("max_batches") or DEFAULT_AI_CLUSTERING_MAX_BATCHES
        ),
        "ai_clustering_api_key_configured": bool(
            ai_clustering.get("api_key_configured")
        ),
        "existing_clusters": tuple(existing_clusters),
        "selected_source_item_ids": tuple(
            str(item.get("source_item_id") or "") for item in selected_items
        ),
        "pending_item_count": len(pending_items),
        "processed_item_count": processed_count,
        "needs_review_item_count": needs_review_count,
        "processing_attempts": tuple(sorted(processing_attempts.items())),
        "processing_feature_id": feature_id,
        "processing_feature_version": feature_version,
        "processing_model": model_name,
        "processing_hash_prefix": hash_prefix,
    }
    if (
        context["cached_signature"]
        and str(context["cached_signature"]) == signature
        and existing_clusters
        and (
            not pending_items
            or not (
                bool(ai_clustering.get("enabled"))
                and bool(ai_clustering.get("api_key_configured"))
            )
        )
    ):
        return TrendRankingPreparation(
            "reused",
            tuple(items),
            signature,
            len(items),
            len(existing_clusters),
            started_at,
            **common,
        )
    return TrendRankingPreparation(
        "ready",
        tuple(items),
        signature,
        len(items),
        len(existing_clusters),
        started_at,
        **common,
    )


def _cluster_id_for_items(items: Iterable[dict[str, Any]]) -> str:
    item_key = "|".join(
        sorted(
            str(item.get("source_item_id") or "")
            for item in items
            if str(item.get("source_item_id") or "")
        )
    )
    return "trend_" + hashlib.sha1(item_key.encode("utf-8")).hexdigest()[:20]


def _clusters_with_all_source_items(
    clusters: list[dict[str, Any]],
    source_items: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    represented = {
        str(item.get("source_item_id") or "")
        for cluster in clusters
        for item in cluster.get("items") or ()
        if str(item.get("source_item_id") or "")
    }
    clusters.extend(
        {
            "cluster_id": _cluster_id_for_items([item]),
            "title": _fallback_candidate_title([item]),
            "items": [item],
        }
        for item in source_items
        if str(item.get("source_item_id") or "") not in represented
    )
    clusters.sort(
        key=lambda cluster: max(
            (_item_time(item) for item in cluster.get("items") or ()),
            default=datetime.min,
        ),
        reverse=True,
    )
    return clusters


def calculate_prepared_trend_rankings(
    preparation: TrendRankingPreparation,
    *,
    progress_callback: Callable[[float, str], None] | None = None,
) -> TrendRankingCalculation:
    """DB 연결 없이 1차 군집 후 최대 200개 단위를 한 번의 Gemini 요청으로 처리합니다."""
    analysis_started = perf_counter()
    if preparation.status != "ready":
        return TrendRankingCalculation(preparation, (), (), 0.0)

    def notify(value: float, message: str) -> None:
        if progress_callback is not None:
            progress_callback(max(0.0, min(1.0, value)), message)

    source_items = list(preparation.items)
    item_map = {
        str(item.get("source_item_id") or ""): item
        for item in source_items
        if str(item.get("source_item_id") or "")
    }
    selected_items = [
        item_map[source_id]
        for source_id in preparation.selected_source_item_ids
        if source_id in item_map
    ]
    selected_items.sort(key=_item_time, reverse=True)
    attempts_map = dict(preparation.processing_attempts)
    notify(0.05, f"최근 미처리 원문 {len(selected_items):,}개에서 1차 군집 구성 중")

    first_stage_candidates, first_stage_stats = _build_first_stage_candidates(
        selected_items
    )
    candidate_window = first_stage_candidates[: preparation.ai_clustering_batch_size]
    candidate_window, existing_candidate_refs = _attach_existing_cluster_candidates(
        candidate_window,
        preparation.existing_clusters,
    )
    batch_candidates = select_cluster_batch_candidates(
        candidate_window,
        batch_id="cluster_batch_0001",
        max_candidates=preparation.ai_clustering_batch_size,
    )
    existing_candidate_refs = sum(
        len(candidate.get("existing_cluster_candidates") or ())
        for candidate in batch_candidates
    )
    deferred_by_payload_limit = max(0, len(candidate_window) - len(batch_candidates))
    ai_clustering: dict[str, Any] = {
        "status": "no_pending",
        "model": preparation.ai_clustering_model,
        "scan_limit": preparation.ai_clustering_max_items,
        "batch_size": preparation.ai_clustering_batch_size,
        "max_batches": preparation.ai_clustering_max_batches,
        "selected_items": len(selected_items),
        "raw_items": int(first_stage_stats.get("raw_items") or 0),
        "url_groups": int(first_stage_stats.get("url_groups") or 0),
        "first_stage_units": int(first_stage_stats.get("first_stage_units") or 0),
        "url_merged_items": int(first_stage_stats.get("url_merged_items") or 0),
        "url_conflict_splits": int(first_stage_stats.get("url_conflict_splits") or 0),
        "title_merged_groups": int(first_stage_stats.get("title_merged_groups") or 0),
        "deferred_by_payload_limit": deferred_by_payload_limit,
        "batch_units": len(batch_candidates),
        "existing_candidate_refs": existing_candidate_refs,
        "processed_units": 0,
        "processed_items": 0,
        "pending_before": preparation.pending_item_count,
        "remaining_items": preparation.pending_item_count,
        "needs_review_before": preparation.needs_review_item_count,
        "needs_review_items": 0,
        "existing_links": 0,
        "new_clusters": 0,
        "uncertain_units": 0,
        "conflict_units": 0,
        "error_message": "",
    }
    ai_calls: tuple[dict[str, Any], ...] = ()
    processing_rows: list[dict[str, Any]] = []
    batch_log: dict[str, Any] = {
        "status": "nothing_to_group",
        "scanned_pending_items": len(selected_items),
        "first_stage_units": len(batch_candidates),
        "all_first_stage_units": int(first_stage_stats.get("first_stage_units") or 0),
        "source_items": sum(
            int(candidate.get("item_count") or 0) for candidate in batch_candidates
        ),
        "url_merged_items": int(first_stage_stats.get("url_merged_items") or 0),
        "url_conflict_splits": int(first_stage_stats.get("url_conflict_splits") or 0),
        "title_merged_groups": int(first_stage_stats.get("title_merged_groups") or 0),
        "existing_candidate_refs": existing_candidate_refs,
        "deferred_units": max(
            0, int(first_stage_stats.get("first_stage_units") or 0) - len(batch_candidates)
        ),
        "deferred_by_payload_limit": deferred_by_payload_limit,
    }

    combined_clusters = [
        {
            "cluster_id": str(cluster.get("cluster_id") or ""),
            "title": str(cluster.get("title") or ""),
            "items": sorted(cluster.get("items") or (), key=_item_time, reverse=True),
        }
        for cluster in preparation.existing_clusters
        if cluster.get("items")
    ]

    if not batch_candidates:
        ai_clustering["status"] = "nothing_to_group"
    elif not preparation.ai_clustering_enabled:
        combined_clusters = cluster_items_deterministically(source_items)
        ai_clustering.update(
            {
                "status": "disabled",
                "fallback_mode": "deterministic_similarity",
                "error_message": "AI 기본 군집화가 꺼져 있어 비상 결정론적 군집을 사용했습니다.",
            }
        )
        batch_log["status"] = "disabled_fallback"
    elif not preparation.ai_clustering_api_key_configured:
        combined_clusters = cluster_items_deterministically(source_items)
        ai_clustering.update(
            {
                "status": "missing_api_key",
                "fallback_mode": "deterministic_similarity",
                "error_message": "GEMINI_API_KEY가 없어 비상 결정론적 군집을 사용했습니다.",
            }
        )
        batch_log["status"] = "missing_api_key_fallback"
    else:
        notify(0.20, f"Flash-Lite 2차 군집 {len(batch_candidates):,}개 요청 중")
        config = replace(get_gemini_config(), model=preparation.ai_clustering_model)
        execution = classify_cluster_batch(
            config,
            batch_candidates,
            batch_id="cluster_batch_0001",
            max_candidates=preparation.ai_clustering_batch_size,
        )
        ai_calls = execution.calls
        batch_log["status"] = execution.status
        batch_log["error_message"] = execution.error_message
        if execution.calls:
            call = execution.calls[0]
            for token_name in (
                "input_tokens",
                "output_tokens",
                "thought_tokens",
                "total_tokens",
            ):
                batch_log[token_name] = int(call.get(token_name) or 0)
        if execution.status != "failed":
            (
                combined_clusters,
                processed_candidate_ids,
                uncertain_candidate_ids,
                existing_links,
                new_cluster_count,
                conflict_count,
            ) = _apply_second_stage_assignments(
                batch_candidates,
                execution.assignments,
                preparation.existing_clusters,
            )
            combined_clusters = _clusters_with_all_source_items(
                combined_clusters,
                source_items,
            )
            source_cluster_map = {
                str(item.get("source_item_id") or ""): str(cluster.get("cluster_id") or "")
                for cluster in combined_clusters
                for item in cluster.get("items") or ()
                if str(item.get("source_item_id") or "")
            }
            now = datetime.now()
            newly_needs_review = 0
            processed_source_ids: set[str] = set()
            for candidate in batch_candidates:
                candidate_id = str(candidate.get("candidate_id") or "")
                candidate_items = list(candidate.get("items") or ())
                if candidate_id in processed_candidate_ids:
                    status = "processed"
                else:
                    status = "retry"
                for item in candidate_items:
                    source_id = str(item.get("source_item_id") or "")
                    if not source_id:
                        continue
                    previous_attempts = int(attempts_map.get(source_id, 0) or 0)
                    attempt_count = previous_attempts + 1
                    row_status = status
                    last_error = ""
                    if status == "processed":
                        processed_source_ids.add(source_id)
                    else:
                        last_error = execution.error_message or "Gemini 2차 군집 판단 불확실"
                        if attempt_count >= AI_CLUSTERING_MAX_ATTEMPTS:
                            row_status = "needs_review"
                            newly_needs_review += 1
                    processing_rows.append(
                        {
                            "source_item_id": source_id,
                            "input_hash": preparation.processing_hash_prefix
                            + _clustering_input_hash(item),
                            "feature_id": preparation.processing_feature_id,
                            "feature_version": preparation.processing_feature_version,
                            "model_name": preparation.processing_model,
                            "first_stage_key": candidate_id,
                            "cluster_id": source_cluster_map.get(source_id) or _cluster_id_for_items([item]),
                            "status": row_status,
                            "attempt_count": attempt_count,
                            "last_error": last_error,
                            "processed_at": now,
                            "updated_at": now,
                        }
                    )
            processed_units = len(processed_candidate_ids)
            processed_items = len(processed_source_ids)
            uncertain_units = len(uncertain_candidate_ids)
            ai_clustering.update(
                {
                    "status": execution.status,
                    "processed_units": processed_units,
                    "processed_items": processed_items,
                    "remaining_items": max(
                        0,
                        preparation.pending_item_count
                        - processed_items
                        - newly_needs_review,
                    ),
                    "needs_review_items": newly_needs_review,
                    "existing_links": existing_links,
                    "new_clusters": new_cluster_count,
                    "uncertain_units": uncertain_units,
                    "conflict_units": conflict_count,
                    "error_message": execution.error_message,
                }
            )
            batch_log.update(
                {
                    "processed_units": processed_units,
                    "processed_source_items": processed_items,
                    "existing_links": existing_links,
                    "new_clusters": new_cluster_count,
                    "uncertain_units": uncertain_units,
                    "conflict_units": conflict_count,
                    "needs_review_items": newly_needs_review,
                }
            )
        else:
            combined_clusters = cluster_items_deterministically(source_items)
            ai_clustering.update(
                {
                    "status": "failed_pending",
                    "fallback_mode": "deterministic_similarity",
                    "uncertain_units": len(batch_candidates),
                    "error_message": execution.error_message,
                }
            )
            batch_log["uncertain_units"] = len(batch_candidates)

    combined_clusters = _clusters_with_all_source_items(
        combined_clusters,
        source_items,
    )
    notify(0.85, "2차 군집 결과의 순위 점수 계산 중")
    calculated_at = datetime.now()
    cluster_rows: list[dict[str, Any]] = []
    cluster_item_rows: list[dict[str, Any]] = []
    for cluster in combined_clusters:
        cluster_items = cluster.get("items") or []
        if not cluster_items:
            continue
        scored = _score_cluster(cluster)
        cluster_id = str(cluster.get("cluster_id") or _cluster_id_for_items(cluster_items))
        cluster_rows.append(
            {
                "cluster_id": cluster_id,
                "canonical_title": scored["title"],
                "trend_score": scored["score"],
                "opportunity_score": scored["opportunity"],
                "fact_risk_score": scored["risk"],
                "quality_score": scored["quality"],
                "rediscovery_score": scored["rediscovery"],
                "recommendation_status": scored["recommendation_status"],
                "item_count": len(cluster_items),
                "source_type_count": len(scored["source_types"]),
                "publisher_count": scored["publisher_count"],
                "source_types_json": json.dumps(scored["source_types"], ensure_ascii=False),
                "score_reasons_json": json.dumps(scored["reasons"], ensure_ascii=False),
                "quality_reasons_json": json.dumps(scored["quality_reasons"], ensure_ascii=False),
                "first_seen_at": scored["first"],
                "last_seen_at": scored["latest"],
                "calculated_at": calculated_at,
            }
        )
        cluster_item_rows.extend(
            {
                "cluster_id": cluster_id,
                "source_item_id": str(item.get("source_item_id") or ""),
                "linked_at": calculated_at,
            }
            for item in cluster_items
            if str(item.get("source_item_id") or "")
        )
    notify(1.0, f"2차 군집 배치 {len(batch_candidates):,}개 계산 완료")
    return TrendRankingCalculation(
        preparation,
        tuple(cluster_rows),
        tuple(cluster_item_rows),
        perf_counter() - analysis_started,
        ai_clustering,
        ai_calls,
        tuple(processing_rows),
        batch_log,
    )


def finalize_prepared_trend_rankings(
    con: duckdb.DuckDBPyConnection,
    calculation: TrendRankingCalculation,
) -> dict[str, Any]:
    """완성된 한 배치 결과만 짧은 트랜잭션으로 반영하고 토큰 로그를 기록합니다."""
    preparation = calculation.preparation
    if preparation.status == "reused":
        return {
            "items": preparation.source_item_count,
            "clusters": preparation.existing_cluster_count,
            "reused": True,
            "ai_clustering": {
                "status": "reused",
                "model": preparation.ai_clustering_model,
                "processed_items": 0,
                "remaining_items": 0,
                "needs_review_items": preparation.needs_review_item_count,
            },
            "ai_review": {"status": "reused", "model": preparation.ai_clustering_model},
            "batch_log": {},
            "timings": {
                "analysis": 0.0,
                "database": 0.0,
                "total": round(perf_counter() - preparation.started_at, 4),
            },
        }

    database_started = perf_counter()
    cluster_rows = list(calculation.cluster_rows)
    cluster_item_rows = list(calculation.cluster_item_rows)
    processing_rows = list(calculation.processing_rows)
    con.execute("BEGIN TRANSACTION")
    try:
        con.execute("DELETE FROM trend_cluster_items")
        con.execute("DELETE FROM trend_clusters")
        if cluster_rows:
            clusters_frame = pd.DataFrame(cluster_rows)
            con.register("_ranked_cluster_rows", clusters_frame)
            try:
                con.execute(
                    """
                    INSERT INTO trend_clusters(
                        cluster_id, canonical_title, trend_score, opportunity_score,
                        fact_risk_score, quality_score, rediscovery_score, recommendation_status,
                        item_count, source_type_count, publisher_count,
                        source_types_json, score_reasons_json, quality_reasons_json,
                        first_seen_at, last_seen_at, calculated_at
                    )
                    SELECT cluster_id, canonical_title, trend_score, opportunity_score,
                           fact_risk_score, quality_score, rediscovery_score, recommendation_status,
                           item_count, source_type_count, publisher_count,
                           source_types_json, score_reasons_json, quality_reasons_json,
                           first_seen_at, last_seen_at, calculated_at
                    FROM _ranked_cluster_rows
                    """
                )
            finally:
                con.unregister("_ranked_cluster_rows")
        if cluster_item_rows:
            items_frame = pd.DataFrame(cluster_item_rows)
            con.register("_ranked_cluster_item_rows", items_frame)
            try:
                con.execute(
                    """
                    INSERT INTO trend_cluster_items(cluster_id, source_item_id, linked_at)
                    SELECT cluster_id, source_item_id, linked_at
                    FROM _ranked_cluster_item_rows
                    """
                )
            finally:
                con.unregister("_ranked_cluster_item_rows")
        if processing_rows:
            processing_frame = pd.DataFrame(processing_rows)
            con.register("_trend_cluster_processing_rows", processing_frame)
            try:
                con.execute(
                    """
                    INSERT INTO trend_cluster_processing(
                        source_item_id, input_hash, feature_id, feature_version,
                        model_name, first_stage_key, cluster_id, status,
                        attempt_count, last_error, processed_at, updated_at
                    )
                    SELECT source_item_id, input_hash, feature_id, feature_version,
                           model_name, first_stage_key, cluster_id, status,
                           attempt_count, last_error, processed_at, updated_at
                    FROM _trend_cluster_processing_rows
                    ON CONFLICT(source_item_id) DO UPDATE SET
                        input_hash = EXCLUDED.input_hash,
                        feature_id = EXCLUDED.feature_id,
                        feature_version = EXCLUDED.feature_version,
                        model_name = EXCLUDED.model_name,
                        first_stage_key = EXCLUDED.first_stage_key,
                        cluster_id = EXCLUDED.cluster_id,
                        status = EXCLUDED.status,
                        attempt_count = EXCLUDED.attempt_count,
                        last_error = EXCLUDED.last_error,
                        processed_at = EXCLUDED.processed_at,
                        updated_at = EXCLUDED.updated_at
                    """
                )
            finally:
                con.unregister("_trend_cluster_processing_rows")
        con.execute(
            """
            DELETE FROM trend_cluster_processing p
            WHERE NOT EXISTS (
                SELECT 1 FROM source_items s WHERE s.source_item_id = p.source_item_id
            )
            """
        )
        con.execute(
            """
            INSERT INTO app_settings(setting_key, setting_value, updated_at)
            VALUES ('trend_ranking_signature', ?, ?)
            ON CONFLICT(setting_key) DO UPDATE SET
                setting_value = EXCLUDED.setting_value,
                updated_at = EXCLUDED.updated_at
            """,
            [preparation.signature, datetime.now()],
        )
        con.execute("COMMIT")
    except Exception:
        con.execute("ROLLBACK")
        raise

    logging_error = ""
    if calculation.ai_clustering_calls:
        config = replace(get_gemini_config(), model=preparation.ai_clustering_model)
        for call in calculation.ai_clustering_calls:
            try:
                record_gemini_api_call(
                    con,
                    config=config,
                    content_pack_id="",
                    request_hash=str(call.get("request_hash") or ""),
                    feature_id=str(call.get("feature_id") or AI_CLUSTERING_FEATURE_ID),
                    feature_version=str(call.get("feature_version") or AI_CLUSTERING_FEATURE_VERSION),
                    attempt_number=1,
                    cache_hit=False,
                    status=str(call.get("status") or "failed"),
                    http_status=call.get("http_status"),
                    error_type=str(call.get("error_type") or ""),
                    retry_reason="",
                    retry_wait_seconds=0,
                    input_tokens=call.get("input_tokens"),
                    output_tokens=call.get("output_tokens"),
                    thought_tokens=call.get("thought_tokens"),
                    total_tokens=call.get("total_tokens"),
                    duration_ms=0,
                    error_message=str(call.get("error_message") or ""),
                    request_text=str(call.get("request_text") or ""),
                    response_text=str(call.get("response_text") or ""),
                    requested_item_count=int(call.get("requested_item_count") or 0),
                    configured_items_per_request=preparation.ai_clustering_batch_size,
                    thinking_level="minimal",
                    request_timeout_seconds=min(max(30, int(config.timeout_seconds)), 240),
                    finish_reason=str(call.get("finish_reason") or ""),
                    finish_message=str(call.get("finish_message") or ""),
                )
            except Exception as exc:
                logging_error = str(exc)

    finished = perf_counter()
    ai_clustering = dict(calculation.ai_clustering)
    if logging_error:
        ai_clustering["logging_warning"] = logging_error
    return {
        "items": len(preparation.items),
        "clusters": len(cluster_rows),
        "reused": False,
        "ai_clustering": ai_clustering,
        "ai_review": ai_clustering,
        "batch_log": dict(calculation.batch_log),
        "timings": {
            "analysis": round(calculation.analysis_seconds, 4),
            "database": round(finished - database_started, 4),
            "total": round(finished - preparation.started_at, 4),
        },
    }


def rebuild_trend_rankings(
    con: duckdb.DuckDBPyConnection,
    *,
    lookback_hours: int = 72,
    source_limits: dict[str, int] | None = None,
) -> dict[str, Any]:
    """기존 단일 연결 호출 호환 래퍼입니다. UI·예약 실행은 짧은 연결 경로를 사용합니다."""
    preparation = prepare_trend_ranking_rebuild(
        con,
        lookback_hours=lookback_hours,
        source_limits=source_limits,
    )
    calculation = calculate_prepared_trend_rankings(preparation)
    return finalize_prepared_trend_rankings(con, calculation)


def get_trend_inventory_summary(
    con: duckdb.DuckDBPyConnection,
    *,
    lookback_hours: int = 72,
) -> dict[str, Any]:
    """Summarize stored source inventory and the active rolling ranking window.

    The trend list is intentionally rebuilt from a rolling window, while repeated
    observations of the same URL/external ID update an existing source row. This
    diagnostic makes that behavior visible so a small cluster count is not confused
    with a stopped scheduler.
    """
    window_hours = max(6, int(lookback_hours))
    now = datetime.now()
    window_cutoff = now - timedelta(hours=window_hours)
    activity_cutoff = now - timedelta(hours=24)
    source_types = (
        "youtube",
        "naver_news",
        "naver_blog",
        "daum_web",
        "daum_cafe",
        "google_trends",
        "wikipedia_pageviews",
    )
    placeholders = ", ".join("?" for _ in source_types)
    rows = con.execute(
        f"""
        SELECT
            CASE
                WHEN source_type = 'youtube' THEN 'youtube'
                WHEN source_type IN ('naver_news', 'naver_blog') THEN 'naver'
                WHEN source_type IN ('daum_web', 'daum_cafe') THEN 'daum'
                WHEN source_type = 'google_trends' THEN 'google_trends'
                WHEN source_type = 'wikipedia_pageviews' THEN 'wikipedia'
                ELSE source_type
            END AS source_group,
            COUNT(*) AS stored_items,
            COUNT(*) FILTER (
                WHERE COALESCE(published_at, observed_at, imported_at) >= ?
            ) AS window_items,
            COUNT(DISTINCT normalized_title) FILTER (
                WHERE COALESCE(published_at, observed_at, imported_at) >= ?
            ) AS window_unique_titles,
            COUNT(*) FILTER (WHERE COALESCE(first_imported_at, imported_at) >= ?) AS new_items_24h,
            COUNT(*) FILTER (WHERE COALESCE(last_imported_at, imported_at) >= ?) AS touched_items_24h,
            SUM(COALESCE(observation_count, 1)) FILTER (
                WHERE COALESCE(published_at, observed_at, imported_at) >= ?
            ) AS window_observations,
            MAX(COALESCE(last_imported_at, imported_at)) AS last_imported_at
        FROM source_items
        WHERE source_type IN ({placeholders})
        GROUP BY source_group
        """,
        [
            window_cutoff,
            window_cutoff,
            activity_cutoff,
            activity_cutoff,
            window_cutoff,
            *source_types,
        ],
    ).fetchall()
    columns = [item[0] for item in con.description]
    by_group = {str(row[0]): dict(zip(columns, row)) for row in rows}
    labels = {
        "youtube": "YouTube",
        "naver": "NAVER",
        "daum": "Daum",
        "google_trends": "Google Trends",
        "wikipedia": "위키백과",
    }
    ordered_groups = []
    for key in ("youtube", "naver", "daum", "google_trends", "wikipedia"):
        item = by_group.get(key, {})
        ordered_groups.append(
            {
                "source_group": key,
                "label": labels[key],
                "stored_items": int(item.get("stored_items") or 0),
                "window_items": int(item.get("window_items") or 0),
                "window_unique_titles": int(item.get("window_unique_titles") or 0),
                "new_items_24h": int(item.get("new_items_24h") or 0),
                "touched_items_24h": int(item.get("touched_items_24h") or 0),
                "window_observations": int(item.get("window_observations") or 0),
                "last_imported_at": item.get("last_imported_at"),
            }
        )

    cluster_row = con.execute(
        """
        SELECT COUNT(*) AS cluster_count,
               COUNT(*) FILTER (WHERE recommendation_status = 'recommended') AS recommended_count,
               COUNT(*) FILTER (WHERE recommendation_status = 'review') AS review_count,
               COUNT(*) FILTER (WHERE recommendation_status = 'hold') AS hold_count
        FROM trend_clusters
        """
    ).fetchone()
    cluster_count, recommended_count, review_count, hold_count = [
        int(value or 0) for value in cluster_row
    ]
    return {
        "lookback_hours": window_hours,
        "stored_items": sum(item["stored_items"] for item in ordered_groups),
        "window_items": sum(item["window_items"] for item in ordered_groups),
        "window_unique_titles": sum(item["window_unique_titles"] for item in ordered_groups),
        "new_items_24h": sum(item["new_items_24h"] for item in ordered_groups),
        "touched_items_24h": sum(item["touched_items_24h"] for item in ordered_groups),
        "window_observations": sum(item["window_observations"] for item in ordered_groups),
        "cluster_count": cluster_count,
        "recommended_count": recommended_count,
        "review_count": review_count,
        "hold_count": hold_count,
        "sources": ordered_groups,
    }


def list_ranked_trends(
    con: duckdb.DuckDBPyConnection,
    *,
    limit: int = 100,
    minimum_score: float = 0,
    recommendation_statuses: Iterable[str] | None = None,
    sort_by: str = "trend",
) -> pd.DataFrame:
    """Filter the candidate scope before applying the display limit."""

    bounded_limit = max(1, min(int(limit), 500))
    try:
        bounded_minimum_score = max(0.0, min(float(minimum_score), 100.0))
    except (TypeError, ValueError, OverflowError):
        bounded_minimum_score = 0.0

    allowed_statuses = {"recommended", "review", "hold"}
    normalized_statuses = tuple(
        dict.fromkeys(
            status
            for status in (
                str(value or "").strip()
                for value in (recommendation_statuses or ())
            )
            if status in allowed_statuses
        )
    )

    where_parts = ["tc.trend_score >= ?"]
    parameters: list[Any] = [bounded_minimum_score]
    if recommendation_statuses is not None:
        if normalized_statuses:
            placeholders = ", ".join("?" for _ in normalized_statuses)
            where_parts.append(
                f"COALESCE(tc.recommendation_status, 'review') IN ({placeholders})"
            )
            parameters.extend(normalized_statuses)
        else:
            where_parts.append("FALSE")

    where_sql = " AND ".join(where_parts)
    order_by_sql = {
        "opportunity": (
            "tc.opportunity_score DESC, tc.trend_score DESC, "
            "tc.quality_score DESC, tc.last_seen_at DESC"
        ),
        "quality": (
            "tc.quality_score DESC, tc.opportunity_score DESC, "
            "tc.trend_score DESC, tc.last_seen_at DESC"
        ),
        "recent": (
            "tc.last_seen_at DESC, tc.opportunity_score DESC, "
            "tc.trend_score DESC"
        ),
        "trend": (
            "tc.trend_score DESC, tc.opportunity_score DESC, "
            "tc.last_seen_at DESC"
        ),
    }.get(str(sort_by or "trend").strip(), (
        "tc.trend_score DESC, tc.opportunity_score DESC, "
        "tc.last_seen_at DESC"
    ))
    parameters.append(bounded_limit)
    return con.execute(
        f"""
        WITH source_counts AS (
            SELECT tci.cluster_id,
                   COUNT(*) FILTER (
                       WHERE s.source_type IN ('naver_news', 'naver_blog')
                   ) AS naver_count,
                   COUNT(*) FILTER (
                       WHERE s.source_type IN ('daum_web', 'daum_cafe')
                   ) AS daum_count,
                   COUNT(*) FILTER (WHERE s.source_type = 'youtube') AS youtube_count,
                   COUNT(*) FILTER (WHERE s.source_type = 'google_trends') AS google_count,
                   COUNT(*) FILTER (
                       WHERE s.source_type = 'wikipedia_pageviews'
                   ) AS wikipedia_count
            FROM trend_cluster_items tci
            JOIN source_items s ON s.source_item_id = tci.source_item_id
            GROUP BY tci.cluster_id
        )
        SELECT tc.cluster_id,
               CASE COALESCE(tc.recommendation_status, 'review')
                   WHEN 'recommended' THEN '추천'
                   WHEN 'hold' THEN '보류'
                   ELSE '검토'
               END AS 판정,
               COALESCE(tc.recommendation_status, 'review') AS recommendation_status,
               tc.canonical_title AS 주제,
               (
                   SELECT s.source_url
                   FROM trend_cluster_items tci
                   JOIN source_items s ON s.source_item_id = tci.source_item_id
                   WHERE tci.cluster_id = tc.cluster_id
                     AND COALESCE(TRIM(s.source_url), '') <> ''
                   ORDER BY COALESCE(s.signal_value, 0) DESC,
                            COALESCE(s.published_at, s.observed_at, s.imported_at) DESC
                   LIMIT 1
               ) AS 원문,
               tc.trend_score AS 트렌드점수,
               COALESCE(tc.rediscovery_score, 0) AS 재포착점수,
               tc.opportunity_score AS 글감기회,
               COALESCE(tc.quality_score, 50) AS 콘텐츠품질,
               tc.fact_risk_score AS 사실위험,
               tc.item_count AS 언급수, tc.source_type_count AS 출처종류,
               tc.publisher_count AS 서로다른출처, tc.last_seen_at AS 최근확인,
               COALESCE(sc.naver_count, 0) AS naver_count,
               COALESCE(sc.daum_count, 0) AS daum_count,
               COALESCE(sc.youtube_count, 0) AS youtube_count,
               COALESCE(sc.google_count, 0) AS google_count,
               COALESCE(sc.wikipedia_count, 0) AS wikipedia_count,
               COUNT(*) OVER () AS matched_count
        FROM trend_clusters tc
        LEFT JOIN source_counts sc ON sc.cluster_id = tc.cluster_id
        WHERE {where_sql}
        ORDER BY {order_by_sql}
        LIMIT ?
        """,
        parameters,
    ).fetchdf()


def get_trend_cluster(con: duckdb.DuckDBPyConnection, cluster_id: str) -> dict[str, Any] | None:
    row = con.execute("SELECT * FROM trend_clusters WHERE cluster_id = ?", [cluster_id]).fetchone()
    if row is None:
        return None
    columns = [item[0] for item in con.description]
    result = dict(zip(columns, row))
    for field in ("source_types_json", "score_reasons_json", "quality_reasons_json"):
        try:
            result[field.removesuffix("_json")] = json.loads(result.get(field) or "[]")
        except json.JSONDecodeError:
            result[field.removesuffix("_json")] = []
    return result


def get_trend_cluster_items(con: duckdb.DuckDBPyConnection, cluster_id: str) -> list[dict[str, Any]]:
    rows = con.execute(
        """
        SELECT s.source_item_id, s.source_type, s.raw_title, s.source_url,
               s.normalized_url, s.source_name, s.published_at, s.observed_at, s.signal_value,
               s.metadata_json, s.first_imported_at, s.previous_imported_at,
               s.last_imported_at, s.observation_count
        FROM trend_cluster_items c
        JOIN source_items s ON s.source_item_id = c.source_item_id
        WHERE c.cluster_id = ?
        ORDER BY COALESCE(s.published_at, s.observed_at, s.imported_at) DESC
        """,
        [cluster_id],
    ).fetchall()
    columns = [item[0] for item in con.description]
    result = []
    for row in rows:
        item = dict(zip(columns, row))
        try:
            item["metadata"] = json.loads(item.pop("metadata_json") or "{}")
        except json.JSONDecodeError:
            item["metadata"] = {}
        item["source_name"] = _publisher_identity(item)
        result.append(item)
    return result


def _angle_item_title(item: dict[str, Any]) -> str:
    metadata = item.get("metadata") or {}
    return _clean_title(str(metadata.get("item_title") or item.get("raw_title") or ""))


def _angle_unique_evidence_count(items: list[dict[str, Any]]) -> int:
    seen_urls: set[str] = set()
    seen_titles: set[str] = set()
    count = 0
    for item in items:
        item_title = _angle_item_title(item)
        normalized_url = str(item.get("normalized_url") or "").strip() or normalize_url(
            str(item.get("source_url") or "")
        )
        normalized_title = normalize_title(item_title)
        if normalized_url and normalized_url in seen_urls:
            continue
        if normalized_title and normalized_title in seen_titles:
            continue
        if normalized_url:
            seen_urls.add(normalized_url)
        if normalized_title:
            seen_titles.add(normalized_title)
        count += 1
    return count


def _angle_candidate_support(
    candidate: str,
    canonical_title: str,
    items: list[dict[str, Any]],
) -> tuple[int, int]:
    """후보 제목의 세부 대상이 여러 독립 원문에서 반복되는지 계산합니다."""
    candidate_tokens = _editorial_identity_tokens(candidate)
    canonical_tokens = _editorial_identity_tokens(canonical_title)
    detail_tokens = candidate_tokens - canonical_tokens
    if not detail_tokens:
        detail_tokens = candidate_tokens
    if not detail_tokens:
        return 0, 0

    required_overlap = max(1, min(2, (len(detail_tokens) + 1) // 2))
    support_count = 0
    publishers: set[str] = set()
    seen_urls: set[str] = set()
    seen_titles: set[str] = set()
    for item in items:
        item_title = _angle_item_title(item)
        item_tokens = _editorial_identity_tokens(item_title)
        if len(detail_tokens & item_tokens) < required_overlap:
            continue
        normalized_url = str(item.get("normalized_url") or "").strip() or normalize_url(
            str(item.get("source_url") or "")
        )
        normalized_title = normalize_title(item_title)
        if normalized_url and normalized_url in seen_urls:
            continue
        if normalized_title and normalized_title in seen_titles:
            continue
        if normalized_url:
            seen_urls.add(normalized_url)
        if normalized_title:
            seen_titles.add(normalized_title)
        support_count += 1
        publisher = _publisher_identity(item)
        if publisher:
            publishers.add(publisher.casefold())
    return support_count, len(publishers)


def _angle_subject(title: str, items: list[dict[str, Any]]) -> str:
    """단일 원문이 아니라 독립 근거가 반복 지지하는 제목만 상세 주제로 채택합니다."""
    canonical_title = strip_collection_scope(title) or "선택한 주제"
    canonical_tokens = _editorial_identity_tokens(canonical_title)
    best_title = canonical_title
    best_score = _title_quality(canonical_title)[0] if canonical_tokens else -999.0

    unique_evidence_count = _angle_unique_evidence_count(items)
    minimum_detail_support = max(2, int(unique_evidence_count * 0.35 + 0.999))
    candidates = [strip_collection_scope(_angle_item_title(item)) for item in items]
    for candidate in dict.fromkeys(item for item in candidates if item):
        candidate_tokens = _editorial_identity_tokens(candidate)
        if not candidate_tokens:
            continue
        support_count, publisher_count = _angle_candidate_support(
            candidate,
            canonical_title,
            items,
        )
        detail_tokens = candidate_tokens - canonical_tokens
        # 대표 군집보다 더 구체적인 단일 기사·후기 제목은 글쓰기 방향을 지배할 수 없습니다.
        if (
            unique_evidence_count > 1
            and detail_tokens
            and (support_count < minimum_detail_support or publisher_count < 2)
        ):
            continue
        # 단일 원문 묶음은 그 원문을 투명하게 사용하되, 여러 원문 묶음은 독립 근거가 필요합니다.
        if (
            unique_evidence_count > 1
            and not canonical_tokens
            and (support_count < 2 or publisher_count < 2)
        ):
            continue

        quality, _ = _title_quality(candidate)
        folded = candidate.casefold()
        score = quality + min(18.0, support_count * 4.0) + min(12.0, publisher_count * 4.0)
        if ":" in candidate and any(term.casefold() in folded for term in _GENERIC_QUERY_TERMS):
            score -= 80
        if candidate in _GENERIC_SEEDS or folded in {item.casefold() for item in _GENERIC_QUERY_TERMS}:
            score -= 60
        if sum("가" <= char <= "힣" for char in candidate) >= 4:
            score += 8
        if 6 <= len(candidate) <= 55:
            score += 5
        if score > best_score:
            best_score = score
            best_title = candidate
    return best_title


def _angle_evidence_text(items: list[dict[str, Any]]) -> str:
    chunks: list[str] = []
    for item in items:
        metadata = item.get("metadata") or {}
        chunks.extend(
            [
                _angle_item_title(item),
                _clean_title(str(metadata.get("description") or "")),
                _clean_title(str(metadata.get("signal_type") or "")),
            ]
        )
    return " ".join(chunk for chunk in chunks if chunk).casefold()


def recommend_content_angle_details(
    title: str,
    items: Iterable[dict[str, Any]],
) -> list[dict[str, Any]]:
    """주제 의도와 실제 근거에 맞는 글쓰기 방향만 반환합니다."""
    item_list = list(items)
    subject = _angle_subject(title, item_list)
    return recommend_writing_angle_details(subject, item_list)


def angle_transfer_value(angle_detail: dict[str, Any]) -> str:
    """선택한 방향의 사용자 표시 문구를 AI 요청 화면으로 그대로 전달합니다."""
    return str(angle_detail.get("text") or "").strip()


def recommend_content_angles(title: str, items: Iterable[dict[str, Any]]) -> list[str]:
    """기존 호출 호환성을 위해 글쓰기 방향 문구 목록만 반환합니다."""
    return [item["text"] for item in recommend_content_angle_details(title, items)]


def promote_trend_cluster(con: duckdb.DuckDBPyConnection, cluster_id: str) -> str:
    cluster = get_trend_cluster(con, cluster_id)
    if cluster is None:
        raise ValueError("선택한 트렌드 주제를 찾을 수 없습니다.")
    topic_id, _ = add_manual_topic(
        con,
        title=cluster["canonical_title"],
        summary=(
            f"트렌드 점수 {cluster['trend_score']}, 글감 기회 {cluster['opportunity_score']}. "
            f"최근 여러 출처에서 {cluster['item_count']}건 확인된 자동 분석 주제."
        ),
        memo="오늘의 트렌드에서 선택",
        priority=3,
    )
    source_ids = [item["source_item_id"] for item in get_trend_cluster_items(con, cluster_id)]
    now = datetime.now()
    for source_id in source_ids:
        con.execute(
            """
            INSERT INTO topic_source_links(topic_id, source_item_id, match_type, match_score, linked_at)
            VALUES (?, ?, 'trend_cluster', 1.0, ?)
            ON CONFLICT(topic_id, source_item_id) DO NOTHING
            """,
            [topic_id, source_id, now],
        )
    con.execute(
        """
        UPDATE topics SET is_interested = TRUE, priority = 3,
            source_count = (SELECT COUNT(*) FROM topic_source_links WHERE topic_id = ?),
            updated_at = ? WHERE topic_id = ?
        """,
        [topic_id, now, topic_id],
    )
    link_topic_to_trend_cluster(
        con,
        topic_id=topic_id,
        cluster_id=cluster_id,
    )
    return topic_id


def _dynamic_query_candidates(
    source_type: str,
    raw_title: str,
    metadata: dict[str, Any],
) -> list[str]:
    """수집 범위명 대신 실제 항목 제목을 포털 확장 검색어 후보로 사용합니다."""
    candidates: list[str] = []
    signal_type = str(metadata.get("signal_type") or "").strip()

    if source_type == "youtube" and signal_type != "emerging_topic":
        candidates.append(str(metadata.get("item_title") or ""))
    candidates.append(str(raw_title or ""))
    if source_type == "youtube" and signal_type == "emerging_topic":
        candidates.append(str(metadata.get("item_title") or ""))
    candidates.append(str(metadata.get("keyword") or ""))

    representative_titles = metadata.get("representative_titles")
    if isinstance(representative_titles, (list, tuple)):
        candidates.extend(str(item or "") for item in representative_titles)
    elif isinstance(representative_titles, str):
        clean = representative_titles.strip()
        if clean.startswith("["):
            try:
                parsed = json.loads(clean)
            except json.JSONDecodeError:
                parsed = []
            if isinstance(parsed, list):
                candidates.extend(str(item or "") for item in parsed)

    return candidates


def _get_dynamic_queries_for_source(
    con: duckdb.DuckDBPyConnection,
    *,
    source_type: str,
    limit: int,
    lookback_hours: int = 72,
) -> list[str]:
    """Return only recent, specific discovery queries.

    YouTube 교환 파일의 ``topic_title``에는 ``지역 인기: KR / Gaming`` 같은
    수집 범위명이 들어갈 수 있습니다. 이런 범위명은 포털 검색에 쓰지 않고
    metadata의 실제 영상 제목이나 구체적인 주제명을 우선합니다.
    """
    query_limit = max(1, int(limit))
    cutoff = datetime.now() - timedelta(hours=max(6, int(lookback_hours)))
    scan_limit = max(100, min(query_limit * 20, 2000))
    rows = con.execute(
        """
        SELECT raw_title, metadata_json, signal_value,
               COALESCE(observed_at, published_at, last_imported_at, imported_at) AS latest_seen
        FROM source_items
        WHERE source_type = ?
          AND COALESCE(observed_at, published_at, last_imported_at, imported_at) >= ?
        ORDER BY signal_value DESC NULLS LAST, latest_seen DESC NULLS LAST
        LIMIT ?
        """,
        [source_type, cutoff, scan_limit],
    ).fetchall()

    selected: list[str] = []
    seen: set[str] = set()
    for raw_title, metadata_json, _signal_value, _latest_seen in rows:
        try:
            metadata = json.loads(metadata_json or "{}")
        except (TypeError, json.JSONDecodeError):
            metadata = {}
        for candidate in _dynamic_query_candidates(
            source_type,
            str(raw_title or ""),
            metadata,
        ):
            clean = strip_collection_scope(candidate)
            if not _is_specific_query(clean):
                continue
            normalized = normalize_title(clean)
            if not normalized or normalized in seen:
                continue
            seen.add(normalized)
            selected.append(clean)
            break
        if len(selected) >= query_limit:
            break
    return selected


def get_dynamic_youtube_queries(
    con: duckdb.DuckDBPyConnection,
    *,
    limit: int = 8,
    lookback_hours: int = 72,
) -> list[str]:
    return _get_dynamic_queries_for_source(
        con,
        source_type="youtube",
        limit=limit,
        lookback_hours=lookback_hours,
    )


def get_dynamic_google_queries(
    con: duckdb.DuckDBPyConnection,
    *,
    limit: int = 8,
    lookback_hours: int = 72,
) -> list[str]:
    return _get_dynamic_queries_for_source(
        con,
        source_type="google_trends",
        limit=limit,
        lookback_hours=lookback_hours,
    )


def build_portal_search_queries(
    con: duckdb.DuckDBPyConnection,
    configured_seed_queries: list[str] | None,
    *,
    limit: int = 50,
    lookback_hours: int = 72,
) -> list[str]:
    query_limit = max(1, int(limit))
    configured = [str(item or "").strip() for item in (configured_seed_queries or [])]
    google_limit = min(20, max(8, query_limit // 3))
    youtube_limit = min(30, max(12, query_limit // 2))
    return list(
        dict.fromkeys(
            get_dynamic_google_queries(
                con,
                limit=google_limit,
                lookback_hours=lookback_hours,
            )
            + get_dynamic_youtube_queries(
                con,
                limit=youtube_limit,
                lookback_hours=lookback_hours,
            )
            + [item for item in configured if item]
        )
    )[:query_limit]



def _exception_chain(exc: BaseException) -> list[BaseException]:
    chain: list[BaseException] = []
    current: BaseException | None = exc
    seen: set[int] = set()
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        chain.append(current)
        current = current.__cause__ or current.__context__
    return chain


def _http_status_from_error(exc: BaseException) -> int | None:
    for item in _exception_chain(exc):
        if isinstance(item, HTTPError):
            return int(item.code)
    match = re.search(r"HTTP\s+(\d{3})", str(exc), flags=re.IGNORECASE)
    return int(match.group(1)) if match else None


def _is_retryable_portal_error(exc: BaseException) -> bool:
    status = _http_status_from_error(exc)
    if status in {408, 425, 429, 500, 502, 503, 504}:
        return True
    for item in _exception_chain(exc):
        if isinstance(item, (URLError, TimeoutError, socket.timeout, ConnectionError)):
            return True
    message = str(exc).casefold()
    return any(
        token in message
        for token in (
            "timed out",
            "timeout",
            "connection reset",
            "connection aborted",
            "temporary failure",
            "네트워크 연결에 실패",
        )
    )


def _is_batch_fatal_portal_error(exc: BaseException) -> bool:
    status = _http_status_from_error(exc)
    if status in {400, 401, 403}:
        return True
    chain = _exception_chain(exc)
    if any(isinstance(item, socket.gaierror) for item in chain):
        return True
    message = str(exc).casefold()
    return any(
        token in message
        for token in (
            "도메인 주소를 찾지 못했습니다",
            "api 키가 없습니다",
            "인증이 http 401",
            "인증이 http 403",
        )
    )


def _retry_delay_seconds(exc: BaseException, retry_number: int, base_delay: float) -> float:
    multiplier = 2.5 if _http_status_from_error(exc) == 429 else 1.0
    return min(3.0, max(0.05, float(base_delay)) * (2 ** max(0, retry_number - 1)) * multiplier)


def _fetch_portal_tasks(
    adapter: Any,
    tasks: list[dict[str, Any]],
    *,
    max_workers: int,
    retry_budget: int = 0,
    max_retries: int = 2,
    retry_base_delay: float = 0.35,
    sleep_func: Callable[[float], None] = sleep,
) -> dict[str, Any]:
    """HTTP만 병렬 처리하고 일시 오류는 제한적으로 재시도하며 부분 성공을 보존합니다."""
    started = perf_counter()
    if not tasks:
        return {
            "signals": [],
            "attempt_count": 0,
            "successful_requests": 0,
            "failed_requests": 0,
            "skipped_requests": 0,
            "retry_count": 0,
            "request_errors": [],
            "network_seconds": 0.0,
        }

    workers = max(1, min(int(max_workers), 12, len(tasks)))
    ordered_results: list[list[dict[str, Any]] | None] = [None] * len(tasks)
    retry_budget_remaining = [max(0, int(retry_budget))]
    retry_budget_lock = threading.Lock()
    fatal_event = threading.Event()
    throttle_until = [0.0]
    throttle_lock = threading.Lock()

    def reserve_retry() -> bool:
        with retry_budget_lock:
            if retry_budget_remaining[0] <= 0:
                return False
            retry_budget_remaining[0] -= 1
            return True

    def wait_for_shared_throttle() -> None:
        with throttle_lock:
            delay = max(0.0, throttle_until[0] - perf_counter())
        if delay:
            sleep_func(delay)

    def extend_shared_throttle(delay: float) -> None:
        with throttle_lock:
            throttle_until[0] = max(throttle_until[0], perf_counter() + delay)

    def execute(index: int, task: dict[str, Any]) -> dict[str, Any]:
        if fatal_event.is_set():
            return {"index": index, "signals": [], "attempts": 0, "retries": 0, "skipped": True}

        kwargs = dict(task["kwargs"])
        seed_kind = str(task["seed_kind"])
        attempts = 0
        retries = 0
        last_error: BaseException | None = None
        while attempts == 0 or retries <= max(0, int(max_retries)):
            if attempts == 0 and fatal_event.is_set():
                return {"index": index, "signals": [], "attempts": 0, "retries": 0, "skipped": True}
            wait_for_shared_throttle()
            attempts += 1
            try:
                signals = adapter.search(**kwargs)
                for signal in signals:
                    metadata = signal.setdefault("metadata", {})
                    metadata["seed_kind"] = seed_kind
                return {
                    "index": index,
                    "signals": signals,
                    "attempts": attempts,
                    "retries": retries,
                    "skipped": False,
                }
            except Exception as exc:  # 요청 하나의 실패가 다른 성공 결과를 버리지 않도록 개별 처리
                last_error = exc
                if _is_batch_fatal_portal_error(exc):
                    fatal_event.set()
                    break
                if not _is_retryable_portal_error(exc) or retries >= max_retries or not reserve_retry():
                    break
                retries += 1
                delay = _retry_delay_seconds(exc, retries, retry_base_delay)
                if _http_status_from_error(exc) == 429:
                    extend_shared_throttle(delay)
                sleep_func(delay)

        return {
            "index": index,
            "signals": [],
            "attempts": attempts,
            "retries": retries,
            "skipped": False,
            "error": str(last_error or "알 수 없는 포털 요청 오류"),
        }

    task_results: list[dict[str, Any] | None] = [None] * len(tasks)
    with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="portal-search") as executor:
        futures = {executor.submit(execute, index, task): index for index, task in enumerate(tasks)}
        for future in as_completed(futures):
            index = futures[future]
            try:
                task_results[index] = future.result()
            except Exception as exc:  # 작업 래퍼 자체의 예상하지 못한 오류도 해당 요청만 실패 처리
                task_results[index] = {
                    "index": index,
                    "signals": [],
                    "attempts": 0,
                    "retries": 0,
                    "skipped": False,
                    "error": str(exc),
                }

    attempt_count = 0
    retry_count = 0
    successful_requests = 0
    failed_requests = 0
    skipped_requests = 0
    request_errors: list[str] = []
    for index, task_result in enumerate(task_results):
        current = task_result or {
            "index": index,
            "signals": [],
            "attempts": 0,
            "retries": 0,
            "skipped": True,
        }
        attempt_count += int(current.get("attempts", 0) or 0)
        retry_count += int(current.get("retries", 0) or 0)
        if current.get("skipped"):
            skipped_requests += 1
            continue
        error = str(current.get("error") or "").strip()
        if error:
            failed_requests += 1
            kwargs = tasks[index].get("kwargs") or {}
            request_errors.append(
                f"{kwargs.get('search_type', '검색')}:{kwargs.get('query', '')}:p{kwargs.get('page', 1)} - {error}"
            )
            continue
        successful_requests += 1
        signals = current.get("signals") or []
        ordered_results[index] = signals

    all_signals: list[dict[str, Any]] = []
    for signals in ordered_results:
        if signals:
            all_signals.extend(signals)
    return {
        "signals": all_signals,
        "attempt_count": attempt_count,
        "successful_requests": successful_requests,
        "failed_requests": failed_requests,
        "skipped_requests": skipped_requests,
        "retry_count": retry_count,
        "request_errors": request_errors,
        "network_seconds": round(perf_counter() - started, 2),
    }


def _seed_kind_for_query(
    query: str,
    *,
    google_queries: set[str],
    youtube_queries: set[str],
) -> str:
    if query in google_queries:
        return "google_trend"
    if query in youtube_queries:
        return "youtube_topic"
    return "configured_seed"


def _portal_retry_budget(usage: Any, planned_calls: int, max_retries: int) -> int:
    daily_extra = max(0, int(usage.daily_remaining) - int(planned_calls))
    monthly_extra = max(0, int(usage.monthly_remaining) - int(planned_calls))
    return max(0, min(int(planned_calls) * max(0, int(max_retries)), daily_extra, monthly_extra))


def _prepare_daum_collection(
    con: duckdb.DuckDBPyConnection,
    *,
    seed_queries: list[str],
    size_per_query: int,
    pages_per_query: int,
    daily_safety_limit: int,
    monthly_safety_limit: int,
    max_workers: int,
    max_retries: int = 2,
) -> dict[str, Any]:
    clean_queries = list(dict.fromkeys(str(item or "").strip() for item in seed_queries if str(item or "").strip()))
    page_count = max(1, min(int(pages_per_query), 5))
    planned_calls = len(clean_queries) * 2 * page_count
    usage = ensure_kakao_daum_capacity(
        con,
        planned_calls=planned_calls,
        daily_limit=daily_safety_limit,
        monthly_limit=monthly_safety_limit,
    )
    dynamic_limit = max(8, len(clean_queries))
    youtube_queries = set(get_dynamic_youtube_queries(con, limit=dynamic_limit))
    google_queries = set(get_dynamic_google_queries(con, limit=dynamic_limit))
    tasks: list[dict[str, Any]] = []
    for query in clean_queries:
        seed_kind = _seed_kind_for_query(query, google_queries=google_queries, youtube_queries=youtube_queries)
        for search_type in ("web", "cafe"):
            for page in range(1, page_count + 1):
                tasks.append({
                    "seed_kind": seed_kind,
                    "kwargs": {
                        "search_type": search_type,
                        "query": query,
                        "size": size_per_query,
                        "sort": "recency",
                        "page": page,
                    },
                })
    return {
        "provider": KAKAO_DAUM_PROVIDER,
        "api_name": KAKAO_DAUM_API,
        "sync_source_type": "daum_search",
        "tasks": tasks,
        "max_workers": max_workers,
        "max_retries": max_retries,
        "retry_budget": _portal_retry_budget(usage, planned_calls, max_retries),
    }


def _prepare_naver_collection(
    con: duckdb.DuckDBPyConnection,
    *,
    seed_queries: list[str],
    display_per_query: int,
    pages_per_query: int,
    daily_safety_limit: int,
    monthly_safety_limit: int,
    max_workers: int,
    max_retries: int = 2,
) -> dict[str, Any]:
    clean_queries = list(dict.fromkeys(str(item or "").strip() for item in seed_queries if str(item or "").strip()))
    page_count = max(1, min(int(pages_per_query), 5))
    planned_calls = len(clean_queries) * 2 * page_count
    usage = ensure_naver_search_capacity(
        con,
        planned_calls=planned_calls,
        daily_limit=daily_safety_limit,
        monthly_limit=monthly_safety_limit,
    )
    dynamic_limit = max(8, len(clean_queries))
    youtube_queries = set(get_dynamic_youtube_queries(con, limit=dynamic_limit))
    google_queries = set(get_dynamic_google_queries(con, limit=dynamic_limit))
    tasks: list[dict[str, Any]] = []
    for query in clean_queries:
        seed_kind = _seed_kind_for_query(query, google_queries=google_queries, youtube_queries=youtube_queries)
        for search_type in ("news", "blog"):
            for page in range(1, page_count + 1):
                tasks.append({
                    "seed_kind": seed_kind,
                    "kwargs": {
                        "search_type": search_type,
                        "query": query,
                        "display": display_per_query,
                        "sort": "date",
                        "page": page,
                    },
                })
    return {
        "provider": "naver",
        "api_name": "search_api",
        "sync_source_type": "naver_search",
        "tasks": tasks,
        "max_workers": max_workers,
        "max_retries": max_retries,
        "retry_budget": _portal_retry_budget(usage, planned_calls, max_retries),
    }


def _finalize_portal_collection(
    con: duckdb.DuckDBPyConnection,
    plan: dict[str, Any],
    fetch_result: dict[str, Any],
    *,
    collection_run_id: str | None = None,
) -> dict[str, Any]:
    attempts = int(fetch_result.get("attempt_count", 0) or 0)
    if attempts:
        record_local_api_calls(
            con,
            provider=str(plan["provider"]),
            api_name=str(plan["api_name"]),
            count=attempts,
        )

    signals = list(fetch_result.get("signals") or [])
    database_started = perf_counter()
    if signals:
        result = import_preloaded_source_signals(
            con,
            signals,
            sync_source_type=str(plan["sync_source_type"]),
            create_topics=False,
            collection_run_id=collection_run_id,
        )
    else:
        result = {
            "items_read": 0,
            "items_added": 0,
            "items_updated": 0,
            "items_skipped": 0,
        }
    failed = int(fetch_result.get("failed_requests", 0) or 0)
    skipped = int(fetch_result.get("skipped_requests", 0) or 0)
    successful = int(fetch_result.get("successful_requests", 0) or 0)
    status = "failed" if successful == 0 and (failed or skipped) else "partial" if (failed or skipped) else "success"
    result.update({
        "status": status,
        "planned_request_count": len(plan.get("tasks") or []),
        "request_count": attempts,
        "successful_requests": successful,
        "failed_requests": failed,
        "skipped_requests": skipped,
        "retry_count": int(fetch_result.get("retry_count", 0) or 0),
        "request_errors": list(fetch_result.get("request_errors") or []),
        "network_seconds": float(fetch_result.get("network_seconds", 0.0) or 0.0),
        "database_seconds": round(perf_counter() - database_started, 2),
    })
    return result


def _portal_result_warning(label: str, source_result: dict[str, Any]) -> str:
    failed = int(source_result.get("failed_requests", 0) or 0)
    skipped = int(source_result.get("skipped_requests", 0) or 0)
    retries = int(source_result.get("retry_count", 0) or 0)
    samples = list(source_result.get("request_errors") or [])[:2]
    detail = f"{label} 요청 중 실패 {failed}회, 생략 {skipped}회, 재시도 {retries}회"
    if samples:
        detail += " · " + " | ".join(samples)
    return detail


def collect_daum_signals(
    con: duckdb.DuckDBPyConnection,
    adapter: DaumSearchAdapter,
    *,
    seed_queries: list[str],
    size_per_query: int = 10,
    pages_per_query: int = 2,
    daily_safety_limit: int = 50000,
    monthly_safety_limit: int = 3000000,
    max_workers: int = 4,
    collection_run_id: str | None = None,
) -> dict[str, Any]:
    plan = _prepare_daum_collection(
        con,
        seed_queries=seed_queries,
        size_per_query=size_per_query,
        pages_per_query=pages_per_query,
        daily_safety_limit=daily_safety_limit,
        monthly_safety_limit=monthly_safety_limit,
        max_workers=max_workers,
    )
    fetch_result = _fetch_portal_tasks(
        adapter,
        plan["tasks"],
        max_workers=plan["max_workers"],
        retry_budget=plan["retry_budget"],
        max_retries=plan["max_retries"],
    )
    return _finalize_portal_collection(
        con,
        plan,
        fetch_result,
        collection_run_id=collection_run_id,
    )


def collect_naver_signals(
    con: duckdb.DuckDBPyConnection,
    adapter: NaverSearchAdapter,
    *,
    seed_queries: list[str],
    display_per_query: int = 10,
    pages_per_query: int = 2,
    daily_safety_limit: int = 25000,
    monthly_safety_limit: int = 775000,
    max_workers: int = 6,
    collection_run_id: str | None = None,
) -> dict[str, Any]:
    plan = _prepare_naver_collection(
        con,
        seed_queries=seed_queries,
        display_per_query=display_per_query,
        pages_per_query=pages_per_query,
        daily_safety_limit=daily_safety_limit,
        monthly_safety_limit=monthly_safety_limit,
        max_workers=max_workers,
    )
    fetch_result = _fetch_portal_tasks(
        adapter,
        plan["tasks"],
        max_workers=plan["max_workers"],
        retry_budget=plan["retry_budget"],
        max_retries=plan["max_retries"],
    )
    return _finalize_portal_collection(
        con,
        plan,
        fetch_result,
        collection_run_id=collection_run_id,
    )


def _collect_public_signals(
    con: duckdb.DuckDBPyConnection,
    adapter: Any,
    *,
    limit: int,
    sync_source_type: str,
    provider: str,
    api_name: str,
) -> dict[str, Any]:
    """무료 공개 데이터 수집과 실제 HTTP 요청 횟수 기록을 함께 처리합니다."""
    before = int(getattr(adapter, "request_count", 0) or 0)
    try:
        result = import_source_signals(
            con,
            adapter,
            limit=limit,
            sync_source_type=sync_source_type,
            create_topics=False,
        )
    finally:
        after = int(getattr(adapter, "request_count", before) or before)
        record_local_api_calls(
            con,
            provider=provider,
            api_name=api_name,
            count=max(0, after - before),
        )
    result["request_count"] = max(0, after - before)
    result["retry_count"] = 0
    return result


def _youtube_parquet_signature(adapter: Any) -> str | None:
    parquet_path = getattr(adapter, "parquet_path", None)
    if parquet_path is None:
        return None
    path = Path(parquet_path)
    if not path.is_file():
        return None
    stat = path.stat()
    raw = f"{path.resolve()}|{stat.st_size}|{stat.st_mtime_ns}"
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()


def _import_youtube_if_changed(
    con: duckdb.DuckDBPyConnection,
    adapter: Any,
    *,
    limit: int,
    sync_source_type: str,
) -> dict[str, Any]:
    file_signature = _youtube_parquet_signature(adapter)
    signature = f"v1|{file_signature}|limit={max(1, int(limit))}" if file_signature else None
    signature_key = "youtube_parquet_last_import_signature"
    existing_count = int(
        con.execute("SELECT COUNT(*) FROM source_items WHERE source_type = 'youtube'").fetchone()[0] or 0
    )
    if signature and existing_count > 0 and get_setting(con, signature_key, "") == signature:
        return {
            "status": "skipped",
            "items_read": 0,
            "items_added": 0,
            "items_updated": 0,
            "items_skipped": 0,
            "unchanged": True,
        }

    result = import_youtube_signals(
        con,
        adapter,
        limit=limit,
        sync_source_type=sync_source_type,
    )
    if signature:
        set_setting(con, signature_key, signature)
    result["unchanged"] = False
    return result


def refresh_trend_sources(
    con: duckdb.DuckDBPyConnection,
    *,
    youtube_adapter: Any | None = None,
    naver_adapter: NaverSearchAdapter | None = None,
    daum_adapter: DaumSearchAdapter | None = None,
    google_trends_adapter: Any | None = None,
    wikipedia_adapter: Any | None = None,
    configured_seed_queries: list[str] | None = None,
    youtube_limit: int = 300,
    google_trends_limit: int = 50,
    wikipedia_limit: int = 50,
    naver_display_per_query: int = 10,
    daum_size_per_query: int = 10,
    portal_query_limit: int = 50,
    portal_pages_per_query: int = 2,
    naver_max_workers: int = 6,
    daum_max_workers: int = 4,
    lookback_hours: int = 72,
    naver_daily_safety_limit: int = 25000,
    naver_monthly_safety_limit: int = 775000,
    kakao_daum_daily_safety_limit: int = 50000,
    kakao_daum_monthly_safety_limit: int = 3000000,
    analysis_source_limits: dict[str, int] | None = None,
    collection_run_id: str | None = None,
    progress_callback: Callable[[float, str], None] | None = None,
) -> dict[str, Any]:
    """출처별 실패를 격리하고 사용 가능한 데이터로 순위를 다시 계산합니다."""
    result: dict[str, Any] = {
        "youtube": None,
        "google_trends": None,
        "wikipedia": None,
        "naver": None,
        "daum": None,
        "errors": {},
        "warnings": {},
        "ranking": None,
        "timings": {},
        "total_elapsed_seconds": 0.0,
    }
    started_at = perf_counter()

    def notify(progress: float, message: str) -> None:
        if progress_callback is not None:
            progress_callback(max(0.0, min(1.0, progress)), message)


    def record_timing(source_key: str, started: float) -> None:
        result["timings"][source_key] = round(perf_counter() - started, 2)

    notify(0.03, "수집 준비 중")

    notify(0.10, "1/7 YouTube 교환 데이터 반영 중")
    if youtube_adapter is not None:
        youtube_started = perf_counter()
        try:
            result["youtube"] = _import_youtube_if_changed(
                con,
                youtube_adapter,
                limit=youtube_limit,
                sync_source_type="youtube_parquet",
            )
        except Exception as exc:  # 출처 하나의 실패가 전체 분석을 막지 않도록 격리
            result["errors"]["youtube"] = str(exc)
        finally:
            record_timing("youtube", youtube_started)

    notify(0.23, "2/7 Google Trends 급상승 검색어 수집 중")
    # Google 급상승 검색어를 먼저 저장해야 같은 실행에서 NAVER 뉴스·블로그 검색어로 활용할 수 있습니다.
    if google_trends_adapter is not None:
        google_started = perf_counter()
        try:
            result["google_trends"] = _collect_public_signals(
                con,
                google_trends_adapter,
                limit=google_trends_limit,
                sync_source_type="google_trends_rss",
                provider=GOOGLE_TRENDS_PROVIDER,
                api_name=GOOGLE_TRENDS_API,
            )
        except Exception as exc:
            result["errors"]["google_trends"] = str(exc)
        finally:
            record_timing("google_trends", google_started)

    notify(0.36, "3/7 위키백과 조회수 수집 중")
    if wikipedia_adapter is not None:
        wikipedia_started = perf_counter()
        try:
            result["wikipedia"] = _collect_public_signals(
                con,
                wikipedia_adapter,
                limit=wikipedia_limit,
                sync_source_type="wikimedia_pageviews",
                provider=WIKIMEDIA_PROVIDER,
                api_name=WIKIMEDIA_API,
            )
        except Exception as exc:
            result["errors"]["wikipedia"] = str(exc)
        finally:
            record_timing("wikipedia", wikipedia_started)

    notify(0.48, "4/7 포털 탐색어 구성 중")
    queries = build_portal_search_queries(
        con,
        configured_seed_queries,
        limit=portal_query_limit,
        lookback_hours=lookback_hours,
    )

    notify(0.58, "5/7 NAVER·Daum 동시 수집 준비 중")
    portal_plans: dict[str, tuple[Any, dict[str, Any]]] = {}
    portal_started: dict[str, float] = {}

    if naver_adapter is not None:
        portal_started["naver"] = perf_counter()
        try:
            portal_plans["naver"] = (
                naver_adapter,
                _prepare_naver_collection(
                    con,
                    seed_queries=queries,
                    display_per_query=naver_display_per_query,
                    pages_per_query=portal_pages_per_query,
                    daily_safety_limit=naver_daily_safety_limit,
                    monthly_safety_limit=naver_monthly_safety_limit,
                    max_workers=naver_max_workers,
                ),
            )
        except Exception as exc:
            result["errors"]["naver"] = str(exc)
            record_timing("naver", portal_started["naver"])

    if daum_adapter is not None:
        portal_started["daum"] = perf_counter()
        try:
            portal_plans["daum"] = (
                daum_adapter,
                _prepare_daum_collection(
                    con,
                    seed_queries=queries,
                    size_per_query=daum_size_per_query,
                    pages_per_query=portal_pages_per_query,
                    daily_safety_limit=kakao_daum_daily_safety_limit,
                    monthly_safety_limit=kakao_daum_monthly_safety_limit,
                    max_workers=daum_max_workers,
                ),
            )
        except Exception as exc:
            result["errors"]["daum"] = str(exc)
            record_timing("daum", portal_started["daum"])

    portal_fetch_results: dict[str, dict[str, Any]] = {}
    if portal_plans:
        notify(0.66, "5/7 NAVER·Daum 네트워크 요청 동시 실행 중")
        with ThreadPoolExecutor(max_workers=min(2, len(portal_plans)), thread_name_prefix="portal-provider") as executor:
            futures = {
                executor.submit(
                    _fetch_portal_tasks,
                    adapter,
                    plan["tasks"],
                    max_workers=plan["max_workers"],
                    retry_budget=plan["retry_budget"],
                    max_retries=plan["max_retries"],
                ): source_key
                for source_key, (adapter, plan) in portal_plans.items()
            }
            for future in as_completed(futures):
                source_key = futures[future]
                try:
                    portal_fetch_results[source_key] = future.result()
                except Exception as exc:
                    result["errors"][source_key] = str(exc)

        for source_key in portal_plans:
            if source_key not in portal_fetch_results and source_key not in result["timings"]:
                record_timing(source_key, portal_started[source_key])

        notify(0.82, "6/7 포털 결과를 DB에 저장 중")
        for source_key in ("naver", "daum"):
            if source_key not in portal_plans or source_key not in portal_fetch_results:
                continue
            _adapter, plan = portal_plans[source_key]
            try:
                source_result = _finalize_portal_collection(
                    con,
                    plan,
                    portal_fetch_results[source_key],
                    collection_run_id=collection_run_id,
                )
                result[source_key] = source_result
                if source_result.get("status") == "failed":
                    result["errors"][source_key] = _portal_result_warning(
                        "NAVER" if source_key == "naver" else "Daum",
                        source_result,
                    )
                elif source_result.get("status") == "partial":
                    result["warnings"][source_key] = _portal_result_warning(
                        "NAVER" if source_key == "naver" else "Daum",
                        source_result,
                    )
            except Exception as exc:
                result["errors"][source_key] = str(exc)
            finally:
                record_timing(source_key, portal_started[source_key])

    notify(0.90, "7/7 통합 군집·순위 계산 중")
    ranking_started = perf_counter()
    result["ranking"] = rebuild_trend_rankings(
        con,
        lookback_hours=lookback_hours,
        source_limits=analysis_source_limits,
    )
    record_timing("ranking", ranking_started)
    result["total_elapsed_seconds"] = round(perf_counter() - started_at, 2)
    notify(1.0, "최신 데이터 분석 완료")
    return result


def refresh_trend_sources_short_connections(
    db_path: str | Path,
    *,
    youtube_adapter: Any | None = None,
    naver_adapter: NaverSearchAdapter | None = None,
    daum_adapter: DaumSearchAdapter | None = None,
    google_trends_adapter: Any | None = None,
    wikipedia_adapter: Any | None = None,
    configured_seed_queries: list[str] | None = None,
    youtube_limit: int = 300,
    google_trends_limit: int = 50,
    wikipedia_limit: int = 50,
    naver_display_per_query: int = 10,
    daum_size_per_query: int = 10,
    portal_query_limit: int = 50,
    portal_pages_per_query: int = 2,
    naver_max_workers: int = 6,
    daum_max_workers: int = 4,
    lookback_hours: int = 72,
    naver_daily_safety_limit: int = 25000,
    naver_monthly_safety_limit: int = 775000,
    kakao_daum_daily_safety_limit: int = 50000,
    kakao_daum_monthly_safety_limit: int = 3000000,
    analysis_source_limits: dict[str, int] | None = None,
    collection_run_id: str | None = None,
    progress_callback: Callable[[float, str], None] | None = None,
) -> dict[str, Any]:
    """외부 통신 중 DuckDB를 닫고 출처별 결과만 짧게 저장합니다."""
    result: dict[str, Any] = {
        "youtube": None,
        "google_trends": None,
        "wikipedia": None,
        "naver": None,
        "daum": None,
        "errors": {},
        "warnings": {},
        "ranking": None,
        "timings": {},
        "total_elapsed_seconds": 0.0,
    }
    started_at = perf_counter()

    def notify(progress: float, message: str) -> None:
        if progress_callback is not None:
            progress_callback(max(0.0, min(1.0, progress)), message)

    def record_timing(source_key: str, started: float) -> None:
        result["timings"][source_key] = round(perf_counter() - started, 2)

    def save_public_signals(
        signals: list[dict[str, Any]],
        *,
        sync_source_type: str,
        provider: str,
        api_name: str,
        request_count: int,
    ) -> dict[str, Any]:
        with connect_database(db_path) as con:
            saved = import_preloaded_source_signals(
                con,
                signals,
                sync_source_type=sync_source_type,
                create_topics=False,
                collection_run_id=collection_run_id,
            )
            record_local_api_calls(
                con,
                provider=provider,
                api_name=api_name,
                count=max(0, int(request_count)),
            )
        saved["request_count"] = max(0, int(request_count))
        saved["retry_count"] = 0
        return saved

    def record_external_failure(sync_source_type: str, error: BaseException) -> None:
        try:
            with connect_database(db_path) as con:
                record_source_import_failure(
                    con,
                    sync_source_type=sync_source_type,
                    error=error,
                )
        except Exception:
            # 원래 외부 수집 오류를 보존합니다.
            pass

    notify(0.03, "수집 준비 중")

    notify(0.08, "1/7 YouTube 교환 파일 확인 중")
    if youtube_adapter is not None:
        youtube_started = perf_counter()
        try:
            file_signature = _youtube_parquet_signature(youtube_adapter)
            signature = (
                f"v1|{file_signature}|limit={max(1, int(youtube_limit))}"
                if file_signature
                else None
            )
            signature_key = "youtube_parquet_last_import_signature"
            with connect_database(db_path) as con:
                existing_count = int(
                    con.execute(
                        "SELECT COUNT(*) FROM source_items WHERE source_type = 'youtube'"
                    ).fetchone()[0]
                    or 0
                )
                unchanged = bool(
                    signature
                    and existing_count > 0
                    and get_setting(con, signature_key, "") == signature
                )
            if unchanged:
                result["youtube"] = {
                    "status": "skipped",
                    "items_read": 0,
                    "items_added": 0,
                    "items_updated": 0,
                    "items_skipped": 0,
                    "unchanged": True,
                }
            else:
                notify(0.12, "1/7 YouTube 교환 파일 읽는 중")
                signals = youtube_adapter.load_signals(limit=youtube_limit)
                notify(0.16, "1/7 YouTube 결과 저장 중")
                with connect_database(db_path) as con:
                    saved = import_preloaded_source_signals(
                        con,
                        list(signals),
                        sync_source_type="youtube_parquet",
                        create_topics=True,
                    )
                    if signature:
                        set_setting(con, signature_key, signature)
                saved["unchanged"] = False
                result["youtube"] = saved
        except Exception as exc:
            record_external_failure("youtube_parquet", exc)
            result["errors"]["youtube"] = str(exc)
        finally:
            record_timing("youtube", youtube_started)

    notify(0.22, "2/7 Google Trends 네트워크 요청 중")
    if google_trends_adapter is not None:
        google_started = perf_counter()
        before = int(getattr(google_trends_adapter, "request_count", 0) or 0)
        try:
            signals = google_trends_adapter.load_signals(limit=google_trends_limit)
            after = int(getattr(google_trends_adapter, "request_count", before) or before)
            notify(0.27, "2/7 Google Trends 결과 저장 중")
            result["google_trends"] = save_public_signals(
                list(signals),
                sync_source_type="google_trends_rss",
                provider=GOOGLE_TRENDS_PROVIDER,
                api_name=GOOGLE_TRENDS_API,
                request_count=max(0, after - before),
            )
        except Exception as exc:
            after = int(getattr(google_trends_adapter, "request_count", before) or before)
            try:
                with connect_database(db_path) as con:
                    record_local_api_calls(
                        con,
                        provider=GOOGLE_TRENDS_PROVIDER,
                        api_name=GOOGLE_TRENDS_API,
                        count=max(0, after - before),
                    )
            except Exception:
                pass
            record_external_failure("google_trends_rss", exc)
            result["errors"]["google_trends"] = str(exc)
        finally:
            record_timing("google_trends", google_started)

    notify(0.32, "3/7 위키백과 네트워크 요청 중")
    if wikipedia_adapter is not None:
        wikipedia_started = perf_counter()
        before = int(getattr(wikipedia_adapter, "request_count", 0) or 0)
        try:
            signals = wikipedia_adapter.load_signals(limit=wikipedia_limit)
            after = int(getattr(wikipedia_adapter, "request_count", before) or before)
            notify(0.37, "3/7 위키백과 결과 저장 중")
            result["wikipedia"] = save_public_signals(
                list(signals),
                sync_source_type="wikimedia_pageviews",
                provider=WIKIMEDIA_PROVIDER,
                api_name=WIKIMEDIA_API,
                request_count=max(0, after - before),
            )
        except Exception as exc:
            after = int(getattr(wikipedia_adapter, "request_count", before) or before)
            try:
                with connect_database(db_path) as con:
                    record_local_api_calls(
                        con,
                        provider=WIKIMEDIA_PROVIDER,
                        api_name=WIKIMEDIA_API,
                        count=max(0, after - before),
                    )
            except Exception:
                pass
            record_external_failure("wikimedia_pageviews", exc)
            result["errors"]["wikipedia"] = str(exc)
        finally:
            record_timing("wikipedia", wikipedia_started)

    notify(0.43, "4/7 포털 탐색어와 호출 한도 확인 중")
    portal_plans: dict[str, tuple[Any, dict[str, Any]]] = {}
    portal_started: dict[str, float] = {}
    try:
        with connect_database(db_path) as con:
            queries = build_portal_search_queries(
                con,
                configured_seed_queries,
                limit=portal_query_limit,
                lookback_hours=lookback_hours,
            )
            if naver_adapter is not None:
                portal_started["naver"] = perf_counter()
                try:
                    portal_plans["naver"] = (
                        naver_adapter,
                        _prepare_naver_collection(
                            con,
                            seed_queries=queries,
                            display_per_query=naver_display_per_query,
                            pages_per_query=portal_pages_per_query,
                            daily_safety_limit=naver_daily_safety_limit,
                            monthly_safety_limit=naver_monthly_safety_limit,
                            max_workers=naver_max_workers,
                        ),
                    )
                except Exception as exc:
                    result["errors"]["naver"] = str(exc)
                    record_timing("naver", portal_started["naver"])
            if daum_adapter is not None:
                portal_started["daum"] = perf_counter()
                try:
                    portal_plans["daum"] = (
                        daum_adapter,
                        _prepare_daum_collection(
                            con,
                            seed_queries=queries,
                            size_per_query=daum_size_per_query,
                            pages_per_query=portal_pages_per_query,
                            daily_safety_limit=kakao_daum_daily_safety_limit,
                            monthly_safety_limit=kakao_daum_monthly_safety_limit,
                            max_workers=daum_max_workers,
                        ),
                    )
                except Exception as exc:
                    result["errors"]["daum"] = str(exc)
                    record_timing("daum", portal_started["daum"])
    except Exception as exc:
        if naver_adapter is not None and "naver" not in result["errors"]:
            result["errors"]["naver"] = str(exc)
        if daum_adapter is not None and "daum" not in result["errors"]:
            result["errors"]["daum"] = str(exc)

    portal_fetch_results: dict[str, dict[str, Any]] = {}
    if portal_plans:
        notify(0.52, "5/7 NAVER·Daum 네트워크 요청 동시 실행 중")
        with ThreadPoolExecutor(
            max_workers=min(2, len(portal_plans)),
            thread_name_prefix="portal-provider",
        ) as executor:
            futures = {
                executor.submit(
                    _fetch_portal_tasks,
                    adapter,
                    plan["tasks"],
                    max_workers=plan["max_workers"],
                    retry_budget=plan["retry_budget"],
                    max_retries=plan["max_retries"],
                ): source_key
                for source_key, (adapter, plan) in portal_plans.items()
            }
            for future in as_completed(futures):
                source_key = futures[future]
                try:
                    portal_fetch_results[source_key] = future.result()
                except Exception as exc:
                    result["errors"][source_key] = str(exc)

        notify(0.76, "6/7 포털 결과를 출처별로 저장 중")
        for source_key in ("naver", "daum"):
            if source_key not in portal_plans or source_key not in portal_fetch_results:
                continue
            _adapter, plan = portal_plans[source_key]
            try:
                with connect_database(db_path) as con:
                    source_result = _finalize_portal_collection(
                        con,
                        plan,
                        portal_fetch_results[source_key],
                        collection_run_id=collection_run_id,
                    )
                result[source_key] = source_result
                if source_result.get("status") == "failed":
                    result["errors"][source_key] = _portal_result_warning(
                        "NAVER" if source_key == "naver" else "Daum",
                        source_result,
                    )
                elif source_result.get("status") == "partial":
                    result["warnings"][source_key] = _portal_result_warning(
                        "NAVER" if source_key == "naver" else "Daum",
                        source_result,
                    )
            except Exception as exc:
                result["errors"][source_key] = str(exc)
            finally:
                record_timing(source_key, portal_started.get(source_key, started_at))

    source_keys = ("youtube", "google_trends", "wikipedia", "naver", "daum")
    usable_statuses = {"success", "partial", "partial_success", "skipped"}
    usable_source_exists = any(
        isinstance(result.get(source_key), dict)
        and str(
            (result.get(source_key) or {}).get("status") or "success"
        ).strip().casefold()
        in usable_statuses
        for source_key in source_keys
    )
    if result["errors"] and not usable_source_exists:
        # 출처 오류가 예외 대신 정상 반환으로 격리되는 경로에서도 기존 자료를
        # 먼저 정리하거나 순위를 다시 쓰지 않습니다.
        with connect_database(db_path) as con:
            existing_clusters = int(
                con.execute("SELECT COUNT(*) FROM trend_clusters").fetchone()[0] or 0
            )
            existing_items = int(
                con.execute("SELECT COUNT(*) FROM source_items").fetchone()[0] or 0
            )
        result["ranking"] = {
            "status": "skipped_source_failure",
            "items": existing_items,
            "clusters": existing_clusters,
            "reused": True,
            "ai_clustering": {
                "status": "skipped_source_failure",
                "processed_items": 0,
                "remaining_items": 0,
                "error_message": (
                    "모든 사용 가능한 출처 수집이 실패해 기존 통합 주제와 "
                    "보존 데이터를 유지했습니다."
                ),
                "defer_topic_angles": True,
            },
            "batch_log": {},
            "timings": {"analysis": 0.0, "database": 0.0, "total": 0.0},
        }
        result["timings"]["ranking"] = 0.0
        result["total_elapsed_seconds"] = round(perf_counter() - started_at, 2)
        notify(1.0, "출처 수집 실패로 기존 통합 주제 유지")
        return result

    notify(0.86, "7/7 통합 군집 자료 읽는 중")
    ranking_started = perf_counter()
    clustering_lock = acquire_trend_clustering_lock(
        data_directory=Path(db_path).resolve().parent,
        launcher="trend-refresh-ranking",
    )
    if not clustering_lock.acquired or clustering_lock.lock is None:
        with connect_database(db_path) as con:
            existing_clusters = int(
                con.execute("SELECT COUNT(*) FROM trend_clusters").fetchone()[0] or 0
            )
            existing_items = int(
                con.execute("SELECT COUNT(*) FROM source_items").fetchone()[0] or 0
            )
        result["ranking"] = {
            "status": "skipped_overlap",
            "items": existing_items,
            "clusters": existing_clusters,
            "reused": True,
            "ai_clustering": {
                "status": "skipped_overlap",
                "processed_items": 0,
                "remaining_items": 0,
                "error_message": clustering_lock.message,
                "defer_topic_angles": True,
            },
            "batch_log": {},
            "timings": {"analysis": 0.0, "database": 0.0, "total": 0.0},
        }
    else:
        try:
            with connect_database(db_path) as con:
                ranking_preparation = prepare_trend_ranking_rebuild(
                    con,
                    lookback_hours=lookback_hours,
                    source_limits=analysis_source_limits,
                )
            notify(0.91, "7/7 DB 연결 없이 통합 군집·순위 계산 중")
            ranking_calculation = calculate_prepared_trend_rankings(
                ranking_preparation,
                progress_callback=lambda value, message: notify(
                    0.91 + (0.05 * value),
                    message,
                ),
            )
            notify(0.96, "7/7 통합 군집·순위 결과 저장 중")
            with connect_database(db_path) as con:
                result["ranking"] = finalize_prepared_trend_rankings(
                    con,
                    ranking_calculation,
                )
        finally:
            clustering_lock.lock.release()
    record_timing("ranking", ranking_started)

    result["total_elapsed_seconds"] = round(perf_counter() - started_at, 2)
    notify(1.0, "최신 데이터 분석 완료")
    return result
