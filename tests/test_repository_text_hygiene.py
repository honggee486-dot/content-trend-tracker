from __future__ import annotations

from pathlib import Path

from scripts.check_text_hygiene import (
    PROJECT_ROOT,
    check_text_bytes,
    check_text_files,
    tracked_text_files,
)


def test_text_hygiene_checker_detects_eof_and_line_whitespace() -> None:
    issues = check_text_bytes(
        b"first line  \nsecond line\n\n",
        display_path="sample.py",
    )

    assert [(issue.code, issue.line_number) for issue in issues] == [
        ("trailing-whitespace", 1),
        ("extra-blank-line-at-eof", None),
    ]


def test_text_hygiene_checker_detects_missing_newline_and_invalid_utf8() -> None:
    missing_newline = check_text_bytes(
        b"print('ok')",
        display_path="missing.py",
    )
    invalid_utf8 = check_text_bytes(
        b"\xff\xfe",
        display_path="invalid.py",
    )

    assert [issue.code for issue in missing_newline] == ["missing-final-newline"]
    assert [issue.code for issue in invalid_utf8] == ["invalid-utf8"]


def test_tracked_repository_source_files_have_clean_text_endings() -> None:
    paths = tracked_text_files(PROJECT_ROOT)

    assert PROJECT_ROOT / "app.py" in paths
    assert PROJECT_ROOT / "scripts" / "check_text_hygiene.py" in paths
    assert PROJECT_ROOT / "tests" / "test_repository_text_hygiene.py" in paths

    issues = check_text_files(paths, root=PROJECT_ROOT)
    assert not issues, "\n".join(issue.display() for issue in issues)


def test_checker_accepts_crlf_with_exactly_one_final_newline(tmp_path: Path) -> None:
    path = tmp_path / "windows.py"
    path.write_bytes(b"line one\r\nline two\r\n")

    assert check_text_files([path], root=tmp_path) == []
