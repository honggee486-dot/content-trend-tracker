"""트렌드 제목과 URL을 결정론적으로 정규화하는 작은 도구 모음입니다."""

from __future__ import annotations

import html
import re
import unicodedata
from functools import lru_cache
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

_HTML_TAG_PATTERN = re.compile(r"<[^>]+>")
_URL_PATTERN = re.compile(r"https?://\S+|www\.\S+", re.IGNORECASE)
_TOKEN_PATTERN = re.compile(
    r"[0-9A-Za-z가-힣]+(?:[._+-][0-9A-Za-z가-힣]+)*"
)
_SPACE_PATTERN = re.compile(r"\s+")
_ORDINAL_NUMBERED_TOKEN_PATTERN = re.compile(
    r"^제(?P<number>\d+)(?P<unit>주|회|차)$"
)
_DATE_LIKE_TOKEN_PATTERN = re.compile(
    r"^(?:\d{1,4}(?:년|월|일|시|분|초)|"
    r"(?:\d{4}년)?\d{1,2}월\d{1,2}일(?:(?:월|화|수|목|금|토|일)요일)?|"
    r"(?:월|화|수|목|금|토|일)요일)$"
)
_NUMERIC_DATE_TOKEN_PATTERN = re.compile(
    r"^\d{1,4}(?:[./-]\d{1,2}){1,2}$"
)
_SCOPE_PREFIX_PATTERN = re.compile(
    r"^(?:카테고리|수동\s*키워드|자동\s*주제|인기\s*영상|인기\s*동영상|지역\s*인기|"
    r"category|manual\s*keyword|auto\s*topic|popular\s*video|regional\s*popular)"
    r"\s*[:：|/-]\s*",
    re.IGNORECASE,
)
_REGION_SCOPE_PREFIX_PATTERN = re.compile(r"^(?:[A-Z]{2,3})\s*/\s*", re.IGNORECASE)
_FRESH_SUFFIX_PATTERN = re.compile(r"\s*/\s*(?:fresh|latest|recent)\s*$", re.IGNORECASE)

# 이 단어들은 맥락을 설명할 수는 있지만, 단독으로 주제의 정체성이 될 수 없습니다.
GENERIC_IDENTITY_TERMS = {
    "video", "videos", "short", "shorts", "official", "live", "livestream",
    "reaction", "review", "reviews", "news", "update", "horror", "moments",
    "moment", "vtuber", "gameplay", "eating", "mukbang", "food", "viral",
    "satisfying", "asmr", "game", "games", "gaming", "comedy", "challenge",
    "animal", "animals", "fyp", "popular", "trending", "fresh", "collection",
    "scope", "category", "ranking", "rankings", "memes", "meme", "clip", "clips",
    "horrorgame", "shortsfeed", "funny", "humor", "shopping", "page", "pages",
    "section", "home", "all", "array", "deep", "artificial", "intelligence", "ai",
    "영상", "동영상", "쇼츠", "공식", "라이브", "생방송", "반응", "리뷰",
    "뉴스", "업데이트", "공포", "모먼트", "버튜버", "버츄얼", "게임플레이",
    "먹방", "음식", "인기", "급상승", "모음", "클립", "게임", "챌린지",
    "동물", "카테고리", "전체", "최신", "신작", "브이로그", "공포게임",
    "먹방브이로그", "ai동물", "스마트폰", "스트리머", "버츄얼", "유튜버",
    "긴급", "체포", "압류", "사고", "사망", "화재", "폭우", "인공지능",
    "headline", "headlines", "headlinenews", "브리핑", "신문", "안내",
    "정기", "점검", "주요", "오늘의", "내일", "어제", "요일", "운세", "날씨", "명언",
    "간추린", "숏뉴스", "뉴스모음", "뉴스요약",
    "오늘의운세", "오늘운세", "일일운세", "띠별운세", "별자리운세", "주간운세", "월간운세",
    "것", "것들",
    "신문을", "뉴스가", "뉴스는", "뉴스를", "뉴스의",
}

STOPWORDS = {
    "the", "a", "an", "and", "or", "but", "for", "with", "from", "into",
    "over", "under", "this", "that", "these", "those", "your", "our", "their",
    "you", "we", "they", "he", "she", "it", "to", "in", "on", "at", "of",
    "vs", "versus", "how", "why", "what", "who", "when", "new", "best",
    "관련", "대한", "위한", "통해", "이번", "오늘", "최근", "공개", "발표",
    "논란", "이슈", "기자", "단독", "속보", "종합", "정리", "알아보기",
    "방법", "이유", "결과", "현재", "사실", "진짜", "새로운", "국내", "해외",
    "있다", "없다", "된다", "한다", "했다", "하는", "에서", "으로", "에게", "까지",
}

COLLECTION_CATEGORY_TERMS = {
    "pets animals", "travel events", "autos vehicles", "film animation", "people blogs",
    "science technology", "news politics", "howto style", "nonprofits activism",
    "music", "sports", "entertainment", "education",
}

