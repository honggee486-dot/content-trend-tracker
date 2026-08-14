from __future__ import annotations

import hashlib
import json
import re
import socket
import time
import urllib.error
import urllib.request
from email.utils import parsedate_to_datetime
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Iterable
from uuid import uuid4

import duckdb

from src.config import GeminiConfig
from src.services.gemini_usage_service import count_text_characters

GEMINI_INTERACTIONS_URL = "https://generativelanguage.googleapis.com/v1/interactions"
GEMINI_FEATURE_ID = "blog_draft_generation_v1"
GEMINI_FEATURE_VERSION = "4"
GEMINI_SCHEMA_VERSION = "2.0"
GEMINI_THINKING_LEVELS = frozenset({"minimal", "low", "medium", "high"})

_BLOCK_SCHEMAS: list[dict[str, Any]] = [
    {
        "type": "object",
        "properties": {
            "type": {"type": "string", "enum": ["paragraph"]},
            "text": {"type": "string"},
        },
        "required": ["type", "text"],
        "additionalProperties": False,
    },
    {
        "type": "object",
        "properties": {
            "type": {"type": "string", "enum": ["heading"]},
            "level": {"type": "integer", "minimum": 1, "maximum": 6},
            "text": {"type": "string"},
        },
        "required": ["type", "level", "text"],
        "additionalProperties": False,
    },
    {
        "type": "object",
        "properties": {
            "type": {"type": "string", "enum": ["bullet_list"]},
            "items": {
                "type": "array",
                "items": {"type": "string"},
                "minItems": 1,
            },
        },
        "required": ["type", "items"],
        "additionalProperties": False,
    },
    {
        "type": "object",
        "properties": {
            "type": {"type": "string", "enum": ["numbered_list"]},
            "items": {
                "type": "array",
                "items": {"type": "string"},
                "minItems": 1,
            },
        },
        "required": ["type", "items"],
        "additionalProperties": False,
    },
    {
        "type": "object",
        "properties": {
            "type": {"type": "string", "enum": ["quote"]},
            "text": {"type": "string"},
        },
        "required": ["type", "text"],
        "additionalProperties": False,
    },
    {
        "type": "object",
        "properties": {
            "type": {"type": "string", "enum": ["image"]},
            "position": {"type": "string"},
            "purpose": {"type": "string"},
            "prompt": {"type": "string"},
            "aspect_ratio": {"type": "string"},
            "caption": {"type": "string"},
            "alt_text": {"type": "string"},
        },
        "required": [
            "type",
            "position",
            "purpose",
            "prompt",
            "aspect_ratio",
            "caption",
            "alt_text",
        ],
        "additionalProperties": False,
    },
]

GEMINI_DRAFT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "schema_version": {"type": "string", "enum": [GEMINI_SCHEMA_VERSION]},
        "title": {"type": "string"},
        "summary": {"type": "string"},
        "category": {"type": "string"},
        "tags": {
            "type": "array",
            "items": {"type": "string"},
            "maxItems": 20,
        },
        "blocks": {
            "type": "array",
            "items": {"anyOf": _BLOCK_SCHEMAS},
            "minItems": 1,
        },
        "fact_checks": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "claim": {"type": "string"},
                    "status": {
                        "type": "string",
                        "enum": ["needs_verification"],
                    },
                    "reason": {"type": "string"},
                    "source_ids": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                },
                "required": ["claim", "status", "reason", "source_ids"],
                "additionalProperties": False,
            },
        },
        "sources": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "string"},
                    "title": {"type": "string"},
                    "publisher": {"type": "string"},
                    "url": {"type": "string"},
                    "published_at": {"type": "string"},
                },
                "required": ["id", "title", "publisher", "url", "published_at"],
                "additionalProperties": False,
            },
        },
    },
    "required": [
        "schema_version",
        "title",
        "summary",
        "category",
        "tags",
        "blocks",
        "fact_checks",
        "sources",
    ],
    "additionalProperties": False,
}


@dataclass(frozen=True)
class SensitiveFinding:
    field: str
    kind: str
    label: str


@dataclass(frozen=True)
class GeminiRequestPreview:
    request_text: str
    request_hash: str
    findings: tuple[SensitiveFinding, ...]
    included_user_memo: bool
    reference_count: int


@dataclass(frozen=True)
class GeminiGenerationResult:
    success: bool
    raw_response: str
    request_hash: str
    model: str
    cache_hit: bool
    attempts: int
    status: str
    error_type: str
    error_message: str
    http_status: int | None
    input_tokens: int | None
    output_tokens: int | None
    total_tokens: int | None
    duration_ms: int
    thought_tokens: int | None = None
    finish_reason: str = ""
    finish_message: str = ""


@dataclass(frozen=True)
class _ApiErrorInfo:
    http_status: int
    error_type: str
    message: str
    retryable: bool
    retry_delay_seconds: float | None
    finish_reason: str = ""
    finish_message: str = ""


