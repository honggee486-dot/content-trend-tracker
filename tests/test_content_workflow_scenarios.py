import json
from pathlib import Path

import pytest

from src.database import connect_database, init_database
from src.services.ai_result_parser import parse_ai_result
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