_TRACKING_QUERY_KEYS = {
    "fbclid", "gclid", "dclid", "msclkid", "ref", "referrer", "source", "spm",
    "cmpid", "campaign", "campaignid", "cooper", "plink", "division", "output",
    "tracking", "trackingid", "ncid", "feature", "si",
}

# 형태소 분석기 없이도 제목 비교에 자주 나타나는 조사만 좁게 정리합니다.
# 고유명이나 버전 번호를 훼손하지 않도록 한글 토큰에만 적용합니다.
_KOREAN_PARTICLES = (
    "에서부터", "으로부터", "에게서", "까지", "부터", "에게", "에서",
    "으로", "처럼", "보다", "에는", "에게는", "은", "는", "이", "가",
    "을", "를", "의", "에", "로", "와", "과", "도", "만",
)
_MULTI_KOREAN_PARTICLES = tuple(
    particle for particle in _KOREAN_PARTICLES if len(particle) > 1
)
_SAFE_SINGLE_KOREAN_PARTICLES = (
    "은", "는", "이", "가", "을", "를", "의", "에", "로", "와", "과", "도", "만",
)

_GENERIC_DAILY_DATE_CONTEXT_TERMS = {
    "dailynews", "headline", "headlines", "headlinenews", "fortune", "운세",
}
_DAILY_DATE_MARKERS = {"daily", "오늘의"}
_DAILY_DIGEST_TERMS = {
    "news", "briefing", "fortune", "뉴스", "브리핑", "신문", "운세",
    "숏뉴스", "뉴스모음", "뉴스요약",
}
_GENERIC_MAINTENANCE_DATE_CONTEXT_TERMS = {"maintenance", "점검", "유지보수"}


def _identity_token(value: str) -> str:
    token = value
    is_hangul = bool(token) and all("가" <= char <= "힣" for char in token)
    particles = _MULTI_KOREAN_PARTICLES if is_hangul else _KOREAN_PARTICLES
    for particle in particles:
        if token.endswith(particle) and len(token) - len(particle) >= 2:
            return token[: -len(particle)]
    # 한글 한 글자 조사는 짧은 일반어를 훼손하지 않도록
    # 세 글자 이상의 이름·기업명 뒤에서만 좁게 제거합니다.
    if is_hangul:
        for particle in _SAFE_SINGLE_KOREAN_PARTICLES:
            if token.endswith(particle) and len(token) - 1 >= 3:
                return token[:-1]
    return token


def clean_text(value: str) -> str:
    """HTML, URL, 반복 공백을 정리하되 사람에게 보여줄 문구는 최대한 보존합니다."""
    text = unicodedata.normalize("NFKC", html.unescape(str(value or "")))
    text = _HTML_TAG_PATTERN.sub(" ", text)
    text = _URL_PATTERN.sub(" ", text)
    text = text.replace("\u200b", " ").replace("#", " ")
    text = re.sub(r"([!?~])\1+", r"\1", text)
    return _SPACE_PATTERN.sub(" ", text).strip(" \t\r\n-|·:;,.!~[](){}")


def strip_collection_scope(value: str) -> str:
    """수집 범위 접두사와 범주명을 제목 후보에서 제거합니다."""
    clean = clean_text(value)
    had_prefix = bool(_SCOPE_PREFIX_PATTERN.match(clean))
    clean = _SCOPE_PREFIX_PATTERN.sub("", clean)
    if had_prefix:
        clean = _REGION_SCOPE_PREFIX_PATTERN.sub("", clean)
    clean = _FRESH_SUFFIX_PATTERN.sub("", clean).strip(" /|-:")
    normalized = normalize_title(clean)
    if normalized in COLLECTION_CATEGORY_TERMS:
        return ""
    if had_prefix and not _identity_tokens_from_clean(clean):
        return ""
    return clean


def normalize_title(value: str) -> str:
    """문장 비교용 제목을 소문자 토큰 문자열로 변환합니다."""
    return " ".join(tokenize(value))


def tokenize(value: str) -> list[str]:
    tokens: list[str] = []
    for raw in _TOKEN_PATTERN.findall(clean_text(value).casefold()):
        token = raw.strip("._+-")
        if not token:
            continue
        ordinal_match = _ORDINAL_NUMBERED_TOKEN_PATTERN.fullmatch(token)
        if ordinal_match:
            token = (
                f"{ordinal_match.group('number')}{ordinal_match.group('unit')}"
            )
        # grannygame처럼 붙어 들어온 영문 게임명을 비교할 수 있게 분리합니다.
        if token.isascii() and token.endswith("games") and len(token) > 7:
            tokens.extend([token[:-5], "games"])
        elif token.isascii() and token.endswith("game") and len(token) > 6:
            tokens.extend([token[:-4], "game"])
        else:
            tokens.append(token)
    return tokens