class GeminiHttpError(RuntimeError):
    def __init__(self, info: _ApiErrorInfo):
        super().__init__(info.message)
        self.info = info


_SENSITIVE_PATTERNS: tuple[tuple[str, str, re.Pattern[str]], ...] = (
    (
        "api_key",
        "API 키 형태",
        re.compile(r"\bAIza[0-9A-Za-z_-]{25,}\b"),
    ),
    (
        "bearer_token",
        "Bearer 인증 토큰",
        re.compile(r"\bBearer\s+[A-Za-z0-9._~+/=-]{12,}", re.IGNORECASE),
    ),
    (
        "secret_assignment",
        "비밀번호·토큰·시크릿 값",
        re.compile(
            r"(?im)^\s*(?:[A-Z][A-Z0-9_]*(?:KEY|TOKEN|SECRET|PASSWORD)|"
            r"GEMINI_API_KEY|비밀번호|토큰)\s*[:=]\s*\S{6,}"
        ),
    ),
    (
        "email",
        "이메일 주소",
        re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.IGNORECASE),
    ),
    (
        "phone",
        "전화번호",
        re.compile(
            r"(?<!\d)(?:01[016789][ -]?\d{3,4}[ -]?\d{4}|"
            r"0\d{1,2}[ -]?\d{3,4}[ -]?\d{4})(?!\d)"
        ),
    ),
    (
        "resident_number",
        "주민등록번호 형태",
        re.compile(r"(?<!\d)\d{6}-?[1-4]\d{6}(?!\d)"),
    ),
    (
        "bank_account",
        "계좌번호로 보이는 값",
        re.compile(r"(?:계좌(?:번호)?|account)\s*[:：]?\s*\d[\d -]{7,}", re.IGNORECASE),
    ),
    (
        "address",
        "상세 주소로 보이는 값",
        re.compile(r"(?:주소|거주지)\s*[:：]\s*[^\r\n]{8,}", re.IGNORECASE),
    ),
    (
        "windows_path",
        "Windows 로컬 경로",
        re.compile(r"\b[A-Za-z]:\\[^\r\n\t]+"),
    ),
    (
        "local_network",
        "localhost 또는 사설 IP",
        re.compile(
            r"\b(?:localhost|127\.0\.0\.1|10(?:\.\d{1,3}){3}|"
            r"192\.168(?:\.\d{1,3}){2}|172\.(?:1[6-9]|2\d|3[01])(?:\.\d{1,3}){2})\b",
            re.IGNORECASE,
        ),
    ),
)


