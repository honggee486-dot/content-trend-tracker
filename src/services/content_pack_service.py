from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Iterable
from uuid import uuid4

import duckdb

from src.services.reference_service import list_topic_references
from src.services.topic_angle_demand_contract import format_direction_for_request
from src.services.topic_service import add_manual_topic, get_topic, get_topic_sources

DEFAULT_TITLE_RULES = [
    "본문에 없는 내용을 제목에 넣지 않는다.",
    "과장·공포·확정 표현을 사용하지 않는다.",
    "검색 의도가 분명하게 드러나는 정확하고 고유한 한국어 제목 하나만 출력한다.",
]

DEFAULT_OUTLINE = [
    "도입: 독자가 이 주제를 찾아본 이유와 핵심 질문을 짧게 짚는다.",
    "핵심 설명: 검색 의도에 가장 직접적인 답을 먼저 설명한다.",
    "세부 정보: 근거와 예시를 구분해 구체적으로 정리한다.",
    "주의사항: 오해하기 쉬운 점과 사실 확인이 필요한 부분을 밝힌다.",
    "정리: 핵심을 기계적으로 반복하지 않고 독자가 다음 행동을 판단할 수 있게 마무리한다.",
]

DEFAULT_FORBIDDEN = ["무조건", "100%", "확실히", "반드시 성공", "전문가가 보장"]
DEFAULT_FACT_CHECK_ITEMS = [
    "구체적인 수치와 조사 시점",
    "정책·가격·법률처럼 변할 수 있는 내용",
    "인물의 현재 직책과 날짜",
]
DEFAULT_CONTENT_ANGLE = (
    "독자가 궁금해하는 핵심을 먼저 설명하고, 확인할 사실과 실용 정보를 구분해 정리"
)

# `현재`·`오늘`·`최신` 같은 일반 표현은 웹 검색 필요 신호로만 사용합니다.
# 순위·경기 결과·일정·환율·가격·날씨처럼 현재값 자체가 글의 핵심인 경우에만
# 사용자가 선택한 사실 참고 자료를 최소 1개 요구합니다.
ALWAYS_FRESHNESS_SENSITIVE_TERMS = (
    "현재", "오늘", "실시간", "최신", "환율", "주가", "시세", "날씨",
    "신청 마감", "당첨 결과", "재고 현황",
)
FACTUAL_REFERENCE_REQUIRED_TERMS = (
    "환율", "주가", "시세", "가격", "날씨", "기온", "강수", "예보",
    "신청 마감", "접수 마감", "당첨 결과", "재고 현황",
    "현재 직책", "현직", "현행 정책", "현재 정책",
)
SPORTS_FRESHNESS_SENSITIVE_TERMS = (
    "중간 순위", "스포츠 순위", "경기 결과", "경기 일정", "최종 결과",
    "승률", "경기 차", "라인업", "부상 명단", "순위",
)
SPORTS_CONTEXT_TERMS = (
    "프로야구", "야구", "kbo", "축구", "농구", "배구", "스포츠", "리그",
    "구단", "팀", "시즌", "경기",
)


def assess_content_pack_readiness(
    topic: dict[str, Any] | str,
    factual_references: list[dict[str, Any]] | None = None,
    *,
    purpose: str = "",
    angle: str = "",
) -> dict[str, Any]:
    """Assess web-research needs and minimum evidence for current-fact topics.

    Trend signals explain *why people care*. They do not establish current standings,
    prices, schedules, weather, policy details, or office-holder information.
    """
    if isinstance(topic, dict):
        title = str(topic.get("title") or "")
        summary = str(topic.get("summary") or "")
        memo = str(topic.get("memo") or "")
    else:
        title = str(topic or "")
        summary = ""
        memo = ""
    combined = " ".join(part for part in (title, summary, memo, purpose, angle) if part).casefold()
    matched_terms = [
        term
        for term in ALWAYS_FRESHNESS_SENSITIVE_TERMS
        if term.casefold() in combined
    ]
    blocking_terms = [
        term
        for term in FACTUAL_REFERENCE_REQUIRED_TERMS
        if term.casefold() in combined
    ]
    has_sports_context = any(
        term.casefold() in combined for term in SPORTS_CONTEXT_TERMS
    )
    if has_sports_context:
        sports_matches = [
            term
            for term in SPORTS_FRESHNESS_SENSITIVE_TERMS
            if term.casefold() in combined
        ]
        matched_terms.extend(sports_matches)
        blocking_terms.extend(sports_matches)
    matched_terms = list(dict.fromkeys(matched_terms))
    blocking_terms = list(dict.fromkeys(blocking_terms))
    references = list(factual_references or [])
    memo_reference_count = sum(
        1
        for reference in references
        if len(str(reference.get("memo") or "").strip()) >= 10
    )
    is_sensitive = bool(matched_terms)
    requires_factual_reference = bool(blocking_terms)
    is_blocked = requires_factual_reference and not references
    return {
        "is_freshness_sensitive": is_sensitive,
        "matched_terms": matched_terms,
        "blocking_terms": blocking_terms,
        "factual_reference_count": len(references),
        "memo_reference_count": memo_reference_count,
        "requires_factual_reference": requires_factual_reference,
        "is_blocked": is_blocked,
        "requires_web_research": is_sensitive or not references,
        "message": (
            "현재 순위·경기 결과·일정·환율·가격·날씨처럼 시점에 따라 달라지는 주제는 "
            "사실 참고 자료를 1개 이상 선택해야 합니다. 주제·트렌드에서 공식 자료를 등록하고 "
            "기준일과 핵심 수치를 활용 메모에 적은 뒤 다시 생성하세요."
            if is_blocked
            else (
                "현재값 확인이 필요한 주제이므로 AI 요청서에 웹 검색과 공식 출처 확인 규칙을 자동으로 포함합니다."
                if is_sensitive
                else ""
            )
        ),
    }


