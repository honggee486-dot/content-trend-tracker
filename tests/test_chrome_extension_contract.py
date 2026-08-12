from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
EXTENSION_ROOT = ROOT / "chrome_extension"


def test_manifest_v3_uses_only_user_gesture_permissions() -> None:
    manifest = json.loads(
        (EXTENSION_ROOT / "manifest.json").read_text(encoding="utf-8")
    )

    assert manifest["manifest_version"] == 3
    assert manifest["version"] == "0.3.0"
    assert manifest["permissions"] == ["activeTab", "scripting", "clipboardRead"]
    assert "host_permissions" not in manifest
    assert "background" not in manifest
    assert manifest["action"]["default_popup"] == "popup.html"


def test_extension_has_no_cookie_storage_or_submit_automation() -> None:
    source = "\n".join(
        path.read_text(encoding="utf-8")
        for path in (
            EXTENSION_ROOT / "manifest.json",
            EXTENSION_ROOT / "popup.js",
            EXTENSION_ROOT / "content.js",
        )
    ).casefold()

    assert '"cookies"' not in source
    assert "chrome.cookies" not in source
    assert "chrome.storage" not in source
    assert ".click(" not in source
    assert "form.submit" not in source
    assert "requestsubmit" not in source
    assert "may_submit !== false" in source


def test_popup_validates_checksum_expiry_host_and_user_action() -> None:
    source = (EXTENSION_ROOT / "popup.js").read_text(encoding="utf-8")

    assert 'const SCHEMA_VERSION = "1.0"' in source
    assert "crypto.subtle.digest" in source
    assert "Date.now() >= expiresAt" in source
    assert "hostMatches(currentHostname, patterns)" in source
    assert "safety.requires_user_action !== true" in source
    assert 'type: "CTT_FILL_EDITOR"' in source
    assert 'files: ["content.js"]' in source


def test_popup_exposes_non_mutating_editor_diagnostic() -> None:
    html = (EXTENSION_ROOT / "popup.html").read_text(encoding="utf-8")
    source = (EXTENSION_ROOT / "popup.js").read_text(encoding="utf-8")

    assert 'id="diagnoseEditor"' in html
    assert "입력칸 진단" in html
    assert 'type: "CTT_DIAGNOSE_EDITOR"' in source
    assert "formatDiagnosticLines" in source
    assert "blocked_iframe_count" in source
    assert "후보 발견" in source


def test_popup_builds_copyable_privacy_limited_compatibility_report() -> None:
    html = (EXTENSION_ROOT / "popup.html").read_text(encoding="utf-8")
    source = (EXTENSION_ROOT / "popup.js").read_text(encoding="utf-8")

    assert 'id="reportSection"' in html
    assert 'id="compatibilityReport"' in html
    assert 'id="copyCompatibilityReport"' in html
    assert "buildCompatibilityReport" in source
    assert 'report_type: "chrome_editor_compatibility"' in source
    assert "sanitizeDiagnostics" in source
    assert "includes_editor_values: false" in source
    assert "includes_payload_content: false" in source
    assert "includes_credentials: false" in source
    assert "includes_url_query_or_hash: false" in source
    assert "stores_browser_session: false" in source
    assert "may_submit: false" in source
    assert "hostname" in source
    assert "pathname" not in source
    assert "searchParams" not in source
    assert "location.hash" not in source
    assert "navigator.clipboard.writeText" in source
    assert 'document.execCommand("copy")' in source


def test_content_script_fills_fields_but_never_publishes() -> None:
    source = (EXTENSION_ROOT / "content.js").read_text(encoding="utf-8")

    assert "__CTT_EDITOR_ASSISTANT_INSTALLED__" in source
    assert "blog.naver.com" in source
    assert ".tistory.com" in source
    assert ".blogger.com" in source
    assert "setFormValue" in source
    assert "title: fillField" in source
    assert "body: fillField" in source
    assert "tags: fillField" in source
    assert "meta_description: fillField" in source
    assert ".click(" not in source
    assert "submit(" not in source


def test_content_script_scans_same_origin_frames_and_reports_matches_only() -> None:
    source = (EXTENSION_ROOT / "content.js").read_text(encoding="utf-8")

    assert "collectDocumentContexts" in source
    assert 'querySelectorAll("iframe, frame")' in source
    assert "frameElement.contentDocument" in source
    assert "element.ownerDocument?.defaultView" in source
    assert "CTT_DIAGNOSE_EDITOR" in source
    assert "blocked_iframe_count" in source
    assert "frame_path" in source
    assert "selector" in source
    assert "tag_name" in source
    assert "값은 읽지 않았습니다" in source
    assert "collectCandidateInventory" in source
    assert "candidate_controls" in source
    assert "candidate_control_count" in source
    assert 'not([type="password"])' in source
    assert 'not([type="file"])' in source


def test_extension_readme_requires_manual_login_and_final_publish() -> None:
    readme = (EXTENSION_ROOT / "README.md").read_text(encoding="utf-8")

    assert "직접 로그인" in readme
    assert "사용자가 직접 수행" in readme
    assert "10분" in readme
    assert "쿠키" in readme
    assert "게시·임시저장 버튼을 누르지 않습니다" in readme
    assert "same-origin iframe" in readme
    assert "현재 값" in readme
    assert "읽거나 출력하지 않습니다" in readme
    assert "호환성 보고서" in readme
    assert "URL 경로·쿼리·해시" in readme
    assert "구조 후보" in readme
    assert "현재 값" in readme
    assert "textContent" in readme
