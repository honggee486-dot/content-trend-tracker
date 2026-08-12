from src.services.blog_output_service import (
    build_full_output_text,
    markdown_to_plain_text,
    render_body_for_output,
    strip_duplicate_leading_title,
)


def test_strip_duplicate_leading_markdown_title() -> None:
    body = "# 같은 제목\n\n첫 문단입니다."
    assert strip_duplicate_leading_title("같은 제목", body) == "첫 문단입니다."


def test_keep_different_leading_heading() -> None:
    body = "## 핵심 내용\n\n첫 문단입니다."
    assert strip_duplicate_leading_title("글 제목", body) == body


def test_markdown_output_keeps_formatting_and_image_placeholder() -> None:
    body = """# 글 제목

## 핵심 내용

- 첫째
- 둘째

[이미지 1 삽입 위치]

*캡션: 설명 이미지*"""
    result = render_body_for_output(
        title="글 제목",
        body_markdown=body,
        output_format="markdown",
    )
    assert result.startswith("## 핵심 내용")
    assert "- 첫째" in result
    assert "[이미지 1 삽입 위치]" in result
    assert "# 글 제목" not in result


def test_plain_text_output_removes_markdown_marks() -> None:
    body = """# 글 제목

## 핵심 내용

- **첫째**
- [둘째](https://example.com)

> 중요한 인용문

[이미지 1 삽입 위치]"""
    result = render_body_for_output(
        title="글 제목",
        body_markdown=body,
        output_format="plain_text",
    )
    assert result.startswith("핵심 내용")
    assert "• 첫째" in result
    assert "• 둘째 (https://example.com)" in result
    assert "중요한 인용문" in result
    assert "[이미지 1 삽입 위치]" in result
    assert "**" not in result
    assert "# 글 제목" not in result


def test_plain_text_converter_preserves_numbered_list() -> None:
    result = markdown_to_plain_text("1. 첫 단계\n2. 두 번째 단계")
    assert result == "1. 첫 단계\n2. 두 번째 단계"


def test_full_output_uses_selected_format_and_combined_tags() -> None:
    draft = {
        "title": "글 제목",
        "body_markdown": "# 글 제목\n\n## 내용\n\n**본문**",
        "tags": ["기존"],
    }
    result = build_full_output_text(
        draft,
        output_format="plain_text",
        tags=["기존", "#추가", "기존"],
    )
    assert result == "글 제목\n\n내용\n\n본문\n\n#기존 #추가"
