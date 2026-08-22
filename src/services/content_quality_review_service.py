from __future__ import annotations

import json
import re
from copy import deepcopy
from dataclasses import asdict, dataclass
from typing import Any, Callable, Mapping

REVIEWER_LABEL = "Luna High"
REVIEW_SCHEMA_VERSION = "1.0"
ALLOWED_REVIEW_STATUS = {"pass", "revision_needed"}
ALLOWED_SEVERITY = {"high", "medium", "low"}
ALLOWED_REVISION_TYPES = {
    "fact_support",
    "consistency",
    "coverage",
    "structure",
    "redundancy",
    "clarity",
    "seo",
    "style",
}
MAX_REVISION_REQUESTS = 30
MAX_KEEP_POINTS = 20


class ContentQualityReviewError(ValueError):
    """Raised when a Luna review response does not satisfy the contract."""


@dataclass(frozen=True)
class RevisionRequest:
    severity: str
    type: str
    target: str
    problem: str
    request: str


@dataclass(frozen=True)
class ContentQualityReview:
    review_status: str
    overall_reason: str
    revision_requests: tuple[RevisionRequest, ...]
    keep_points: tuple[str, ...]
    reviewer: str = REVIEWER_LABEL
    schema_version: str = REVIEW_SCHEMA_VERSION

    def to_dict(self) -> dict[str, Any]:
        result = asdict(self)
        result["revision_requests"] = [asdict(item) for item in self.revision_requests]
        result["keep_points"] = list(self.keep_points)
        return result


@dataclass(frozen=True)
class PostWritingQualityCycleResult:
    status: str
    review: ContentQualityReview | None
    final_text_data: dict[str, Any] | None
    review_response_text: str = ""
    rewrite_response_text: str = ""
    errors: tuple[str, ...] = ()
    requires_final_fact_check: bool = False
    requires_image_planning: bool = False

    @property
    def succeeded(self) -> bool:
        return self.status in {"review_pass", "revision_complete"} and self.final_text_data is not None


TextRunner = Callable[[str], str]
ResultParser = Callable[[str], Any]


def _clean_text(value: object, *, maximum: int) -> str:
    return " ".join(str(value or "").split())[:maximum].strip()


def _extract_json_object(text: object) -> Mapping[str, Any] | None:
    cleaned = str(text or "").strip()
    if not cleaned:
        return None
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r"\s*```$", "", cleaned)
    try:
        value = json.loads(cleaned)
        return value if isinstance(value, Mapping) else None
    except json.JSONDecodeError:
        pass
    start, end = cleaned.find("{"), cleaned.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        value = json.loads(cleaned[start : end + 1])
    except json.JSONDecodeError:
        return None
    return value if isinstance(value, Mapping) else None


def _text_only_blocks(data: Mapping[str, Any]) -> list[dict[str, Any]]:
    blocks = data.get("blocks")
    if not isinstance(blocks, list):
        return []
    return [
        deepcopy(dict(block))
        for block in blocks
        if isinstance(block, Mapping) and str(block.get("type") or "") != "image"
    ]