OUTPUT_SCHEMA_EXAMPLE = {
    "schema_version": "2.1",
    "title": "검색 의도와 본문을 정확히 반영한 글 제목",
    "summary": "두세 문장 요약",
    "category": "카테고리",
    "tags": ["태그1", "태그2"],
    "seo": {
        "primary_keyword": "대표 검색어",
        "secondary_keywords": ["연관 검색어1", "연관 검색어2"],
        "search_intent": "독자가 이 검색어로 해결하려는 질문 또는 목적",
        "meta_description": "검색 결과에서 내용을 정확히 설명하는 자연스러운 요약문",
    },
    "blocks": [
        {
            "type": "paragraph",
            "text": "독자가 주제를 이해하는 데 필요한 도입 문단",
        },
        {
            "type": "heading",
            "level": 2,
            "text": "첫 번째 핵심 내용",
        },
        {
            "type": "bullet_list",
            "items": ["핵심 항목 1", "핵심 항목 2"],
        },
        {
            "type": "image",
            "position": "첫 번째 핵심 내용 뒤",
            "purpose": "본문 이해를 돕는 설명 이미지",
            "free_image": {
                "status": "verified_free",
                "search_query": "무료 이미지 검색에 사용할 구체적인 검색어",
                "page_url": "https://example.com/specific-free-asset-page",
                "provider": "이미지 제공 사이트",
                "creator": "제작자 또는 촬영자",
                "license_name": "확인된 무료 이용 라이선스 또는 이용 조건명",
                "license_url": "https://example.com/official-license-terms",
                "attribution": "필요한 경우 표시할 출처 문구, 불필요하면 빈 문자열",
                "checked_at": "YYYY-MM-DD",
                "commercial_use_allowed": True,
                "payment_required": False,
                "premium_or_subscription_required": False,
                "editorial_only": False,
                "verification_note": "개별 자산 페이지와 별도 공식 라이선스 페이지를 각각 확인한 근거",
            },
            "prompt": "무료 이미지가 없거나 사용하지 않을 때 바로 사용할 이미지 생성 프롬프트",
            "aspect_ratio": "16:9",
            "caption": "이미지 아래에 표시할 캡션",
            "alt_text": "이미지를 볼 수 없을 때도 내용을 이해할 수 있는 자연스러운 대체 설명",
        },
        {
            "type": "quote",
            "text": "강조할 핵심 문장",
        },
    ],
    "fact_checks": [
        {
            "claim": "확인이 필요한 주장",
            "status": "needs_verification",
            "reason": "확인이 필요한 이유",
            "source_ids": ["S1"],
        }
    ],
    "sources": [
        {
            "id": "S1",
            "title": "참고 자료 제목",
            "publisher": "출처명",
            "url": "https://example.com",
            "published_at": "2026-07-01",
        }
    ],
}


def _clean_lines(values: str | Iterable[str]) -> list[str]:
    if isinstance(values, str):
        raw = values.replace("\r", "").split("\n")
    else:
        raw = list(values)
    return [str(item).strip(" -\t") for item in raw if str(item).strip(" -\t")]


def _load_json_list(value: Any) -> list[str]:
    try:
        parsed = json.loads(str(value or "[]"))
    except (TypeError, json.JSONDecodeError):
        parsed = []
    return _clean_lines(parsed) if isinstance(parsed, list) else []


def _merge_unique_lines(*groups: Iterable[str]) -> list[str]:
    merged: list[str] = []
    seen: set[str] = set()
    for group in groups:
        for item in _clean_lines(group):
            key = item.casefold()
            if key not in seen:
                seen.add(key)
                merged.append(item)
    return merged


def link_topic_to_trend_cluster(
    con: duckdb.DuckDBPyConnection,
    *,
    topic_id: str,
    cluster_id: str,
) -> None:
    """Persist the originating trend cluster without overwriting user preferences."""
    now = datetime.now()
    existing = con.execute(
        "SELECT topic_id FROM topic_content_preferences WHERE topic_id = ?",
        [topic_id],
    ).fetchone()
    if existing is None:
        con.execute(
            """
            INSERT INTO topic_content_preferences(
                topic_id, source_cluster_id, audience, purpose, angle, category,
                target_length, title_rules_json, outline_json,
                forbidden_expressions_json, fact_check_items_json,
                created_at, updated_at
            ) VALUES (?, ?, '', '', '', '', NULL, '[]', '[]', '[]', '[]', ?, ?)
            """,
            [topic_id, cluster_id, now, now],
        )
    else:
        con.execute(
            """
            UPDATE topic_content_preferences
            SET source_cluster_id = ?, updated_at = ?
            WHERE topic_id = ?
            """,
            [cluster_id, now, topic_id],
        )


def _infer_topic_cluster_id(
    con: duckdb.DuckDBPyConnection,
    topic_id: str,
) -> str:
    row = con.execute(
        """
        SELECT tci.cluster_id, COUNT(*) AS overlap_count
        FROM topic_source_links tsl
        JOIN trend_cluster_items tci ON tci.source_item_id = tsl.source_item_id
        WHERE tsl.topic_id = ?
        GROUP BY tci.cluster_id
        ORDER BY overlap_count DESC, tci.cluster_id
        LIMIT 1
        """,
        [topic_id],
    ).fetchone()
    return str(row[0]) if row else ""


