from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Iterable, Mapping, Sequence

import duckdb


@dataclass(frozen=True)
class BlogChannelRecommendation:
    blog_profile_id: str
    strategy_code: str
    profile_name: str
    score: int
    matched_terms: tuple[str, ...]
    excluded_terms: tuple[str, ...]
    reason: str


def _channel(
    profile_id: str,
    code: str,
    name: str,
    platform: str,
    category: str,
    allowed: tuple[str, ...],
    excluded: tuple[str, ...],
    terms: tuple[str, ...],
    audience: str,
    tone: str,
    length: int,
    title_rules: tuple[str, ...],
    seo: str,
) -> dict[str, Any]:
    urls = {
        "tistory": ("https://www.tistory.com/auth/login", "https://www.tistory.com/", "markdown"),
        "naver_blog": ("https://nid.naver.com/nidlogin.login", "https://blog.naver.com/", "plain_text"),
        "blogger": ("https://www.blogger.com/", "https://www.blogger.com/", "plain_text"),
    }
    login_url, write_url, output_format = urls[platform]
    return {
        "profile_id": profile_id,
        "strategy_code": code,
        "profile_name": name,
        "platform": platform,
        "login_url": login_url,
        "write_url": write_url,
        "output_format": output_format,
        "default_category": category,
        "allowed_categories": allowed,
        "excluded_categories": excluded,
        "routing_terms": terms,
        "target_audience": audience,
        "writing_tone": tone,
        "target_length": length,
        "title_rules": title_rules,
        "seo_strategy": seo,
        "default_image_count": 3,
    }


