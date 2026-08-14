from datetime import date
from pathlib import Path

from src.services.content_pack_freshness_review_runtime import (
    build_latest_research_review_section,
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
    assert "caller_globals[\"st\"] = _ContentPackRequestLayoutProxy(original_streamlit)" in source


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

    assert "요청서 생성 기준일: 2026-08-14" in section
    assert "실제 답변을 작성하기 시작할 때 현재 날짜를 다시 확인" in prompt
    assert "실제 답변 시점의 날짜를 우선" in prompt
    assert "웹 검색을 반드시 수행" in prompt
    assert "기존 지식이나 자료팩만으로 최신 사실을 확정하지 않습니다" in prompt
    assert "가장 최신의 공식 자료와 1차 출처" in prompt
    assert "시행일·적용일·기준일·갱신일" in prompt


def test_ai_request_requires_two_post_draft_web_rechecks_before_final_json() -> None:
    prompt = _build_prompt()

    first_index = prompt.index("[초안 작성 후 1차 재검증]")
    second_index = prompt.index("[1차 수정 후 2차 재검증]")
    assert first_index < second_index
    assert "초안을 한 번 완성한 뒤 추가 재검증을 반드시 두 번 수행" in prompt
    assert "초안 전체를 대상으로 웹 검색을 다시 수행" in prompt
    assert "1차 재검증 결과가 반영된 수정본 전체를 대상으로 웹 검색을 다시 수행" in prompt
    assert "수정할 사항이 없더라도 1차 재검증을 생략하지 않습니다" in prompt
    assert "수정할 사항이 없더라도 2차 재검증을 생략하지 않습니다" in prompt
    assert "두 번의 추가 재검증과 필요한 수정 반영을 모두 끝내기 전에는 최종 JSON을 출력하지 않습니다" in prompt
    assert "needs_verification" in prompt