def get_topic_content_defaults(
    con: duckdb.DuckDBPyConnection,
    *,
    topic_id: str,
    default_audience: str,
    default_purpose: str,
) -> dict[str, Any]:
    """Merge saved user values, the initial Gemini plan, and global defaults."""
    pref_row = con.execute(
        """
        SELECT source_cluster_id, audience, purpose, angle, category,
               target_length, title_rules_json, outline_json,
               forbidden_expressions_json, fact_check_items_json
        FROM topic_content_preferences
        WHERE topic_id = ?
        """,
        [topic_id],
    ).fetchone()
    pref_columns = [
        "source_cluster_id",
        "audience",
        "purpose",
        "angle",
        "category",
        "target_length",
        "title_rules_json",
        "outline_json",
        "forbidden_expressions_json",
        "fact_check_items_json",
    ]
    preferences = dict(zip(pref_columns, pref_row)) if pref_row else {}
    cluster_id = str(preferences.get("source_cluster_id") or "").strip()
    if not cluster_id:
        cluster_id = _infer_topic_cluster_id(con, topic_id)

    plan: dict[str, Any] = {}
    verification_points: list[str] = []
    first_angle = ""
    if cluster_id:
        profile_row = con.execute(
            """
            SELECT content_plan_json, verification_points_json
            FROM trend_cluster_ai_profiles
            WHERE cluster_id = ?
            """,
            [cluster_id],
        ).fetchone()
        if profile_row:
            try:
                parsed_plan = json.loads(str(profile_row[0] or "{}"))
            except (TypeError, json.JSONDecodeError):
                parsed_plan = {}
            plan = parsed_plan if isinstance(parsed_plan, dict) else {}
            verification_points = _load_json_list(profile_row[1])
        angle_row = con.execute(
            """
            SELECT angle_text, search_queries_json, search_intent, reader_question,
                   demand_evidence_json
            FROM trend_cluster_ai_angles
            WHERE cluster_id = ?
            ORDER BY angle_order
            LIMIT 1
            """,
            [cluster_id],
        ).fetchone()
        if angle_row:
            first_angle = format_direction_for_request(
                {
                    "angle_text": angle_row[0],
                    "search_queries": _load_json_list(angle_row[1]),
                    "search_intent": angle_row[2],
                    "reader_question": angle_row[3],
                    "demand_evidence": _load_json_list(angle_row[4]),
                }
            )

    topic_row = con.execute(
        "SELECT category FROM topics WHERE topic_id = ?",
        [topic_id],
    ).fetchone()
    topic_category = str(topic_row[0] or "").strip() if topic_row else ""

    saved_title_rules = _load_json_list(preferences.get("title_rules_json"))
    saved_outline = _load_json_list(preferences.get("outline_json"))
    saved_forbidden = _load_json_list(
        preferences.get("forbidden_expressions_json")
    )
    saved_fact_checks = _load_json_list(preferences.get("fact_check_items_json"))
    plan_title_rules = _clean_lines(plan.get("title_rules") or [])
    plan_outline = _clean_lines(plan.get("outline") or [])
    plan_forbidden = _clean_lines(plan.get("forbidden_expressions") or [])
    timeliness = plan.get("timeliness") if isinstance(plan.get("timeliness"), dict) else {}
    evidence_plan = (
        plan.get("evidence_plan")
        if isinstance(plan.get("evidence_plan"), dict)
        else {}
    )
    evidence_gaps = _clean_lines(evidence_plan.get("evidence_gaps") or [])
    primary_direction_reason = str(
        plan.get("primary_direction_reason") or ""
    ).strip()

    saved_value_exists = any(
        [
            str(preferences.get("audience") or "").strip(),
            str(preferences.get("purpose") or "").strip(),
            str(preferences.get("angle") or "").strip(),
            str(preferences.get("category") or "").strip(),
            preferences.get("target_length"),
            saved_title_rules,
            saved_outline,
            saved_forbidden,
            saved_fact_checks,
        ]
    )
    source = "saved" if saved_value_exists else ("ai" if plan else "global")
    target_length_raw = preferences.get("target_length") or plan.get("target_length") or 2500
    try:
        target_length = int(target_length_raw)
    except (TypeError, ValueError):
        target_length = 2500
    target_length = min(max(target_length, 500), 10000)

    return {
        "source": source,
        "source_cluster_id": cluster_id,
        "audience": str(preferences.get("audience") or plan.get("audience") or default_audience).strip(),
        "purpose": str(preferences.get("purpose") or plan.get("purpose") or default_purpose).strip(),
        "angle": str(preferences.get("angle") or first_angle or DEFAULT_CONTENT_ANGLE).strip(),
        "category": str(
            preferences.get("category")
            or plan.get("category")
            or topic_category
        ).strip(),
        "target_length": target_length,
        "title_rules": (
            saved_title_rules
            if saved_title_rules
            else _merge_unique_lines(DEFAULT_TITLE_RULES, plan_title_rules)
        ),
        "outline": saved_outline or plan_outline or list(DEFAULT_OUTLINE),
        "forbidden_expressions": (
            saved_forbidden
            if saved_forbidden
            else _merge_unique_lines(DEFAULT_FORBIDDEN, plan_forbidden)
        ),
        "fact_check_items": (
            saved_fact_checks
            or _merge_unique_lines(verification_points, evidence_gaps)
            or list(DEFAULT_FACT_CHECK_ITEMS)
        ),
        "timeliness": timeliness,
        "evidence_plan": evidence_plan,
        "primary_direction_reason": primary_direction_reason,
    }


