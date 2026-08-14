from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any, Callable, Iterable

from src.config import GeminiConfig
from src.services.gemini_service import (
    GeminiHttpError,
    call_gemini_structured_output,
    normalize_gemini_api_result,
)
from src.services.trend_cluster_safety_service import (
    build_existing_option_payload,
    refine_first_stage_candidates,
    resolve_existing_option_id,
)

FEATURE_ID = "trend_cluster_grouping_v3"
FEATURE_VERSION = "4"
MAX_REQUEST_CHARACTERS = 500_000
MAX_CANDIDATES_PER_REQUEST = 300
RESPONSE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "assignments": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "candidate_id": {"type": "string"},
                    "decision": {
                        "type": "string",
                        "enum": ["existing", "new", "uncertain"],
                    },
                    "existing_option_id": {
                        "type": "integer",
                        "minimum": 0,
                        "maximum": 5,
                    },
                    "new_group_id": {"type": "string"},
                    "representative_title": {"type": "string"},
                    "confidence": {
                        "type": "integer",
                        "minimum": 0,
                        "maximum": 100,
                    },
                },
                "required": [
                    "candidate_id",
                    "decision",
                    "existing_option_id",
                    "new_group_id",
                    "representative_title",
                    "confidence",
                ],
                "additionalProperties": False,
            },
        }
    },
    "required": ["assignments"],
    "additionalProperties": False,
}


@dataclass(frozen=True)
class ClusterGroupingExecution:
    status: str
    assignments: tuple[dict[str, Any], ...]
    calls: tuple[dict[str, Any], ...]
    requested_candidates: int
    completed_candidates: int
    uncertain_candidates: int
    error_message: str = ""

    @property
    def groups(self) -> tuple[dict[str, Any], ...]:
        """과거 호출부가 읽던 이름을 비어 있는 호환 값으로 제공합니다."""
        return ()

    @property
    def requested_batches(self) -> int:
        return 1 if self.requested_candidates else 0

    @property
    def requested_candidate_ids(self) -> tuple[str, ...]:
        return tuple(
            str(row.get("candidate_id") or "")
            for row in self.assignments
            if str(row.get("candidate_id") or "")
        )

    @property
    def completed_candidate_ids(self) -> tuple[str, ...]:
        return tuple(
            str(row.get("candidate_id") or "")
            for row in self.assignments
            if str(row.get("candidate_id") or "")
            and str(row.get("decision") or "") != "uncertain"
        )


def _request_text(batch_id: str, candidates: list[dict[str, Any]]) -> str:
    instructions = (
        "아래 1차 군집 후보를 같은 구체적 사건 또는 같은 정보성 주제로 2차 분류하세요. "
        "1차 단계는 must_merge·must_split 규칙으로 확실한 병합과 분리를 먼저 적용했습니다. "
        "각 candidate_id는 assignments에 정확히 한 번만 반환하세요. "
        "candidate마다 existing_options가 있고, 같은 사건이면 decision을 existing으로 한 뒤 "
        "그 candidate 안의 option_id만 existing_option_id로 선택하세요. "
        "existing_options가 없거나 맞는 선택지가 없으면 existing을 선택하지 마세요. "
        "option_id는 candidate마다 1부터 다시 시작하므로 다른 candidate의 번호를 근거로 사용하지 마세요. "
        "기존 군집끼리 서로 병합하지 말고 새 후보만 기존 군집에 연결하세요. "
        "같은 배치의 새 후보끼리 같은 사건이면 decision을 new로 하고 동일한 new_group_id를 사용하세요. "
        "first_stage_rule_ids와 safety_profile의 날짜·회차·제품·행동·방향이 충돌하면 합치지 마세요. "
        "같은 기업이라는 이유만으로 제품 출시·공장 투자·공장 증축·주가 상승·주가 하락·목표주가 전망을 합치지 마세요. "
        "같은 로또 회차·같은 경기·같은 정책 시행처럼 행동·대상·시점이 같은 표현 차이는 묶을 수 있습니다. "
        "판단 근거가 부족하거나 기존 후보와 새 그룹 중 확신하기 어렵다면 uncertain으로 반환하세요. "
        "decision이 existing이 아니면 existing_option_id는 반드시 0으로 반환하세요. "
        "representative_title은 입력 제목에 있는 사실만 사용해 자연스러운 한국어 제목으로 작성하고, "
        "입력에 없는 날짜·수치·제품명·인물·결과를 만들지 마세요. 설명 없이 지정된 JSON만 반환하세요."
    )
