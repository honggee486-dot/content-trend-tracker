import json
from pathlib import Path

from src.database import connect_database, init_database
from src.services.ai_result_parser import parse_ai_result
from src.services.content_pack_service import save_content_pack
from src.services.draft_service import get_draft, save_generation_and_draft
from src.services.topic_service import add_manual_topic


def test_end_to_end_pack_to_draft(tmp_path: Path) -> None:
    db_path = tmp_path / "main.duckdb"
    init_database(db_path)
    with connect_database(db_path) as con:
        topic_id, _ = add_manual_topic(con, title="메인 주제", summary="설명")
        pack = save_content_pack(
            con,
            topic_id=topic_id,
            audience="일반 독자",
            purpose="정보 제공",
            angle="핵심 정리",
            category="정보",
            target_length=1200,
            title_rules="과장 금지",
            outline="도입\n핵심\n정리",
            forbidden_expressions="무조건",
            fact_check_items="수치 확인",
        )
        payload = {
            "schema_version": "1.0",
            "title": "완성 제목",
            "summary": "요약",
            "category": "정보",
            "tags": ["메인", "주제"],
            "body_markdown": "# 완성 제목\n\n" + ("본문 내용입니다. " * 40),
            "fact_checks": [],
            "sources": [],
            "image_prompts": [],
        }
        raw = json.dumps(payload, ensure_ascii=False)
        parsed = parse_ai_result(raw)
        _, draft_id = save_generation_and_draft(
            con,
            content_pack_id=pack["content_pack_id"],
            ai_provider="ChatGPT",
            raw_response=raw,
            result=parsed,
        )
        draft = get_draft(con, draft_id)
        assert draft is not None
        assert draft["title"] == "완성 제목"
        assert draft["tags"] == ["메인", "주제"]
        assert "<h1>" not in draft["body_html"]
        assert draft["body_markdown"].startswith("본문 내용입니다.")


def test_same_validated_ai_response_is_saved_only_once(tmp_path: Path) -> None:
    db_path = tmp_path / "main.duckdb"
    init_database(db_path)
    with connect_database(db_path) as con:
        topic_id, _ = add_manual_topic(con, title="중복 저장 방지")
        pack = save_content_pack(
            con,
            topic_id=topic_id,
            audience="일반 독자",
            purpose="정보 제공",
            angle="핵심 정리",
            category="정보",
            target_length=1200,
            title_rules="과장 금지",
            outline="도입\n핵심\n정리",
            forbidden_expressions="무조건",
            fact_check_items="수치 확인",
        )
        payload = {
            "schema_version": "1.0",
            "title": "같은 결과",
            "summary": "요약",
            "category": "정보",
            "tags": ["중복"],
            "body_markdown": "# 같은 결과\n\n" + ("본문입니다. " * 50),
            "fact_checks": [],
            "sources": [],
            "image_prompts": [],
        }
        raw = json.dumps(payload, ensure_ascii=False)
        parsed = parse_ai_result(raw)
        first = save_generation_and_draft(
            con,
            content_pack_id=pack["content_pack_id"],
            ai_provider="ChatGPT",
            raw_response=raw,
            result=parsed,
        )
        second = save_generation_and_draft(
            con,
            content_pack_id=pack["content_pack_id"],
            ai_provider="ChatGPT",
            raw_response=raw,
            result=parsed,
        )
        assert first == second
        assert con.execute("SELECT COUNT(*) FROM generation_sessions").fetchone()[0] == 1
        assert con.execute("SELECT COUNT(*) FROM drafts").fetchone()[0] == 1

def test_schema_v2_blocks_are_saved_through_legacy_draft_fields(tmp_path: Path) -> None:
    db_path = tmp_path / "main.duckdb"
    init_database(db_path)
    with connect_database(db_path) as con:
        topic_id, _ = add_manual_topic(con, title="블록형 주제")
        pack = save_content_pack(
            con,
            topic_id=topic_id,
            audience="일반 독자",
            purpose="정보 제공",
            angle="핵심 정리",
            category="정보",
            target_length=1200,
            title_rules="과장 금지",
            outline="도입\n핵심\n정리",
            forbidden_expressions="무조건",
            fact_check_items="수치 확인",
        )
        payload = {
            "schema_version": "2.0",
            "title": "블록형 완성 제목",
            "summary": "요약",
            "category": "정보",
            "tags": ["블록"],
            "blocks": [
                {
                    "type": "heading",
                    "level": 1,
                    "text": "블록형 완성 제목",
                },
                {
                    "type": "paragraph",
                    "text": "본문 내용입니다. " * 40,
                },
                {
                    "type": "image",
                    "position": "본문 뒤",
                    "purpose": "설명",
                    "prompt": "설명 이미지",
                    "aspect_ratio": "16:9",
                    "caption": "본문 설명",
                    "alt_text": "본문을 설명하는 이미지",
                },
            ],
            "fact_checks": [],
            "sources": [],
        }
        raw = json.dumps(payload, ensure_ascii=False)
        parsed = parse_ai_result(raw)
        assert parsed.is_valid
        generation_id, draft_id = save_generation_and_draft(
            con,
            content_pack_id=pack["content_pack_id"],
            ai_provider="ChatGPT",
            raw_response=raw,
            result=parsed,
        )
        draft = get_draft(con, draft_id)
        parsed_json = json.loads(
            con.execute(
                "SELECT parsed_json FROM generation_sessions WHERE generation_id = ?",
                [generation_id],
            ).fetchone()[0]
        )

    assert draft is not None
    assert draft["body_markdown"].startswith("본문 내용입니다.")
    assert "# 블록형 완성 제목" not in draft["body_markdown"]
    assert "[이미지 1 삽입 위치]" in draft["body_markdown"]
    assert draft["image_prompts"][0]["alt_text"] == "본문을 설명하는 이미지"
    assert parsed_json["schema_version"] == "2.0"
    assert parsed_json["blocks"][0]["type"] == "heading"
