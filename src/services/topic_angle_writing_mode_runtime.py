from __future__ import annotations

import hashlib
import json
from functools import wraps
from typing import Any, Mapping


WRITING_MODE_CONTRACT_VERSION = "1"
WRITING_MODE_AUTO = "auto"
WRITING_MODE_MANUAL = "manual"
WRITING_MODE_VALUES = frozenset({WRITING_MODE_AUTO, WRITING_MODE_MANUAL})


def normalize_writing_mode(value: object) -> str:
    normalized = str(value or "").strip().casefold()
    return normalized if normalized in WRITING_MODE_VALUES else WRITING_MODE_MANUAL


def normalize_writing_mode_reason(value: object, *, mode: str) -> str:
    reason = str(value or "").strip()
    if reason:
        return reason[:500]
    if mode == WRITING_MODE_AUTO:
        return "자동 작성 적합 근거가 불명확하여 수동 작성으로 보수적으로 전환했습니다."
    return "작성 방식 판정이 없거나 불명확하여 수동 작성을 추천합니다."


def writing_mode_from_plan(plan: Mapping[str, Any] | None) -> tuple[str, str]:
    source = plan if isinstance(plan, Mapping) else {}
    raw_mode = str(source.get("writing_mode_recommendation") or "").strip().casefold()
    raw_reason = source.get("writing_mode_reason")
    if raw_mode not in WRITING_MODE_VALUES:
        return (
            WRITING_MODE_MANUAL,
            "기존 분석에는 자동/수동 작성 판정이 없어 안전하게 수동 작성을 추천합니다.",
        )
    mode = normalize_writing_mode(raw_mode)
    reason = normalize_writing_mode_reason(raw_reason, mode=mode)
    if mode == WRITING_MODE_AUTO and not str(raw_reason or "").strip():
        return WRITING_MODE_MANUAL, reason
    return mode, reason


def _patch_schema(module) -> None:
    plan_schema = (
        module.TOPIC_ANGLE_SCHEMA["properties"]["clusters"]["items"]["properties"][
            "content_plan"
        ]
    )
    properties = plan_schema.setdefault("properties", {})
    properties["writing_mode_recommendation"] = {
        "type": "string",
        "enum": [WRITING_MODE_AUTO, WRITING_MODE_MANUAL],
    }
    properties["writing_mode_reason"] = {"type": "string"}
    required = plan_schema.setdefault("required", [])
    for name in ("writing_mode_recommendation", "writing_mode_reason"):
        if name not in required:
            required.append(name)


def _install_request_contract(module) -> None:
    target = module._build_request
    if getattr(target, "_writing_mode_request_contract", False):
        return

    @wraps(target)
    def wrapped(config, clusters):
        request_text, request_hash = target(config, clusters)
        marker = "\n\n[글감 목록]\n"
        instructions = (
            " 각 content_plan에는 writing_mode_recommendation을 auto 또는 manual 중 하나로, "
            "writing_mode_reason에는 그 이유를 작성하세요. 모델명은 절대 쓰지 마세요. "
            "auto는 일반 정보성·생활형처럼 최신성 의존과 사실 검증 부담이 낮고, "
            "약간의 표현 오차가 독자 의사결정에 큰 영향을 주지 않으며 출처 충돌 가능성이 낮은 경우에만 선택하세요. "
            "정확한 날짜·가격·금액·비율·지원 조건·자격 요건이 중요하거나 정책·금융·법률·건강처럼 "
            "오류 영향이 큰 주제, 최신성 재확인이 필요한 주제, 출처가 충돌하거나 검증 난도가 높은 주제는 manual로 선택하세요. "
            "자동과 수동 사이에서 애매하거나 확신이 낮으면 반드시 manual로 선택하세요."
        )
        if marker in request_text:
            request_text = request_text.replace(marker, instructions + marker, 1)
        else:
            request_text = request_text + instructions
        salted_hash = hashlib.sha256(
            f"{request_hash}|writing-mode-v{WRITING_MODE_CONTRACT_VERSION}".encode("utf-8")
        ).hexdigest()
        return request_text, salted_hash

    wrapped._writing_mode_request_contract = True  # type: ignore[attr-defined]
    module._build_request = wrapped


def _raw_plan_map(raw_response: str) -> dict[str, Mapping[str, Any]]:
    try:
        parsed = json.loads(str(raw_response or ""))
    except json.JSONDecodeError:
        return {}
    if not isinstance(parsed, Mapping):
        return {}
    rows = parsed.get("clusters")
    if not isinstance(rows, list):
        return {}
    result: dict[str, Mapping[str, Any]] = {}
    for row in rows:
        if not isinstance(row, Mapping):
            continue
        cluster_id = str(row.get("cluster_id") or "").strip()
        plan = row.get("content_plan")
        if cluster_id and isinstance(plan, Mapping):
            result[cluster_id] = plan
    return result


def _install_validation_contract(module) -> None:
    target = module._validated_enrichments
    if getattr(target, "_writing_mode_validation_contract", False):
        return

    @wraps(target)
    def wrapped(raw_response, requested_clusters):
        validated, errors = target(raw_response, requested_clusters)
        raw_plans = _raw_plan_map(raw_response)
        for cluster_id, enrichment in validated.items():
            content_plan = enrichment.get("content_plan")
            if not isinstance(content_plan, dict):
                continue
            mode, reason = writing_mode_from_plan(raw_plans.get(cluster_id))
            content_plan["writing_mode_recommendation"] = mode
            content_plan["writing_mode_reason"] = reason
        return validated, errors

    wrapped._writing_mode_validation_contract = True  # type: ignore[attr-defined]
    module._validated_enrichments = wrapped


def install_topic_angle_writing_mode_contract() -> None:
    """Add conservative auto/manual routing to the existing topic-angle call.

    This runtime contract intentionally keeps the stored topic-angle feature version
    unchanged for backward compatibility with existing tests and persisted rows, while
    salting the request hash so the extended prompt cannot collide with the previous
    request contract. Legacy profiles without the new fields remain readable and resolve
    to manual recommendation.
    """

    import src.services.topic_angle_ai_service as module

    if getattr(module, "_writing_mode_contract_installed", False):
        return
    _patch_schema(module)
    _install_request_contract(module)
    _install_validation_contract(module)
    module._writing_mode_contract_installed = True
