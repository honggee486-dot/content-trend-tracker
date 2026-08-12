from pathlib import Path
import json

import pytest

from src.database import connect_database, init_database
from src.services.content_pack_service import (
    assess_content_pack_readiness,
    build_content_pack,
    get_topic_content_defaults,
    link_topic_to_trend_cluster,
    save_content_pack,
    save_quick_content_pack,
)
from src.services.reference_service import add_topic_reference
from src.services.topic_service import add_manual_topic, get_topic_sources, upsert_source_signal


def test_prompt_supports_gemini_and_chatgpt() -> None:
    pack = build_content_pack(
        {"title": "테스트 주제", "summary": "설명", "category": "정보", "memo": ""},
        [],
        audience="일반 독자",
        purpose="정보 제공",
        angle="핵심 정리",
        category="정보",
        target_length=1500,
        title_rules="과장하지 않는다",
        outline="도입\n핵심\n정리",
        forbidden_expressions="무조건",
        fact_check_items="수치 확인",
    )
    assert "Gemini와 ChatGPT" in pack["prompt_text"]
    assert '"schema_version": "2.0"' in pack["prompt_text"]
    assert '"blocks"' in pack["prompt_text"]
    assert '"body_markdown"' not in pack["prompt_text"]
    assert "paragraph, heading, bullet_list, numbered_list, quote, image" in pack["prompt_text"]
    assert "선택한 트렌드 신호 없음" in pack["pack_markdown"]


def test_content_pack_summarizes_selected_trend_evidence() -> None:
    sources = [
        {
            "source_item_id": "src_topic",
            "source_type": "youtube",
            "raw_title": "AI 검색 변화",
            "item_title": "AI 검색 변화",
            "source_name": "YouTube · 떠오르는 주제",
            "source_url": "https://example.com/topic",
            "observed_at": "2026-07-14 12:00:00",
            "signal_value": 9.5,
            "signal_type": "emerging_topic",
            "signal_type_label": "떠오르는 주제",
            "topic_score": 9.5,
            "views_per_hour": 3200,
            "metadata": {},
        },
        {
            "source_item_id": "src_video",
            "source_type": "youtube",
            "raw_title": "AI 검색 변화",
            "item_title": "검색 방식이 달라지는 이유",
            "source_name": "YouTube · 최근 영상",
            "source_url": "https://example.com/video",
            "observed_at": "2026-07-14 12:10:00",
            "signal_value": 12345,
            "signal_type": "recent_video",
            "signal_type_label": "최근 영상",
            "view_count": 12345,
            "view_delta": 2300,
            "metadata": {},
        },
    ]

    pack = build_content_pack(
        {"title": "AI 검색 변화", "summary": "설명", "category": "IT", "memo": ""},
        sources,
        audience="일반 독자",
        purpose="정보 제공",
        angle="변화 원인 정리",
        category="IT",
        target_length=1800,
        title_rules="과장하지 않는다",
        outline="도입\n핵심\n정리",
        forbidden_expressions="무조건",
        fact_check_items="수치 확인",
    )

    assert "떠오르는 주제 1개, 최근 영상 1개" in pack["pack_markdown"]
    assert "관련 영상 조회수 최고값: 12,345" in pack["pack_markdown"]
    assert "검색 방식이 달라지는 이유" in pack["pack_markdown"]
    assert pack["references"][1]["title"] == "검색 방식이 달라지는 이유"


def test_save_content_pack_uses_only_selected_sources(tmp_path: Path) -> None:
    db_path = tmp_path / "main.duckdb"
    init_database(db_path)

    with connect_database(db_path) as con:
        topic_id, _ = add_manual_topic(con, title="AI 검색 변화")
        for external_id, item_title, signal_type in [
            ("topic:ai", "AI 검색 변화", "emerging_topic"),
            ("video:ai", "검색 방식이 달라지는 이유", "recent_video"),
        ]:
            upsert_source_signal(
                con,
                {
                    "source_type": "youtube",
                    "external_id": external_id,
                    "title": "AI 검색 변화",
                    "source_name": "YouTube",
                    "signal_value": 10,
                    "metadata": {
                        "signal_type": signal_type,
                        "item_title": item_title,
                    },
                },
            )

        sources = get_topic_sources(con, topic_id)
        selected = next(
            source
            for source in sources
            if source["signal_type"] == "recent_video"
        )
        pack = save_content_pack(
            con,
            topic_id=topic_id,
            audience="일반 독자",
            purpose="정보 제공",
            angle="핵심 정리",
            category="IT",
            target_length=1500,
            title_rules="과장하지 않는다",
            outline="도입\n핵심\n정리",
            forbidden_expressions="무조건",
            fact_check_items="수치 확인",
            selected_source_item_ids=[selected["source_item_id"]],
        )

    assert len(pack["references"]) == 1
    assert pack["references"][0]["title"] == "검색 방식이 달라지는 이유"
    assert "AI 검색 변화 / YouTube" not in pack["pack_markdown"]