# 새 기본 전략은 기존 티스토리 중심 전략과 다른 영속 식별자를 사용한다.
# 기존 프로필·전략·초안 배정은 그대로 두고, 사용자가 준비 버튼을 누른 경우에만
# Blogger 3개·네이버 1개 전략을 별도로 추가한다.
MANAGED_BLOG_CHANNELS: tuple[dict[str, Any], ...] = (
    _channel(
        "blog_blogger_life", "blogger_life", "생활자료", "blogger",
        "생활정보",
        ("생활 제도", "지원금", "복지", "건강 상식", "교통", "소비자 정보", "주거", "금융 기초", "취업", "교육", "공공요금"),
        ("Windows 오류", "앱 설치", "전자기기 설정", "스포츠 결과", "방송 화제", "연예인", "사건 속보", "방문 후기"),
        ("지원금", "정책", "제도", "복지", "건강", "교통", "운전", "소비자", "환불", "주거", "전세", "월세", "금융", "대출", "보험", "연금", "세금", "취업", "교육", "신청", "자격", "요금", "정부"),
        "생활 제도와 일상 문제의 정확한 기준·절차를 확인하려는 독자",
        "공식 근거와 기준일을 먼저 제시하는 차분한 설명형", 2600,
        ("제도·서비스명과 독자 행동을 제목 앞부분에 배치", "대상·조건·기준일이 중요하면 제목에 명시", "건강·금융·혜택을 과장하거나 결과를 단정하지 않음"),
        "대상·조건·신청 또는 대응 방법·주의사항을 검색 의도별로 분리",
    ),
    _channel(
        "blog_blogger_tech", "blogger_tech", "IT 사용법", "blogger",
        "IT·AI·기기",
        ("AI", "앱", "PC", "Windows", "스마트폰", "전자기기", "소프트웨어", "인터넷 서비스", "오류 해결"),
        ("지원금", "세금", "연금", "스포츠 결과", "방송 화제", "연예인", "사건 속보", "국내 장소 후기"),
        ("ai", "인공지능", "앱", "pc", "윈도우", "windows", "mac", "스마트폰", "안드로이드", "아이폰", "전자기기", "소프트웨어", "프로그램", "오류", "설치", "설정", "사용법", "브라우저", "인터넷", "웹서비스"),
        "도구를 직접 설치·설정하거나 오류를 해결하려는 일반 사용자",
        "재현 조건과 단계별 해결 순서를 분명히 하는 실무 가이드형", 2400,
        ("제품·서비스·버전·증상을 제목에 구체적으로 표시", "확인하지 않은 원인이나 해결 효과를 단정하지 않음", "사용자가 실행할 행동을 제목과 앞부분에 포함"),
        "증상·원인 후보·해결 단계·복구 방법을 검색 질문 순서로 구성",
    ),
    _channel(
        "blog_naver_local", "naver_local", "네이버 국내 장소·서비스·경험", "naver_blog",
        "국내 장소·서비스",
        ("국내 장소", "국내 서비스", "방문 정보", "개인 경험", "사진 후기", "지역 여행", "매장", "지역 행사"),
        ("해외 서비스", "글로벌 기술", "개발자 문서", "앱 설치 오류", "사건 속보", "국제 이슈", "스포츠 순위"),
        ("국내", "한국", "지역", "장소", "방문", "여행", "맛집", "카페", "매장", "서비스", "후기", "체험", "사진", "이용", "예약", "축제", "전시", "공연장"),
        "국내 장소와 서비스를 실제 이용하기 전에 필요한 정보를 찾는 독자",
        "핵심 이용 정보와 직접 확인한 범위를 구분하는 친절한 안내형", 2200,
        ("지역·장소·서비스명을 제목에 자연스럽게 포함", "직접 경험과 공개 자료를 구분하고 기준 시점을 표시", "방문하지 않았거나 사진이 없으면 체험한 것처럼 쓰지 않음"),
        "위치·이용 조건·준비 사항·직접 확인 범위를 앞부분에 정리",
    ),
    _channel(
        "blog_blogger_current", "blogger_current", "요즘 화제", "blogger",
        "시점 의존 화제",
        ("스포츠", "방송", "콘텐츠", "인물", "사건", "국내외 이슈", "경기 결과", "순위", "일정", "검색 급증"),
        ("지원금 신청", "장기 생활 제도", "앱 설치 오류", "PC 설정", "국내 장소 후기", "개인 체험"),
        ("스포츠", "야구", "축구", "경기", "순위", "일정", "방송", "드라마", "예능", "영화", "콘텐츠", "인물", "화제", "이슈", "사건", "발표", "논란", "급상승", "검색량", "오늘", "이번 주"),
        "검색이 늘어난 주제의 확인된 사실과 배경을 빠르게 파악하려는 독자",
        "속보 경쟁보다 공식 근거·기준 시각·확인 범위를 우선하는 설명형", 2200,
        ("검색되는 인물·경기·작품·사건명을 제목에 명확히 표시", "순위·결과·일정·정책은 기준 날짜나 시각을 포함", "미확인 주장·추측·선정적 표현으로 관심을 과장하지 않음"),
        "무슨 일인지·왜 검색되는지·확인된 내용·추가 확인점을 분리하고 기준 시각을 표시",
    ),
)

MANAGED_STRATEGY_CODES: tuple[str, ...] = tuple(
    str(item["strategy_code"]) for item in MANAGED_BLOG_CHANNELS
)
_PRIORITY = {item["strategy_code"]: i for i, item in enumerate(MANAGED_BLOG_CHANNELS)}
_LEGACY_REUSE = {"naver_local": "blog_naver_default"}


def _table_exists(con: duckdb.DuckDBPyConnection, name: str) -> bool:
    return name in {str(row[0]) for row in con.execute("SHOW TABLES").fetchall()}


def _dump(values: Iterable[str]) -> str:
    return json.dumps([str(value) for value in values], ensure_ascii=False)


def _load(value: Any) -> list[str]:
    try:
        parsed = json.loads(str(value or "[]"))
    except json.JSONDecodeError:
        return []
    return [str(item).strip() for item in parsed if str(item).strip()] if isinstance(parsed, list) else []


