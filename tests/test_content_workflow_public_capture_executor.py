from __future__ import annotations

import os
from pathlib import Path

import pytest

from src.services.content_pack_image_acquisition_service import (
    build_image_acquisition_plans,
)
from src.services.content_pack_public_capture_runtime import (
    apply_public_capture_execution,
)
from src.services.content_pack_public_capture_service import (
    CapturePlan,
    FakeCaptureExecutor,
    HeadlessCdpCaptureExecutor,
    _browser_candidates,
    process_content_pack_captures,
    validate_public_capture_url,
)


def _public_resolver(*_args):
    return [
        (2, 1, 6, "", ("8.8.8.8", 0)),
        (10, 1, 6, "", ("2001:4860:4860::8888", 0, 0, 0)),
    ]


def _data(*, anchor: str = "신청기간", url: str = "https://example.com/policy") -> dict:
    return {
        "schema_version": "2.1",
        "title": "지원 정책 신청 방법",
        "sources": [{"id": "R1", "url": url}],
        "blocks": [
            {
                "type": "image",
                "position": "신청 방법 뒤",
                "purpose": "공식 조건 근거",
                "image_strategy": "official_capture",
                "source_capture": {
                    "needed": True,
                    "source_id": "R1",
                    "source_url": url,
                    "capture_target": "신청기간과 지원 대상 안내 영역",
                    "capture_anchor": anchor,
                    "capture_note": "개인정보와 로그인 영역 제외",
                    "checked_at": "2026-08-22",
                },
                "caption": "공식 신청 조건",
                "alt_text": "공식 안내의 신청기간 영역",
            }
        ],
    }


def test_public_url_validator_allows_public_http_and_https() -> None:
    assert validate_public_capture_url(
        "https://example.com/policy",
        resolver=_public_resolver,
    ) == (True, "")
    assert validate_public_capture_url(
        "http://example.com/public",
        resolver=_public_resolver,
    ) == (True, "")


@pytest.mark.parametrize("url", ["file:///tmp/a", "data:text/plain,a", "javascript:alert(1)"])
def test_public_url_validator_blocks_non_http_schemes(url: str) -> None:
    safe, _ = validate_public_capture_url(url, resolver=_public_resolver)
    assert safe is False


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1/",
        "http://10.0.0.1/",
        "http://192.168.1.10/",
        "http://169.254.1.2/",
        "http://[::1]/",
    ],
)
def test_public_url_validator_blocks_loopback_private_and_link_local(url: str) -> None:
    safe, _ = validate_public_capture_url(url, resolver=_public_resolver)
    assert safe is False


def test_public_url_validator_blocks_sensitive_paths_and_credentials() -> None:
    safe_login, _ = validate_public_capture_url(
        "https://example.com/account/login",
        resolver=_public_resolver,
    )
    safe_credentials, _ = validate_public_capture_url(
        "https://user:pass@example.com/policy",
        resolver=_public_resolver,
    )
    assert safe_login is False
    assert safe_credentials is False


def test_public_url_validator_rejects_domain_resolving_to_private_ip() -> None:
    def private_resolver(*_args):
        return [(2, 1, 6, "", ("192.168.1.20", 0))]

    safe, reason = validate_public_capture_url(
        "https://example.com/policy",
        resolver=private_resolver,
    )
    assert safe is False
    assert "사설" in reason or "로컬" in reason


def test_fake_executor_returns_capture_with_provenance(tmp_path: Path) -> None:
    executor = FakeCaptureExecutor(url_validator=lambda _url: (True, ""))
    result = executor.capture_public_source(
        CapturePlan(
            source_id="R1",
            source_url="https://example.com/policy",
            capture_target="신청기간 안내",
            capture_anchor="신청기간",
            output_dir=tmp_path,
        )
    )

    assert result.status == "success"
    assert Path(result.image_path).is_file()
    assert result.provenance["source_id"] == "R1"
    assert result.provenance["capture_anchor"] == "신청기간"
    assert len(result.provenance["sha256"]) == 64


def test_fake_executor_stops_when_anchor_is_missing(tmp_path: Path) -> None:
    executor = FakeCaptureExecutor(url_validator=lambda _url: (True, ""))
    result = executor.capture_public_source(
        CapturePlan(
            source_id="R1",
            source_url="https://example.com/policy",
            capture_target="신청 조건",
            capture_anchor="",
            output_dir=tmp_path,
        )
    )

    assert result.status == "needs_review"
    assert "anchor_not_found" in result.review_reason


def test_process_content_pack_captures_only_executes_ready_official_plan(tmp_path: Path) -> None:
    data = _data()
    plans = build_image_acquisition_plans(data)
    data["blocks"][0]["image_acquisition"] = plans[0]
    executor = FakeCaptureExecutor(url_validator=lambda _url: (True, ""))

    results = process_content_pack_captures(
        data,
        executor=executor,
        output_dir=tmp_path,
    )

    assert len(results) == 1
    assert results[0].status == "success"
    assert len(executor.captured_plans) == 1


def test_runtime_records_needs_review_without_fake_success(tmp_path: Path) -> None:
    data = _data()
    plans = build_image_acquisition_plans(data)
    data["blocks"][0]["image_acquisition"] = plans[0]
    executor = FakeCaptureExecutor(
        forced_status="needs_review",
        forced_reason="anchor_ambiguous",
        url_validator=lambda _url: (True, ""),
    )

    results = apply_public_capture_execution(
        data,
        executor=executor,
        output_dir=tmp_path,
    )

    assert results[0].status == "needs_review"
    image = data["blocks"][0]
    assert "captured_image" not in image
    assert image["image_acquisition"]["status"] == "needs_review"
    assert image["image_acquisition"]["action"] == "manual_review"
    assert "anchor_ambiguous" in image["user_action"]


@pytest.mark.skipif(
    os.environ.get("CONTENT_TREND_BROWSER_SMOKE") != "1",
    reason="외부 네트워크 브라우저 스모크는 명시적으로 요청한 로컬/Agent 검증에서만 실행합니다.",
)
def test_live_headless_chrome_executor_uses_isolated_profile(tmp_path: Path) -> None:
    candidates = _browser_candidates()
    if not candidates:
        pytest.skip("Chrome/Edge 실행 파일이 없습니다.")

    executor = HeadlessCdpCaptureExecutor(browser_path=candidates[0])
    result = executor.capture_public_source(
        CapturePlan(
            source_id="PYDOC",
            source_url="https://docs.python.org/3/library/pathlib.html",
            capture_target="Path.read_text 설명이 보이는 영역",
            capture_anchor="Path.read_text",
            output_dir=tmp_path,
            timeout_seconds=20,
        )
    )

    assert result.status == "success", result.review_reason
    assert Path(result.image_path).is_file()
    assert result.provenance["safety_checked"] is True
    assert result.provenance["region_locator"] == "visible_text_nearest_semantic_container"