def _load_json_list(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    try:
        parsed = json.loads(value or "[]")
    except (TypeError, json.JSONDecodeError):
        return []
    return parsed if isinstance(parsed, list) else []


def _clean_text(value: Any) -> str:
    return str(value or "").strip()


def normalize_gemini_thinking_level(value: Any, *, fallback: str = "") -> str:
    normalized = _clean_text(value).casefold()
    if normalized in GEMINI_THINKING_LEVELS:
        return normalized
    normalized_fallback = _clean_text(fallback).casefold()
    return normalized_fallback if normalized_fallback in GEMINI_THINKING_LEVELS else ""


def effective_gemini_timeout_seconds(
    config: GeminiConfig,
    timeout_seconds: int | None = None,
) -> int:
    return max(5, int(timeout_seconds or config.timeout_seconds))


def scan_sensitive_fields(fields: Iterable[tuple[str, Any]]) -> tuple[SensitiveFinding, ...]:
    findings: list[SensitiveFinding] = []
    seen: set[tuple[str, str]] = set()
    for field, value in fields:
        text = _clean_text(value)
        if not text:
            continue
        for kind, label, pattern in _SENSITIVE_PATTERNS:
            if not pattern.search(text):
                continue
            key = (field, kind)
            if key in seen:
                continue
            seen.add(key)
            findings.append(SensitiveFinding(field=field, kind=kind, label=label))
    return tuple(findings)


def _iter_string_fields(value: Any, path: str = "전송 자료") -> Iterable[tuple[str, str]]:
    if isinstance(value, dict):
        for key, item in value.items():
            yield from _iter_string_fields(item, f"{path}.{key}")
    elif isinstance(value, list):
        for index, item in enumerate(value, start=1):
            yield from _iter_string_fields(item, f"{path}[{index}]")
    elif isinstance(value, str):
        yield path, value


def _reference_for_gemini(reference: dict[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = {
        "id": _clean_text(reference.get("id")),
        "kind": _clean_text(reference.get("reference_kind")),
        "title": _clean_text(reference.get("title")),
        "publisher": _clean_text(reference.get("publisher")),
        "published_at": _clean_text(reference.get("published_at")),
    }
    if result["kind"] == "trend_signal":
        result.update(
            {
                "signal_type": _clean_text(reference.get("signal_type_label")),
                "observed_at": _clean_text(reference.get("observed_at")),
                "signal_value": reference.get("signal_value"),
                "view_count": reference.get("view_count"),
                "view_delta": reference.get("view_delta"),
                "views_per_hour": reference.get("views_per_hour"),
                "topic_score": reference.get("topic_score"),
            }
        )
    else:
        result.update(
            {
                "reference_type": _clean_text(reference.get("reference_type_label")),
                "evidence_memo": _clean_text(reference.get("memo")),
            }
        )
    return result


def _request_payload(
    pack: dict[str, Any],
    topic: dict[str, Any],
    *,
    include_user_memo: bool,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    references = [
        item
        for item in _load_json_list(pack.get("references_json"))
        if isinstance(item, dict)
    ]
    payload: dict[str, Any] = {
        "topic": {
            "title": _clean_text(topic.get("title")),
            "summary": _clean_text(topic.get("summary")),
            "category": _clean_text(pack.get("category") or topic.get("category")),
        },
        "writing_goal": {
            "audience": _clean_text(pack.get("audience")),
            "purpose": _clean_text(pack.get("purpose")),
            "angle": _clean_text(pack.get("angle")),
            "target_length": int(pack.get("target_length") or 2500),
        },
        "title_rules": [_clean_text(item) for item in _load_json_list(pack.get("title_rules_json"))],
        "outline": [_clean_text(item) for item in _load_json_list(pack.get("outline_json"))],
        "forbidden_expressions": [
            _clean_text(item)
            for item in _load_json_list(pack.get("forbidden_expressions_json"))
        ],
        "required_fact_checks": [
            _clean_text(item)
            for item in _load_json_list(pack.get("fact_check_items_json"))
        ],
        "references": [_reference_for_gemini(item) for item in references],
    }
    if include_user_memo:
        payload["topic"]["user_memo"] = _clean_text(topic.get("memo"))
    return payload, references


def build_gemini_request_preview(
    pack: dict[str, Any],
    topic: dict[str, Any],
    config: GeminiConfig,
    *,
    include_user_memo: bool = False,
) -> GeminiRequestPreview:
    payload, references = _request_payload(
        pack,
        topic,
        include_user_memo=include_user_memo,
    )
    research_hour = (
        datetime.now()
        .astimezone()
        .replace(minute=0, second=0, microsecond=0)
        .isoformat()
    )
    payload["research_context"] = {
        "requested_at_hour": research_hour,
        "freshness_rule": "이 시각을 기준으로 최신 상태를 웹 검색해 확인",
    }
    findings = scan_sensitive_fields(_iter_string_fields(payload))

    instructions = (
        "사실 확인을 우선하는 한국어 정보성 블로그 초안을 작성하세요. "
        "Google Search를 사용해 현재 시점의 공식 자료와 신뢰할 수 있는 최신 자료를 먼저 확인하세요. "
        "트렌드 신호는 관심 증가의 근거로만 사용하고 사실을 보증하는 자료로 취급하지 마세요. "
        "제공된 자료 ID는 S1, S2처럼 유지하고, 웹 검색으로 새로 확인한 자료는 R1, R2처럼 번호를 붙이세요. "
        "sources에는 실제로 사용한 자료만 title, publisher, url, published_at과 함께 기록하세요. "
        "출처·통계·날짜·인물·정책을 임의로 만들지 말고, 확인하지 못한 주장은 fact_checks에 needs_verification으로 남기세요. "
        "제목을 본문 첫 heading으로 반복하지 말고, 과장·공포·확정 표현과 불필요한 반복을 피하세요. "
        "응답은 지정된 schema_version 2.0 JSON 구조 하나로만 반환하세요."
    )
    request_text = instructions + "\n\n[전송 자료]\n" + json.dumps(
        payload,
        ensure_ascii=False,
        indent=2,
        default=str,
    )
    hash_payload = {
        "app_id": config.app_id,
        "quota_scope_id": config.quota_scope_id,
        "feature_id": GEMINI_FEATURE_ID,
        "feature_version": GEMINI_FEATURE_VERSION,
        "model": config.model,
        "thinking_level": config.draft_thinking_level,
        "schema_version": GEMINI_SCHEMA_VERSION,
        "request_text": request_text,
        "local_reference_identity": [
            {
                "id": _clean_text(item.get("id")),
                "title": _clean_text(item.get("title")),
                "publisher": _clean_text(item.get("publisher")),
                "url": _clean_text(item.get("url")),
                "published_at": _clean_text(item.get("published_at")),
            }
            for item in references
        ],
    }
    request_hash = hashlib.sha256(
        json.dumps(hash_payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()
    return GeminiRequestPreview(
        request_text=request_text,
        request_hash=request_hash,
        findings=findings,
        included_user_memo=include_user_memo,
        reference_count=len(references),
    )


def _canonicalize_output_sources(
    raw_response: str,
    references: Iterable[dict[str, Any]],
) -> str:
    try:
        data = json.loads(raw_response)
    except json.JSONDecodeError:
        return raw_response
    if not isinstance(data, dict):
        return raw_response

    allowed = {
        _clean_text(item.get("id")): item
        for item in references
        if isinstance(item, dict) and _clean_text(item.get("id"))
    }
    sources = data.get("sources")
    if isinstance(sources, list):
        canonical: list[dict[str, Any]] = []
        for source in sources:
            if not isinstance(source, dict):
                canonical.append(source)
                continue
            source_id = _clean_text(source.get("id"))
            reference = allowed.get(source_id)
            if reference is None:
                canonical.append(
                    {
                        "id": source_id,
                        "title": _clean_text(source.get("title")),
                        "publisher": _clean_text(source.get("publisher")),
                        "url": _clean_text(source.get("url")),
                        "published_at": _clean_text(source.get("published_at")),
                    }
                )
                continue
            canonical.append(
                {
                    "id": source_id,
                    "title": _clean_text(reference.get("title")),
                    "publisher": _clean_text(reference.get("publisher")),
                    "url": _clean_text(reference.get("url")),
                    "published_at": _clean_text(reference.get("published_at")),
                }
            )
        data["sources"] = canonical
    return json.dumps(data, ensure_ascii=False)


def _extract_output_text(response: dict[str, Any]) -> str:
    direct = response.get("output_text")
    if isinstance(direct, str) and direct.strip():
        return direct.strip()
    steps = response.get("steps")
    if not isinstance(steps, list):
        return ""
    for step in reversed(steps):
        if not isinstance(step, dict) or step.get("type") != "model_output":
            continue
        content = step.get("content")
        if not isinstance(content, list):
            continue
        texts = [
            _clean_text(item.get("text"))
            for item in content
            if isinstance(item, dict)
            and item.get("type") == "text"
            and _clean_text(item.get("text"))
        ]
        if texts:
            return "".join(texts)
    return ""


def _extract_finish_metadata(response: dict[str, Any]) -> tuple[str, str]:
    candidates = response.get("candidates")
    if not isinstance(candidates, list):
        return "", ""
    candidate = next((item for item in candidates if isinstance(item, dict)), None)
    if candidate is None:
        return "", ""
    return (
        _clean_text(candidate.get("finishReason")),
        _clean_text(candidate.get("finishMessage")),
    )


def _usage_counts(
    response: dict[str, Any],
) -> tuple[int | None, int | None, int | None, int | None]:
    usage = response.get("usage")
    if not isinstance(usage, dict):
        return None, None, None, None

    def as_int(key: str) -> int | None:
        value = usage.get(key)
        if isinstance(value, bool):
            return None
        try:
            return int(value) if value is not None else None
        except (TypeError, ValueError):
            return None

    return (
        as_int("total_input_tokens"),
        as_int("total_output_tokens"),
        as_int("total_thought_tokens"),
        as_int("total_tokens"),
    )


def normalize_gemini_api_result(
    value: tuple[Any, ...],
) -> tuple[
    str,
    int | None,
    int | None,
    int | None,
    int | None,
    str,
    str,
]:
    if len(value) == 4:
        output_text, input_tokens, output_tokens, total_tokens = value
        thought_tokens = None
        finish_reason = ""
        finish_message = ""
    elif len(value) == 5:
        output_text, input_tokens, output_tokens, thought_tokens, total_tokens = value
        finish_reason = ""
        finish_message = ""
    elif len(value) == 7:
        (
            output_text,
            input_tokens,
            output_tokens,
            thought_tokens,
            total_tokens,
            finish_reason,
            finish_message,
        ) = value
    else:
        raise ValueError(
            "Gemini API 결과는 텍스트·토큰 수와 선택적 종료 메타데이터를 반환해야 합니다."
        )
    return (
        str(output_text or ""),
        input_tokens,
        output_tokens,
        thought_tokens,
        total_tokens,
        _clean_text(finish_reason),
        _clean_text(finish_message),
    )


def _parse_retry_delay(value: Any) -> float | None:
    text = _clean_text(value)
    if not text:
        return None
    match = re.fullmatch(r"([0-9]+(?:\.[0-9]+)?)(?:s)?", text, re.IGNORECASE)
    if match:
        return float(match.group(1))
    try:
        retry_at = parsedate_to_datetime(text)
    except (TypeError, ValueError, OverflowError):
        return None
    if retry_at.tzinfo is None:
        retry_at = retry_at.replace(tzinfo=timezone.utc)
    return max(0.0, (retry_at - datetime.now(timezone.utc)).total_seconds())


def _parse_api_error(http_status: int, body: str, retry_after: str = "") -> _ApiErrorInfo:
    message = body.strip() or f"Gemini API HTTP {http_status} 오류"
    status_name = ""
    details: list[Any] = []
    try:
        parsed = json.loads(body)
    except json.JSONDecodeError:
        parsed = None
    if isinstance(parsed, dict):
        error = parsed.get("error")
        if isinstance(error, dict):
            message = _clean_text(error.get("message")) or message
            status_name = _clean_text(error.get("status"))
            raw_details = error.get("details")
            if isinstance(raw_details, list):
                details = raw_details

    quota_ids: list[str] = []
    retry_delay = _parse_retry_delay(retry_after)
    for detail in details:
        if not isinstance(detail, dict):
            continue
        detail_type = _clean_text(detail.get("@type"))
        if detail_type.endswith("RetryInfo"):
            retry_delay = _parse_retry_delay(detail.get("retryDelay")) or retry_delay
        if detail_type.endswith("QuotaFailure"):
            violations = detail.get("violations")
            if isinstance(violations, list):
                for violation in violations:
                    if isinstance(violation, dict):
                        quota_ids.append(_clean_text(violation.get("quotaId")))

    message_folded = message.casefold()
    daily_quota = any(
        "perday" in quota_id.casefold() or "daily" in quota_id.casefold()
        for quota_id in quota_ids
    ) or any(
        marker in message_folded
        for marker in ("per day", "daily quota", "daily limit", "requests per day")
    )
    if http_status == 429 or status_name == "RESOURCE_EXHAUSTED":
        if daily_quota:
            return _ApiErrorInfo(
                http_status=http_status,
                error_type="daily_quota_exhausted",
                message=message,
                retryable=False,
                retry_delay_seconds=retry_delay,
            )
        return _ApiErrorInfo(
            http_status=http_status,
            error_type="rate_limited",
            message=message,
            retryable=True,
            retry_delay_seconds=retry_delay,
        )
    if http_status == 499 or status_name == "CANCELLED":
        return _ApiErrorInfo(
            http_status=http_status,
            error_type="request_cancelled",
            message=(
                "Gemini API 요청이 완료되기 전에 연결이 종료됐습니다. "
                f"원본 오류: {message}"
            ),
            retryable=False,
            retry_delay_seconds=retry_delay,
        )
    if http_status in {500, 502, 503, 504} or status_name == "UNAVAILABLE":
        return _ApiErrorInfo(
            http_status=http_status,
            error_type="service_unavailable",
            message=message,
            retryable=True,
            retry_delay_seconds=retry_delay,
        )
    error_type = {
        400: "invalid_request",
        401: "authentication_error",
        403: "permission_error",
        404: "model_not_found",
    }.get(http_status, "api_error")
    return _ApiErrorInfo(
        http_status=http_status,
        error_type=error_type,
        message=message,
        retryable=False,
        retry_delay_seconds=retry_delay,
    )


def call_gemini_structured_output(
    config: GeminiConfig,
    request_text: str,
    request_hash: str,
    *,
    feature_id: str,
    response_schema: dict[str, Any],
    use_google_search: bool = False,
    thinking_level: str | None = None,
    timeout_seconds: int | None = None,
) -> tuple[
    str,
    int | None,
    int | None,
    int | None,
    int | None,
    str,
    str,
]:
    effective_thinking_level = normalize_gemini_thinking_level(
        thinking_level,
        fallback=config.draft_thinking_level,
    )
    payload: dict[str, Any] = {
        "model": config.model,
        "input": request_text,
        "store": False,
        "response_format": {
            "type": "text",
            "mime_type": "application/json",
            "schema": response_schema,
        },
        "generation_config": {
            "thinking_level": effective_thinking_level,
        },
    }
    if use_google_search:
        payload["tools"] = [{"type": "google_search"}]

    request = urllib.request.Request(
        GEMINI_INTERACTIONS_URL,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "x-goog-api-key": config.api_key,
        },
        method="POST",
    )
    effective_timeout = effective_gemini_timeout_seconds(config, timeout_seconds)
    try:
        with urllib.request.urlopen(request, timeout=effective_timeout) as response:
            body = response.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise GeminiHttpError(
            _parse_api_error(
                int(exc.code),
                body,
                _clean_text(exc.headers.get("Retry-After") if exc.headers else ""),
            )
        ) from exc
    except (TimeoutError, socket.timeout) as exc:
        raise GeminiHttpError(
            _ApiErrorInfo(
                http_status=0,
                error_type="request_timeout",
                message=(
                    f"Gemini API 응답이 {effective_timeout}초 안에 완료되지 않아 연결을 종료했습니다. "
                    "대량 주제 방향 생성은 더 긴 제한 시간이 필요할 수 있습니다."
                ),
                retryable=False,
                retry_delay_seconds=None,
            )
        ) from exc
    except urllib.error.URLError as exc:
        reason = getattr(exc, "reason", None)
        if isinstance(reason, (TimeoutError, socket.timeout)) or "timed out" in str(reason).casefold():
            error_type = "request_timeout"
            message = (
                f"Gemini API 응답이 {effective_timeout}초 안에 완료되지 않아 연결을 종료했습니다. "
                "대량 주제 방향 생성은 더 긴 제한 시간이 필요할 수 있습니다."
            )
        else:
            error_type = "network_error"
            message = f"Gemini API 네트워크 오류: {exc}"
        raise GeminiHttpError(
            _ApiErrorInfo(
                http_status=0,
                error_type=error_type,
                message=message,
                retryable=False,
                retry_delay_seconds=None,
            )
        ) from exc

    try:
        parsed = json.loads(body)
    except json.JSONDecodeError as exc:
        raise GeminiHttpError(
            _ApiErrorInfo(
                http_status=200,
                error_type="invalid_api_response",
                message=f"Gemini API 응답 JSON을 읽을 수 없습니다: {exc.msg}",
                retryable=False,
                retry_delay_seconds=None,
            )
        ) from exc
    if not isinstance(parsed, dict):
        raise GeminiHttpError(
            _ApiErrorInfo(
                http_status=200,
                error_type="invalid_api_response",
                message="Gemini API 응답 객체가 올바르지 않습니다.",
                retryable=False,
                retry_delay_seconds=None,
            )
        )
    finish_reason, finish_message = _extract_finish_metadata(parsed)
    output_text = _extract_output_text(parsed)
    if not output_text:
        raise GeminiHttpError(
            _ApiErrorInfo(
                http_status=200,
                error_type="empty_api_response",
                message="Gemini API가 구조화 JSON을 반환하지 않았습니다.",
                retryable=False,
                retry_delay_seconds=None,
                finish_reason=finish_reason,
                finish_message=finish_message,
            )
        )
    return output_text, *_usage_counts(parsed), finish_reason, finish_message


def _call_interactions_api(
    config: GeminiConfig,
    request_text: str,
    request_hash: str,
) -> tuple[
    str,
    int | None,
    int | None,
    int | None,
    int | None,
    str,
    str,
]:
    return call_gemini_structured_output(
        config,
        request_text,
        request_hash,
        feature_id=GEMINI_FEATURE_ID,
        response_schema=GEMINI_DRAFT_SCHEMA,
        use_google_search=True,
        thinking_level=normalize_gemini_thinking_level(
            config.draft_thinking_level,
            fallback="high",
        ),
    )


def _get_cached_response(
    con: duckdb.DuckDBPyConnection,
    request_hash: str,
) -> str | None:
    row = con.execute(
        "SELECT raw_response FROM gemini_response_cache WHERE request_hash = ?",
        [request_hash],
    ).fetchone()
    return str(row[0]) if row is not None else None


def _save_cached_response(
    con: duckdb.DuckDBPyConnection,
    *,
    config: GeminiConfig,
    content_pack_id: str,
    request_hash: str,
    feature_id: str = GEMINI_FEATURE_ID,
    raw_response: str,
) -> None:
    con.execute(
        """
        INSERT INTO gemini_response_cache(
            request_hash, app_id, quota_scope_id, feature_id, feature_version,
            model_name, schema_version, content_pack_id, raw_response, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(request_hash) DO NOTHING
        """,
        [
            request_hash,
            config.app_id,
            config.quota_scope_id,
            feature_id,
            GEMINI_FEATURE_VERSION,
            config.model,
            GEMINI_SCHEMA_VERSION,
            content_pack_id,
            raw_response,
            datetime.now(),
        ],
    )


def record_gemini_api_call(
    con: duckdb.DuckDBPyConnection,
    *,
    config: GeminiConfig,
    content_pack_id: str,
    request_hash: str,
    feature_id: str = GEMINI_FEATURE_ID,
    feature_version: str = GEMINI_FEATURE_VERSION,
    attempt_number: int,
    cache_hit: bool,
    status: str,
    http_status: int | None,
    error_type: str,
    retry_reason: str,
    retry_wait_seconds: float,
    input_tokens: int | None,
    output_tokens: int | None,
    total_tokens: int | None,
    duration_ms: int,
    error_message: str,
    thought_tokens: int | None = None,
    request_text: str = "",
    response_text: str = "",
    requested_item_count: int | None = None,
    configured_items_per_request: int | None = None,
    thinking_level: str = "",
    request_timeout_seconds: int | None = None,
    finish_reason: str = "",
    finish_message: str = "",
) -> None:
    request_counts = count_text_characters(request_text)
    response_counts = count_text_characters(response_text)
    con.execute(
        """
        INSERT INTO gemini_api_calls(
            call_id, app_id, quota_scope_id, feature_id, feature_version, content_pack_id,
            request_hash, model_name, attempt_number, cache_hit, status,
            http_status, error_type, retry_reason, retry_wait_seconds,
            input_tokens, output_tokens, thought_tokens, total_tokens,
            request_char_count, request_non_whitespace_char_count,
            request_hangul_char_count, response_char_count,
            response_non_whitespace_char_count, response_hangul_char_count,
            requested_item_count, configured_items_per_request, thinking_level,
            request_timeout_seconds, finish_reason, finish_message,
            duration_ms, error_message, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            f"gemcall_{uuid4().hex}",
            config.app_id,
            config.quota_scope_id,
            feature_id,
            _clean_text(feature_version)[:40],
            content_pack_id,
            request_hash,
            config.model,
            int(attempt_number),
            bool(cache_hit),
            status,
            http_status,
            error_type,
            retry_reason,
            float(retry_wait_seconds),
            input_tokens,
            output_tokens,
            thought_tokens,
            total_tokens,
            request_counts.total,
            request_counts.non_whitespace,
            request_counts.hangul,
            response_counts.total,
            response_counts.non_whitespace,
            response_counts.hangul,
            (
                None
                if requested_item_count is None
                else max(0, int(requested_item_count))
            ),
            (
                None
                if configured_items_per_request is None
                else max(0, int(configured_items_per_request))
            ),
            normalize_gemini_thinking_level(thinking_level),
            (
                None
                if request_timeout_seconds is None
                else max(0, int(request_timeout_seconds))
            ),
            _clean_text(finish_reason)[:100],
            _clean_text(finish_message)[:1000],
            int(duration_ms),
            _clean_text(error_message)[:1000],
            datetime.now(),
        ],
    )


def list_recent_gemini_calls(
    con: duckdb.DuckDBPyConnection,
    *,
    app_id: str,
    limit: int = 10,
) -> list[dict[str, Any]]:
    rows = con.execute(
        """
        SELECT created_at, feature_id, feature_version, model_name, attempt_number, cache_hit,
               status, http_status, error_type, retry_wait_seconds,
               request_char_count, request_non_whitespace_char_count,
               request_hangul_char_count, input_tokens,
               response_char_count, response_non_whitespace_char_count,
               response_hangul_char_count, output_tokens, thought_tokens,
               total_tokens, requested_item_count,
               configured_items_per_request, thinking_level,
               request_timeout_seconds, finish_reason, finish_message,
               duration_ms
        FROM gemini_api_calls
        WHERE app_id = ?
        ORDER BY created_at DESC
        LIMIT ?
        """,
        [app_id, max(1, min(int(limit), 100))],
    ).fetchall()
    columns = [item[0] for item in con.description]
    return [dict(zip(columns, row)) for row in rows]


def generate_gemini_draft(
    con: duckdb.DuckDBPyConnection,
    *,
    config: GeminiConfig,
    content_pack_id: str,
    preview: GeminiRequestPreview,
    references: Iterable[dict[str, Any]],
    status_callback: Callable[[str], None] | None = None,
    api_call: Callable[[GeminiConfig, str, str], tuple[Any, ...]] = _call_interactions_api,
    sleep_func: Callable[[float], None] = time.sleep,
) -> GeminiGenerationResult:
    started = time.perf_counter()
    if not config.api_key:
        return GeminiGenerationResult(
            success=False,
            raw_response="",
            request_hash=preview.request_hash,
            model=config.model,
            cache_hit=False,
            attempts=0,
            status="missing_api_key",
            error_type="missing_api_key",
            error_message="GEMINI_API_KEY가 설정되지 않았습니다.",
            http_status=None,
            input_tokens=None,
            output_tokens=None,
            total_tokens=None,
            duration_ms=0,
        )
    if preview.findings:
        return GeminiGenerationResult(
            success=False,
            raw_response="",
            request_hash=preview.request_hash,
            model=config.model,
            cache_hit=False,
            attempts=0,
            status="sensitive_data_blocked",
            error_type="sensitive_data_blocked",
            error_message="민감정보 후보가 있어 Gemini 전송을 차단했습니다.",
            http_status=None,
            input_tokens=None,
            output_tokens=None,
            total_tokens=None,
            duration_ms=0,
        )

    cached = _get_cached_response(con, preview.request_hash)
    if cached is not None:
        duration_ms = int((time.perf_counter() - started) * 1000)
        record_gemini_api_call(
            con,
            config=config,
            content_pack_id=content_pack_id,
            request_hash=preview.request_hash,
            attempt_number=0,
            cache_hit=True,
            status="cache_hit",
            http_status=200,
            error_type="",
            retry_reason="",
            retry_wait_seconds=0,
            input_tokens=None,
            output_tokens=None,
            total_tokens=None,
            duration_ms=duration_ms,
            error_message="",
            request_text=preview.request_text,
            response_text=cached,
        )
        return GeminiGenerationResult(
            success=True,
            raw_response=cached,
            request_hash=preview.request_hash,
            model=config.model,
            cache_hit=True,
            attempts=0,
            status="cache_hit",
            error_type="",
            error_message="",
            http_status=200,
            input_tokens=None,
            output_tokens=None,
            total_tokens=None,
            duration_ms=duration_ms,
        )

    attempt = 0
    waited_seconds = 0.0
    effective_thinking_level = normalize_gemini_thinking_level(
        config.draft_thinking_level,
        fallback="high",
    )
    effective_timeout_seconds = effective_gemini_timeout_seconds(config)
    while True:
        attempt += 1
        call_started = time.perf_counter()
        try:
            api_result = api_call(
                config,
                preview.request_text,
                preview.request_hash,
            )
            (
                output_text,
                input_tokens,
                output_tokens,
                thought_tokens,
                total_tokens,
                finish_reason,
                finish_message,
            ) = normalize_gemini_api_result(api_result)
            canonical_response = _canonicalize_output_sources(output_text, references)
            call_duration_ms = int((time.perf_counter() - call_started) * 1000)
            status = "success_after_retry" if attempt > 1 else "success"
            record_gemini_api_call(
                con,
                config=config,
                content_pack_id=content_pack_id,
                request_hash=preview.request_hash,
                attempt_number=attempt,
                cache_hit=False,
                status=status,
                http_status=200,
                error_type="",
                retry_reason="",
                retry_wait_seconds=0,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                total_tokens=total_tokens,
                duration_ms=call_duration_ms,
                error_message="",
                thought_tokens=thought_tokens,
                request_text=preview.request_text,
                response_text=canonical_response,
                thinking_level=effective_thinking_level,
                request_timeout_seconds=effective_timeout_seconds,
                finish_reason=finish_reason,
                finish_message=finish_message,
            )
            _save_cached_response(
                con,
                config=config,
                content_pack_id=content_pack_id,
                request_hash=preview.request_hash,
                raw_response=canonical_response,
            )
            return GeminiGenerationResult(
                success=True,
                raw_response=canonical_response,
                request_hash=preview.request_hash,
                model=config.model,
                cache_hit=False,
                attempts=attempt,
                status=status,
                error_type="",
                error_message="",
                http_status=200,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                total_tokens=total_tokens,
                duration_ms=int((time.perf_counter() - started) * 1000),
                thought_tokens=thought_tokens,
                finish_reason=finish_reason,
                finish_message=finish_message,
            )
        except GeminiHttpError as exc:
            info = exc.info
            call_duration_ms = int((time.perf_counter() - call_started) * 1000)
            delay = (
                info.retry_delay_seconds
                if info.retry_delay_seconds is not None
                else config.retry_wait_seconds
            )
            can_retry = (
                info.retryable
                and delay >= 0
                and waited_seconds + delay <= config.retry_max_wait_seconds
            )
            record_gemini_api_call(
                con,
                config=config,
                content_pack_id=content_pack_id,
                request_hash=preview.request_hash,
                attempt_number=attempt,
                cache_hit=False,
                status="retrying" if can_retry else info.error_type,
                http_status=info.http_status or None,
                error_type=info.error_type,
                retry_reason=info.error_type if can_retry else "",
                retry_wait_seconds=delay if can_retry else 0,
                input_tokens=None,
                output_tokens=None,
                total_tokens=None,
                duration_ms=call_duration_ms,
                error_message=info.message,
                request_text=preview.request_text,
                thinking_level=effective_thinking_level,
                request_timeout_seconds=effective_timeout_seconds,
                finish_reason=info.finish_reason,
                finish_message=info.finish_message,
            )
            if not can_retry:
                final_status = (
                    "rate_limit_timeout"
                    if info.retryable and info.error_type == "rate_limited"
                    else info.error_type
                )
                return GeminiGenerationResult(
                    success=False,
                    raw_response="",
                    request_hash=preview.request_hash,
                    model=config.model,
                    cache_hit=False,
                    attempts=attempt,
                    status=final_status,
                    error_type=info.error_type,
                    error_message=info.message,
                    http_status=info.http_status or None,
                    input_tokens=None,
                    output_tokens=None,
                    total_tokens=None,
                    duration_ms=int((time.perf_counter() - started) * 1000),
                    finish_reason=info.finish_reason,
                    finish_message=info.finish_message,
                )

            waited_seconds += delay
            if status_callback is not None:
                status_callback(
                    "Gemini API가 일시적으로 제한되었습니다. "
                    f"{delay:g}초 후 다시 시도합니다. "
                    f"{waited_seconds:g}/{config.retry_max_wait_seconds:g}초"
                )
            sleep_func(delay)


def mark_latest_gemini_call_validation_failure(
    con: duckdb.DuckDBPyConnection,
    *,
    app_id: str,
    request_hash: str,
    errors: Iterable[str],
) -> None:
    row = con.execute(
        """
        SELECT call_id
        FROM gemini_api_calls
        WHERE app_id = ? AND request_hash = ?
        ORDER BY created_at DESC
        LIMIT 1
        """,
        [app_id, request_hash],
    ).fetchone()
    if row is None:
        return
    con.execute(
        """
        UPDATE gemini_api_calls
        SET status = 'response_validation_error',
            error_type = 'response_validation_error',
            error_message = ?
        WHERE call_id = ?
        """,
        [" | ".join(str(item) for item in errors)[:1000], str(row[0])],
    )
    con.execute(
        "DELETE FROM gemini_response_cache WHERE request_hash = ?",
        [request_hash],
    )
