from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]


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


def test_copy_component_uses_safe_dom_token_for_uuid_keys() -> None:
    source = (PROJECT_ROOT / "src" / "ui.py").read_text(encoding="utf-8")

    assert "def _component_token(key: str)" in source
    assert "hashlib.sha1" in source
    assert "copyText_{safe_key}" not in source