def _candidate_payload(candidate: dict[str, Any]) -> dict[str, Any]:
    return {
        "candidate_id": str(candidate.get("candidate_id") or ""),
        "title": str(candidate.get("title") or ""),
        "examples": list(candidate.get("examples") or ())[:3],
        "item_count": int(candidate.get("item_count") or 0),
        "source_types": list(candidate.get("source_types") or ())[:7],
        "publishers": list(candidate.get("publishers") or ())[:5],
        "first_seen_at": str(candidate.get("first_seen_at") or ""),
        "last_seen_at": str(candidate.get("last_seen_at") or ""),
        "first_stage_rule_ids": list(
            candidate.get("first_stage_rule_ids") or ("undetermined",)
        ),
        "safety_profile": dict(candidate.get("safety_profile") or {}),
        "existing_options": build_existing_option_payload(candidate),
    }


def _request_text(batch_id: str, candidates: list[dict[str, Any]]) -> str:
    instructions = (
        "아래 1차 군집 후보를 같은 구체적 사건 또는 같은 정보성 주제로 2차 분류하세요. "
        "1차 단계는 must_merge·must_split 규칙으로 확실한 병합과 분리를 먼저 적용했습니다. "
        "각 candidate_id는 assignments에 정확히 한 번만 반환하세요. "
        "candidate마다 existing_options가 있고, 같은 사건이면 decision을 existing으로 한 뒤 "
        "그 candidate 안의 option_id만 existing_option_id로 선택하세요. "
        "existing_options가 없거나 맞는 선택지가 없으면 existing을 선택하지 마세요. "
        "option_id는 candidate마다 1부터 다시 시작하므로 다른 candidate의 번호를 근거로 사용하지 마세요. "
        "기존 군집끼리 서로 병합하지 말고 새 후보만 기존 군집에 연결하세요. "
        "같은 배치의 새 후보끼리 같은 사건이면 decision을 new로 하고 동일한 new_group_id를 사용하세요. "
        "first_stage_rule_ids와 safety_profile의 날짜·회차·제품·행동·방향이 충돌하면 합치지 마세요. "
        "같은 기업이라는 이유만으로 제품 출시·공장 투자·공장 증축·주가 상승·주가 하락·목표주가 전망을 합치지 마세요. "
        "같은 로또 회차·같은 경기·같은 정책 시행처럼 행동·대상·시점이 같은 표현 차이는 묶을 수 있습니다. "
        "판단 근거가 부족하거나 기존 후보와 새 그룹 중 확신하기 어렵다면 uncertain으로 반환하세요. "
        "decision이 existing이 아니면 existing_option_id는 반드시 0으로 반환하세요. "
        "representative_title은 입력 제목에 있는 사실만 사용해 자연스러운 한국어 제목으로 작성하고, "
        "입력에 없는 날짜·수치·제품명·인물·결과를 만들지 마세요. 설명 없이 지정된 JSON만 반환하세요."
    )
    payload = {
        "batch_id": batch_id,
        "candidates": [_candidate_payload(candidate) for candidate in candidates],
    }
    return instructions + "\n\n" + json.dumps(
        payload,
        ensure_ascii=False,
        separators=(",", ":"),
    )


def _record(**values: Any) -> dict[str, Any]:
    return {
        "feature_id": FEATURE_ID,
        "feature_version": FEATURE_VERSION,
        **values,
    }


def _normalized_candidates(
    candidates: Iterable[dict[str, Any]],
    *,
    max_candidates: int,
) -> list[dict[str, Any]]:
    limit = max(0, min(int(max_candidates), MAX_CANDIDATES_PER_REQUEST))
    normalized: list[dict[str, Any]] = []
    seen: set[str] = set()
    for candidate in candidates:
        candidate_id = str(candidate.get("candidate_id") or "").strip()
        if not candidate_id or candidate_id in seen:
            continue
        seen.add(candidate_id)
        normalized.append(dict(candidate))
        if len(normalized) >= limit:
            break
    return normalized


