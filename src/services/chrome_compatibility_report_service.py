from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, Mapping


REPORT_SCHEMA_VERSION = "1.1"
SUPPORTED_REPORT_SCHEMA_VERSIONS = {"1.0", "1.1"}
REPORT_TYPE = "chrome_editor_compatibility"
REPORT_SOURCE = "content-trend-tracker"
FIELD_NAMES = ("title", "body", "tags", "meta_description")
FIELD_LABELS = {
    "title": "제목",
    "body": "본문",
    "tags": "태그",
    "meta_description": "검색 설명",
}

_HOSTNAME_RE = re.compile(r"^[A-Za-z0-9.-]+$")


@dataclass(frozen=True)
class CompatibilityFieldReview:
    field_name: str
    label: str
    found: bool
    filled: bool
    selector: str
    frame_path: str
    tag_name: str
    contenteditable: bool
    status: str


@dataclass(frozen=True)
class CompatibilityCandidateControl:
    frame_path: str
    tag_name: str
    input_type: str
    element_id: str
    name: str
    role: str
    placeholder: str
    aria_label: str
    data_placeholder: str
    class_names: tuple[str, ...]
    contenteditable: bool


@dataclass(frozen=True)
class CompatibilityReportReview:
    status: str
    severity: str
    summary: str
    hostname: str
    expected_platform: str
    detected_adapter: str
    action: str
    accessible_documents: int
    blocked_iframe_count: int
    fields: tuple[CompatibilityFieldReview, ...]
    candidate_controls: tuple[CompatibilityCandidateControl, ...]
    candidate_control_count: int
    candidate_controls_truncated: bool
    reasons: tuple[str, ...]
    next_step: str