def save_topic_content_preferences(
    con: duckdb.DuckDBPyConnection,
    *,
    topic_id: str,
    audience: str,
    purpose: str,
    angle: str,
    category: str,
    target_length: int,
    title_rules: str | Iterable[str],
    outline: str | Iterable[str],
    forbidden_expressions: str | Iterable[str],
    fact_check_items: str | Iterable[str],
) -> None:
    existing = con.execute(
        """
        SELECT source_cluster_id, created_at
        FROM topic_content_preferences
        WHERE topic_id = ?
        """,
        [topic_id],
    ).fetchone()
    source_cluster_id = str(existing[0] or "").strip() if existing else ""
    if not source_cluster_id:
        source_cluster_id = _infer_topic_cluster_id(con, topic_id)
    created_at = existing[1] if existing else datetime.now()
    now = datetime.now()
    con.execute(
        """
        INSERT INTO topic_content_preferences(
            topic_id, source_cluster_id, audience, purpose, angle, category,
            target_length, title_rules_json, outline_json,
            forbidden_expressions_json, fact_check_items_json,
            created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(topic_id) DO UPDATE SET
            source_cluster_id = EXCLUDED.source_cluster_id,
            audience = EXCLUDED.audience,
            purpose = EXCLUDED.purpose,
            angle = EXCLUDED.angle,
            category = EXCLUDED.category,
            target_length = EXCLUDED.target_length,
            title_rules_json = EXCLUDED.title_rules_json,
            outline_json = EXCLUDED.outline_json,
            forbidden_expressions_json = EXCLUDED.forbidden_expressions_json,
            fact_check_items_json = EXCLUDED.fact_check_items_json,
            updated_at = EXCLUDED.updated_at
        """,
        [
            topic_id,
            source_cluster_id,
            audience.strip(),
            purpose.strip(),
            angle.strip(),
            category.strip(),
            int(target_length),
            json.dumps(_clean_lines(title_rules), ensure_ascii=False),
            json.dumps(_clean_lines(outline), ensure_ascii=False),
            json.dumps(_clean_lines(forbidden_expressions), ensure_ascii=False),
            json.dumps(_clean_lines(fact_check_items), ensure_ascii=False),
            created_at,
            now,
        ],
    )


def _number_text(value: Any) -> str:
    if value is None or value == "":
        return "-"
    try:
        number = float(value)
    except (TypeError, ValueError):
        return str(value)
    if number.is_integer():
        return f"{int(number):,}"
    return f"{number:,.2f}".rstrip("0").rstrip(".")


def _source_reference_rows(
    sources: list[dict[str, Any]],
    *,
    start_index: int = 1,
) -> list[dict[str, Any]]:
    rows = []
    for index, source in enumerate(sources, start=start_index):
        metadata = source.get("metadata") or {}
        rows.append(
            {
                "id": f"S{index}",
                "reference_kind": "trend_signal",
                "reference_kind_label": "트렌드 신호",
                "source_item_id": source.get("source_item_id"),
                "topic_reference_id": None,
                "signal_type": source.get("signal_type") or "other",
                "signal_type_label": source.get("signal_type_label") or "기타 신호",
                "reference_type": "",
                "reference_type_label": "",
                "title": source.get("item_title") or source.get("raw_title") or "참고 신호",
                "topic_title": source.get("raw_title") or "",
                "publisher": source.get("source_name") or source.get("source_type") or "출처",
                "url": source.get("source_url") or "",
                "published_at": str(source.get("published_at") or ""),
                "observed_at": str(source.get("observed_at") or ""),
                "signal_value": source.get("signal_value"),
                "keyword": source.get("keyword") or metadata.get("keyword") or "",
                "view_count": source.get("view_count"),
                "view_delta": source.get("view_delta"),
                "views_per_hour": source.get("views_per_hour"),
                "topic_score": source.get("topic_score"),
                "memo": "",
                "metadata": metadata,
            }
        )
    return rows


def _factual_reference_rows(
    references: list[dict[str, Any]],
    *,
    start_index: int = 1,
) -> list[dict[str, Any]]:
    rows = []
    for index, reference in enumerate(references, start=start_index):
        rows.append(
            {
                "id": f"S{index}",
                "reference_kind": "factual_reference",
                "reference_kind_label": "사실 참고 자료",
                "source_item_id": None,
                "topic_reference_id": reference.get("reference_id"),
                "signal_type": "",
                "signal_type_label": "",
                "reference_type": reference.get("reference_type") or "user_reference",
                "reference_type_label": reference.get("reference_type_label") or "사용자 직접 자료",
                "title": reference.get("title") or "참고 자료",
                "topic_title": "",
                "publisher": reference.get("publisher") or "출처 미입력",
                "url": reference.get("url") or "",
                "published_at": str(reference.get("published_at") or ""),
                "observed_at": "",
                "signal_value": None,
                "keyword": "",
                "view_count": None,
                "view_delta": None,
                "views_per_hour": None,
                "topic_score": None,
                "memo": reference.get("memo") or "",
                "metadata": {},
            }
        )
    return rows


def build_trend_evidence_summary(sources: list[dict[str, Any]]) -> list[str]:
    if not sources:
        return ["선택한 외부 트렌드 근거가 없습니다."]

    counts: dict[str, int] = {}
    for source in sources:
        label = str(source.get("signal_type_label") or "기타 신호")
        counts[label] = counts.get(label, 0) + 1

    count_text = ", ".join(f"{label} {count}개" for label, count in counts.items())
    lines = [f"선택한 근거는 총 {len(sources)}개이며, {count_text}입니다."]

    numeric_fields = [
        ("topic_score", "주제 점수 최고값"),
        ("view_count", "관련 영상 조회수 최고값"),
        ("view_delta", "조회수 증가량 최고값"),
        ("views_per_hour", "시간당 조회수 지표 최고값"),
    ]
    for field, label in numeric_fields:
        values = []
        for source in sources:
            value = source.get(field)
            try:
                if value is not None:
                    values.append(float(value))
            except (TypeError, ValueError):
                continue
        if values:
            lines.append(f"{label}: {_number_text(max(values))}")

    lines.append(
        "YouTube 수치와 영상은 관심 증가를 보여주는 트렌드 신호이며, 본문의 사실 근거는 별도로 확인해야 합니다."
    )
    return lines


