from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass, replace
from datetime import datetime
from typing import Any, Callable

from src.config import GeminiConfig, get_gemini_config

GEMINI_MODELS_URL = "https://generativelanguage.googleapis.com/v1beta/models"

AUTO_MODEL_SETTING = "gemini_auto_analysis_model"
# 기본 군집화 모델은 기존 자료 검토 키를 재사용하고 과거 초안 키는 읽기 호환합니다.
DATA_REVIEW_MODEL_SETTING = "gemini_data_review_model"
MANUAL_MODEL_SETTING = "gemini_manual_draft_model"
MODEL_CATALOG_SETTING = "gemini_model_catalog_json"
MODEL_CATALOG_REFRESHED_AT_SETTING = "gemini_model_catalog_refreshed_at"

MODEL_PURPOSE_AUTO = "auto_analysis"
MODEL_PURPOSE_DATA_REVIEW = "data_review"
MODEL_PURPOSE_MANUAL = "manual_draft"  # legacy alias

DEFAULT_AUTO_MODEL = "gemini-3.6-flash"
DEFAULT_DATA_REVIEW_MODEL = "gemini-3.5-flash-lite"
DEFAULT_MANUAL_MODEL = DEFAULT_DATA_REVIEW_MODEL

# Google AI Studio의 프로젝트별 실제 한도가 우선입니다. 아래 값은 사용자가
# 2026-07-27에 확인한 무료 티어 참고값이며 화면 비교에만 사용합니다.
MODEL_RATE_LIMIT_REFERENCES: dict[str, dict[str, int]] = {
    "gemini-3.6-flash": {"rpm": 5, "tpm": 250_000, "rpd": 20},
    "gemini-3.5-flash-lite": {"rpm": 15, "tpm": 250_000, "rpd": 500},
}


@dataclass(frozen=True)
class GeminiModelInfo:
    model_id: str
    display_name: str
    description: str = ""
    input_token_limit: int | None = None
    output_token_limit: int | None = None
    supported_generation_methods: tuple[str, ...] = ()
    thinking: bool | None = None
    lifecycle: str = "stable"


class GeminiModelCatalogError(RuntimeError):
    pass


def _get_setting(con: Any, key: str, default: str = "") -> str:
    row = con.execute(
        "SELECT setting_value FROM app_settings WHERE setting_key = ?",
        [key],
    ).fetchone()
    return default if row is None or row[0] is None else str(row[0])


def _set_setting(con: Any, key: str, value: str) -> None:
    con.execute(
        """
        INSERT INTO app_settings(setting_key, setting_value, updated_at)
        VALUES (?, ?, ?)
        ON CONFLICT(setting_key) DO UPDATE SET
            setting_value = EXCLUDED.setting_value,
            updated_at = EXCLUDED.updated_at
        """,
        [key, value, datetime.now()],
    )


def normalize_model_id(value: Any) -> str:
    model_id = str(value or "").strip()
    if model_id.startswith("models/"):
        model_id = model_id.split("/", 1)[1]
    return model_id


def _as_optional_int(value: Any) -> int | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _lifecycle_for(model_id: str) -> str:
    normalized = model_id.casefold()
    if "experimental" in normalized or "-exp" in normalized:
        return "experimental"
    if "preview" in normalized:
        return "preview"
    if normalized.endswith("-latest"):
        return "latest_alias"
    return "stable"


def _is_selectable_text_model(raw: dict[str, Any]) -> bool:
    methods = {
        str(item or "").strip()
        for item in (raw.get("supportedGenerationMethods") or [])
    }
    model_id = normalize_model_id(raw.get("baseModelId") or raw.get("name"))
    normalized = model_id.casefold()
    if "generateContent" not in methods or not normalized.startswith("gemini-"):
        return False
    excluded_markers = (
        "embedding",
        "imagen",
        "veo",
        "tts",
        "live",
        "native-audio",
        "image-generation",
        "-image",
        "robotics",
        "computer-use",
    )
    return not any(marker in normalized for marker in excluded_markers)


def _model_info_from_api(raw: dict[str, Any]) -> GeminiModelInfo:
    model_id = normalize_model_id(raw.get("baseModelId") or raw.get("name"))
    display_name = str(raw.get("displayName") or model_id).strip() or model_id
    methods = tuple(
        str(item or "").strip()
        for item in (raw.get("supportedGenerationMethods") or [])
        if str(item or "").strip()
    )
    thinking_raw = raw.get("thinking")
    thinking = thinking_raw if isinstance(thinking_raw, bool) else None
    return GeminiModelInfo(
        model_id=model_id,
        display_name=display_name,
        description=str(raw.get("description") or "").strip(),
        input_token_limit=_as_optional_int(raw.get("inputTokenLimit")),
        output_token_limit=_as_optional_int(raw.get("outputTokenLimit")),
        supported_generation_methods=methods,
        thinking=thinking,
        lifecycle=_lifecycle_for(model_id),
    )


