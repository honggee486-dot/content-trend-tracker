import json
from pathlib import Path

import pytest

from src.database import connect_database, init_database
from src.services.ai_result_parser import parse_ai_result
from src.services.content_pack_image_acquisition_service import build_image_acquisition_plans
from src.services.content_pack_service import list_content_packs, save_quick_content_pack
from src.services.draft_service import get_draft, save_generation_and_draft, update_draft
from src.services.publish_service import mark_published


SCENARIOS = [
    ("7월 16일 캐시워크 정답", "문제별 정답을 빠르게 정리"),
    ("윈도우 업데이트 후 블루투스 끊김 해결", "증상과 해결 순서를 정리"),
    ("아이폰과 갤럭시 카메라 비교", "차이와 상황별 선택 기준을 정리"),
    ("신제품 출시 일정과 변경 사항", "일정과 달라진 내용을 구분"),
    ("무선 이어폰 실사용 후기", "반복되는 장점과 불편을 근거별로 정리"),
]


@pytest.mark.parametrize(("topic_title", "angle"), SCENARIOS)
def test_representative_manual_workflow_reaches_publish_record(
    tmp_path: Path,
    topic_title: str,
    angle: str,
) -> None:
    db_path = tmp_path / "main.duckdb"
    init_database(db_path)

    with connect_database(db_path) as con:
        pack = save_quick_content_pack(
            con,
            topic_title=topic_title,
            topic_summary="실전 흐름 검증용 주제",
            topic_category="정보",
            topic_memo="",
            audience="일반 독자",
            purpose="정보 제공",
            angle=angle,
            category="정보",
            target_length=1200,
            title_rules="과장하지 않는다",
            outline="핵심 답변\n근거 정리\n주의사항",
            forbidden_expressions="무조건",
            fact_check_items="구체적인 날짜와 수치 확인",
        )
        assert list_content_packs(con)[0]["content_pack_id"] == pack["content_pack_id"]

        payload = {
            "schema_version": "1.0",
            "title": topic_title,
            "summary": "핵심 내용을 정리한 초안",
            "category": "정보",
            "tags": ["정보", "정리"],
            "body_markdown": f"# {topic_title}\n\n" + ("확인된 범위 안에서 핵심 내용을 정리합니다. " * 35),
            "fact_checks": [],
            "sources": [],
            "image_prompts": [],
        }
        raw = json.dumps(payload, ensure_ascii=False)
        result = parse_ai_result(raw)
        assert result.is_valid

        _, draft_id = save_generation_and_draft(
            con,
            content_pack_id=pack["content_pack_id"],
            ai_provider="ChatGPT",
            raw_response=raw,
            result=result,
        )
        draft = get_draft(con, draft_id)
        assert draft is not None

        revision = update_draft(
            con,
            draft_id=draft_id,
            title=draft["title"],
            summary=draft["summary"],
            category=draft["category"],
            tags=draft["tags"],
            body_markdown=draft["body_markdown"] + "\n\n최종 검토를 완료했습니다.",
            create_revision=True,
            change_note="실전 흐름 검증",
        )
        assert revision == 2

        publish_id = mark_published(
            con,
            draft_id=draft_id,
            platform="네이버 블로그",
            write_url="https://blog.naver.com/write",
            published_url=f"https://blog.naver.com/example/{draft_id}",
            blog_profile_id="blog_naver_default",
        )
        assert publish_id.startswith("pub_")
        assert con.execute(
            "SELECT blog_profile_id FROM publish_records WHERE publish_id = ?",
            [publish_id],
        ).fetchone()[0] == "blog_naver_default"
        assert con.execute(
            "SELECT status FROM topics WHERE topic_id = ?",
            [pack["topic_id"]],
        ).fetchone()[0] == "published"


def _official_capture_block(*, url: str = "https://www.example.go.kr/policy", anchor: str = "신청기간") -> dict:
    return {
        "type": "image",
        "position": "신청 방법 설명 뒤",
        "purpose": "공식 신청 조건을 실제 화면으로 보여준다",
        "image_strategy": "official_capture",
        "source_capture": {
            "needed": True,
            "source_id": "R1",
            "source_url": url,
            "capture_target": "신청기간과 지원 대상이 함께 보이는 안내 영역",
            "capture_anchor": anchor,
            "capture_note": "페이지 제목과 기준일을 포함하고 개인정보·로그인 영역은 제외",
            "checked_at": "2026-08-22",
        },
        "user_action": "",
        "free_image": {
            "status": "not_found",
            "search_query": "official policy capture fallback",
            "page_url": "",
            "provider": "",
            "creator": "",
            "license_name": "",
            "license_url": "",
            "attribution": "",
            "checked_at": "2026-08-22",
            "commercial_use_allowed": False,
            "payment_required": False,
            "premium_or_subscription_required": False,
            "editorial_only": False,
            "verification_note": "공식 화면 캡처 우선",
        },
        "prompt": "공식 페이지 캡처가 부적절할 때만 사용할 중립적 설명 이미지",
        "aspect_ratio": "16:9",
        "caption": "공식 안내 페이지의 신청 조건",
        "alt_text": "신청기간과 지원 대상이 표시된 공식 안내 화면",
    }