def ensure_blog_channel_strategy_schema(con: duckdb.DuckDBPyConnection) -> None:
    """기존 프로필과 초안을 건드리지 않고 P4 전략·배정 테이블만 추가합니다."""
    con.execute("""
        CREATE TABLE IF NOT EXISTS blog_profile_strategies (
            blog_profile_id VARCHAR PRIMARY KEY, strategy_code VARCHAR NOT NULL UNIQUE,
            allowed_categories_json VARCHAR NOT NULL DEFAULT '[]',
            excluded_categories_json VARCHAR NOT NULL DEFAULT '[]',
            routing_terms_json VARCHAR NOT NULL DEFAULT '[]',
            target_audience VARCHAR NOT NULL DEFAULT '', writing_tone VARCHAR NOT NULL DEFAULT '',
            target_length INTEGER, title_rules_json VARCHAR NOT NULL DEFAULT '[]',
            seo_strategy VARCHAR NOT NULL DEFAULT '', default_image_count INTEGER NOT NULL DEFAULT 3,
            created_at TIMESTAMP NOT NULL, updated_at TIMESTAMP NOT NULL
        )
    """)
    con.execute("""
        CREATE TABLE IF NOT EXISTS draft_blog_assignments (
            draft_id VARCHAR PRIMARY KEY, recommended_blog_profile_id VARCHAR NOT NULL,
            selected_blog_profile_id VARCHAR NOT NULL, recommendation_reason VARCHAR NOT NULL,
            matched_terms_json VARCHAR NOT NULL DEFAULT '[]',
            selection_source VARCHAR NOT NULL DEFAULT 'recommended',
            created_at TIMESTAMP NOT NULL, updated_at TIMESTAMP NOT NULL
        )
    """)
    con.execute("CREATE INDEX IF NOT EXISTS idx_blog_profile_strategies_code ON blog_profile_strategies(strategy_code)")
    con.execute("CREATE INDEX IF NOT EXISTS idx_draft_blog_assignments_profile ON draft_blog_assignments(selected_blog_profile_id)")


