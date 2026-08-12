import pytest

from src.services.content_pack_service import (
    assess_content_pack_readiness,
    build_content_pack,
    save_quick_content_pack,
)


def _factual_reference() -> dict[str, object]:
    return {
        "reference_id": "ref_current",
        "reference_type": "official",
        "reference_type_label": "공식 자료",
        "title": "현재 기준 공식 자료",
        "publisher": "공식 기관",
        "url": "https://example.com/current",
        "published_at": "2026-07-31",
        "memo": "2026년 7월 31일 기준 핵심 수치를 확인했습니다.",
    }


def _build_pack(*, title: str, factual_references=None):
    return build_content_pack(
        {"title": title, "summary": "", "category": "정보", "memo": ""},
        [],
        factual_references=list(factual_references or []),
        audience="일반 독자",
        purpose="현재 정보를 정확히 정리",
        angle="핵심 수치와 기준일 확인",
        category="정보",
        target_length=1500,
        title_rules="과장 금지",
        outline="핵심 정보\n주의사항",
        forbidden_expressions="무조건",
        fact_check_items="기준일 확인",
    )


def test_sports_standings_require_a_factual_reference() -> None:
    readiness = assess_content_pack_readiness("2026 KBO 현재 순위", [])

    assert readiness["is_freshness_sensitive"] is True
    assert readiness["requires_factual_reference"] is True
    assert readiness["is_blocked"] is True
    assert "순위" in readiness["blocking_terms"]


def test_sports_standings_are_unblocked_with_a_selected_reference() -> None:
    readiness = assess_content_pack_readiness(
        "2026 KBO 현재 순위",
        [_factual_reference()],
    )

    assert readiness["requires_factual_reference"] is True
    assert readiness["factual_reference_count"] == 1
    assert readiness["is_blocked"] is False


def test_exchange_rate_and_weather_require_factual_references() -> None:
    exchange = assess_content_pack_readiness("오늘 원달러 환율", [])
    weather = assess_content_pack_readiness("내일 서울 날씨와 강수 예보", [])

    assert exchange["is_blocked"] is True
    assert "환율" in exchange["blocking_terms"]
    assert weather["is_blocked"] is True
    assert {"날씨", "강수", "예보"}.issubset(weather["blocking_terms"])


def test_general_fresh_news_requires_web_research_without_blocking() -> None:
    readiness = assess_content_pack_readiness("오늘 공개된 신제품 특징 정리", [])

    assert readiness["is_freshness_sensitive"] is True
    assert readiness["requires_web_research"] is True
    assert readiness["requires_factual_reference"] is False
    assert readiness["is_blocked"] is False


def test_search_ranking_advice_is_not_mistaken_for_live_sports_rankings() -> None:
    readiness = assess_content_pack_readiness("블로그 검색 순위 올리는 법", [])

    assert readiness["is_freshness_sensitive"] is False
    assert readiness["requires_factual_reference"] is False
    assert readiness["is_blocked"] is False


def test_build_content_pack_blocks_current_fact_topic_without_reference() -> None:
    with pytest.raises(ValueError, match="사실 참고 자료를 1개 이상"):
        _build_pack(title="프로야구 경기 결과와 현재 순위")

    pack = _build_pack(
        title="프로야구 경기 결과와 현재 순위",
        factual_references=[_factual_reference()],
    )
    assert pack["factual_reference_count"] == 1
    assert pack["research_requirements"]["is_blocked"] is False


def test_quick_pack_blocks_before_creating_an_orphan_topic() -> None:
    class _UnexpectedDatabaseAccess:
        def execute(self, *_args, **_kwargs):
            raise AssertionError("차단 전에 데이터베이스를 사용하면 안 됩니다.")

    with pytest.raises(ValueError, match="새 글감 바로 입력"):
        save_quick_content_pack(
            _UnexpectedDatabaseAccess(),
            topic_title="오늘 KBO 경기 결과",
            audience="일반 독자",
            purpose="경기 결과 정리",
            angle="현재 결과 확인",
            category="스포츠",
            target_length=1500,
            title_rules="과장 금지",
            outline="결과\n정리",
            forbidden_expressions="무조건",
            fact_check_items="공식 결과 확인",
        )
