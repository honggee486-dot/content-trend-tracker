from __future__ import annotations

import json
from typing import Any, Mapping, Sequence

import duckdb


ADSENSE_INITIAL_FIT = "A:적합"
ADSENSE_INITIAL_REVIEW = "A:검토"
ADSENSE_INITIAL_AVOID = "A:피함"

# 이 표시는 AdSense 승인 가능성을 예측하는 점수가 아니다. Google의 공개 정책에서
# 후보 단계에 적용할 수 있는 요소(정책·제한 가능 주제, 독창적·유용한 설명으로
# 발전할 여지)와 이 앱의 자료 준비 신호를 합쳐 초기 승인 전 글감 선택을 보조한다.
_HARD_INITIAL_AVOID_TERMS = {
    "포르노",
    "음란",
    "성인물",
    "성매매",
    "성관계",
    "섹스",
    "나체",
    "도박",
    "카지노",
    "스포츠토토",
    "마약",
    "필로폰",
    "코카인",
    "헤로인",
    "대마초",
    "전자담배",
    "담배",
    "니코틴",
    "폭탄 제조",
    "폭발물 제조",
    "총기 구매",
    "총기 판매",
    "자살 방법",
    "자해 방법",
    "암살",
    "참수",
    "총격",
    "테러 공격",
}
_FAST_EXPIRY_TERMS = {
    "태풍",
    "날씨",
    "예보",
    "기온",
    "실시간",
    "속보",
    "환율",
    "주가",
    "개표",
    "투표 결과",
    "경기 결과",
    "경기 일정",
}
_CURRENT_AFFAIRS_REVIEW_TERMS = {
    "대통령",
    "정당",
    "선거",
    "탄핵",
    "북한",
    "미사일",
}
_HIGH_TRUST_TERMS = {
    "대출",
    "금리",
    "세금",
    "투자",
    "주식",
    "코인",
    "법률",
    "소송",
    "의료",
    "질병",
    "약물",
}
_HELPFUL_FORMAT_TERMS = {
    "신청",
    "방법",
    "기준",
    "조건",
    "절차",
    "비교",
    "이유",
    "원인",
    "영향",
    "정리",
    "총정리",
    "가이드",
    "사용법",
    "체크",
    "주의",
    "혜택",
    "변경",
    "달라",
    "어떻게",
    "왜",
    "준비",
}
_NEWS_EVENT_TERMS = {
    "발사",
    "영입",
    "체포",
    "사퇴",
    "회담",
    "논란",
    "발표",
    "공개",
    "승리",
    "패배",
}


def _safe_float(value: object, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError, OverflowError):
        return float(default)


def _safe_int(value: object, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError, OverflowError):
        return int(default)


def _contains_any(text: str, terms: set[str]) -> list[str]:
    return [term for term in terms if term in text]


def _candidate_text(
    row: Mapping[str, Any],
    ai_context: Mapping[str, str] | None = None,
) -> str:
    context = ai_context or {}
    parts = [
        str(context.get("display_title") or ""),
        str(context.get("summary") or ""),
        str(context.get("category") or ""),
        str(row.get("주제") or ""),
        str(row.get("원문") or ""),
    ]
    return " ".join(part.strip() for part in parts if part and part.strip()).casefold()