def build_content_pack(
    topic: dict[str, Any],
    sources: list[dict[str, Any]],
    *,
    factual_references: list[dict[str, Any]] | None = None,
    audience: str,
    purpose: str,
    angle: str,
    category: str,
    target_length: int,
    title_rules: str | Iterable[str],
    outline: str | Iterable[str],
    forbidden_expressions: str | Iterable[str],
    fact_check_items: str | Iterable[str],
) -> dict[str, Any]:
    factual_references = factual_references or []
    research_requirements = assess_content_pack_readiness(
        topic,
        factual_references,
        purpose=purpose,
        angle=angle,
    )
    if research_requirements["is_blocked"]:
        raise ValueError(str(research_requirements["message"]))
    trend_references = _source_reference_rows(sources)
    fact_references = _factual_reference_rows(
        factual_references,
        start_index=len(trend_references) + 1,
    )
    references = [*trend_references, *fact_references]
    evidence_summary = build_trend_evidence_summary(sources)
    title_rule_list = _clean_lines(title_rules) or DEFAULT_TITLE_RULES
    outline_list = _clean_lines(outline) or DEFAULT_OUTLINE
    forbidden_list = _clean_lines(forbidden_expressions) or DEFAULT_FORBIDDEN
    fact_check_list = _clean_lines(fact_check_items)

    trend_lines = []
    for ref in trend_references:
        metrics = [
            f"신호값 {_number_text(ref['signal_value'])}",
            f"조회수 {_number_text(ref['view_count'])}",
            f"증가량 {_number_text(ref['view_delta'])}",
            f"시간당 {_number_text(ref['views_per_hour'])}",
            f"주제점수 {_number_text(ref['topic_score'])}",
        ]
        trend_lines.append(
            f"- [{ref['id']}] [{ref['signal_type_label']}] {ref['title']} / "
            f"{ref['publisher']} / {' · '.join(metrics)} / "
            f"관찰: {ref['observed_at'] or '미상'} / URL: {ref['url'] or '없음'}"
        )
    if not trend_lines:
        trend_lines = ["- 선택한 트렌드 신호 없음"]

    factual_lines = []
    for ref in fact_references:
        details = [
            f"기관·출처: {ref['publisher']}",
            f"게시일: {ref['published_at'] or '미입력'}",
            f"URL: {ref['url']}",
        ]
        if ref["memo"]:
            details.append(f"메모: {ref['memo']}")
        factual_lines.append(
            f"- [{ref['id']}] [{ref['reference_type_label']}] {ref['title']} / "
            + " / ".join(details)
        )
    if not factual_lines:
        factual_lines = ["- 선택한 사실 참고 자료 없음: AI가 웹 검색으로 최신 공식 자료를 조사하도록 요청"]

    pack_markdown = f"""# 블로그 글 제작 자료팩

## 글 주제
- 주제: {topic.get('title', '')}
- 설명: {topic.get('summary') or '아직 별도 설명 없음'}
- 카테고리: {category or topic.get('category') or '미분류'}
- 사용자 메모: {topic.get('memo') or '없음'}

## 글 작성 목표
- 독자 대상: {audience}
- 글 목적: {purpose}
- 글의 관점: {angle}
- 목표 분량: 약 {int(target_length)}자

## 선택한 트렌드 근거 요약
{chr(10).join(f'- {item}' for item in evidence_summary)}

## 선택한 트렌드 신호
{chr(10).join(trend_lines)}

## 선택한 사실 참고 자료
{chr(10).join(factual_lines)}

## 최신성·조사 요구
- 시점 의존 주제: {"예" if research_requirements["is_freshness_sensitive"] else "아니오"}
- 감지 표현: {", ".join(research_requirements["matched_terms"]) if research_requirements["matched_terms"] else "없음"}
- 사용자가 추가한 참고 자료: {research_requirements["factual_reference_count"]}개
- 웹 검색 필요: {"예" if research_requirements.get("requires_web_research") else "아니오"}

## 자료 사용 원칙
- 트렌드 신호는 사람들이 관심을 보이는 이유와 글감 우선순위 판단에 사용한다.
- 구체적인 주장, 수치, 날짜, 정책, 현재 상태는 AI가 웹 검색으로 최신 자료를 확인한다.
- 공식 기관·기업·리그·정부 등 1차 출처를 우선하고 게시일과 적용 기준일을 확인한다.
- 사용자가 추가한 참고 자료가 있으면 웹 검색 결과와 함께 교차 확인한다.
- 시점 의존 주제는 현재값을 확인한 뒤 작성하며 `보는 법`, `해석 방법` 같은 일반론으로 바꾸지 않는다.

## 검색 최적화·콘텐츠 품질 원칙
- Google·NAVER 등 검색 포털에서 독자가 실제로 입력할 검색어와 검색 의도를 먼저 파악한다.
- 대표 검색어와 연관 검색어는 제목·도입·관련 소제목·본문·alt text에 문맥상 필요한 만큼만 자연스럽게 사용한다.
- 같은 검색어를 반복하거나 검색 순위를 조작하기 위한 키워드 나열·문구 변형·낚시성 제목은 사용하지 않는다.
- 다른 자료를 단순 복사·짜깁기·재서술하지 않고 여러 근거를 비교·정리해 이 글만의 설명 가치가 남게 한다.
- AI가 대량 생성한 글처럼 보이는 상투적인 도입, 같은 문장 패턴, 불필요한 요약 반복과 빈말을 피하고 자연스러운 한국어 편집 품질을 유지한다.
- 직접 해보지 않은 경험이나 존재하지 않는 전문성·후기·사례를 꾸며내지 않는다.
- SEO보다 사실 정확성과 독자 만족을 우선하되, 정확한 제목·설명·구조·이미지 대체텍스트 등 검색엔진이 내용을 이해하기 좋은 형태를 갖춘다.

## 이미지 사용 원칙
- 본문 이해에 실제 도움이 되는 위치만 선정하고 이미지 개수를 억지로 채우지 않는다.
- 각 위치에서 먼저 상업적 블로그에 비용 없이 사용할 수 있는 무료 이미지를 찾는다.
- 무료 판정은 `개별 자산 페이지`와 `별도의 공식 라이선스·이용약관 페이지` 두 곳을 모두 확인해야 한다.
- Premium·Pro·유료 다운로드·구독·크레딧·워터마크 미리보기·editorial-only·라이선스 불명확·재배포 사이트 이미지는 무료 후보에서 제외한다.
- 두 확인 중 하나라도 불명확하면 무료로 판정하지 않고 이미지 생성 프롬프트를 fallback으로 사용한다.

## 제목 규칙
{chr(10).join(f'- {item}' for item in title_rule_list)}

## 본문 구성
{chr(10).join(f'{index}. {item}' for index, item in enumerate(outline_list, start=1))}

## 금지 표현
{chr(10).join(f'- {item}' for item in forbidden_list)}

## 사실 확인 목록
{chr(10).join(f'- {item}' for item in fact_check_list) if fact_check_list else '- 구체적인 숫자, 날짜, 법률, 가격, 정책, 인물 직책은 작성 시점 기준으로 별도 확인'}
"""

    prompt = build_ai_prompt(
        pack_markdown=pack_markdown,
        references=references,
        target_length=int(target_length),
        research_requirements=research_requirements,
    )
    return {
        "title_rules": title_rule_list,
        "outline": outline_list,
        "forbidden_expressions": forbidden_list,
        "fact_check_items": fact_check_list,
        "references": references,
        "trend_reference_count": len(trend_references),
        "factual_reference_count": len(fact_references),
        "evidence_summary": evidence_summary,
        "research_requirements": research_requirements,
        "pack_markdown": pack_markdown,
        "prompt_text": prompt,
    }