def _fallback_models() -> list[GeminiModelInfo]:
    return [
        GeminiModelInfo(
            model_id="gemini-3.6-flash",
            display_name="Gemini 3.6 Flash",
            input_token_limit=1_048_576,
            output_token_limit=65_536,
            supported_generation_methods=("generateContent",),
            thinking=True,
        ),
        GeminiModelInfo(
            model_id="gemini-3.5-flash-lite",
            display_name="Gemini 3.5 Flash-Lite",
            input_token_limit=1_048_576,
            output_token_limit=65_536,
            supported_generation_methods=("generateContent",),
            thinking=True,
        ),
    ]


def _sort_key(model: GeminiModelInfo) -> tuple[int, int, str]:
    preferred = {
        "gemini-3.6-flash": 0,
        "gemini-3.5-flash-lite": 1,
    }
    lifecycle_order = {
        "stable": 0,
        "latest_alias": 1,
        "preview": 2,
        "experimental": 3,
    }
    return (
        preferred.get(model.model_id, 100),
        lifecycle_order.get(model.lifecycle, 9),
        model.display_name.casefold(),
    )


def fetch_gemini_model_catalog(
    api_key: str,
    *,
    timeout_seconds: int = 30,
    opener: Callable[..., Any] | None = None,
) -> list[GeminiModelInfo]:
    key = str(api_key or "").strip()
    if not key:
        raise GeminiModelCatalogError("GEMINI_API_KEY가 설정되지 않았습니다.")

    open_url = opener or urllib.request.urlopen
    page_token = ""
    found: dict[str, GeminiModelInfo] = {}

    while True:
        query: dict[str, str | int] = {"pageSize": 1000}
        if page_token:
            query["pageToken"] = page_token
        url = f"{GEMINI_MODELS_URL}?{urllib.parse.urlencode(query)}"
        request = urllib.request.Request(
            url,
            headers={"x-goog-api-key": key},
            method="GET",
        )
        try:
            with open_url(request, timeout=max(5, int(timeout_seconds))) as response:
                body = response.read().decode("utf-8", errors="replace")
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace").strip()
            raise GeminiModelCatalogError(
                f"Gemini 모델 목록 조회 실패(HTTP {int(exc.code)}): {detail or exc.reason}"
            ) from exc
        except (urllib.error.URLError, TimeoutError) as exc:
            raise GeminiModelCatalogError(
                f"Gemini 모델 목록 네트워크 오류: {exc}"
            ) from exc

        try:
            payload = json.loads(body)
        except json.JSONDecodeError as exc:
            raise GeminiModelCatalogError(
                f"Gemini 모델 목록 응답 JSON을 읽을 수 없습니다: {exc.msg}"
            ) from exc
        if not isinstance(payload, dict):
            raise GeminiModelCatalogError("Gemini 모델 목록 응답 형식이 올바르지 않습니다.")

        for raw in payload.get("models") or []:
            if not isinstance(raw, dict) or not _is_selectable_text_model(raw):
                continue
            model = _model_info_from_api(raw)
            if model.model_id:
                found[model.model_id] = model

        page_token = str(payload.get("nextPageToken") or "").strip()
        if not page_token:
            break

    if not found:
        raise GeminiModelCatalogError(
            "generateContent를 지원하는 Gemini 텍스트 모델을 찾지 못했습니다."
        )
    return sorted(found.values(), key=_sort_key)


def save_gemini_model_catalog(
    con: Any,
    models: list[GeminiModelInfo],
    *,
    refreshed_at: datetime | None = None,
) -> None:
    payload = [
        {
            **asdict(model),
            "supported_generation_methods": list(model.supported_generation_methods),
        }
        for model in models
    ]
    timestamp = (refreshed_at or datetime.now().astimezone()).isoformat(
        timespec="seconds"
    )
    _set_setting(con, MODEL_CATALOG_SETTING, json.dumps(payload, ensure_ascii=False))
    _set_setting(con, MODEL_CATALOG_REFRESHED_AT_SETTING, timestamp)