def test_content_pack_includes_selected_factual_references(tmp_path: Path) -> None:
    db_path = tmp_path / "main.duckdb"
    init_database(db_path)

    with connect_database(db_path) as con:
        topic_id, _ = add_manual_topic(con, title="전기요금 절약")
        first_id, _ = add_topic_reference(
            con,
            topic_id=topic_id,
            reference_type="official_agency",
            title="주택용 전기요금 안내",
            publisher="한국전력공사",
            url="https://example.com/official-rate",
            published_at="2026-07-01",
            memo="요금제 기준 확인",
        )
        add_topic_reference(
            con,
            topic_id=topic_id,
            reference_type="news",
            title="전기요금 관련 기사",
            publisher="테스트뉴스",
            url="https://example.com/news-rate",
        )

        pack = save_content_pack(
            con,
            topic_id=topic_id,
            audience="일반 독자",
            purpose="정보 제공",
            angle="공식 기준 정리",
            category="생활",
            target_length=1500,
            title_rules="과장하지 않는다",
            outline="도입\n핵심\n정리",
            forbidden_expressions="무조건",
            fact_check_items="요금 기준 확인",
            selected_source_item_ids=[],
            selected_reference_ids=[first_id],
        )

    assert pack["trend_reference_count"] == 0
    assert pack["factual_reference_count"] == 1
    assert len(pack["references"]) == 1
    assert pack["references"][0]["reference_kind"] == "factual_reference"
    assert pack["references"][0]["reference_type_label"] == "공식 기관"
    assert "주택용 전기요금 안내" in pack["pack_markdown"]
    assert "전기요금 관련 기사" not in pack["pack_markdown"]
    assert "사용자가 추가한 참고 자료: S1" in pack["prompt_text"]


def test_quick_content_pack_starts_without_pre_registered_topic(tmp_path: Path) -> None:
    db_path = tmp_path / "main.duckdb"
    init_database(db_path)

    with connect_database(db_path) as con:
        pack = save_quick_content_pack(
            con,
            topic_title="정속형 에어컨 전기요금 줄이기",
            topic_summary="하루 종일 집에 있는 사용자를 위한 실용 정보",
            topic_category="생활",
            topic_memo="공식 요금 기준은 나중에 추가",
            audience="일반 독자",
            purpose="정보 제공",
            angle="실제 사용 방법 중심",
            category="생활",
            target_length=1800,
            title_rules="과장하지 않는다",
            outline="도입\n핵심\n정리",
            forbidden_expressions="무조건",
            fact_check_items="전기요금 기준 확인",
        )
        topic_row = con.execute(
            "SELECT title, status, is_interested FROM topics WHERE topic_id = ?",
            [pack["topic_id"]],
        ).fetchone()
        topic_count = con.execute("SELECT COUNT(*) FROM topics").fetchone()[0]

    assert pack["topic_created"] is True
    assert pack["trend_reference_count"] == 0
    assert pack["factual_reference_count"] == 0
    assert topic_count == 1
    assert topic_row == ("정속형 에어컨 전기요금 줄이기", "ai_ready", True)
    assert "정속형 에어컨 전기요금 줄이기" in pack["pack_markdown"]


def test_quick_content_pack_reuses_same_normalized_topic(tmp_path: Path) -> None:
    db_path = tmp_path / "main.duckdb"
    init_database(db_path)

    common = {
        "topic_summary": "설명",
        "topic_category": "IT",
        "audience": "일반 독자",
        "purpose": "정보 제공",
        "angle": "핵심 정리",
        "category": "IT",
        "target_length": 1500,
        "title_rules": "과장하지 않는다",
        "outline": "도입\n핵심\n정리",
        "forbidden_expressions": "무조건",
        "fact_check_items": "수치 확인",
    }
    with connect_database(db_path) as con:
        first = save_quick_content_pack(
            con, topic_title="AI 검색 변화", **common
        )
        second = save_quick_content_pack(
            con, topic_title="AI 검색 변화!", **common
        )
        topic_count = con.execute("SELECT COUNT(*) FROM topics").fetchone()[0]

    assert first["topic_created"] is True
    assert second["topic_created"] is False
    assert first["topic_id"] == second["topic_id"]
    assert second["version"] == 2
    assert topic_count == 1