def assess_initial_adsense_candidate(
    row: Mapping[str, Any],
    ai_context: Mapping[str, str] | None = None,
) -> dict[str, str]:
    """Return a conservative, display-only AdSense-initial-phase hint.

    This does not predict site approval. It only helps choose candidates that are
    easier to turn into original, useful informational posts while avoiding
    obviously sensitive/restricted themes during the initial review period.
    """
    text = _candidate_text(row, ai_context)
    hard_terms = _contains_any(text, _HARD_INITIAL_AVOID_TERMS)
    if hard_terms:
        matched = " · ".join(hard_terms[:3])
        return {
            "label": ADSENSE_INITIAL_AVOID,
            "reason": (
                "초기 심사 전에는 보수적으로 피하는 주제입니다. "
                f"민감·광고 제한 가능 신호: {matched}. "
                "이 표시는 정책 위반 확정이나 승인 결과 예측이 아닙니다."
            ),
        }

    quality = _safe_float(row.get("콘텐츠품질"), 50.0)
    fact_risk = _safe_float(row.get("사실위험"), 0.0)
    publishers = _safe_int(row.get("서로다른출처"), 0)
    source_types = _safe_int(row.get("출처종류"), 0)

    score = 48.0
    if quality >= 65:
        score += 12
    elif quality >= 55:
        score += 8
    elif quality >= 45:
        score += 4
    else:
        score -= 8

    if publishers >= 4:
        score += 8
    elif publishers >= 2:
        score += 4
    elif publishers == 1:
        score -= 4

    if source_types >= 3:
        score += 6
    elif source_types >= 2:
        score += 3

    helpful_terms = _contains_any(text, _HELPFUL_FORMAT_TERMS)
    if helpful_terms:
        score += 12

    if fact_risk > 18:
        score -= 20
    elif fact_risk > 10:
        score -= 10
    elif fact_risk > 5:
        score -= 4

    high_trust_terms = _contains_any(text, _HIGH_TRUST_TERMS)
    if high_trust_terms:
        score -= 3

    fast_terms = _contains_any(text, _FAST_EXPIRY_TERMS)
    if fast_terms:
        score -= 6

    current_affairs_terms = _contains_any(text, _CURRENT_AFFAIRS_REVIEW_TERMS)
    event_terms = _contains_any(text, _NEWS_EVENT_TERMS)
    if event_terms and not helpful_terms:
        score -= 8

    # 시점 의존성이 매우 크거나 정치·안보 중심인 후보는 점수가 높아도 초기 승인 전
    # 우선 후보로 자동 승격하지 않는다. 최신 공식 근거와 자체 해설이 필요하다.
    force_review = bool(fast_terms or current_affairs_terms)
    if high_trust_terms and fact_risk > 10:
        force_review = True

    if score < 44:
        return {
            "label": ADSENSE_INITIAL_AVOID,
            "reason": (
                "초기 심사 후보로는 자료 준비도·독자 가치 신호가 약합니다. "
                "원문 재요약보다 자체 설명과 독립 근거를 충분히 보강한 뒤 다시 검토하세요. "
                "승인 결과를 예측하는 표시는 아닙니다."
            ),
        }

    if force_review or score < 62:
        reasons: list[str] = []
        if fast_terms:
            reasons.append("시점 의존: " + " · ".join(fast_terms[:3]))
        if current_affairs_terms:
            reasons.append("정치·안보/현안: " + " · ".join(current_affairs_terms[:3]))
        if high_trust_terms:
            reasons.append("정확성 중요: " + " · ".join(high_trust_terms[:3]))
        if not helpful_terms:
            reasons.append("기사 요약형보다 설명·비교·절차형 보강 필요")
        if fact_risk > 10:
            reasons.append(f"사실 위험 {fact_risk:.1f}")
        detail = "; ".join(reasons[:3]) or "자체 설명과 독립 근거를 더 보강할 필요가 있음"
        return {
            "label": ADSENSE_INITIAL_REVIEW,
            "reason": (
                f"초기 심사 전 검토 권장: {detail}. "
                "최신 공식 출처를 확인하고 단순 기사 재작성보다 독자에게 필요한 설명을 추가하세요."
            ),
        }

    helpful_text = " · ".join(helpful_terms[:3]) if helpful_terms else "정보성 설명"
    return {
        "label": ADSENSE_INITIAL_FIT,
        "reason": (
            f"초기 심사 우선 후보: {helpful_text} 형태로 발전시키기 좋고 자료 준비 신호가 양호합니다. "
            "최종 글에는 자체 설명·출처·독자 가치를 넣어야 하며 AdSense 승인 자체를 보장하지 않습니다."
        ),
    }


def _load_ai_profile_contexts(
    con: duckdb.DuckDBPyConnection,
    cluster_ids: Sequence[str],
) -> dict[str, dict[str, str]]:
    normalized_ids = list(dict.fromkeys(str(value or "").strip() for value in cluster_ids))
    normalized_ids = [value for value in normalized_ids if value]
    if not normalized_ids:
        return {}
    try:
        placeholders = ", ".join("?" for _ in normalized_ids)
        rows = con.execute(
            f"""
            SELECT cluster_id, display_title, summary, content_plan_json
            FROM trend_cluster_ai_profiles
            WHERE cluster_id IN ({placeholders})
            """,
            normalized_ids,
        ).fetchall()
    except Exception:
        return {}

    result: dict[str, dict[str, str]] = {}
    for cluster_id, display_title, summary, content_plan_json in rows:
        try:
            content_plan = json.loads(str(content_plan_json or "{}"))
        except (TypeError, json.JSONDecodeError):
            content_plan = {}
        category = (
            str(content_plan.get("category") or "").strip()
            if isinstance(content_plan, dict)
            else ""
        )
        result[str(cluster_id)] = {
            "display_title": str(display_title or "").strip(),
            "summary": str(summary or "").strip(),
            "category": category,
        }
    return result


def build_adsense_candidate_assessments(
    con: duckdb.DuckDBPyConnection,
    rows: Sequence[Mapping[str, Any]],
) -> dict[str, dict[str, str]]:
    """Assess visible trend rows without API calls or persistent DB changes."""
    visible_rows = [dict(row) for row in rows]
    if not visible_rows:
        return {}
    cluster_ids = [str(row.get("cluster_id") or "") for row in visible_rows]
    contexts = _load_ai_profile_contexts(con, cluster_ids)
    result: dict[str, dict[str, str]] = {}
    for row in visible_rows:
        cluster_id = str(row.get("cluster_id") or "").strip()
        if not cluster_id:
            continue
        result[cluster_id] = assess_initial_adsense_candidate(
            row,
            contexts.get(cluster_id),
        )
    return result