def select_cluster_batch_candidates(
    candidates: Iterable[dict[str, Any]],
    *,
    batch_id: str = "cluster_batch_0001",
    max_candidates: int = 200,
    max_request_characters: int = MAX_REQUEST_CHARACTERS,
) -> list[dict[str, Any]]:
    """1차 안전 규칙과 요청 상한을 함께 적용해 실제 Gemini 배치를 만듭니다."""
    refined = refine_first_stage_candidates(candidates)
    normalized = _normalized_candidates(refined, max_candidates=max_candidates)
    if not normalized:
        return []
    character_limit = max(20_000, min(int(max_request_characters), 500_000))
    base_text_len = len(_request_text(batch_id, []))
    selected: list[dict[str, Any]] = []
    current_json_len = 0
    for candidate in normalized:
        item_len = len(
            json.dumps(
                _candidate_payload(candidate),
                ensure_ascii=False,
                separators=(",", ":"),
            )
        )
        trial_count = len(selected) + 1
        trial_len = (
            base_text_len - 1 + current_json_len + item_len + (trial_count - 1) + 1
        )
        if selected and trial_len > character_limit:
            break
        selected.append(candidate)
        current_json_len += item_len
    return selected


def classify_cluster_batch(
    config: GeminiConfig,
    candidates: Iterable[dict[str, Any]],
    *,
    batch_id: str = "cluster_batch_0001",
    max_candidates: int = 200,
    api_call: Callable[..., tuple[Any, ...]] = call_gemini_structured_output,
    progress_callback: Callable[[float, str], None] | None = None,
) -> ClusterGroupingExecution:
    selected = select_cluster_batch_candidates(
        candidates,
        batch_id=batch_id,
        max_candidates=max_candidates,
    )
    requested_candidates = len(selected)
    if not selected:
        return ClusterGroupingExecution("nothing_to_group", (), (), 0, 0, 0)
    if not config.api_key:
        return ClusterGroupingExecution(
            "missing_api_key",
            (),
            (),
            requested_candidates,
            0,
            0,
            "GEMINI_API_KEY가 없어 2차 군집을 실행하지 않았습니다.",
        )

    request_text = _request_text(batch_id, selected)
    request_hash = hashlib.sha256(
        f"{FEATURE_ID}|{FEATURE_VERSION}|{config.model}|minimal|{request_text}".encode(
            "utf-8"
        )
    ).hexdigest()
    call_row: dict[str, Any]
    try:
        result = api_call(
            config,
            request_text,
            request_hash,
            feature_id=FEATURE_ID,
            response_schema=RESPONSE_SCHEMA,
            use_google_search=False,
            thinking_level="minimal",
            timeout_seconds=min(max(30, int(config.timeout_seconds)), 240),
        )
        (
            output_text,
            input_tokens,
            output_tokens,
            thought_tokens,
            total_tokens,
            finish_reason,
            finish_message,
        ) = normalize_gemini_api_result(result)
        parsed = json.loads(output_text)
        rows = parsed.get("assignments") if isinstance(parsed, dict) else None
        if not isinstance(rows, list):
            raise ValueError("assignments 배열이 없습니다.")

        candidate_map = {
            str(candidate.get("candidate_id") or ""): candidate
            for candidate in selected
        }
        accepted: list[dict[str, Any]] = []
        seen_ids: set[str] = set()
        validation_errors: list[str] = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            candidate_id = str(row.get("candidate_id") or "").strip()
            if not candidate_id or candidate_id not in candidate_map or candidate_id in seen_ids:
                continue
            decision = str(row.get("decision") or "").strip()
            if decision not in {"existing", "new", "uncertain"}:
                continue
            try:
                existing_option_id = int(row.get("existing_option_id") or 0)
            except (TypeError, ValueError, OverflowError):
                existing_option_id = -1
            existing_cluster_id = ""
            new_group_id = str(row.get("new_group_id") or "").strip()
            title = " ".join(str(row.get("representative_title") or "").split()).strip()
            try:
                confidence = int(row.get("confidence"))
            except (TypeError, ValueError, OverflowError):
                continue
            if not 0 <= confidence <= 100:
                continue

            if decision == "existing":
                existing_cluster_id = resolve_existing_option_id(
                    candidate_map[candidate_id],
                    existing_option_id,
                )
                new_group_id = ""
                if not existing_cluster_id:
                    validation_errors.append(
                        f"{candidate_id}: 허용되지 않은 기존 군집 선택 {existing_option_id}"
                    )
                    decision = "uncertain"
            elif decision == "new":
                if existing_option_id != 0:
                    validation_errors.append(
                        f"{candidate_id}: new 결정의 existing_option_id는 0이어야 함"
                    )
                    decision = "uncertain"
                elif not new_group_id:
                    validation_errors.append(f"{candidate_id}: new_group_id 누락")
                    decision = "uncertain"
            else:
                if existing_option_id != 0:
                    validation_errors.append(
                        f"{candidate_id}: uncertain 결정의 existing_option_id는 0이어야 함"
                    )
                decision = "uncertain"

            if decision == "uncertain":
                existing_cluster_id = ""
                new_group_id = ""
                existing_option_id = 0
            if decision != "uncertain" and not title:
                validation_errors.append(f"{candidate_id}: 대표 제목 누락")
                decision = "uncertain"
                existing_cluster_id = ""
                new_group_id = ""
                existing_option_id = 0

            seen_ids.add(candidate_id)
            accepted.append(
                {
                    "candidate_id": candidate_id,
                    "decision": decision,
                    "existing_option_id": existing_option_id,
                    "existing_cluster_id": existing_cluster_id,
                    "new_group_id": new_group_id,
                    "representative_title": title,
                    "confidence": confidence,
                }
            )

        missing = [candidate_id for candidate_id in candidate_map if candidate_id not in seen_ids]
        accepted.extend(
            {
                "candidate_id": candidate_id,
                "decision": "uncertain",
                "existing_option_id": 0,
                "existing_cluster_id": "",
                "new_group_id": "",
                "representative_title": "",
                "confidence": 0,
            }
            for candidate_id in missing
        )
        uncertain_count = sum(
            str(row.get("decision") or "") == "uncertain" for row in accepted
        )
        completed_count = requested_candidates - uncertain_count
        error_parts = []
        if missing:
            error_parts.append(f"응답 누락 {len(missing)}개")
        if validation_errors:
            error_parts.append("; ".join(validation_errors[:10]))
        call_row = _record(
            request_hash=request_hash,
            request_text=request_text,
            response_text=output_text,
            requested_item_count=requested_candidates,
            status="success",
            http_status=200,
            error_type="",
            error_message=" | ".join(error_parts),
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            thought_tokens=thought_tokens,
            total_tokens=total_tokens,
            finish_reason=finish_reason,
            finish_message=finish_message,
        )
        if completed_count == requested_candidates:
            status = "success"
        elif completed_count > 0:
            status = "partial"
        else:
            status = "uncertain"
        return ClusterGroupingExecution(
            status=status,
            assignments=tuple(accepted),
            calls=(call_row,),
            requested_candidates=requested_candidates,
            completed_candidates=completed_count,
            uncertain_candidates=uncertain_count,
            error_message=" | ".join(error_parts)[:1500],
        )
    except Exception as exc:
        info = exc.info if isinstance(exc, GeminiHttpError) else None
        error_type = str(getattr(info, "error_type", "") or type(exc).__name__)
        error_message = str(getattr(info, "message", "") or exc)
        call_row = _record(
            request_hash=request_hash,
            request_text=request_text,
            response_text="",
            requested_item_count=requested_candidates,
            status="failed",
            http_status=getattr(info, "http_status", None),
            error_type=error_type,
            error_message=error_message,
            input_tokens=None,
            output_tokens=None,
            thought_tokens=None,
            total_tokens=None,
            finish_reason="",
            finish_message="",
        )
        return ClusterGroupingExecution(
            "failed",
            (),
            (call_row,),
            requested_candidates,
            0,
            requested_candidates,
            error_message,
        )


def cluster_title_batches(
    config: GeminiConfig,
    batches: Iterable[dict[str, Any]],
    *,
    max_candidates: int = 200,
    api_call: Callable[..., tuple[Any, ...]] = call_gemini_structured_output,
    progress_callback: Callable[[float, str], None] | None = None,
) -> ClusterGroupingExecution:
    """과거 호출부를 위한 단일 배치 호환 래퍼입니다."""
    selected_candidates: list[dict[str, Any]] = []
    batch_id = "cluster_batch_0001"
    for batch in batches:
        batch_id = str(batch.get("batch_id") or batch_id)
        selected_candidates.extend(batch.get("candidates") or ())
        if len(selected_candidates) >= max_candidates:
            break
    if progress_callback is not None:
        progress_callback(0.0, f"Gemini {config.model} · 2차 군집 배치 요청 중")
    result = classify_cluster_batch(
        config,
        selected_candidates,
        batch_id=batch_id,
        max_candidates=max_candidates,
        api_call=api_call,
    )
    if progress_callback is not None:
        progress_callback(1.0, f"Gemini {config.model} · 2차 군집 배치 완료")
    return result