def _schema_21_with_capture(block: dict) -> dict:
    source_url = block["source_capture"]["source_url"]
    return {
        "schema_version": "2.1",
        "title": "지원 정책 신청 방법",
        "summary": "공식 안내 페이지를 기준으로 신청 절차를 정리합니다.",
        "category": "정책",
        "tags": ["지원정책", "신청방법"],
        "seo": {
            "primary_keyword": "지원 정책 신청 방법",
            "secondary_keywords": ["지원 대상", "신청 기간"],
            "search_intent": "지원 정책 신청 조건과 절차를 확인한다",
            "meta_description": "공식 안내 페이지를 기준으로 지원 대상과 신청 방법을 확인합니다.",
        },
        "blocks": [
            {
                "type": "paragraph",
                "text": (
                    "지원 정책은 신청 시점의 공식 안내를 기준으로 대상과 기간을 확인해야 합니다. "
                    "본문에서는 신청 절차와 주의할 조건을 구분해 설명하고, 독자가 원문을 다시 확인할 수 있도록 공식 링크를 함께 제공합니다. "
                    "정확한 날짜와 대상 조건은 공식 페이지의 현재 표시를 근거로 확인합니다."
                ),
            },
            block,
        ],
        "fact_checks": [
            {
                "claim": "지원 대상과 신청 기간은 공식 안내 페이지 기준이다",
                "status": "verified",
                "reason": "공식 안내 페이지를 확인함",
                "source_ids": ["R1"],
            }
        ],
        "sources": [
            {
                "id": "R1",
                "title": "공식 지원 정책 안내",
                "publisher": "Example Government",
                "url": source_url,
                "published_at": "2026-08-22",
            }
        ],
    }


def test_public_official_capture_becomes_automatic_isolated_browser_plan() -> None:
    payload = _schema_21_with_capture(_official_capture_block())
    result = parse_ai_result(json.dumps(payload, ensure_ascii=False))

    assert result.is_valid, result.errors
    assert result.data is not None
    image = result.data["blocks"][1]
    plan = image["image_acquisition"]
    assert plan["strategy"] == "official_capture"
    assert plan["status"] == "ready"
    assert plan["action"] == "capture_public_source"
    assert plan["capture_anchor"] == "신청기간"
    assert plan["use_isolated_unauthenticated_browser"] is True
    assert plan["allow_login"] is False
    assert plan["allow_cookie_import"] is False
    assert plan["allow_captcha_bypass"] is False
    assert "*내가 할 일:" not in result.data["body_markdown"]


def test_official_capture_without_anchor_stays_compatible_but_requires_review() -> None:
    payload = _schema_21_with_capture(_official_capture_block(anchor=""))
    result = parse_ai_result(json.dumps(payload, ensure_ascii=False))

    assert result.is_valid, result.errors
    assert result.data is not None
    plan = result.data["blocks"][1]["image_acquisition"]
    assert plan["status"] == "needs_review"
    assert plan["action"] == "manual_review"
    assert "capture_anchor" in plan["reason"]
    assert any("자동 캡처 준비 미완료" in warning for warning in result.warnings)
    assert "*내가 할 일:" in result.data["body_markdown"]


def test_private_or_login_capture_url_is_never_automatic() -> None:
    url = "http://127.0.0.1/login"
    plan = build_image_acquisition_plans(
        {
            "sources": [{"id": "R1", "url": url}],
            "blocks": [_official_capture_block(url=url)],
        }
    )[0]

    assert plan["status"] == "needs_review"
    assert plan["action"] == "manual_review"
    assert plan["allow_login"] is False
    assert plan["allow_cookie_import"] is False


def test_article_can_route_official_capture_and_generated_images_in_order() -> None:
    generated = {
        "type": "image",
        "position": "마무리 설명 뒤",
        "purpose": "개념을 쉽게 보여준다",
        "image_strategy": "generated",
        "prompt": "clean editorial illustration with no text or logo",
        "caption": "개념 설명 이미지",
        "alt_text": "정책 절차를 단순화한 설명 이미지",
    }
    data = {
        "sources": [{"id": "R1", "url": "https://www.example.go.kr/policy"}],
        "blocks": [_official_capture_block(), generated],
    }

    plans = build_image_acquisition_plans(data)

    assert [plan["strategy"] for plan in plans] == ["official_capture", "generated"]
    assert [plan["action"] for plan in plans] == ["capture_public_source", "generate_zero_cost_image"]
    assert all(plan["zero_cost_only"] for plan in plans)


def test_image_acquisition_plan_is_preserved_when_valid_result_becomes_draft(tmp_path: Path) -> None:
    db_path = tmp_path / "image-plan.duckdb"
    init_database(db_path)
    payload = _schema_21_with_capture(_official_capture_block())
    raw = json.dumps(payload, ensure_ascii=False)
    result = parse_ai_result(raw)
    assert result.is_valid, result.errors

    with connect_database(db_path) as con:
        pack = save_quick_content_pack(
            con,
            topic_title="지원 정책 신청 방법",
            topic_summary="자동 이미지 획득 계획 저장 검증",
            topic_category="정책",
            topic_memo="",
            audience="일반 독자",
            purpose="정보 제공",
            angle="공식 근거 중심",
            category="정책",
            target_length=1200,
            title_rules="과장하지 않는다",
            outline="대상\n기간\n신청방법",
            forbidden_expressions="무조건",
            fact_check_items="지원 대상과 기간",
        )
        _, draft_id = save_generation_and_draft(
            con,
            content_pack_id=pack["content_pack_id"],
            ai_provider="automatic-test",
            raw_response=raw,
            result=result,
        )
        draft = get_draft(con, draft_id)

    assert draft is not None
    assert draft["image_prompts"][0]["image_acquisition"]["status"] == "ready"
    assert draft["image_prompts"][0]["image_acquisition"]["action"] == "capture_public_source"
