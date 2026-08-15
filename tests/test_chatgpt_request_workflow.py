import json
from datetime import date
from pathlib import Path

from src.services.ai_result_parser import (
    parse_ai_result,
    validate_ai_result_against_references,
)
from src.services.content_pack_freshness_review_runtime import (
    build_latest_research_review_section,
    ensure_latest_research_review_prompt,
)
from src.services.content_pack_service import build_content_pack


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _build_prompt() -> str:
    pack = build_content_pack(
        {
            "title": "정책대출 결혼 페널티 변경",
            "summary": "소득 기준 개편 내용을 최신 공식 자료로 확인",
            "category": "정책",
            "memo": "",
        },
        [],
        audience="일반 독자",
        purpose="최신 정책 변경 내용을 정확히 설명",
        angle="기존 기준과 개편 기준 비교",
        category="정책",
        target_length=1800,
        title_rules="과장하지 않는다",
        outline="도입\n기존 기준\n개편 기준\n주의사항\n정리",
        forbidden_expressions="무조건",
        fact_check_items="시행일 확인\n소득 기준 확인",
    )
    return pack["prompt_text"]


def _build_v21_result_payload(*, body_url: str, source_url: str) -> dict:
    return {
        "schema_version": "2.1",
        "title": "정책대출 결혼 페널티 변경",
        "summary": "정책 변경 핵심을 정리합니다.",
        "category": "정책",
        "tags": ["정책대출", "결혼 페널티"],
        "seo": {
            "primary_keyword": "정책대출 결혼 페널티",
            "secondary_keywords": ["신혼부부 정책대출"],
            "search_intent": "정책대출 소득 기준 변경 확인",
            "meta_description": "정책대출 결혼 페널티 변경 내용을 공식 자료 기준으로 정리합니다.",
        },
        "blocks": [
            {
                "type": "paragraph",
                "text": f"금융위원회 공식 자료({body_url})*",
            }
        ],
        "fact_checks": [
            {
                "claim": "정책대출 소득 기준이 변경됩니다.",
                "status": "verified",
                "reason": "공식 자료 확인",
                "source_ids": ["R1"],
            }
        ],
        "sources": [
            {
                "id": "R1",
                "title": "부동산 시장 안정을 위한 금융 종합대책",
                "publisher": "금융위원회",
                "url": source_url,
                "published_at": "2026-08-13",
            }
        ],
    }


def test_chatgpt_request_button_copies_prompt_and_opens_chatgpt() -> None:
    source = (PROJECT_ROOT / "src" / "ui.py").read_text(encoding="utf-8")

    assert "def render_chatgpt_request_button(" in source
    assert 'href="https://chatgpt.com/"' in source
    assert 'target="_blank"' in source
    assert "navigator.clipboard.writeText(text)" in source
    assert "Ctrl+V 후 전송하세요" in source
    assert "clipboard unavailable" in source


def test_ai_request_screen_uses_manual_chatgpt_result_handoff() -> None:
    source = (PROJECT_ROOT / "app.py").read_text(encoding="utf-8")

    assert "render_chatgpt_request_button(" in source
    assert '"ChatGPT 결과 붙여넣기로 이동"' in source
    assert '"검색·사실 확인 요청서 복사"' not in source
    assert "API를 호출하지 않고 요청서를 복사해 새 탭만 엽니다." in source
    assert "입력·전송과 답변 JSON 복사는 사용자가 직접 진행하세요." in source
    handoff_section = source.split('"ChatGPT 결과 붙여넣기로 이동"', 1)[1][:600]
    assert '"AI 결과 가져오기"' in handoff_section


def test_ai_request_screen_uses_half_width_prompt_and_compact_action_columns() -> None:
    source = (PROJECT_ROOT / "src" / "ui.py").read_text(encoding="utf-8")

    assert '"ChatGPT 또는 Gemini에 그대로 붙여넣기"' in source
    assert "class _ContentPackRequestLayoutProxy:" in source
    assert "[1.0, 1.0]" in source
    assert "[0.9, 1.1]" in source
    assert 'gap="medium"' in source
    assert 'gap="small"' in source
    assert 'caller_globals["st"] = _ContentPackRequestLayoutProxy(original_streamlit)' in source


def test_copy_component_uses_safe_dom_token_for_uuid_keys() -> None:
    source = (PROJECT_ROOT / "src" / "ui.py").read_text(encoding="utf-8")

    assert "def _component_token(key: str)" in source
    assert "hashlib.sha1" in source
    assert "copyText_{safe_key}" not in source


def test_ai_request_requires_actual_response_date_and_latest_web_research() -> None:
    section = build_latest_research_review_section(
        reference_date=date(2026, 8, 14)
    )
    prompt = _build_prompt()

    assert "요청서 확인 기준일: 2026-08-14" in section
    assert "실제 답변을 작성하기 시작할 때 현재 날짜를 다시 확인" in prompt
    assert "실제 답변 시점의 날짜를 우선" in prompt
    assert "웹 검색을 반드시 수행" in prompt
    assert "기존 지식이나 자료팩만으로 최신 사실을 확정하지 않습니다" in prompt
    assert "가장 최신의 공식 자료와 1차 출처" in prompt
    assert "시행일·적용일·기준일·갱신일" in prompt


def test_saved_request_refreshes_review_date_without_mutating_database_text() -> None:
    legacy = "기존 요청서\n\n[SEO 필수 규칙]\n- 기존 규칙"
    first = ensure_latest_research_review_prompt(
        legacy,
        reference_date=date(2026, 8, 14),
    )
    refreshed = ensure_latest_research_review_prompt(
        first,
        reference_date=date(2026, 8, 15),
    )

    assert first.count("[현재 날짜·최신 검색·3중 재검증 필수]") == 1
    assert refreshed.count("[현재 날짜·최신 검색·3중 재검증 필수]") == 1
    assert "[현재 날짜·최신 검색·2중 재검증 필수]" not in refreshed
    assert "요청서 확인 기준일: 2026-08-15" in refreshed
    assert "요청서 확인 기준일: 2026-08-14" not in refreshed
    assert "[SEO 필수 규칙]" in refreshed


