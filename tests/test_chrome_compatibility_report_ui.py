from __future__ import annotations

from pathlib import Path

from src import chrome_compatibility_report_ui as ui


def test_field_rows_keep_only_review_metadata() -> None:
    rows = ui._field_rows(
        {
            "fields": [
                {
                    "field_name": "title",
                    "label": "제목",
                    "status": "입력 성공",
                    "selector": "#post-title-inp",
                    "frame_path": "top",
                    "tag_name": "input",
                    "contenteditable": False,
                },
                {
                    "field_name": "body",
                    "label": "본문",
                    "status": "후보만 발견",
                    "selector": ".ProseMirror",
                    "frame_path": "top/frame[0]",
                    "tag_name": "div",
                    "contenteditable": True,
                },
            ]
        }
    )

    assert rows == [
        {
            "항목": "제목",
            "상태": "입력 성공",
            "선택자": "#post-title-inp",
            "iframe 위치": "top",
            "태그": "input",
            "contenteditable": "아니요",
        },
        {
            "항목": "본문",
            "상태": "후보만 발견",
            "선택자": ".ProseMirror",
            "iframe 위치": "top/frame[0]",
            "태그": "div",
            "contenteditable": "예",
        },
    ]


def test_review_ui_is_session_only_and_does_not_write_database() -> None:
    source = Path(ui.__file__).read_text(encoding="utf-8")

    assert "review_chrome_compatibility_report" in source
    assert "Chrome 호환성 보고서 검사" in source
    assert "DB에 저장하지 않으며" in source
    assert "session_state" in source
    assert "duckdb" not in source.casefold()
    assert "INSERT INTO" not in source
    assert "UPDATE " not in source


def test_candidate_rows_expose_attributes_without_values() -> None:
    rows = ui._candidate_rows(
        {
            "candidate_controls": [
                {
                    "frame_path": "top/frame[0]",
                    "tag_name": "div",
                    "input_type": "",
                    "element_id": "editor-body",
                    "name": "",
                    "role": "textbox",
                    "placeholder": "내용을 입력하세요",
                    "aria_label": "본문",
                    "data_placeholder": "내용",
                    "class_names": ["ProseMirror", "editor"],
                    "contenteditable": True,
                }
            ]
        }
    )

    assert rows[0]["iframe 위치"] == "top/frame[0]"
    assert rows[0]["id"] == "editor-body"
    assert rows[0]["class"] == "ProseMirror editor"
    assert "value" not in rows[0]
    assert "본문 내용" not in rows[0].values()
