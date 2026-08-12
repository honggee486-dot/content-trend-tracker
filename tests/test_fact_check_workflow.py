import json
from pathlib import Path

import pytest

from src.database import connect_database, init_database
from src.services.ai_result_parser import parse_ai_result
from src.services.content_pack_service import save_content_pack
from src.services.draft_service import (
    get_fact_check_summary,
    get_fact_checks,
    save_generation_and_draft,
    update_fact_check,
)
from src.services.publish_service import mark_published
from src.services.topic_service import add_manual_topic


def _create_draft_with_fact_check(tmp_path: Path) -> tuple[Path, str, str]:
    db_path = tmp_path / "main.duckdb"
    init_database(db_path)
    with connect_database(db_path) as con:
        topic_id, _ = add_manual_topic(con, title="검증 주제", summary="설명")
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
            fact_check_items="가격 확인",
        )
        payload = {
            "schema_version": "1.0",
            "title": "검증할 글",
            "summary": "요약",
            "category": "정보",
            "tags": ["검증"],
            "body_markdown": "# 검증할 글\n\n" + ("가격은 10,000원입니다. " * 30),
            "fact_checks": [
                {
                    "claim": "가격은 10,000원이다.",
                    "status": "verified",
                    "reason": "최신 가격 확인 필요",
                    "source_ids": [],
                }
            ],
            "sources": [],
            "image_prompts": [],
        }
        raw = json.dumps(payload, ensure_ascii=False)
        result = parse_ai_result(raw)
        _, draft_id = save_generation_and_draft(
            con,
            content_pack_id=pack["content_pack_id"],
            ai_provider="ChatGPT",
            raw_response=raw,
            result=result,
        )
        fact_check_id = get_fact_checks(con, draft_id)[0]["fact_check_id"]
    return db_path, draft_id, fact_check_id


def test_ai_fact_check_starts_unverified_and_can_be_verified(tmp_path: Path) -> None:
    db_path, draft_id, fact_check_id = _create_draft_with_fact_check(tmp_path)
    with connect_database(db_path) as con:
        checks = get_fact_checks(con, draft_id)
        assert checks[0]["check_status"] == "needs_verification"
        assert get_fact_check_summary(con, draft_id)["unresolved"] == 1

        with pytest.raises(ValueError, match="확인 메모나 근거 URL"):
            update_fact_check(
                con,
                fact_check_id=fact_check_id,
                check_status="verified",
                evidence="",
                source_url="",
            )

        update_fact_check(
            con,
            fact_check_id=fact_check_id,
            check_status="verified",
            evidence="공식 가격표에서 확인",
            source_url="https://example.com/official-price",
        )
        summary = get_fact_check_summary(con, draft_id)
        assert summary["verified"] == 1
        assert summary["unresolved"] == 0
        topic_status = con.execute(
            "SELECT t.status FROM topics t JOIN drafts d ON d.topic_id = t.topic_id WHERE d.draft_id = ?",
            [draft_id],
        ).fetchone()[0]
        assert topic_status == "publish_ready"


def test_publish_requires_acknowledgement_for_unresolved_checks(tmp_path: Path) -> None:
    db_path, draft_id, _ = _create_draft_with_fact_check(tmp_path)
    with connect_database(db_path) as con:
        with pytest.raises(ValueError, match="사실 확인 항목"):
            mark_published(
                con,
                draft_id=draft_id,
                platform="네이버 블로그",
                write_url="https://blog.naver.com/write",
                published_url="",
            )

        publish_id = mark_published(
            con,
            draft_id=draft_id,
            platform="네이버 블로그",
            write_url="https://blog.naver.com/write",
            published_url="",
            allow_unverified=True,
        )
        assert publish_id.startswith("pub_")


def test_duplicate_publish_record_is_not_created(tmp_path: Path) -> None:
    db_path, draft_id, fact_check_id = _create_draft_with_fact_check(tmp_path)
    with connect_database(db_path) as con:
        update_fact_check(
            con,
            fact_check_id=fact_check_id,
            check_status="verified",
            evidence="공식 자료에서 확인",
            source_url="https://example.com/official",
        )
        first = mark_published(
            con,
            draft_id=draft_id,
            platform="네이버 블로그",
            write_url="https://blog.naver.com/write",
            published_url="https://blog.naver.com/example/1",
            blog_profile_id="blog_naver_default",
        )
        second = mark_published(
            con,
            draft_id=draft_id,
            platform="네이버 블로그",
            write_url="https://blog.naver.com/write",
            published_url="https://blog.naver.com/example/1",
            blog_profile_id="blog_naver_default",
        )
        assert first == second
        assert con.execute("SELECT COUNT(*) FROM publish_records").fetchone()[0] == 1

        third = mark_published(
            con,
            draft_id=draft_id,
            platform="네이버 블로그",
            write_url="https://blog.naver.com/write",
            published_url="https://blog.naver.com/example/1",
            blog_profile_id="another_naver_profile",
        )
        assert third != first
        assert con.execute("SELECT COUNT(*) FROM publish_records").fetchone()[0] == 2