def test_saved_request_migrates_old_two_recheck_section_to_three_rechecks() -> None:
    legacy = (
        "기존 요청서\n\n"
        "[현재 날짜·최신 검색·2중 재검증 필수]\n"
        "- 과거 규칙\n\n"
        "[SEO 필수 규칙]\n"
        "- 기존 규칙"
    )

    refreshed = ensure_latest_research_review_prompt(
        legacy,
        reference_date=date(2026, 8, 15),
    )

    assert refreshed.count("[현재 날짜·최신 검색·3중 재검증 필수]") == 1
    assert "[현재 날짜·최신 검색·2중 재검증 필수]" not in refreshed
    assert "과거 규칙" not in refreshed
    assert "요청서 확인 기준일: 2026-08-15" in refreshed
    assert "[SEO 필수 규칙]" in refreshed


def test_ai_request_requires_three_post_draft_web_rechecks_before_final_json() -> None:
    prompt = _build_prompt()

    first_index = prompt.index("[초안 작성 후 1차 재검증 — 전체 사실 감사]")
    second_index = prompt.index("[1차 수정 후 2차 재검증 — 미확인·변경 항목 집중 검증]")
    third_index = prompt.index("[2차 수정 후 3차 재검증 — 최종 정합성·출처 감사]")
    assert first_index < second_index < third_index
    assert "초안을 한 번 완성한 뒤 추가 재검증을 반드시 세 번 수행" in prompt
    assert "초안 전체를 대상으로 웹 검색을 다시 수행" in prompt
    assert "needs_verification 후보로 남은 항목을 하나씩 별도의 검색 질문으로 다시 조사" in prompt
    assert "최종 수정본 전체를 대상으로 웹 검색을 다시 수행" in prompt
    assert "수정할 사항이 없더라도 1차 재검증을 생략하지 않습니다" in prompt
    assert "수정할 사항이 없더라도 2차 재검증을 생략하지 않습니다" in prompt
    assert "수정할 사항이 없더라도 3차 재검증을 생략하지 않습니다" in prompt
    assert "세 번의 추가 재검증과 필요한 사실·문체 수정 반영을 모두 끝내기 전에는 최종 JSON을 출력하지 않습니다" in prompt
    assert "최초 조사와 1차·2차·3차 재검증을 모두 수행했음에도" in prompt
    assert "추측해서 verified로 바꾸지 않습니다" in prompt


def test_ai_request_rechecks_natural_human_editing_quality_without_fake_errors() -> None:
    prompt = _build_prompt()

    assert "[자연스러운 인간 편집 품질 규칙]" in prompt
    assert "[초안 작성 직후 자연스러운 인간 편집 품질 점검]" in prompt
    assert "[1차 자연스러움 재검토]" in prompt
    assert "[2차 자연스러움 재검토]" in prompt
    assert "[3차 최종 인간 편집 품질 점검]" in prompt
    assert "문장 길이와 문단 길이를 획일적으로 맞추지 않고" in prompt
    assert "모든 소제목을 같은 `설명 → 목록 → 정리` 구조로 반복하지 않고" in prompt
    assert "고의적인 오타, 띄어쓰기 오류, 맞춤법 오류, 문장부호 오류나 사실 오류를 만들지 않습니다" in prompt
    assert "개인 경험, 직접 사용 후기, 전문성, 인터뷰, 감정이나 의견을 실제 근거 없이 만들어 사람인 것처럼 꾸미지 않습니다" in prompt
    assert "사실·수치·출처를 바꾸지 않는 범위" in prompt


def test_ai_request_forbids_ui_citation_markers_in_final_json() -> None:
    prompt = _build_prompt()

    assert "[최종 JSON 인용·출력 정리 규칙]" in prompt
    assert "`contentReference`" in prompt
    assert "`oaicite`" in prompt
    assert "내부 citation token" in prompt
    assert "sources의 S1, S2, R1, R2 형식 ID와 fact_checks.source_ids만 사용" in prompt
    assert "UI 내부 인용 마커를 S/R 출처 ID로 임의 변환하거나 추측하지 않습니다" in prompt


def test_ai_result_validation_accepts_markdown_wrapped_researched_url() -> None:
    source_url = (
        "https://www.fsc.go.kr/comm/getFile?fileNo=3&fileTy=ATTACH"
        "&srvcld=BBSTY1&upperNo=87517"
    )
    payload = _build_v21_result_payload(body_url=source_url, source_url=source_url)

    parsed = parse_ai_result(json.dumps(payload, ensure_ascii=False))
    checked = validate_ai_result_against_references(parsed, [])

    assert parsed.is_valid
    assert checked.is_valid
    assert checked.data is not None
    assert checked.data["body_markdown"].endswith(f"({source_url})*")


def test_ai_result_validation_still_rejects_unknown_markdown_wrapped_url() -> None:
    source_url = (
        "https://www.fsc.go.kr/comm/getFile?fileNo=3&fileTy=ATTACH"
        "&srvcld=BBSTY1&upperNo=87517"
    )
    unknown_url = "https://invented.example/fake"
    payload = _build_v21_result_payload(body_url=unknown_url, source_url=source_url)

    parsed = parse_ai_result(json.dumps(payload, ensure_ascii=False))
    checked = validate_ai_result_against_references(parsed, [])

    assert parsed.is_valid
    assert not checked.is_valid
    assert any(unknown_url in error for error in checked.errors)
