from __future__ import annotations

import re
from typing import Any, Iterable

HEADING_PATTERN = re.compile(r"^\s{0,3}#{1,6}\s+(.+?)\s*#*\s*$")
UNORDERED_LIST_PATTERN = re.compile(r"^(\s*)[-*+]\s+(.+)$")
ORDERED_LIST_PATTERN = re.compile(r"^(\s*)\d+[.)]\s+(.+)$")
BLOCKQUOTE_PATTERN = re.compile(r"^\s*>\s?(.*)$")
HORIZONTAL_RULE_PATTERN = re.compile(r"^\s{0,3}(?:[-*_]\s*){3,}$")
IMAGE_PATTERN = re.compile(r"!\[([^\]]*)\]\(([^)]+)\)")
LINK_PATTERN = re.compile(r"\[([^\]]+)\]\(([^)]+)\)")
INLINE_CODE_PATTERN = re.compile(r"`([^`]+)`")
STRONG_PATTERN = re.compile(r"(\*\*|__)(.*?)\1")
EMPHASIS_PATTERN = re.compile(r"(?<!\*)\*([^*\n]+)\*(?!\*)|(?<!_)_([^_\n]+)_(?!_)")
STRIKETHROUGH_PATTERN = re.compile(r"~~(.*?)~~")


def _normalize_title(value: Any) -> str:
    text = str(value or "").strip()
    match = HEADING_PATTERN.match(text)
    if match:
        text = match.group(1)
    text = text.strip("*_`~ \t")
    return re.sub(r"\s+", " ", text).casefold()


def _collapse_blank_lines(lines: Iterable[str]) -> str:
    result: list[str] = []
    previous_blank = False
    for raw_line in lines:
        line = str(raw_line).rstrip()
        is_blank = not line.strip()
        if is_blank and previous_blank:
            continue
        result.append("" if is_blank else line)
        previous_blank = is_blank
    return "\n".join(result).strip()


def strip_duplicate_leading_title(title: str, body_markdown: str) -> str:
    """Remove only a leading body line that duplicates the separately copied title."""
    body = str(body_markdown or "").replace("\r\n", "\n").replace("\r", "\n")
    lines = body.split("\n")
    first_index = next((index for index, line in enumerate(lines) if line.strip()), None)
    if first_index is None:
        return ""

    first_line = lines[first_index].strip()
    heading_match = HEADING_PATTERN.match(first_line)
    candidate = heading_match.group(1) if heading_match else first_line
    if _normalize_title(candidate) != _normalize_title(title):
        return body.strip()

    remaining = lines[:first_index] + lines[first_index + 1 :]
    while remaining and not remaining[0].strip():
        remaining.pop(0)
    return "\n".join(remaining).strip()


def render_markdown_body(title: str, body_markdown: str) -> str:
    """Return Markdown suitable for platforms that preserve Markdown formatting."""
    return strip_duplicate_leading_title(title, body_markdown)


def _plain_inline(text: str) -> str:
    def replace_image(match: re.Match[str]) -> str:
        alt = match.group(1).strip()
        return f"[이미지: {alt}]" if alt else "[이미지]"

    def replace_link(match: re.Match[str]) -> str:
        label = match.group(1).strip()
        url = match.group(2).strip()
        if not label:
            return url
        if label == url:
            return url
        return f"{label} ({url})"

    value = IMAGE_PATTERN.sub(replace_image, text)
    value = LINK_PATTERN.sub(replace_link, value)
    value = INLINE_CODE_PATTERN.sub(r"\1", value)
    value = STRONG_PATTERN.sub(r"\2", value)
    value = STRIKETHROUGH_PATTERN.sub(r"\1", value)

    def replace_emphasis(match: re.Match[str]) -> str:
        return match.group(1) or match.group(2) or ""

    value = EMPHASIS_PATTERN.sub(replace_emphasis, value)
    return value.strip()


def markdown_to_plain_text(markdown_text: str) -> str:
    """Convert the supported draft Markdown subset into copy-friendly plain text."""
    text = str(markdown_text or "").replace("\r\n", "\n").replace("\r", "\n")
    output: list[str] = []
    in_fence = False

    for raw_line in text.split("\n"):
        stripped = raw_line.strip()
        if stripped.startswith("```"):
            in_fence = not in_fence
            continue
        if HORIZONTAL_RULE_PATTERN.match(raw_line):
            continue

        heading_match = HEADING_PATTERN.match(raw_line)
        if heading_match:
            output.append(_plain_inline(heading_match.group(1)))
            continue

        unordered_match = UNORDERED_LIST_PATTERN.match(raw_line)
        if unordered_match:
            indent = unordered_match.group(1)
            output.append(f"{indent}• {_plain_inline(unordered_match.group(2))}")
            continue

        ordered_match = ORDERED_LIST_PATTERN.match(raw_line)
        if ordered_match:
            indent = ordered_match.group(1)
            number_match = re.match(r"^\s*(\d+)", raw_line)
            number = number_match.group(1) if number_match else "1"
            output.append(f"{indent}{number}. {_plain_inline(ordered_match.group(2))}")
            continue

        quote_match = BLOCKQUOTE_PATTERN.match(raw_line)
        if quote_match:
            output.append(_plain_inline(quote_match.group(1)))
            continue

        if in_fence:
            output.append(raw_line.rstrip())
        else:
            output.append(_plain_inline(raw_line) if stripped else "")

    return _collapse_blank_lines(output)


def render_body_for_output(
    *,
    title: str,
    body_markdown: str,
    output_format: str,
) -> str:
    clean_format = str(output_format or "").strip()
    markdown_body = render_markdown_body(title, body_markdown)
    if clean_format == "markdown":
        return markdown_body
    if clean_format == "plain_text":
        return markdown_to_plain_text(markdown_body)
    raise ValueError(f"지원하지 않는 출력 형식입니다: {clean_format or '(비어 있음)'}")


def build_full_output_text(
    draft: dict[str, Any],
    *,
    output_format: str,
    tags: Iterable[str] | None = None,
) -> str:
    body = render_body_for_output(
        title=str(draft.get("title") or ""),
        body_markdown=str(draft.get("body_markdown") or ""),
        output_format=output_format,
    )
    tag_values = list(tags) if tags is not None else list(draft.get("tags") or [])
    normalized_tags: list[str] = []
    seen: set[str] = set()
    for tag in tag_values:
        clean = str(tag or "").strip().lstrip("#")
        folded = clean.casefold()
        if not clean or folded in seen:
            continue
        seen.add(folded)
        normalized_tags.append(clean)

    parts = [str(draft.get("title") or "").strip(), "", body]
    if normalized_tags:
        parts.extend(["", " ".join(f"#{tag}" for tag in normalized_tags)])
    return "\n".join(parts).strip()