def build_ai_prompt(
    *,
    pack_markdown: str,
    references: list[dict[str, Any]],
    target_length: int,
    research_requirements: dict[str, Any] | None = None,
) -> str:
    reference_ids = [item["id"] for item in references]
    trend_ids = [item["id"] for item in references if item.get("reference_kind") == "trend_signal"]
    factual_ids = [
        item["id"] for item in references if item.get("reference_kind") == "factual_reference"
    ]
    schema = json.dumps(OUTPUT_SCHEMA_EXAMPLE, ensure_ascii=False, indent=2)
    requirements = research_requirements or {}
    freshness_section = ""
    if requirements.get("is_freshness_sensitive"):
        matched = ", ".join(str(item) for item in requirements.get("matched_terms") or []) or "시점 의존 표현"
        freshness_section = f"""
[시점 의존 주제 특별 규칙]
- 감지된 표현: {matched}
- 웹 검색을 사용해 요청 시점의 현재 순위·결과·일정·가격·정책 상태를 직접 확인합니다.
- 공식 기관·기업·리그·정부 등 1차 출처를 우선하고 게시일과 실제 적용 기준일을 확인합니다.
- 사용자의 의도를 `보는 법`, `해석 방법`, `일반적인 주의점`으로 바꾸지 않습니다.
- 검색으로 확인되지 않은 현재값은 추정하지 않고 fact_checks에 남깁니다.
"""
    return f"""당신은 사실 확인과 사람 중심의 검색 품질을 우선하는 한국어 콘텐츠 편집자입니다.
아래 자료팩을 사용해 Google·NAVER 등 검색 포털에서도 이해하기 쉽고 실제 독자에게 도움이 되는 완성도 높은 초안을 작성하세요.
이 요청은 Gemini와 ChatGPT에서 공통으로 사용할 수 있습니다.

[중요 원칙]
1. 웹 검색 기능을 사용해 글에 필요한 사실과 최신 정보를 직접 조사합니다.
2. 공식 기관·기업·리그·정부 등 1차 출처를 우선하고 게시일과 적용 기준일을 확인합니다.
3. 출처, 통계, 날짜, 인물, 정책을 임의로 만들지 않습니다.
4. 확인하지 못한 구체적 주장은 fact_checks에 needs_verification으로 기록합니다.
5. 트렌드 신호는 관심 증가의 근거일 뿐 사실을 보증하는 자료가 아닙니다.
6. 사용자가 추가한 사실 참고 자료가 있으면 웹 검색 결과와 함께 교차 확인합니다.
7. 광고성 과장, 단정, 공포 조장, 근거 없는 비교를 피합니다.
8. 목표 분량은 약 {target_length}자이며, 불필요한 반복으로 분량을 채우지 않습니다.
9. SEO는 필수이지만 검색엔진을 속이는 문구나 키워드 반복이 아니라 검색 의도·정확한 제목·구조·설명·대체텍스트와 실제 독자 가치를 통해 적용합니다.
10. 다른 글을 단순 재작성하거나 짜깁기하지 말고, 확인한 근거를 비교·정리해 독자가 추가 검색 없이 핵심을 이해할 수 있는 고유한 설명 가치를 만듭니다.
11. 상투적인 AI 문구, 기계적인 도입·결론, 같은 표현 반복, 불필요한 목록 남발을 피하고 사람이 편집한 자연스러운 한국어 문장과 문단 흐름을 사용합니다.
12. 직접 겪지 않은 경험·후기·전문성·인터뷰를 꾸며내지 않습니다. 자연스러움을 위해 사실을 조작하지 않습니다.
{freshness_section}
[SEO 필수 규칙]
- 검색 전에 이 글의 대표 검색어, 자연스러운 연관 검색어, 주된 검색 의도를 정합니다.
- 대표 검색어는 제목과 도입부에 자연스럽게 반영하고, 관련 소제목에는 내용상 필요한 경우에만 사용합니다.
- 연관 검색어는 의미가 맞는 문맥에서만 사용하며 같은 단어나 유사어를 억지로 반복하지 않습니다.
- 제목은 본문 내용을 정확히 예고하는 고유한 문장으로 만들고 과장·낚시를 금지합니다.
- meta_description은 본문을 정확히 요약하면서 검색자가 읽을 이유를 알 수 있는 자연스러운 문장으로 작성합니다.
- 이미지 alt_text는 이미지 내용과 본문 맥락을 설명하고 키워드 나열용으로 사용하지 않습니다.
- 검색 노출만을 목적으로 내용과 무관한 인기 키워드·지역명·질문 변형을 추가하지 않습니다.

[이미지 필수 규칙]
- 이미지가 실제로 이해를 돕는 본문 위치를 스스로 정합니다. 필요 없는 위치에 이미지를 억지로 추가하지 않습니다.
- 각 image 블록마다 무료 이미지 검색용 search_query와, 무료 이미지를 쓰지 못할 때 즉시 사용할 수 있는 생성용 prompt를 둘 다 작성합니다.
- 무료 이미지를 찾았다면 `free_image.status`를 `verified_free`로 쓰기 전에 반드시 두 번 확인합니다.
  1) 해당 이미지의 `개별 자산 페이지`에서 무료 제공이며 결제·Premium·Pro·구독·크레딧이 필요하지 않음을 확인합니다.
  2) 같은 제공자의 `별도 공식 라이선스 또는 이용약관 페이지`에서 상업적 블로그 사용이 비용 없이 허용됨을 확인합니다.
- 위 두 확인 URL은 서로 다른 실제 페이지여야 하며 page_url과 license_url에 각각 기록합니다.
- 워터마크 미리보기만 무료, 고해상도 유료, 무료 체험·구독 가입 전제, 크레딧 소모, Premium/Pro, editorial-only, 상업 이용 불명확, 출처 불명 재배포 이미지는 절대 verified_free로 표시하지 않습니다.
- 인물·상표·사유재산 등 별도 권리가 명확하지 않아 상업적 게시가 위험한 경우도 verified_free로 표시하지 않습니다.
- 하나라도 확실하지 않으면 `free_image.status`를 `not_found`로 두고 URL·라이선스를 추측하지 않으며 생성용 prompt를 사용합니다.
- verified_free일 때만 commercial_use_allowed=true, payment_required=false, premium_or_subscription_required=false, editorial_only=false가 허용됩니다.
- attribution이 필요하면 정확한 표시 문구를 기록합니다. 직접 이미지 파일 URL을 핫링크하지 말고 원본 자산 페이지를 기록합니다.

[자료팩]
{pack_markdown}

[자료팩에 포함된 참고 자료 ID]
- 전체: {', '.join(reference_ids) if reference_ids else '없음'}
- 트렌드 신호: {', '.join(trend_ids) if trend_ids else '없음'}
- 사용자가 추가한 참고 자료: {', '.join(factual_ids) if factual_ids else '없음'}
- 웹 검색으로 새로 확인한 사실 출처는 R1, R2, R3처럼 별도 ID를 부여합니다.

[출력 규칙]
- 아래 schema_version 2.1 구조를 따르는 JSON 코드 블록 하나만 출력합니다.
- JSON 앞뒤에 설명 문장을 쓰지 않습니다.
- seo 객체를 반드시 작성하고 primary_keyword, secondary_keywords, search_intent, meta_description을 모두 채웁니다.
- 본문은 blocks 배열에 작성하고 body_markdown이나 image_prompts 항목을 별도로 만들지 않습니다.
- blocks에서 사용할 수 있는 type은 paragraph, heading, bullet_list, numbered_list, quote, image뿐입니다.
- paragraph와 quote는 text, heading은 level(1~6)과 text, 목록은 items 문자열 배열을 사용합니다.
- image 블록은 본문에서 삽입할 순서에 배치하고 position, purpose, free_image, prompt, aspect_ratio, caption, alt_text를 모두 작성합니다.
- free_image는 status, search_query, page_url, provider, creator, license_name, license_url, attribution, checked_at, commercial_use_allowed, payment_required, premium_or_subscription_required, editorial_only, verification_note를 모두 포함합니다.
- 무료 이미지를 찾지 못했거나 2중 확인을 통과하지 못하면 free_image.status=`not_found`로 하고 검증되지 않은 page_url/license_url은 비웁니다.
- title과 같은 제목을 blocks 첫 heading으로 반복하지 않습니다.
- sources에는 본문의 사실 작성에 실제로 참고한 자료만 넣습니다. 무료 이미지 라이선스 확인 URL은 sources에 섞지 않고 해당 image 블록의 free_image에 기록합니다.
- 자료팩 출처는 기존 S번호를 유지하고 URL을 변경하지 않습니다.
- 웹 검색으로 확인한 사실 출처는 R1, R2 형식으로 title, publisher, url, published_at을 모두 기록합니다.
- 검색 결과에 없는 URL을 추측하거나 만들어내지 않습니다.
- 트렌드 신호를 사용했다면 관심 증가의 근거로만 표현합니다.
- 이미지가 필요하지 않은 글은 image 블록을 만들지 않아도 됩니다.

[출력 JSON 예시]
```json
{schema}
```
"""