def load_gemini_model_catalog(
    con: Any,
) -> list[GeminiModelInfo]:
    raw = _get_setting(con, MODEL_CATALOG_SETTING, "")
    try:
        rows = json.loads(raw) if raw else []
    except json.JSONDecodeError:
        rows = []
    models: dict[str, GeminiModelInfo] = {}
    if isinstance(rows, list):
        for row in rows:
            if not isinstance(row, dict):
                continue
            model_id = normalize_model_id(row.get("model_id"))
            if not model_id:
                continue
            methods = tuple(
                str(item or "").strip()
                for item in (row.get("supported_generation_methods") or [])
                if str(item or "").strip()
            )
            models[model_id] = GeminiModelInfo(
                model_id=model_id,
                display_name=str(row.get("display_name") or model_id).strip()
                or model_id,
                description=str(row.get("description") or "").strip(),
                input_token_limit=_as_optional_int(row.get("input_token_limit")),
                output_token_limit=_as_optional_int(row.get("output_token_limit")),
                supported_generation_methods=methods,
                thinking=(
                    row.get("thinking")
                    if isinstance(row.get("thinking"), bool)
                    else None
                ),
                lifecycle=str(row.get("lifecycle") or _lifecycle_for(model_id)),
            )
    return sorted(models.values(), key=_sort_key)


def get_available_gemini_models(
    con: Any,
    *,
    base_config: GeminiConfig | None = None,
) -> list[GeminiModelInfo]:
    base = base_config or get_gemini_config()
    merged: dict[str, GeminiModelInfo] = {
        model.model_id: model for model in _fallback_models()
    }
    for model in load_gemini_model_catalog(con):
        merged[model.model_id] = model

    for model_id in (
        normalize_model_id(_get_setting(con, AUTO_MODEL_SETTING, "")),
        normalize_model_id(_get_setting(con, DATA_REVIEW_MODEL_SETTING, "")),
        normalize_model_id(_get_setting(con, MANUAL_MODEL_SETTING, "")),
        normalize_model_id(base.model),
    ):
        if model_id and model_id not in merged:
            merged[model_id] = GeminiModelInfo(
                model_id=model_id,
                display_name=model_id,
                lifecycle=_lifecycle_for(model_id),
            )
    return sorted(merged.values(), key=_sort_key)


def _setting_key_for_purpose(purpose: str) -> str:
    normalized = str(purpose or "").strip().casefold()
    if normalized == MODEL_PURPOSE_AUTO:
        return AUTO_MODEL_SETTING
    if normalized in {MODEL_PURPOSE_DATA_REVIEW, MODEL_PURPOSE_MANUAL}:
        return DATA_REVIEW_MODEL_SETTING
    raise ValueError(f"지원하지 않는 Gemini 모델 용도입니다: {purpose}")


def get_selected_gemini_model(
    con: Any,
    purpose: str,
    *,
    base_config: GeminiConfig | None = None,
) -> str:
    base = base_config or get_gemini_config()
    normalized_purpose = str(purpose or "").strip().casefold()
    key = _setting_key_for_purpose(normalized_purpose)
    stored = normalize_model_id(_get_setting(con, key, ""))
    if stored:
        return stored
    if normalized_purpose in {MODEL_PURPOSE_DATA_REVIEW, MODEL_PURPOSE_MANUAL}:
        legacy = normalize_model_id(_get_setting(con, MANUAL_MODEL_SETTING, ""))
        if legacy:
            return legacy
        return DEFAULT_DATA_REVIEW_MODEL
    fallback = normalize_model_id(base.model)
    if fallback:
        return fallback
    return DEFAULT_AUTO_MODEL


def set_selected_gemini_model(
    con: Any,
    purpose: str,
    model_id: str,
) -> str:
    normalized = normalize_model_id(model_id)
    if not normalized:
        raise ValueError("Gemini 모델명을 비워 둘 수 없습니다.")
    _set_setting(con, _setting_key_for_purpose(purpose), normalized)
    return normalized


def build_gemini_config_for_purpose(
    con: Any,
    purpose: str,
    *,
    base_config: GeminiConfig | None = None,
) -> GeminiConfig:
    base = base_config or get_gemini_config()
    return replace(
        base,
        model=get_selected_gemini_model(con, purpose, base_config=base),
    )


def model_rate_limit_reference(model_id: str) -> dict[str, int] | None:
    return MODEL_RATE_LIMIT_REFERENCES.get(normalize_model_id(model_id))


def model_info_map(
    con: Any,
    *,
    base_config: GeminiConfig | None = None,
) -> dict[str, GeminiModelInfo]:
    return {
        model.model_id: model
        for model in get_available_gemini_models(con, base_config=base_config)
    }


def model_display_label(model: GeminiModelInfo) -> str:
    suffix = {
        "preview": " · 미리보기",
        "experimental": " · 실험",
        "latest_alias": " · 자동 별칭",
    }.get(model.lifecycle, "")
    if model.display_name.casefold() == model.model_id.casefold():
        return f"{model.model_id}{suffix}"
    return f"{model.display_name} ({model.model_id}){suffix}"