def test_current_standings_topic_requires_factual_reference(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "standings-reference-required.duckdb"
    init_database(db_path)

    with connect_database(db_path) as con:
        topic_id, _ = add_manual_topic(con, title="프로야구 중간 순위")
        with pytest.raises(ValueError, match="사실 참고 자료를 1개 이상"):
            save_content_pack(
                con,
                topic_id=topic_id,
                audience="야구 팬",
                purpose="현재 순위 정리",
                angle="현재 순위와 경기 차 분석",
                category="스포츠",
                target_length=1800,
                title_rules="과장하지 않는다",
                outline="도입\n순위\n정리",
                forbidden_expressions="무조건",
                fact_check_items="순위 기준일 확인",
                selected_source_item_ids=[],
                selected_reference_ids=[],
            )
        pack_count = con.execute("SELECT COUNT(*) FROM content_packs").fetchone()[0]

    assert pack_count == 0


def test_current_standings_prompt_forbids_generic_substitution(tmp_path: Path) -> None:
    db_path = tmp_path / "standings-ready.duckdb"
    init_database(db_path)

    with connect_database(db_path) as con:
        topic_id, _ = add_manual_topic(con, title="프로야구 중간 순위")
        reference_id, _ = add_topic_reference(
            con,
            topic_id=topic_id,
            reference_type="official_agency",
            title="KBO 공식 팀 순위",
            publisher="KBO",
            url="https://example.com/kbo-standings",
            published_at="2026-07-25",
            memo="2026-07-25 경기 종료 기준 순위와 승률, 경기 차를 확인",
        )
        pack = save_content_pack(
            con,
            topic_id=topic_id,
            audience="야구 팬",
            purpose="현재 순위 정리",
            angle="현재 순위와 경기 차 분석",
            category="스포츠",
            target_length=1800,
            title_rules="과장하지 않는다",
            outline="도입\n순위\n정리",
            forbidden_expressions="무조건",
            fact_check_items="순위 기준일 확인",
            selected_source_item_ids=[],
            selected_reference_ids=[reference_id],
        )

    assert pack["research_requirements"]["is_freshness_sensitive"] is True
    assert pack["research_requirements"]["memo_reference_count"] == 1
    assert "시점 의존 주제 특별 규칙" in pack["prompt_text"]
    assert "`보는 법`, `해석 방법`, `일반적인 주의점`으로 바꾸지 않습니다" in pack["prompt_text"]
    assert "웹 검색을 사용해 요청 시점의 현재 순위" in pack["prompt_text"]
    assert "공식 기관·기업·리그·정부" in pack["prompt_text"]


def test_readiness_does_not_block_general_cost_saving_topic() -> None:
    readiness = assess_content_pack_readiness("정속형 에어컨 전기요금 줄이기", [])
    assert readiness["is_freshness_sensitive"] is False
    assert readiness["is_blocked"] is False


def test_search_ranking_advice_is_not_mistaken_for_live_sports_rankings() -> None:
    readiness = assess_content_pack_readiness("블로그 검색 순위 올리는 법", [])
    assert readiness["is_freshness_sensitive"] is False
    assert readiness["is_blocked"] is False


def test_topic_content_defaults_use_ai_plan_then_saved_user_values(tmp_path: Path) -> None:
    db_path = tmp_path / "topic-content-defaults.duckdb"
    init_database(db_path)

    ai_plan = {
        "audience": "처음 정책을 확인하는 일반 독자",
        "purpose": "변경 내용과 신청 전 확인점을 쉽게 정리",
        "category": "정책",
        "target_length": 2300,
        "title_rules": ["적용 대상과 시점을 제목에 드러낸다", "확정되지 않은 혜택을 단정하지 않는다"],
        "outline": ["변경 배경", "핵심 내용", "적용 대상", "신청 방법", "주의사항"],
        "forbidden_expressions": ["누구나 지급", "무조건 신청", "100% 확정"],
        "timeliness": {
            "type": "short_lived",
            "publish_priority": 4,
            "freshness_window_hours": 72,
            "recheck_before_writing": True,
            "reason": "시행 일정이 바뀔 수 있음",
        },
        "evidence_plan": {
            "required_source_types": ["공식 시행 공고"],
            "evidence_gaps": ["최종 접수 마감일"],
            "official_search_queries": ["새 지원 정책 공식 시행 공고"],
        },
        "primary_direction_reason": "신청 조건 중심 설명이 가장 실용적임",
    }
    with connect_database(db_path) as con:
        topic_id, _ = add_manual_topic(con, title="새 지원 정책")
        con.execute(
            """
            INSERT INTO trend_cluster_ai_profiles(
                cluster_id, canonical_title, display_title, summary,
                verification_points_json, content_plan_json,
                model_name, feature_version, created_at, updated_at
            ) VALUES (
                'cluster_policy', '새 지원 정책', '새 지원 정책 핵심 정리',
                '공개 관심 신호가 확인된 정책 글감', ?, ?,
                'gemini-3.6-flash', '4', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
            )
            """,
            [
                json.dumps(["공식 시행일", "적용 대상", "신청 조건"], ensure_ascii=False),
                json.dumps(ai_plan, ensure_ascii=False),
            ],
        )
        con.execute(
            """
            INSERT INTO trend_cluster_ai_angles(
                angle_id, cluster_id, canonical_title, angle_order,
                angle_label, angle_text, rationale, search_queries_json,
                model_name, feature_version, created_at, updated_at
            ) VALUES (
                'angle_policy', 'cluster_policy', '새 지원 정책', 1,
                '핵심 설명', '지원 대상과 신청 조건 중심으로 정리',
                '독자가 실제 신청 전에 확인할 정보가 필요함', '[]',
                'gemini-3.6-flash', '4', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
            )
            """
        )
        link_topic_to_trend_cluster(
            con,
            topic_id=topic_id,
            cluster_id="cluster_policy",
        )

        ai_defaults = get_topic_content_defaults(
            con,
            topic_id=topic_id,
            default_audience="일반 독자",
            default_purpose="정보 제공",
        )
        assert ai_defaults["source"] == "ai"
        assert ai_defaults["audience"] == ai_plan["audience"]
        assert ai_defaults["target_length"] == 2300
        assert ai_defaults["angle"] == "지원 대상과 신청 조건 중심으로 정리"
        assert "본문에 없는 내용을 제목에 넣지 않는다." in ai_defaults["title_rules"]
        assert "적용 대상과 시점을 제목에 드러낸다" in ai_defaults["title_rules"]
        assert ai_defaults["fact_check_items"] == [
            "공식 시행일",
            "적용 대상",
            "신청 조건",
            "최종 접수 마감일",
        ]
        assert ai_defaults["timeliness"]["freshness_window_hours"] == 72
        assert ai_defaults["evidence_plan"]["required_source_types"] == ["공식 시행 공고"]
        assert ai_defaults["primary_direction_reason"] == "신청 조건 중심 설명이 가장 실용적임"

        save_content_pack(
            con,
            topic_id=topic_id,
            audience="취업 준비생",
            purpose="신청 가능 여부를 빠르게 판단",
            angle="자격 조건 중심",
            category="취업",
            target_length=1900,
            title_rules="대상과 조건을 제목에 포함",
            outline="도입\n자격 조건\n신청 방법\n주의사항",
            forbidden_expressions="누구나 가능",
            fact_check_items="접수 마감일\n지원 자격",
            selected_source_item_ids=[],
            selected_reference_ids=[],
        )
        saved_defaults = get_topic_content_defaults(
            con,
            topic_id=topic_id,
            default_audience="일반 독자",
            default_purpose="정보 제공",
        )

    assert saved_defaults["source"] == "saved"
    assert saved_defaults["audience"] == "취업 준비생"
    assert saved_defaults["purpose"] == "신청 가능 여부를 빠르게 판단"
    assert saved_defaults["angle"] == "자격 조건 중심"
    assert saved_defaults["target_length"] == 1900
    assert saved_defaults["outline"] == ["도입", "자격 조건", "신청 방법", "주의사항"]
    assert saved_defaults["fact_check_items"] == ["접수 마감일", "지원 자격"]
