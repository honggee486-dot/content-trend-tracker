from __future__ import annotations

import inspect
import re
from collections.abc import Mapping, Sequence
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any


_GENERIC_WARNING_PREFIXES = (
    "Gemini 자동 방향 · 미생성",
    "Gemini 자동 방향 · 불완전",
)
_GENERIC_CAPTION_PREFIX = "자동 생성된 분석 정보가 없습니다."
_AUTO_MODEL_CAPTION_PREFIX = "자동·예약 분석 모델:"
_AUTO_MODEL_BATCH_PATTERN = re.compile(r"실행당 새 분석 대상 상위 \d+개")
_PENDING_CAPTION: ContextVar[str | None] = ContextVar(
    "topic_angle_status_pending_caption",
    default=None,
)
_RECOMMENDATION_LABELS = {
    "recommended": "추천",
    "review": "검토",
    "hold": "보류",
}
_ALLOWED_RECOMMENDATION_STATUSES = {"recommended", "review"}


@dataclass(frozen=True)
class TopicAngleStatusExplanation:
    state: str
    status_text: str
    caption_text: str
    blockers: tuple[str, ...] = ()


def _safe_float(value: object, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return float(default)


def _safe_int(value: object, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return int(default)


def _safe_public_text(value: object, field: str) -> str:
    text = str(value or "").strip()
    if not text:
        return ""

    from src.services.gemini_service import scan_sensitive_fields

    return "" if scan_sensitive_fields([(field, text)]) else text


def format_runtime_batch_caption(
    value: object,
    *,
    config: object | None = None,
) -> str:
    """자동 분석 모델 안내의 고정 처리량을 현재 런타임 설정값으로 교체합니다."""
    text = str(value or "")
    if not text.strip().startswith(_AUTO_MODEL_CAPTION_PREFIX):
        return text

    if config is None:
        try:
            from src.config import get_gemini_config

            config = get_gemini_config()
        except Exception:
            return text

    batch_limit = max(
        1,
        _safe_int(getattr(config, "topic_angle_batch_limit", 15), 15),
    )
    return _AUTO_MODEL_BATCH_PATTERN.sub(
        f"실행당 새 분석 대상 상위 {batch_limit}개",
        text,
        count=1,
    )


def explain_topic_angle_status(
    *,
    cluster: Mapping[str, Any],
    items: Sequence[Mapping[str, Any]],
    stored_angle_count: int,
    config: object | None = None,
) -> TopicAngleStatusExplanation:
    """선택 글감이 자동 방향 생성 대상인지 실제 선별 조건으로 설명합니다."""
    if config is None:
        from src.config import get_gemini_config

        config = get_gemini_config()

    angle_count = max(0, min(_safe_int(stored_angle_count), 3))
    if angle_count >= 3:
        return TopicAngleStatusExplanation(
            state="complete",
            status_text="Gemini 자동 방향 · 생성 완료 3/3",
            caption_text="자동 방향 3개가 모두 저장되어 있습니다.",
        )

    min_score = _safe_float(
        getattr(config, "topic_angle_min_opportunity_score", 50.0),
        50.0,
    )
    batch_limit = max(
        1,
        _safe_int(getattr(config, "topic_angle_batch_limit", 15), 15),
    )
    opportunity_score = _safe_float(cluster.get("opportunity_score"), 0.0)
    recommendation_status = (
        str(cluster.get("recommendation_status") or "review").strip().casefold()
        or "review"
    )

    blockers: list[str] = []
    if not str(getattr(config, "api_key", "") or "").strip():
        blockers.append("Gemini API 키가 설정되지 않았습니다")

    if opportunity_score < min_score:
        blockers.append(
            f"글감 기회 {opportunity_score:.1f}점으로 자동 분석 기준 {min_score:g}점에 미달합니다"
        )

    if recommendation_status not in _ALLOWED_RECOMMENDATION_STATUSES:
        status_label = _RECOMMENDATION_LABELS.get(
            recommendation_status,
            recommendation_status or "알 수 없음",
        )
        blockers.append(
            f"현재 판정이 ‘{status_label}’라 자동 대상이 아닙니다. ‘추천’ 또는 ‘검토’ 판정이 필요합니다"
        )

    title = _safe_public_text(cluster.get("canonical_title"), "글감 제목")
    if not title:
        blockers.append("제목이 비어 있거나 민감정보 안전성 검사에서 제외됐습니다")

    from src.services.topic_angle_demand_contract import build_evidence_contract

    evidence, _evidence_map = build_evidence_contract(
        [dict(item) for item in items if isinstance(item, Mapping)],
        safe_public_text=_safe_public_text,
        maximum=8,
    )
    if not evidence:
        blockers.append("Gemini에 전달할 수 있는 유효한 원문 근거가 없습니다")

    if blockers:
        return TopicAngleStatusExplanation(
            state="blocked",
            status_text=f"Gemini 자동 방향 · 조건 미충족 {angle_count}/3",
            caption_text=(
                "자동 방향 미생성 이유: "
                + " · ".join(blockers)
                + ". 조건이 충족되면 다음 자동 수집 또는 ‘주제 방향 자동 생성’ 실행에서 다시 판정합니다. "
                "아래 값은 저장된 자동 방향이 아니라 사용자가 직접 입력하는 수동 방향입니다."
            ),
            blockers=tuple(blockers),
        )

    pending_label = "생성 대기 중" if angle_count == 0 else "재생성 대기 중"
    return TopicAngleStatusExplanation(
        state="pending",
        status_text=f"Gemini 자동 방향 · {pending_label} {angle_count}/3",
        caption_text=(
            "자동 생성 조건을 모두 충족했습니다. 다음 자동 수집 또는 ‘주제 방향 자동 생성’ 실행에서 "
            "글감 기회 점수 순으로 처리됩니다. "
            f"실행당 최대 {batch_limit}개이므로 앞선 대기 후보가 많으면 다음 실행으로 넘어갈 수 있습니다. "
            "아래 값은 저장된 자동 방향이 아니라 사용자가 직접 입력하는 수동 방향입니다."
        ),
    )


def _explanation_from_frame(frame) -> TopicAngleStatusExplanation | None:
    local_values = frame.f_locals
    cluster = local_values.get("cluster")
    items = local_values.get("items")
    if not isinstance(cluster, Mapping) or not isinstance(items, Sequence):
        return None

    return explain_topic_angle_status(
        cluster=cluster,
        items=items,
        stored_angle_count=_safe_int(local_values.get("stored_angle_count"), 0),
        config=local_values.get("gemini_config"),
    )


def install_topic_angle_status_explainer(st_module=None) -> None:
    """상세 상태와 자동 분석 모델 안내가 실제 런타임 설정을 사용하게 합니다."""
    if st_module is None:
        import streamlit as st_module

    if getattr(st_module, "_topic_angle_status_explainer_installed", False):
        return

    original_warning = st_module.warning
    original_caption = st_module.caption

    def warning(value: object, *args, **kwargs):
        text = str(value or "").strip()
        if not text.startswith(_GENERIC_WARNING_PREFIXES):
            return original_warning(value, *args, **kwargs)

        frame = inspect.currentframe()
        caller = frame.f_back if frame is not None else None
        try:
            explanation = _explanation_from_frame(caller) if caller is not None else None
        except Exception:
            explanation = None
        finally:
            del frame

        if explanation is None:
            return original_warning(value, *args, **kwargs)

        _PENDING_CAPTION.set(explanation.caption_text)
        return original_warning(explanation.status_text, *args, **kwargs)

    def caption(value: object, *args, **kwargs):
        rendered_value = value
        replacement = _PENDING_CAPTION.get()
        if replacement is not None:
            _PENDING_CAPTION.set(None)
            if str(value or "").strip().startswith(_GENERIC_CAPTION_PREFIX):
                rendered_value = replacement

        rendered_value = format_runtime_batch_caption(rendered_value)
        return original_caption(rendered_value, *args, **kwargs)

    st_module.warning = warning
    st_module.caption = caption
    st_module._topic_angle_status_explainer_installed = True