def build_text_review_payload(
    data: Mapping[str, Any],
    *,
    context: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Build the text/fact/source subset reviewed by Luna; image work is excluded."""
    payload: dict[str, Any] = {}
    for key in (
        "schema_version",
        "title",
        "summary",
        "category",
        "tags",
        "seo",
        "fact_checks",
        "sources",
    ):
        if key in data:
            payload[key] = deepcopy(data[key])
    blocks = _text_only_blocks(data)
    if blocks:
        payload["blocks"] = blocks
    elif "body_markdown" in data:
        payload["body_markdown"] = str(data.get("body_markdown") or "")
    if context:
        selected = {
            key: deepcopy(context[key])
            for key in ("topic_title", "angle", "audience", "purpose", "target_length")
            if key in context and str(context[key]).strip()
        }
        if selected:
            payload["writing_context"] = selected
    payload["image_review_excluded"] = True
    return payload


def build_luna_review_prompt(
    data: Mapping[str, Any],
    *,
    context: Mapping[str, Any] | None = None,
) -> str:
    schema = {
        "schema_version": REVIEW_SCHEMA_VERSION,
        "review_status": "pass | revision_needed",
        "overall_reason": "전체 판단 이유",
        "revision_requests": [
            {
                "severity": "high | medium | low",
                "type": "fact_support | consistency | coverage | structure | redundancy | clarity | seo | style",
                "target": "수정할 문단/소제목/요소",
                "problem": "문제 설명",
                "request": "기존 작성 모델에 전달할 구체적인 수정 요청",
            }
        ],
        "keep_points": ["재작성 때 반드시 보존할 좋은 점"],
    }
    return (
        f"당신은 {REVIEWER_LABEL} 역할의 정보성 블로그 글 품질 감사자입니다. "
        "글을 직접 다시 쓰지 말고 현재 원고의 텍스트만 검토해 기존 작성 모델에 전달할 수정 요청만 만드세요.\n\n"
        "[검토 범위]\n"
        "- 사실 근거보다 강한 단정, source/fact_check와 본문 주장 불일치\n"
        "- 모순, 중요한 정보 누락, 검색 의도 불충족\n"
        "- 반복, 장황함, 구조, 핵심 답변이 늦는 문제\n"
        "- 제목·요약·SEO와 본문 불일치, 부자연스러운 키워드 반복\n"
        "- AI 상투문구, 가독성·명료성 문제\n\n"
        "[금지]\n"
        "- 원고를 대신 재작성하지 마세요.\n"
        "- 새로운 사실·수치·날짜·출처 URL·source_id를 만들지 마세요.\n"
        "- 근거가 부족하면 정답을 지어내지 말고 삭제/완화/공식 근거 재확인을 요청하세요.\n"
        "- 이미지·캡처·썸네일·image block은 검토하지 마세요. 이미지는 최종 텍스트 뒤 별도 처리합니다.\n"
        "- 문제가 없는 부분까지 고치라고 요구하지 마세요.\n\n"
        "[판정]\n"
        "- 수정이 필요 없으면 review_status=pass, revision_requests=[]입니다.\n"
        "- 수정이 필요하면 review_status=revision_needed이고 필요한 요청만 적습니다.\n"
        "- keep_points에는 재작성 때 훼손하면 안 되는 부분만 적습니다.\n"
        "- 설명이나 Markdown 없이 JSON object 하나만 반환하세요.\n\n"
        "[출력 schema]\n"
        + json.dumps(schema, ensure_ascii=False, separators=(",", ":"))
        + "\n\n[검토할 원고]\n"
        + json.dumps(
            build_text_review_payload(data, context=context),
            ensure_ascii=False,
            separators=(",", ":"),
        )
    )


def _require_string(item: Mapping[str, Any], key: str, *, maximum: int) -> str:
    value = item.get(key)
    if not isinstance(value, str):
        raise ContentQualityReviewError(f"{key}는 문자열이어야 합니다.")
    cleaned = _clean_text(value, maximum=maximum)
    if not cleaned:
        raise ContentQualityReviewError(f"{key}는 비워둘 수 없습니다.")
    return cleaned


def parse_luna_review_response(text: object) -> ContentQualityReview:
    payload = _extract_json_object(text)
    if payload is None:
        raise ContentQualityReviewError("Luna 검수 응답에서 JSON object를 찾을 수 없습니다.")
    version = str(payload.get("schema_version") or REVIEW_SCHEMA_VERSION).strip()
    if version != REVIEW_SCHEMA_VERSION:
        raise ContentQualityReviewError(f"지원하지 않는 검수 schema_version입니다: {version}")
    status = str(payload.get("review_status") or "").strip()
    if status not in ALLOWED_REVIEW_STATUS:
        raise ContentQualityReviewError("review_status는 pass 또는 revision_needed여야 합니다.")
    reason = _require_string(payload, "overall_reason", maximum=1200)
    raw_requests = payload.get("revision_requests")
    if not isinstance(raw_requests, list):
        raise ContentQualityReviewError("revision_requests는 배열이어야 합니다.")
    if len(raw_requests) > MAX_REVISION_REQUESTS:
        raise ContentQualityReviewError("revision_requests가 허용 개수를 초과했습니다.")

    requests: list[RevisionRequest] = []
    seen: set[tuple[str, str, str]] = set()
    for raw in raw_requests:
        if not isinstance(raw, Mapping):
            raise ContentQualityReviewError("revision_requests의 각 항목은 객체여야 합니다.")
        severity = str(raw.get("severity") or "").strip()
        kind = str(raw.get("type") or "").strip()
        if severity not in ALLOWED_SEVERITY:
            raise ContentQualityReviewError(f"지원하지 않는 severity입니다: {severity}")
        if kind not in ALLOWED_REVISION_TYPES:
            raise ContentQualityReviewError(f"지원하지 않는 revision type입니다: {kind}")
        target = _require_string(raw, "target", maximum=500)
        problem = _require_string(raw, "problem", maximum=1200)
        request = _require_string(raw, "request", maximum=1600)
        dedupe_key = (kind, target.casefold(), request.casefold())
        if dedupe_key in seen:
            continue
        seen.add(dedupe_key)
        requests.append(RevisionRequest(severity, kind, target, problem, request))

    raw_keep = payload.get("keep_points", [])
    if not isinstance(raw_keep, list):
        raise ContentQualityReviewError("keep_points는 배열이어야 합니다.")
    keep_points: list[str] = []
    for value in raw_keep[:MAX_KEEP_POINTS]:
        if not isinstance(value, str):
            raise ContentQualityReviewError("keep_points의 각 항목은 문자열이어야 합니다.")
        cleaned = _clean_text(value, maximum=600)
        if cleaned and cleaned not in keep_points:
            keep_points.append(cleaned)

    if status == "pass" and requests:
        raise ContentQualityReviewError("pass 판정에는 revision_requests를 둘 수 없습니다.")
    if status == "revision_needed" and not requests:
        raise ContentQualityReviewError("revision_needed 판정에는 최소 하나의 수정 요청이 필요합니다.")
    return ContentQualityReview(status, reason, tuple(requests), tuple(keep_points))


def prepare_final_text_data(data: Mapping[str, Any]) -> dict[str, Any]:
    """Remove provisional image plans so final-image routing is rebuilt from final text."""
    result = deepcopy(dict(data))
    if isinstance(result.get("blocks"), list):
        blocks = _text_only_blocks(result)
        result["blocks"] = blocks
        try:
            from src.services.ai_result_parser import blocks_to_markdown

            result["body_markdown"] = blocks_to_markdown(
                title=str(result.get("title") or ""),
                blocks=blocks,
            )
        except Exception:
            # blocks remain authoritative; body_markdown is a presentation derivative.
            pass
    result["image_prompts"] = []
    result.pop("image_acquisition_plans", None)
    return result


def build_writer_revision_prompt(
    original_data: Mapping[str, Any],
    review: ContentQualityReview,
    *,
    context: Mapping[str, Any] | None = None,
) -> str:
    if review.review_status != "revision_needed":
        return ""
    instructions = {
        "revision_requests": [asdict(item) for item in review.revision_requests],
        "keep_points": list(review.keep_points),
    }
    return (
        "아래 원고는 1차 작성과 기본 형식·출처 검사를 통과했습니다. "
        "Luna High 품질 감사의 수정 요청만 필요한 범위에서 반영해 최종 글을 정리하세요.\n\n"
        "[수정 원칙]\n"
        "- 문제가 없는 문장·구조와 keep_points는 가능한 그대로 유지하세요.\n"
        "- revision_requests에 없는 이유로 글 전체를 새로 쓰지 마세요.\n"
        "- 사실·수치·날짜·URL·source_id를 임의로 만들지 마세요.\n"
        "- 기존 근거로 확정할 수 없는 주장은 삭제하거나 완화하고 needs_verification으로 남기세요.\n"
        "- 새 근거는 실제 검색·검증 가능한 실행환경에서 확인했을 때만 추가하세요.\n"
        "- 최종 본문 기준으로 fact_checks를 다시 감사하고 verified는 실제 source_ids가 직접 뒷받침할 때만 사용하세요.\n"
        "- 모든 image block을 제외하고 image_prompts는 빈 배열로 두세요. 이미지는 최종 텍스트 검증 뒤 다시 계획합니다.\n"
        "- 기존 schema_version과 필수 JSON 구조를 유지하고 JSON object 하나만 반환하세요.\n\n"
        "[Luna 수정 요청]\n"
        + json.dumps(instructions, ensure_ascii=False, separators=(",", ":"))
        + "\n\n[원래 원고]\n"
        + json.dumps(
            build_text_review_payload(original_data, context=context),
            ensure_ascii=False,
            separators=(",", ":"),
        )
    )


def run_post_writing_quality_cycle(
    original_data: Mapping[str, Any],
    *,
    luna_runner: TextRunner,
    writer_runner: TextRunner,
    context: Mapping[str, Any] | None = None,
    parse_result: ResultParser | None = None,
) -> PostWritingQualityCycleResult:
    """Run exactly one Luna audit and, when requested, exactly one writer revision."""
    review_response = ""
    try:
        review_response = str(
            luna_runner(build_luna_review_prompt(original_data, context=context)) or ""
        )
        review = parse_luna_review_response(review_response)
    except Exception as exc:
        return PostWritingQualityCycleResult(
            "review_invalid",
            None,
            None,
            review_response_text=review_response,
            errors=(str(exc),),
        )

    if review.review_status == "pass":
        return PostWritingQualityCycleResult(
            "review_pass",
            review,
            prepare_final_text_data(original_data),
            review_response_text=review_response,
            requires_final_fact_check=True,
            requires_image_planning=True,
        )

    revision_prompt = build_writer_revision_prompt(
        original_data,
        review,
        context=context,
    )
    try:
        rewrite_response = str(writer_runner(revision_prompt) or "")
    except Exception as exc:
        return PostWritingQualityCycleResult(
            "rewrite_failed",
            review,
            None,
            review_response_text=review_response,
            errors=(str(exc),),
        )

    if parse_result is None:
        from src.services.ai_result_parser import parse_ai_result

        parse_result = parse_ai_result
    try:
        parsed = parse_result(rewrite_response)
    except Exception as exc:
        return PostWritingQualityCycleResult(
            "rewrite_invalid",
            review,
            None,
            review_response_text=review_response,
            rewrite_response_text=rewrite_response,
            errors=(str(exc),),
        )
    if not bool(getattr(parsed, "is_valid", False)) or getattr(parsed, "data", None) is None:
        errors = tuple(str(item) for item in (getattr(parsed, "errors", None) or ()))
        return PostWritingQualityCycleResult(
            "rewrite_invalid",
            review,
            None,
            review_response_text=review_response,
            rewrite_response_text=rewrite_response,
            errors=errors
            or ("기존 AI 결과 검사기가 수정 원고를 유효하다고 인정하지 않았습니다.",),
        )

    return PostWritingQualityCycleResult(
        "revision_complete",
        review,
        prepare_final_text_data(parsed.data),
        review_response_text=review_response,
        rewrite_response_text=rewrite_response,
        requires_final_fact_check=True,
        requires_image_planning=True,
    )