def _require_mapping(value: Any, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{label}은 JSON 객체여야 합니다.")
    return value


def _require_exact_keys(
    value: Mapping[str, Any],
    *,
    allowed: set[str],
    label: str,
) -> None:
    extras = sorted(str(key) for key in value.keys() if str(key) not in allowed)
    if extras:
        raise ValueError(
            f"{label}에 허용되지 않은 항목이 있습니다: {', '.join(extras)}"
        )


def _normalize_platform(value: Any) -> str:
    text = str(value or "").strip().casefold().replace("-", "_")
    aliases = {
        "naver": "naver_blog",
        "naverblog": "naver_blog",
        "naver_blog": "naver_blog",
        "tistory": "tistory",
        "blogger": "blogger",
        "google_blogger": "blogger",
        "generic": "generic",
        "custom": "generic",
    }
    return aliases.get(text, text)


def _parse_raw_report(raw_report: str | Mapping[str, Any]) -> Mapping[str, Any]:
    if isinstance(raw_report, Mapping):
        return raw_report
    try:
        parsed = json.loads(str(raw_report or ""))
    except json.JSONDecodeError as exc:
        raise ValueError(
            f"호환성 보고서 JSON을 읽을 수 없습니다: {exc.msg}"
        ) from exc
    return _require_mapping(parsed, "호환성 보고서")


def _validate_privacy_contract(safety: Mapping[str, Any]) -> None:
    required_false = (
        "includes_editor_values",
        "includes_payload_content",
        "includes_credentials",
        "includes_url_query_or_hash",
        "stores_browser_session",
        "may_submit",
    )
    _require_exact_keys(safety, allowed=set(required_false), label="safety")
    invalid = [name for name in required_false if safety.get(name) is not False]
    if invalid:
        raise ValueError(
            "개인정보 제한 계약을 만족하지 않는 보고서입니다: "
            + ", ".join(invalid)
        )


def _validate_hostname(value: Any) -> str:
    hostname = str(value or "").strip().casefold().rstrip(".")
    if not hostname or not _HOSTNAME_RE.fullmatch(hostname):
        raise ValueError(
            "page.hostname에는 URL 경로·쿼리 없이 호스트 이름만 있어야 합니다."
        )
    return hostname


def _field_status(*, action: str, found: bool, filled: bool) -> str:
    if action == "fill" and filled:
        return "입력 성공"
    if found:
        return "후보 발견" if action == "diagnose" else "후보만 발견"
    return "미발견"


def review_chrome_compatibility_report(
    raw_report: str | Mapping[str, Any],
    *,
    expected_platform: str | None = None,
) -> CompatibilityReportReview:
    report = _parse_raw_report(raw_report)
    _require_exact_keys(
        report,
        allowed={
            "schema_version",
            "report_type",
            "source",
            "generated_at",
            "page",
            "expected_platform",
            "action",
            "result",
            "safety",
        },
        label="호환성 보고서",
    )
    schema_version = str(report.get("schema_version") or "")
    if schema_version not in SUPPORTED_REPORT_SCHEMA_VERSIONS:
        raise ValueError("지원하지 않는 호환성 보고서 스키마입니다.")
    if str(report.get("report_type") or "") != REPORT_TYPE:
        raise ValueError("Chrome 편집기 호환성 보고서가 아닙니다.")
    if str(report.get("source") or "") != REPORT_SOURCE:
        raise ValueError("콘텐츠 트렌드 트래커가 만든 보고서가 아닙니다.")

    page = _require_mapping(report.get("page"), "page")
    _require_exact_keys(page, allowed={"hostname"}, label="page")
    hostname = _validate_hostname(page.get("hostname"))

    action = str(report.get("action") or "").strip().casefold()
    if action not in {"diagnose", "fill"}:
        raise ValueError("action은 diagnose 또는 fill이어야 합니다.")

    safety = _require_mapping(report.get("safety"), "safety")
    _validate_privacy_contract(safety)

    result = _require_mapping(report.get("result"), "result")
    _require_exact_keys(
        result,
        allowed={"ok", "fields", "diagnostics"},
        label="result",
    )
    fields_result = _require_mapping(result.get("fields"), "result.fields")
    _require_exact_keys(
        fields_result,
        allowed=set(FIELD_NAMES),
        label="result.fields",
    )
    diagnostics = _require_mapping(
        result.get("diagnostics"), "result.diagnostics"
    )
    diagnostic_keys = {
        "adapter",
        "accessible_documents",
        "blocked_iframe_count",
        "blocked_iframes",
        "matches",
    }
    if schema_version == "1.1":
        diagnostic_keys.update(
            {
                "candidate_controls",
                "candidate_control_count",
                "candidate_controls_truncated",
            }
        )
    _require_exact_keys(
        diagnostics,
        allowed=diagnostic_keys,
        label="result.diagnostics",
    )
    matches = _require_mapping(diagnostics.get("matches"), "diagnostics.matches")
    _require_exact_keys(matches, allowed=set(FIELD_NAMES), label="diagnostics.matches")

    blocked_iframes = diagnostics.get("blocked_iframes")
    if not isinstance(blocked_iframes, list):
        raise ValueError("diagnostics.blocked_iframes는 배열이어야 합니다.")
    for index, item in enumerate(blocked_iframes):
        frame = _require_mapping(item, f"blocked_iframes[{index}]")
        _require_exact_keys(
            frame,
            allowed={"frame_path", "reason"},
            label=f"blocked_iframes[{index}]",
        )

    candidate_controls: list[CompatibilityCandidateControl] = []
    candidate_control_count = 0
    candidate_controls_truncated = False
    if schema_version == "1.1":
        raw_candidates = diagnostics.get("candidate_controls")
        if not isinstance(raw_candidates, list):
            raise ValueError("diagnostics.candidate_controls는 배열이어야 합니다.")
        if len(raw_candidates) > 40:
            raise ValueError("구조 후보는 최대 40개까지 허용합니다.")
        candidate_control_count = max(
            0, int(diagnostics.get("candidate_control_count") or 0)
        )
        candidate_controls_truncated = (
            diagnostics.get("candidate_controls_truncated") is True
        )
        if candidate_control_count < len(raw_candidates):
            raise ValueError("구조 후보 전체 수가 포함된 후보 수보다 작습니다.")
        if candidate_control_count > len(raw_candidates) and not candidate_controls_truncated:
            raise ValueError("일부 구조 후보가 생략됐지만 truncated 표시가 없습니다.")
        allowed_candidate_keys = {
            "frame_path",
            "tag_name",
            "input_type",
            "id",
            "name",
            "role",
            "placeholder",
            "aria_label",
            "data_placeholder",
            "class_names",
            "contenteditable",
        }
        for index, item in enumerate(raw_candidates):
            candidate = _require_mapping(item, f"candidate_controls[{index}]")
            _require_exact_keys(
                candidate,
                allowed=allowed_candidate_keys,
                label=f"candidate_controls[{index}]",
            )
            class_names = candidate.get("class_names")
            if not isinstance(class_names, list) or len(class_names) > 8:
                raise ValueError("구조 후보 class_names는 최대 8개 배열이어야 합니다.")
            values = {
                key: str(candidate.get(key) or "")
                for key in allowed_candidate_keys
                if key not in {"class_names", "contenteditable"}
            }
            if any(len(value) > 160 for value in values.values()):
                raise ValueError("구조 후보 속성 길이가 허용 범위를 초과했습니다.")
            normalized_classes = tuple(str(value or "") for value in class_names)
            if any(len(value) > 80 for value in normalized_classes):
                raise ValueError("구조 후보 class 이름이 너무 깁니다.")
            candidate_controls.append(
                CompatibilityCandidateControl(
                    frame_path=values["frame_path"],
                    tag_name=values["tag_name"],
                    input_type=values["input_type"],
                    element_id=values["id"],
                    name=values["name"],
                    role=values["role"],
                    placeholder=values["placeholder"],
                    aria_label=values["aria_label"],
                    data_placeholder=values["data_placeholder"],
                    class_names=normalized_classes,
                    contenteditable=candidate.get("contenteditable") is True,
                )
            )

    field_reviews: list[CompatibilityFieldReview] = []
    for field_name in FIELD_NAMES:
        match = _require_mapping(matches.get(field_name), f"matches.{field_name}")
        _require_exact_keys(
            match,
            allowed={
                "found",
                "selector",
                "frame_path",
                "tag_name",
                "contenteditable",
            },
            label=f"matches.{field_name}",
        )
        found = match.get("found") is True
        filled = fields_result.get(field_name) is True
        if filled and not found:
            raise ValueError(
                f"{FIELD_LABELS[field_name]}은 선택자 미발견인데 입력 성공으로 기록됐습니다."
            )
        field_reviews.append(
            CompatibilityFieldReview(
                field_name=field_name,
                label=FIELD_LABELS[field_name],
                found=found,
                filled=filled,
                selector=str(match.get("selector") or ""),
                frame_path=str(match.get("frame_path") or ""),
                tag_name=str(match.get("tag_name") or ""),
                contenteditable=match.get("contenteditable") is True,
                status=_field_status(action=action, found=found, filled=filled),
            )
        )

    expected = _normalize_platform(
        expected_platform if expected_platform is not None else report.get("expected_platform")
    )
    detected = _normalize_platform(diagnostics.get("adapter")) or "generic"
    accessible_documents = max(0, int(diagnostics.get("accessible_documents") or 0))
    blocked_iframe_count = max(0, int(diagnostics.get("blocked_iframe_count") or 0))

    by_name = {field.field_name: field for field in field_reviews}
    critical = (by_name["title"], by_name["body"])
    reasons: list[str] = []

    if expected and expected != "generic" and detected != expected:
        status = "대상 플랫폼 확인"
        severity = "warning"
        summary = (
            f"예상 플랫폼 {expected}과 감지된 편집기 {detected}가 다릅니다."
        )
        reasons.append("잘못된 글쓰기 탭이거나 플랫폼 감지 규칙을 점검해야 합니다.")
        next_step = "올바른 글쓰기 탭에서 다시 진단한 뒤 보고서를 확인합니다."
    elif action == "fill" and all(field.filled for field in critical):
        status = "핵심 입력 성공"
        severity = "success"
        summary = "제목과 본문 입력이 모두 성공으로 기록됐습니다."
        optional_failures = [
            field.label
            for field in field_reviews
            if field.field_name not in {"title", "body"} and not field.filled
        ]
        if optional_failures:
            reasons.append(
                "선택 입력 미완료: " + ", ".join(optional_failures)
            )
            next_step = (
                "화면 반영을 직접 확인하고 선택 입력이 필요한 경우에만 해당 필드를 보강합니다."
            )
        else:
            reasons.append("제목·본문·태그·검색 설명이 모두 입력 성공으로 기록됐습니다.")
            next_step = "화면과 저장 상태를 확인한 뒤 사용자가 직접 임시저장 또는 발행합니다."
    elif action == "fill" and any(field.found and not field.filled for field in critical):
        status = "입력 이벤트 점검"
        severity = "warning"
        summary = "핵심 입력칸 후보는 찾았지만 제목 또는 본문 입력이 완료되지 않았습니다."
        reasons.append(
            "선택자는 잡혔으므로 플랫폼 편집기 상태 반영 이벤트를 우선 점검합니다."
        )
        next_step = "실패한 핵심 필드의 입력 이벤트 처리만 최소 범위로 보강합니다."
    elif blocked_iframe_count > 0 and any(not field.found for field in critical):
        status = "iframe 접근 제한 확인"
        severity = "warning"
        summary = "핵심 입력칸이 보이지 않고 접근하지 못한 iframe이 있습니다."
        reasons.append(
            f"접근 불가 iframe {blocked_iframe_count}개가 핵심 편집기를 포함하는지 확인해야 합니다."
        )
        next_step = "iframe 출처와 편집기 위치를 확인하고 권한 확대 없이 가능한 대안을 검토합니다."
    elif all(field.found for field in critical):
        status = "입력 테스트 가능"
        severity = "info"
        summary = "제목과 본문 입력칸 후보를 모두 찾았습니다."
        reasons.append("진단 단계이므로 실제 입력과 편집기 내부 상태 반영은 아직 확인되지 않았습니다.")
        next_step = "전달 데이터를 불러와 입력을 실행한 뒤 fill 보고서를 다시 검사합니다."
    else:
        status = "선택자 보강 필요"
        severity = "warning"
        missing = [field.label for field in critical if not field.found]
        summary = "핵심 입력칸 선택자를 찾지 못했습니다: " + ", ".join(missing)
        reasons.append("실제 보고서에 근거해 누락된 플랫폼 선택자만 추가해야 합니다.")
        if candidate_controls:
            reasons.append(
                f"개인정보를 제외한 구조 후보 {len(candidate_controls)}개를 확인할 수 있습니다."
            )
            next_step = "구조 후보 표에서 제목·본문에 해당하는 속성을 확인해 선택자 한 축만 보강합니다."
        else:
            next_step = "누락된 핵심 필드의 실제 DOM 선택자만 최소 범위로 보강합니다."

    if accessible_documents == 0:
        reasons.append("탐색 가능한 문서 수가 0개로 기록돼 확장 실행 상태도 확인해야 합니다.")

    return CompatibilityReportReview(
        status=status,
        severity=severity,
        summary=summary,
        hostname=hostname,
        expected_platform=expected or "미지정",
        detected_adapter=detected,
        action=action,
        accessible_documents=accessible_documents,
        blocked_iframe_count=blocked_iframe_count,
        fields=tuple(field_reviews),
        candidate_controls=tuple(candidate_controls),
        candidate_control_count=candidate_control_count,
        candidate_controls_truncated=candidate_controls_truncated,
        reasons=tuple(reasons),
        next_step=next_step,
    )
