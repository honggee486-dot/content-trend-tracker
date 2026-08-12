from __future__ import annotations

import copy

import pytest

from src.services.chrome_compatibility_report_service import (
    review_chrome_compatibility_report,
)


def _match(*, found: bool = True, selector: str = "#field") -> dict:
    return {
        "found": found,
        "selector": selector if found else "",
        "frame_path": "top" if found else "",
        "tag_name": "input" if found else "",
        "contenteditable": False,
    }


def _report(*, action: str = "fill") -> dict:
    return {
        "schema_version": "1.0",
        "report_type": "chrome_editor_compatibility",
        "source": "content-trend-tracker",
        "generated_at": "2026-08-03T01:00:00+09:00",
        "page": {"hostname": "example.tistory.com"},
        "expected_platform": "tistory",
        "action": action,
        "result": {
            "ok": True,
            "fields": {
                "title": action == "fill",
                "body": action == "fill",
                "tags": action == "fill",
                "meta_description": False,
            },
            "diagnostics": {
                "adapter": "tistory",
                "accessible_documents": 2,
                "blocked_iframe_count": 0,
                "blocked_iframes": [],
                "matches": {
                    "title": _match(selector="#post-title-inp"),
                    "body": _match(selector=".ProseMirror"),
                    "tags": _match(selector='input[placeholder*="태그"]'),
                    "meta_description": _match(found=False),
                },
            },
        },
        "safety": {
            "includes_editor_values": False,
            "includes_payload_content": False,
            "includes_credentials": False,
            "includes_url_query_or_hash": False,
            "stores_browser_session": False,
            "may_submit": False,
        },
    }


def test_fill_report_marks_core_title_and_body_as_success() -> None:
    review = review_chrome_compatibility_report(
        _report(),
        expected_platform="tistory",
    )

    assert review.status == "핵심 입력 성공"
    assert review.severity == "success"
    assert review.hostname == "example.tistory.com"
    assert review.expected_platform == "tistory"
    assert review.detected_adapter == "tistory"
    assert [field.status for field in review.fields] == [
        "입력 성공",
        "입력 성공",
        "입력 성공",
        "미발견",
    ]
    assert "선택 입력 미완료: 검색 설명" in review.reasons


def test_found_but_unfilled_core_field_requests_event_review() -> None:
    report = _report()
    report["result"]["fields"]["body"] = False
    report["result"]["ok"] = False

    review = review_chrome_compatibility_report(report)

    assert review.status == "입력 이벤트 점검"
    assert review.severity == "warning"
    assert "입력 이벤트 처리" in review.next_step


def test_diagnose_report_missing_core_selector_requests_selector_update() -> None:
    report = _report(action="diagnose")
    report["result"]["diagnostics"]["matches"]["body"] = _match(found=False)

    review = review_chrome_compatibility_report(report)

    assert review.status == "선택자 보강 필요"
    assert "본문" in review.summary
    assert "실제 DOM 선택자" in review.next_step


def test_missing_core_selector_with_blocked_iframe_is_classified_separately() -> None:
    report = _report(action="diagnose")
    diagnostics = report["result"]["diagnostics"]
    diagnostics["matches"]["body"] = _match(found=False)
    diagnostics["blocked_iframe_count"] = 1
    diagnostics["blocked_iframes"] = [
        {"frame_path": "top/frame[0]", "reason": "cross_origin_or_blocked"}
    ]

    review = review_chrome_compatibility_report(report)

    assert review.status == "iframe 접근 제한 확인"
    assert review.blocked_iframe_count == 1
    assert "권한 확대 없이" in review.next_step


def test_expected_platform_mismatch_is_reported_before_selector_changes() -> None:
    report = _report(action="diagnose")
    report["result"]["diagnostics"]["adapter"] = "blogger"

    review = review_chrome_compatibility_report(
        report,
        expected_platform="tistory",
    )

    assert review.status == "대상 플랫폼 확인"
    assert review.detected_adapter == "blogger"


@pytest.mark.parametrize(
    "mutator",
    [
        lambda report: report["safety"].update({"includes_payload_content": True}),
        lambda report: report["page"].update(
            {"hostname": "example.tistory.com/manage/newpost?type=post"}
        ),
        lambda report: report.update({"content": {"body": "private draft"}}),
    ],
)
def test_privacy_or_extra_content_contract_violation_is_rejected(mutator) -> None:
    report = copy.deepcopy(_report())
    mutator(report)

    with pytest.raises(ValueError):
        review_chrome_compatibility_report(report)


def test_report_cannot_mark_unfound_field_as_filled() -> None:
    report = _report()
    report["result"]["diagnostics"]["matches"]["title"] = _match(found=False)

    with pytest.raises(ValueError, match="선택자 미발견"):
        review_chrome_compatibility_report(report)


def _candidate(**overrides) -> dict:
    candidate = {
        "frame_path": "top",
        "tag_name": "textarea",
        "input_type": "",
        "id": "editor-title",
        "name": "title",
        "role": "textbox",
        "placeholder": "제목",
        "aria_label": "글 제목",
        "data_placeholder": "",
        "class_names": ["editor", "title-field"],
        "contenteditable": False,
    }
    candidate.update(overrides)
    return candidate


def test_schema_1_1_accepts_privacy_limited_candidate_inventory() -> None:
    report = _report(action="diagnose")
    report["schema_version"] = "1.1"
    diagnostics = report["result"]["diagnostics"]
    diagnostics.update(
        {
            "candidate_controls": [_candidate()],
            "candidate_control_count": 1,
            "candidate_controls_truncated": False,
        }
    )
    diagnostics["matches"]["body"] = _match(found=False)

    review = review_chrome_compatibility_report(report)

    assert review.status == "선택자 보강 필요"
    assert review.candidate_control_count == 1
    assert review.candidate_controls[0].element_id == "editor-title"
    assert "구조 후보 표" in review.next_step


def test_schema_1_0_report_remains_supported_without_candidates() -> None:
    review = review_chrome_compatibility_report(_report(action="diagnose"))

    assert review.candidate_controls == ()
    assert review.candidate_control_count == 0
    assert review.candidate_controls_truncated is False


def test_candidate_inventory_rejects_editor_values_or_extra_keys() -> None:
    report = _report(action="diagnose")
    report["schema_version"] = "1.1"
    diagnostics = report["result"]["diagnostics"]
    diagnostics.update(
        {
            "candidate_controls": [_candidate(value="private draft")],
            "candidate_control_count": 1,
            "candidate_controls_truncated": False,
        }
    )

    with pytest.raises(ValueError, match="허용되지 않은 항목"):
        review_chrome_compatibility_report(report)