def identity_tokens(value: str) -> set[str]:
    """일반 표현을 제외하고 실제 대상 식별에 쓸 수 있는 토큰만 반환합니다."""
    return _identity_tokens_from_clean(strip_collection_scope(value))


def _identity_tokens_from_clean(value: str) -> set[str]:
    return set(_cached_identity_tokens(value))


def _uses_generic_date_context(tokens: list[str]) -> bool:
    token_set = set(tokens)
    if token_set & _GENERIC_DAILY_DATE_CONTEXT_TERMS:
        return True
    if token_set & _DAILY_DATE_MARKERS and token_set & _DAILY_DIGEST_TERMS:
        return True

    generic_non_date_only = all(
        _DATE_LIKE_TOKEN_PATTERN.fullmatch(raw_token)
        or _NUMERIC_DATE_TOKEN_PATTERN.fullmatch(raw_token)
        or len(_identity_token(raw_token)) < 2
        or _identity_token(raw_token) in STOPWORDS
        or _identity_token(raw_token) in GENERIC_IDENTITY_TERMS
        or _identity_token(raw_token).isdigit()
        for raw_token in tokens
    )
    if generic_non_date_only and token_set & _DAILY_DIGEST_TERMS:
        return True
    if generic_non_date_only and token_set & _GENERIC_MAINTENANCE_DATE_CONTEXT_TERMS:
        return True
    return generic_non_date_only and "정기" in token_set and bool(
        token_set & {"안내", "공지", "update", "업데이트"}
    )


@lru_cache(maxsize=50000)
def _cached_identity_tokens(value: str) -> frozenset[str]:
    result: set[str] = set()
    raw_tokens = tokenize(value)
    suppress_generic_dates = _uses_generic_date_context(raw_tokens)
    for raw_token in raw_tokens:
        if suppress_generic_dates and (
            _DATE_LIKE_TOKEN_PATTERN.fullmatch(raw_token)
            or _NUMERIC_DATE_TOKEN_PATTERN.fullmatch(raw_token)
        ):
            continue
        token = _identity_token(raw_token)
        if token in STOPWORDS or token in GENERIC_IDENTITY_TERMS:
            continue
        if token.isdigit() and (suppress_generic_dates or len(token) < 2):
            continue
        if len(token) < 2:
            continue
        result.add(token)
    return frozenset(result)


def compact_title(value: str) -> str:
    return "".join(tokenize(strip_collection_scope(value)))


def is_specific_topic(value: str) -> bool:
    clean = strip_collection_scope(value)
    if not clean:
        return False
    identities = identity_tokens(clean)
    if len(identities) >= 2:
        return True
    if len(identities) != 1:
        return False
    token = next(iter(identities))
    # 버전·모델명과 고유명사는 한 토큰이어도 주제 식별값이 될 수 있습니다.
    return any(char.isdigit() for char in token) or len(token) >= 2


def normalize_url(value: str) -> str:
    """추적 파라미터를 제거해 같은 원문의 URL 변형을 하나로 비교합니다."""
    raw = html.unescape(str(value or "")).strip()
    if not raw:
        return ""
    if raw.startswith("www."):
        raw = "https://" + raw
    try:
        parts = urlsplit(raw)
    except ValueError:
        return raw
    if not parts.netloc:
        return raw

    host = (parts.hostname or "").casefold().removeprefix("www.")
    if not host:
        return raw
    port = parts.port
    netloc = host if port in {None, 80, 443} else f"{host}:{port}"
    path = re.sub(r"/{2,}", "/", parts.path or "/")
    if path != "/":
        path = path.rstrip("/")

    query_pairs = []
    for key, val in parse_qsl(parts.query, keep_blank_values=True):
        folded = key.casefold()
        if folded.startswith("utm_") or folded in _TRACKING_QUERY_KEYS:
            continue
        query_pairs.append((key, val))

    if host in {"youtu.be", "youtube.com", "m.youtube.com"}:
        video_id = ""
        if host == "youtu.be":
            path_parts = [p for p in path.strip("/").split("/") if p]
            if path_parts:
                video_id = path_parts[0]
        else:
            video_id = next((val for key, val in query_pairs if key == "v" and val), "")
            if not video_id:
                path_parts = [p for p in path.strip("/").split("/") if p]
                if path_parts and path_parts[0] in {"shorts", "live", "embed", "v"} and len(path_parts) > 1:
                    video_id = path_parts[1]
        if video_id:
            host, path, query_pairs = "youtube.com", "/watch", [("v", video_id)]

    query = urlencode(sorted(query_pairs), doseq=True)
    return urlunsplit(("https", netloc if host != "youtube.com" else host, path, query, ""))


def source_domain(value: str) -> str:
    normalized = normalize_url(value)
    if not normalized:
        return ""
    try:
        return (urlsplit(normalized).hostname or "").removeprefix("www.")
    except ValueError:
        return ""