def save_content_pack(
    con: duckdb.DuckDBPyConnection,
    *,
    topic_id: str,
    audience: str,
    purpose: str,
    angle: str,
    category: str,
    target_length: int,
    title_rules: str | Iterable[str],
    outline: str | Iterable[str],
    forbidden_expressions: str | Iterable[str],
    fact_check_items: str | Iterable[str],
    selected_source_item_ids: Iterable[str] | None = None,
    selected_reference_ids: Iterable[str] | None = None,
) -> dict[str, Any]:
    topic = get_topic(con, topic_id)
    if topic is None:
        raise ValueError("선택한 주제를 찾을 수 없습니다.")

    sources = get_topic_sources(con, topic_id)
    if selected_source_item_ids is not None:
        selected_ids = {str(item) for item in selected_source_item_ids}
        sources = [
            source
            for source in sources
            if str(source.get("source_item_id")) in selected_ids
        ]

    factual_references = list_topic_references(con, topic_id)
    if selected_reference_ids is not None:
        selected_ids = {str(item) for item in selected_reference_ids}
        factual_references = [
            reference
            for reference in factual_references
            if str(reference.get("reference_id")) in selected_ids
        ]

    pack = build_content_pack(
        topic,
        sources,
        factual_references=factual_references,
        audience=audience,
        purpose=purpose,
        angle=angle,
        category=category,
        target_length=target_length,
        title_rules=title_rules,
        outline=outline,
        forbidden_expressions=forbidden_expressions,
        fact_check_items=fact_check_items,
    )
    version_row = con.execute(
        "SELECT COALESCE(MAX(version), 0) + 1 FROM content_packs WHERE topic_id = ?",
        [topic_id],
    ).fetchone()
    version = int(version_row[0])
    content_pack_id = f"pack_{uuid4().hex}"
    now = datetime.now()
    con.execute(
        """
        INSERT INTO content_packs(
            content_pack_id, topic_id, version, audience, purpose, angle,
            category, target_length, title_rules_json, outline_json,
            forbidden_expressions_json, fact_check_items_json,
            references_json, pack_markdown, prompt_text, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            content_pack_id,
            topic_id,
            version,
            audience.strip(),
            purpose.strip(),
            angle.strip(),
            category.strip(),
            int(target_length),
            json.dumps(pack["title_rules"], ensure_ascii=False),
            json.dumps(pack["outline"], ensure_ascii=False),
            json.dumps(pack["forbidden_expressions"], ensure_ascii=False),
            json.dumps(pack["fact_check_items"], ensure_ascii=False),
            json.dumps(pack["references"], ensure_ascii=False, default=str),
            pack["pack_markdown"],
            pack["prompt_text"],
            now,
        ],
    )
    con.execute(
        "UPDATE topics SET status = 'ai_ready', updated_at = ? WHERE topic_id = ?",
        [now, topic_id],
    )
    save_topic_content_preferences(
        con,
        topic_id=topic_id,
        audience=audience,
        purpose=purpose,
        angle=angle,
        category=category,
        target_length=int(target_length),
        title_rules=pack["title_rules"],
        outline=pack["outline"],
        forbidden_expressions=pack["forbidden_expressions"],
        fact_check_items=pack["fact_check_items"],
    )
    return {"content_pack_id": content_pack_id, "version": version, **pack}


def save_quick_content_pack(
    con: duckdb.DuckDBPyConnection,
    *,
    topic_title: str,
    topic_summary: str = "",
    topic_category: str = "",
    topic_memo: str = "",
    audience: str,
    purpose: str,
    angle: str,
    category: str,
    target_length: int,
    title_rules: str | Iterable[str],
    outline: str | Iterable[str],
    forbidden_expressions: str | Iterable[str],
    fact_check_items: str | Iterable[str],
) -> dict[str, Any]:
    """Create or reuse an internal topic and immediately save a content pack.

    This keeps the database relationship intact while allowing the user to start
    from a free-form writing idea without visiting the topic management screen.
    """
    readiness = assess_content_pack_readiness(
        {
            "title": topic_title,
            "summary": topic_summary,
            "memo": topic_memo,
        },
        [],
        purpose=purpose,
        angle=angle,
    )
    if readiness["is_blocked"]:
        raise ValueError(
            str(readiness["message"])
            + " 새 글감 바로 입력에서는 사실 참고 자료를 함께 등록할 수 없으므로, "
              "먼저 주제·트렌드에서 주제를 저장하고 참고 자료를 추가하세요."
        )

    topic_id, topic_created = add_manual_topic(
        con,
        title=topic_title,
        summary=topic_summary,
        category=topic_category or category,
        memo=topic_memo,
    )
    pack = save_content_pack(
        con,
        topic_id=topic_id,
        audience=audience,
        purpose=purpose,
        angle=angle,
        category=category or topic_category,
        target_length=target_length,
        title_rules=title_rules,
        outline=outline,
        forbidden_expressions=forbidden_expressions,
        fact_check_items=fact_check_items,
        selected_source_item_ids=[],
        selected_reference_ids=[],
    )
    return {
        "topic_id": topic_id,
        "topic_created": topic_created,
        **pack,
    }


def list_content_packs(con: duckdb.DuckDBPyConnection) -> list[dict[str, Any]]:
    rows = con.execute(
        """
        SELECT p.content_pack_id, p.topic_id, p.version, p.prompt_text,
               p.pack_markdown, p.created_at, t.title AS topic_title
        FROM content_packs p
        JOIN topics t ON t.topic_id = p.topic_id
        ORDER BY p.created_at DESC
        """
    ).fetchall()
    columns = [item[0] for item in con.description]
    return [dict(zip(columns, row)) for row in rows]


def get_content_pack(
    con: duckdb.DuckDBPyConnection,
    content_pack_id: str,
) -> dict[str, Any] | None:
    row = con.execute(
        "SELECT * FROM content_packs WHERE content_pack_id = ?",
        [content_pack_id],
    ).fetchone()
    if row is None:
        return None
    columns = [item[0] for item in con.description]
    return dict(zip(columns, row))