def install_default_blog_channels(con: duckdb.DuckDBPyConnection) -> dict[str, Any]:
    """비어 있는 기본 전략만 준비하고 기존 프로필·전략은 그대로 보존합니다."""
    if not _table_exists(con, "blog_profiles"):
        raise ValueError("blog_profiles 테이블이 없어 기본 발행 채널을 준비할 수 없습니다.")
    ensure_blog_channel_strategy_schema(con)
    now = datetime.now()
    created_profiles: list[str] = []
    created_strategies: list[str] = []
    profile_map: dict[str, str] = {}

    for channel in MANAGED_BLOG_CHANNELS:
        code = str(channel["strategy_code"])
        existing = con.execute(
            "SELECT blog_profile_id FROM blog_profile_strategies WHERE strategy_code = ?", [code]
        ).fetchone()
        if existing:
            profile_map[code] = str(existing[0])
            continue

        target_id = str(channel["profile_id"])
        if not con.execute("SELECT 1 FROM blog_profiles WHERE blog_profile_id = ?", [target_id]).fetchone():
            legacy_id = _LEGACY_REUSE.get(code)
            reusable = bool(
                legacy_id
                and con.execute("SELECT 1 FROM blog_profiles WHERE blog_profile_id = ?", [legacy_id]).fetchone()
                and not con.execute("SELECT 1 FROM blog_profile_strategies WHERE blog_profile_id = ?", [legacy_id]).fetchone()
            )
            if reusable:
                target_id = str(legacy_id)
            else:
                con.execute("""
                    INSERT INTO blog_profiles(
                        blog_profile_id, profile_name, platform, login_url, write_url,
                        output_format, default_category, default_tags_json,
                        is_default, is_active, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, '[]', FALSE, TRUE, ?, ?)
                """, [
                    target_id, channel["profile_name"], channel["platform"],
                    channel["login_url"], channel["write_url"], channel["output_format"],
                    channel["default_category"], now, now,
                ])
                created_profiles.append(target_id)

        con.execute("""
            INSERT INTO blog_profile_strategies(
                blog_profile_id, strategy_code, allowed_categories_json,
                excluded_categories_json, routing_terms_json, target_audience,
                writing_tone, target_length, title_rules_json, seo_strategy,
                default_image_count, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, [
            target_id, code, _dump(channel["allowed_categories"]),
            _dump(channel["excluded_categories"]), _dump(channel["routing_terms"]),
            channel["target_audience"], channel["writing_tone"], channel["target_length"],
            _dump(channel["title_rules"]), channel["seo_strategy"],
            channel["default_image_count"], now, now,
        ])
        created_strategies.append(code)
        profile_map[code] = target_id

    return {
        "created_profile_ids": created_profiles,
        "created_strategy_codes": created_strategies,
        "profile_ids_by_strategy": profile_map,
        "managed_profile_count": len(MANAGED_BLOG_CHANNELS),
    }


def list_blog_channel_strategies(
    con: duckdb.DuckDBPyConnection, *, active_only: bool = True
) -> list[dict[str, Any]]:
    ensure_blog_channel_strategy_schema(con)
    where = "WHERE p.is_active = TRUE" if active_only else ""
    cursor = con.execute(f"""
        SELECT p.blog_profile_id, p.profile_name, p.platform, p.login_url, p.write_url,
               p.output_format, p.default_category, p.default_tags_json, p.is_default, p.is_active,
               s.strategy_code, s.allowed_categories_json, s.excluded_categories_json,
               s.routing_terms_json, s.target_audience, s.writing_tone, s.target_length,
               s.title_rules_json, s.seo_strategy, s.default_image_count,
               s.created_at AS strategy_created_at, s.updated_at AS strategy_updated_at
        FROM blog_profiles p JOIN blog_profile_strategies s ON s.blog_profile_id = p.blog_profile_id
        {where}
        ORDER BY p.is_default DESC, s.strategy_code, p.profile_name
    """)
    columns = [str(item[0]) for item in cursor.description]
    result: list[dict[str, Any]] = []
    for values in cursor.fetchall():
        row = dict(zip(columns, values, strict=True))
        for output_key, input_key in (
            ("default_tags", "default_tags_json"),
            ("allowed_categories", "allowed_categories_json"),
            ("excluded_categories", "excluded_categories_json"),
            ("routing_terms", "routing_terms_json"),
            ("title_rules", "title_rules_json"),
        ):
            row[output_key] = _load(row.pop(input_key, "[]"))
        result.append(row)
    return result


def list_managed_blog_channel_strategies(
    con: duckdb.DuckDBPyConnection, *, active_only: bool = True
) -> list[dict[str, Any]]:
    """새 기본 구성에 속한 4개 전략만 반환합니다."""
    managed_codes = set(MANAGED_STRATEGY_CODES)
    return [
        strategy
        for strategy in list_blog_channel_strategies(con, active_only=active_only)
        if str(strategy.get("strategy_code") or "") in managed_codes
    ]


def _normalize(value: Any) -> str:
    return " ".join(str(value or "").casefold().split())


def _fields(draft: Mapping[str, Any]) -> tuple[tuple[str, int], ...]:
    tags = draft.get("tags") or []
    tags_text = tags if isinstance(tags, str) else " ".join(str(tag) for tag in tags)
    return (
        (_normalize(draft.get("title")), 6),
        (_normalize(draft.get("category")), 5),
        (_normalize(tags_text), 4),
        (_normalize(draft.get("summary")), 3),
        (_normalize(str(draft.get("body_markdown") or "")[:1200]), 1),
    )


def _score_terms(fields: Sequence[tuple[str, int]], terms: Iterable[str]) -> tuple[int, list[str]]:
    score, matched, seen = 0, [], set()
    for raw in terms:
        term = _normalize(raw)
        if not term or term in seen:
            continue
        seen.add(term)
        weight = max((field_weight for text, field_weight in fields if term in text), default=0)
        if weight:
            score += weight
            matched.append(str(raw))
    return score, matched


def recommend_blog_channel(
    draft: Mapping[str, Any], strategies: Sequence[Mapping[str, Any]]
) -> BlogChannelRecommendation | None:
    """초안의 제목·카테고리·태그·요약·본문에서 전문 발행처 하나를 추천합니다."""
    if not strategies:
        return None
    fields = _fields(draft)
    candidates = []
    for item in strategies:
        route_score, route_matches = _score_terms(fields, item.get("routing_terms") or [])
        category_score, category_matches = _score_terms(fields, item.get("allowed_categories") or [])
        excluded_score, excluded_matches = _score_terms(fields, item.get("excluded_categories") or [])
        matched = list(dict.fromkeys([*route_matches, *category_matches]))
        score = route_score * 2 + category_score - excluded_score * 3
        code = str(item.get("strategy_code") or "")
        candidates.append((score, int(bool(item.get("is_default"))), -_PRIORITY.get(code, 99), item, matched, excluded_matches))

    score, _default, _priority, selected, matched, excluded = max(candidates, key=lambda value: value[:3])
    name = str(selected.get("profile_name") or selected.get("strategy_code") or "")
    reason = (
        f"{', '.join(matched[:5])} 관련 표현이 가장 많이 일치해 ‘{name}’을 추천합니다."
        if matched
        else f"명확한 분류어가 없어 기본 우선순위와 활성 상태를 기준으로 ‘{name}’을 추천합니다."
    )
    if excluded:
        reason += f" 제외 표현({', '.join(excluded[:3])})은 감점에 반영했습니다."
    return BlogChannelRecommendation(
        str(selected.get("blog_profile_id") or ""), str(selected.get("strategy_code") or ""),
        name, int(score), tuple(matched), tuple(excluded), reason,
    )


def save_draft_blog_assignment(
    con: duckdb.DuckDBPyConnection, *, draft_id: str,
    recommendation: BlogChannelRecommendation, selected_blog_profile_id: str | None = None,
) -> str:
    ensure_blog_channel_strategy_schema(con)
    draft_id = str(draft_id or "").strip()
    if not draft_id:
        raise ValueError("배정할 초안 ID가 없습니다.")
    if _table_exists(con, "drafts") and not con.execute("SELECT 1 FROM drafts WHERE draft_id = ?", [draft_id]).fetchone():
        raise ValueError("배정할 초안을 찾을 수 없습니다.")
    recommended_id = str(recommendation.blog_profile_id or "").strip()
    selected_id = str(selected_blog_profile_id or recommended_id).strip()
    active_ids = {str(row[0]) for row in con.execute("""
        SELECT blog_profile_id FROM blog_profiles
        WHERE is_active = TRUE AND blog_profile_id IN (?, ?)
    """, [recommended_id, selected_id]).fetchall()}
    if recommended_id not in active_ids:
        raise ValueError("추천 블로그 프로필이 없거나 비활성 상태입니다.")
    if selected_id not in active_ids:
        raise ValueError("선택한 블로그 프로필이 없거나 비활성 상태입니다.")
    now = datetime.now()
    con.execute("""
        INSERT INTO draft_blog_assignments(
            draft_id, recommended_blog_profile_id, selected_blog_profile_id,
            recommendation_reason, matched_terms_json, selection_source, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(draft_id) DO UPDATE SET
            recommended_blog_profile_id = EXCLUDED.recommended_blog_profile_id,
            selected_blog_profile_id = EXCLUDED.selected_blog_profile_id,
            recommendation_reason = EXCLUDED.recommendation_reason,
            matched_terms_json = EXCLUDED.matched_terms_json,
            selection_source = EXCLUDED.selection_source, updated_at = EXCLUDED.updated_at
    """, [
        draft_id, recommended_id, selected_id, recommendation.reason,
        _dump(recommendation.matched_terms),
        "recommended" if selected_id == recommended_id else "user_override", now, now,
    ])
    return selected_id


def get_draft_blog_assignment(con: duckdb.DuckDBPyConnection, draft_id: str) -> dict[str, Any] | None:
    ensure_blog_channel_strategy_schema(con)
    cursor = con.execute("""
        SELECT a.draft_id, a.recommended_blog_profile_id,
               rp.profile_name AS recommended_profile_name,
               a.selected_blog_profile_id, sp.profile_name AS selected_profile_name,
               a.recommendation_reason, a.matched_terms_json, a.selection_source,
               a.created_at, a.updated_at
        FROM draft_blog_assignments a
        LEFT JOIN blog_profiles rp ON rp.blog_profile_id = a.recommended_blog_profile_id
        LEFT JOIN blog_profiles sp ON sp.blog_profile_id = a.selected_blog_profile_id
        WHERE a.draft_id = ?
    """, [str(draft_id or "").strip()])
    row = cursor.fetchone()
    if row is None:
        return None
    result = dict(zip([str(item[0]) for item in cursor.description], row, strict=True))
    result["matched_terms"] = _load(result.pop("matched_terms_json", "[]"))
    return result
