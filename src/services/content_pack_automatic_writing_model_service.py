from __future__ import annotations

import html
import json
import math
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from html.parser import HTMLParser
from typing import Any, Iterable, Mapping, Protocol, Sequence

from src.database import get_setting, set_setting


CATALOG_TTL_SECONDS = 60 * 60
PERFORMANCE_TTL_SECONDS = 24 * 60 * 60
PRIORITY_SLOT_COUNT = 4

CATALOG_SETTING = "automatic_writing_model_catalog_json"
CATALOG_CHECKED_AT_SETTING = "automatic_writing_model_catalog_checked_at"
PERFORMANCE_SETTING = "automatic_writing_model_performance_json"
PERFORMANCE_REFRESHED_AT_SETTING = "automatic_writing_model_performance_refreshed_at"
PRIORITY_SETTING = "automatic_writing_model_priority_json"

PROVIDERS = ("openrouter", "groq", "opencode")
PROVIDER_LABELS = {
    "openrouter": "OpenRouter",
    "groq": "Groq",
    "opencode": "OpenCode Zen",
}

OPENROUTER_MODELS_URL = "https://openrouter.ai/api/v1/models?output_modalities=text"
OPENROUTER_MODEL_URL = "https://openrouter.ai/api/v1/model/{model_path}"
OPENROUTER_CHAT_URL = "https://openrouter.ai/api/v1/chat/completions"
GROQ_MODELS_URL = "https://api.groq.com/openai/v1/models"
GROQ_CHAT_URL = "https://api.groq.com/openai/v1/chat/completions"
OPENCODE_MODELS_URL = "https://opencode.ai/zen/v1/models"
OPENCODE_PRICING_URL = "https://opencode.ai/docs/zen/"
OPENCODE_CHAT_URL = "https://opencode.ai/zen/v1/chat/completions"
OPENCODE_RESPONSES_URL = "https://opencode.ai/zen/v1/responses"
DEFAULT_TIMEOUT_SECONDS = 20.0
MAX_RESPONSE_BYTES = 4 * 1024 * 1024

_ALLOWED_CONFIDENCE = {"high", "medium", "low", "unknown", "provisional"}

# Initial values mirror the reviewed task-aware scoring method used by AI Workflow Helper.
# They are only a bootstrap for known families. Runtime refreshes are stored in DuckDB
# settings and never rewrite Git.
_SEED_MODEL_SCORES: tuple[tuple[tuple[str, ...], tuple[float, float, float, str]], ...] = (
    (("glm-5.2", "glm-5-2"), (89.0, 92.0, 90.0, "medium")),
    (("qwen3.6-27b", "qwen3-6-27b"), (82.0, 88.0, 82.0, "medium")),
    (("gemma-4-31b-it", "gemma-4-31b"), (84.0, 84.0, 82.0, "high")),
    (("gemma-4-26b-a4b",), (82.0, 81.0, 80.0, "high")),
    (("nemotron-3.5-lightning", "nemotron-3-5-lightning"), (76.0, 80.0, 79.0, "medium")),
    (("nemotron-3-ultra",), (78.0, 82.0, 74.0, "provisional")),
    (("nemotron-3-120b-a12b", "nemotron-3-super"), (78.0, 81.0, 78.0, "high")),
    (("gpt-oss-120b",), (76.0, 78.0, 75.0, "medium")),
    (("glm-4.7-flash", "glm-4-7-flash"), (76.0, 74.0, 78.0, "medium")),
    (("gpt-oss-20b",), (68.0, 68.0, 70.0, "medium")),
    (("nemotron-3-nano-30b-a3b",), (72.0, 75.0, 74.0, "medium")),
    (("laguna-s-2.1", "laguna-s-2-1"), (64.0, 76.0, 74.0, "medium")),
    (("north-mini-code",), (58.0, 64.0, 68.0, "medium")),
)


@dataclass(frozen=True)
class HttpResponse:
    status_code: int
    body: bytes
    headers: Mapping[str, str]


class HttpTransport(Protocol):
    def request(
        self,
        *,
        method: str,
        url: str,
        headers: Mapping[str, str],
        body: bytes | None,
        timeout_seconds: float,
    ) -> HttpResponse: ...


class UrllibHttpTransport:
    def request(
        self,
        *,
        method: str,
        url: str,
        headers: Mapping[str, str],
        body: bytes | None,
        timeout_seconds: float,
    ) -> HttpResponse:
        request = urllib.request.Request(
            url,
            data=body,
            headers=dict(headers),
            method=method.upper(),
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
                return HttpResponse(
                    int(response.status),
                    response.read(MAX_RESPONSE_BYTES + 1),
                    dict(response.headers.items()),
                )
        except urllib.error.HTTPError as exc:
            return HttpResponse(
                int(exc.code),
                exc.read(MAX_RESPONSE_BYTES + 1),
                dict(exc.headers.items()) if exc.headers is not None else {},
            )
        except (OSError, urllib.error.URLError) as exc:
            raise RuntimeError("network_error") from exc


@dataclass(frozen=True)
class AutomaticWritingModel:
    provider: str
    model_id: str
    display_name: str
    zero_cost: bool
    zero_cost_reason: str
    context_length: int | None = None
    stale: bool = False

    @property
    def key(self) -> str:
        return f"{self.provider}:{self.model_id}"


@dataclass(frozen=True)
class ProviderCatalogState:
    provider: str
    status: str
    model_count: int
    error_message: str = ""


@dataclass(frozen=True)
class ModelCatalogSnapshot:
    checked_at: str
    models: tuple[AutomaticWritingModel, ...]
    providers: tuple[ProviderCatalogState, ...]


@dataclass(frozen=True)
class ModelPerformance:
    provider: str
    model_id: str
    writing: float | None
    reasoning: float | None
    instruction_following: float | None
    overall: float | None
    confidence: str
    evidence_note: str
    evaluated_at: str
    source: str = "runtime"

    @property
    def key(self) -> str:
        return f"{self.provider}:{self.model_id}"


@dataclass(frozen=True)
class PerformanceSnapshot:
    refreshed_at: str
    scores: tuple[ModelPerformance, ...]
    evaluator_provider: str = ""
    evaluator_model_id: str = ""


@dataclass(frozen=True)
class FallbackAttempt:
    provider: str
    model_id: str
    status: str
    message: str = ""


@dataclass(frozen=True)
class FallbackResult:
    status: str
    response_text: str = ""
    provider: str = ""
    model_id: str = ""
    attempts: tuple[FallbackAttempt, ...] = ()


@dataclass(frozen=True)
class PerformanceRefreshResult:
    status: str
    snapshot: PerformanceSnapshot
    attempts: tuple[FallbackAttempt, ...] = ()
    message: str = ""


def _now(value: datetime | None = None) -> datetime:
    return value or datetime.now()


def _iso(value: datetime) -> str:
    return value.isoformat(timespec="seconds")


def _parse_dt(value: object) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return None


def _json_loads(value: object, default: Any) -> Any:
    text = str(value or "").strip()
    if not text:
        return default
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return default


def _request_json(
    transport: HttpTransport,
    *,
    method: str,
    url: str,
    headers: Mapping[str, str],
    payload: object | None = None,
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
) -> tuple[int, object | None]:
    body = None
    if payload is not None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    response = transport.request(
        method=method,
        url=url,
        headers=headers,
        body=body,
        timeout_seconds=timeout_seconds,
    )
    if len(response.body) > MAX_RESPONSE_BYTES:
        return response.status_code, None
    try:
        decoded = json.loads(response.body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        decoded = None
    return response.status_code, decoded


def _request_text(
    transport: HttpTransport,
    *,
    url: str,
    headers: Mapping[str, str] | None = None,
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
) -> tuple[int, str]:
    response = transport.request(
        method="GET",
        url=url,
        headers=headers or {},
        body=None,
        timeout_seconds=timeout_seconds,
    )
    if len(response.body) > MAX_RESPONSE_BYTES:
        return response.status_code, ""
    return response.status_code, response.body.decode("utf-8", errors="replace")


def _float_zero(value: object) -> bool:
    try:
        return math.isclose(float(str(value).strip()), 0.0, abs_tol=1e-15)
    except (TypeError, ValueError):
        return False


def _openrouter_zero_cost(row: Mapping[str, Any]) -> bool:
    pricing = row.get("pricing")
    if not isinstance(pricing, Mapping):
        return False
    # Prompt and completion must be explicitly zero. Optional cost dimensions that
    # may be charged by a plain text generation request must also be zero if present.
    if not _float_zero(pricing.get("prompt")) or not _float_zero(pricing.get("completion")):
        return False
    for key in ("request", "internal_reasoning"):
        if key in pricing and pricing.get(key) not in (None, "") and not _float_zero(pricing.get(key)):
            return False
    return True


def _text_output_model(row: Mapping[str, Any]) -> bool:
    architecture = row.get("architecture")
    if isinstance(architecture, Mapping):
        outputs = architecture.get("output_modalities")
        if isinstance(outputs, list) and outputs:
            return "text" in {str(item).strip().casefold() for item in outputs}
    outputs = row.get("output_modalities")
    if isinstance(outputs, list) and outputs:
        return "text" in {str(item).strip().casefold() for item in outputs}
    return True


def _model_id(row: Mapping[str, Any]) -> str:
    value = row.get("id") or row.get("name")
    return str(value or "").strip()


def _rows(payload: object) -> list[Mapping[str, Any]]:
    if not isinstance(payload, Mapping):
        return []
    value = payload.get("data")
    if not isinstance(value, list):
        value = payload.get("result")
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, Mapping)]


def _headers(api_key: str, *, user_agent: str = "content-trend-tracker") -> dict[str, str]:
    result = {
        "Accept": "application/json",
        "Content-Type": "application/json",
        "User-Agent": user_agent,
    }
    if api_key:
        result["Authorization"] = f"Bearer {api_key}"
    return result


def _fetch_openrouter(
    transport: HttpTransport,
    environ: Mapping[str, str],
) -> tuple[list[AutomaticWritingModel], ProviderCatalogState]:
    api_key = str(environ.get("OPENROUTER_API_KEY", "")).strip()
    if not api_key:
        return [], ProviderCatalogState("openrouter", "missing_api_key", 0)
    status, payload = _request_json(
        transport,
        method="GET",
        url=OPENROUTER_MODELS_URL,
        headers=_headers(api_key),
    )
    if status == 429:
        return [], ProviderCatalogState("openrouter", "rate_limited", 0)
    if status < 200 or status >= 300:
        return [], ProviderCatalogState("openrouter", "http_error", 0, f"HTTP {status}")
    parsed = _rows(payload)
    models: list[AutomaticWritingModel] = []
    for row in parsed:
        model_id = _model_id(row)
        if not model_id or not _text_output_model(row) or not _openrouter_zero_cost(row):
            continue
        context_length = row.get("context_length")
        models.append(
            AutomaticWritingModel(
                provider="openrouter",
                model_id=model_id,
                display_name=str(row.get("name") or model_id).strip() or model_id,
                zero_cost=True,
                zero_cost_reason="현재 OpenRouter pricing의 입력·출력 비용이 0",
                context_length=int(context_length) if isinstance(context_length, int) else None,
            )
        )
    return models, ProviderCatalogState("openrouter", "ok", len(models))


def _fetch_groq(
    transport: HttpTransport,
    environ: Mapping[str, str],
) -> tuple[list[AutomaticWritingModel], ProviderCatalogState]:
    if str(environ.get("GROQ_PLAN", "")).strip().casefold() != "free":
        return [], ProviderCatalogState("groq", "free_plan_not_confirmed", 0)
    api_key = str(environ.get("GROQ_API_KEY", "")).strip()
    if not api_key:
        return [], ProviderCatalogState("groq", "missing_api_key", 0)
    status, payload = _request_json(
        transport,
        method="GET",
        url=GROQ_MODELS_URL,
        headers=_headers(api_key),
    )
    if status == 429:
        return [], ProviderCatalogState("groq", "rate_limited", 0)
    if status < 200 or status >= 300:
        return [], ProviderCatalogState("groq", "http_error", 0, f"HTTP {status}")
    models: list[AutomaticWritingModel] = []
    for row in _rows(payload):
        if row.get("active") is False:
            continue
        model_id = _model_id(row)
        if not model_id:
            continue
        models.append(
            AutomaticWritingModel(
                provider="groq",
                model_id=model_id,
                display_name=model_id,
                zero_cost=True,
                zero_cost_reason="GROQ_PLAN=free 확인 + 현재 모델 목록에 활성 상태",
                context_length=None,
            )
        )
    return models, ProviderCatalogState("groq", "ok", len(models))


class _TableParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.in_row = False
        self.in_cell = False
        self.current_cell: list[str] = []
        self.current_row: list[str] = []
        self.rows: list[list[str]] = []

    def handle_starttag(self, tag: str, attrs) -> None:
        lowered = tag.casefold()
        if lowered == "tr":
            self.in_row = True
            self.current_row = []
        elif self.in_row and lowered in {"td", "th"}:
            self.in_cell = True
            self.current_cell = []

    def handle_data(self, data: str) -> None:
        if self.in_cell:
            self.current_cell.append(data)

    def handle_endtag(self, tag: str) -> None:
        lowered = tag.casefold()
        if self.in_row and self.in_cell and lowered in {"td", "th"}:
            text = html.unescape(" ".join(self.current_cell))
            text = re.sub(r"\s+", " ", text).strip()
            self.current_row.append(text)
            self.in_cell = False
            self.current_cell = []
        elif self.in_row and lowered == "tr":
            if self.current_row:
                self.rows.append(self.current_row)
            self.in_row = False
            self.current_row = []


def _pricing_free_names(document: str) -> tuple[str, ...]:
    parser = _TableParser()
    try:
        parser.feed(document)
    except Exception:
        return ()
    result: list[str] = []
    for row in parser.rows:
        if len(row) < 3:
            continue
        first = row[0].strip()
        if not first or first.casefold() in {"model", "모델"}:
            continue
        if row[1].strip().casefold() == "free" and row[2].strip().casefold() == "free":
            result.append(first)
    return tuple(result)


def _name_key(value: str) -> str:
    lowered = value.casefold()
    lowered = re.sub(r"\b(contributor|free)\b", " ", lowered)
    return re.sub(r"[^a-z0-9]+", "", lowered)


def _match_opencode_free_ids(model_ids: Sequence[str], free_names: Sequence[str]) -> set[str]:
    free_keys = {_name_key(name) for name in free_names if _name_key(name)}
    matched: set[str] = set()
    for model_id in model_ids:
        key = _name_key(model_id)
        if not key:
            continue
        if key in free_keys or any(key == item or key in item or item in key for item in free_keys):
            matched.add(model_id)
    return matched


def _fetch_opencode(
    transport: HttpTransport,
    environ: Mapping[str, str],
) -> tuple[list[AutomaticWritingModel], ProviderCatalogState]:
    api_key = str(environ.get("OPENCODE_API_KEY", "")).strip()
    if not api_key:
        return [], ProviderCatalogState("opencode", "missing_api_key", 0)
    status, payload = _request_json(
        transport,
        method="GET",
        url=OPENCODE_MODELS_URL,
        headers=_headers(api_key),
    )
    if status < 200 or status >= 300:
        return [], ProviderCatalogState("opencode", "http_error", 0, f"models HTTP {status}")
    model_ids = [_model_id(row) for row in _rows(payload)]
    model_ids = [model_id for model_id in model_ids if model_id]
    doc_status, pricing_document = _request_text(
        transport,
        url=OPENCODE_PRICING_URL,
        headers={"Accept": "text/html", "User-Agent": "content-trend-tracker"},
    )
    if doc_status < 200 or doc_status >= 300 or not pricing_document.strip():
        return [], ProviderCatalogState(
            "opencode",
            "pricing_unavailable",
            0,
            f"pricing HTTP {doc_status}",
        )
    free_names = _pricing_free_names(pricing_document)
    free_ids = _match_opencode_free_ids(model_ids, free_names)
    models = [
        AutomaticWritingModel(
            provider="opencode",
            model_id=model_id,
            display_name=model_id,
            zero_cost=True,
            zero_cost_reason="현재 OpenCode Zen 공식 가격표의 입력·출력 모두 Free",
        )
        for model_id in model_ids
        if model_id in free_ids
    ]
    return models, ProviderCatalogState("opencode", "ok", len(models))


def _serialize_catalog(snapshot: ModelCatalogSnapshot) -> str:
    return json.dumps(
        {
            "checked_at": snapshot.checked_at,
            "models": [asdict(item) for item in snapshot.models],
            "providers": [asdict(item) for item in snapshot.providers],
        },
        ensure_ascii=False,
        separators=(",", ":"),
    )


def load_model_catalog(con: Any) -> ModelCatalogSnapshot:
    payload = _json_loads(get_setting(con, CATALOG_SETTING), {})
    if not isinstance(payload, Mapping):
        payload = {}
    models: list[AutomaticWritingModel] = []
    for item in payload.get("models") or []:
        if not isinstance(item, Mapping):
            continue
        provider = str(item.get("provider") or "").strip()
        model_id = str(item.get("model_id") or "").strip()
        if provider not in PROVIDERS or not model_id:
            continue
        models.append(
            AutomaticWritingModel(
                provider=provider,
                model_id=model_id,
                display_name=str(item.get("display_name") or model_id).strip() or model_id,
                zero_cost=bool(item.get("zero_cost")),
                zero_cost_reason=str(item.get("zero_cost_reason") or "").strip(),
                context_length=int(item["context_length"])
                if isinstance(item.get("context_length"), int)
                else None,
                stale=bool(item.get("stale")),
            )
        )
    providers: list[ProviderCatalogState] = []
    for item in payload.get("providers") or []:
        if not isinstance(item, Mapping):
            continue
        provider = str(item.get("provider") or "").strip()
        if provider not in PROVIDERS:
            continue
        providers.append(
            ProviderCatalogState(
                provider=provider,
                status=str(item.get("status") or "unknown"),
                model_count=int(item.get("model_count") or 0),
                error_message=str(item.get("error_message") or ""),
            )
        )
    checked_at = str(payload.get("checked_at") or get_setting(con, CATALOG_CHECKED_AT_SETTING) or "")
    return ModelCatalogSnapshot(checked_at, tuple(models), tuple(providers))


def model_catalog_due(con: Any, *, now: datetime | None = None) -> bool:
    checked_at = _parse_dt(get_setting(con, CATALOG_CHECKED_AT_SETTING))
    if checked_at is None:
        return True
    return (_now(now) - checked_at).total_seconds() >= CATALOG_TTL_SECONDS


def refresh_model_catalog(
    con: Any,
    *,
    force: bool = False,
    now: datetime | None = None,
    transport: HttpTransport | None = None,
    environ: Mapping[str, str] | None = None,
) -> ModelCatalogSnapshot:
    if not force and not model_catalog_due(con, now=now):
        return load_model_catalog(con)

    active_transport = transport or UrllibHttpTransport()
    active_environ = environ if environ is not None else os.environ
    previous = load_model_catalog(con)
    previous_by_provider: dict[str, list[AutomaticWritingModel]] = {provider: [] for provider in PROVIDERS}
    for item in previous.models:
        previous_by_provider.setdefault(item.provider, []).append(item)

    fetchers = {
        "openrouter": _fetch_openrouter,
        "groq": _fetch_groq,
        "opencode": _fetch_opencode,
    }
    combined: list[AutomaticWritingModel] = []
    states: list[ProviderCatalogState] = []
    for provider in PROVIDERS:
        try:
            models, state = fetchers[provider](active_transport, active_environ)
        except RuntimeError as exc:
            models = []
            state = ProviderCatalogState(provider, str(exc), 0)
        except Exception as exc:
            models = []
            state = ProviderCatalogState(provider, "unexpected_error", 0, str(exc))
        if state.status == "ok":
            combined.extend(models)
        else:
            combined.extend(
                AutomaticWritingModel(
                    provider=item.provider,
                    model_id=item.model_id,
                    display_name=item.display_name,
                    zero_cost=item.zero_cost,
                    zero_cost_reason=item.zero_cost_reason,
                    context_length=item.context_length,
                    stale=True,
                )
                for item in previous_by_provider.get(provider, [])
            )
        states.append(state)

    deduped: dict[str, AutomaticWritingModel] = {}
    for item in combined:
        if item.zero_cost:
            deduped[item.key] = item
    checked = _iso(_now(now))
    snapshot = ModelCatalogSnapshot(
        checked_at=checked,
        models=tuple(sorted(deduped.values(), key=lambda item: (item.provider, item.model_id))),
        providers=tuple(states),
    )
    set_setting(con, CATALOG_SETTING, _serialize_catalog(snapshot))
    set_setting(con, CATALOG_CHECKED_AT_SETTING, checked)
    return snapshot


def load_priority(con: Any) -> tuple[str, ...]:
    payload = _json_loads(get_setting(con, PRIORITY_SETTING), [])
    if not isinstance(payload, list):
        return ()
    result: list[str] = []
    for item in payload:
        value = str(item or "").strip()
        if not value or ":" not in value or value in result:
            continue
        provider, model_id = value.split(":", 1)
        if provider not in PROVIDERS or not model_id:
            continue
        result.append(value)
        if len(result) >= PRIORITY_SLOT_COUNT:
            break
    return tuple(result)


def save_priority(con: Any, values: Sequence[str]) -> tuple[str, ...]:
    result: list[str] = []
    for raw in values:
        value = str(raw or "").strip()
        if not value:
            continue
        if ":" not in value:
            raise ValueError("자동 작성 모델은 provider:model_id 형식이어야 합니다.")
        provider, model_id = value.split(":", 1)
        if provider not in PROVIDERS or not model_id.strip():
            raise ValueError(f"지원하지 않는 자동 작성 모델입니다: {value}")
        if value in result:
            raise ValueError("자동 작성 우선순위에는 같은 모델을 중복 지정할 수 없습니다.")
        result.append(value)
    if len(result) > PRIORITY_SLOT_COUNT:
        raise ValueError(f"자동 작성 우선순위는 최대 {PRIORITY_SLOT_COUNT}개입니다.")
    set_setting(con, PRIORITY_SETTING, json.dumps(result, ensure_ascii=False))
    return tuple(result)


def _score_key(provider: str, model_id: str) -> str:
    return f"{provider}:{model_id}"


def _seed_score(provider: str, model_id: str, *, evaluated_at: str) -> ModelPerformance | None:
    lowered = model_id.casefold()
    for aliases, values in _SEED_MODEL_SCORES:
        if any(alias in lowered for alias in aliases):
            writing, reasoning, instruction, confidence = values
            overall = round(0.45 * writing + 0.35 * reasoning + 0.20 * instruction, 1)
            return ModelPerformance(
                provider=provider,
                model_id=model_id,
                writing=writing,
                reasoning=reasoning,
                instruction_following=instruction,
                overall=overall,
                confidence=confidence,
                evidence_note="AI Workflow Helper의 검토된 task-aware 점수를 초기 기준으로 복사",
                evaluated_at=evaluated_at,
                source="seed",
            )
    return None


def _serialize_performance(snapshot: PerformanceSnapshot) -> str:
    return json.dumps(
        {
            "refreshed_at": snapshot.refreshed_at,
            "evaluator_provider": snapshot.evaluator_provider,
            "evaluator_model_id": snapshot.evaluator_model_id,
            "scores": [asdict(item) for item in snapshot.scores],
        },
        ensure_ascii=False,
        separators=(",", ":"),
    )


def load_performance(con: Any, catalog: ModelCatalogSnapshot | None = None) -> PerformanceSnapshot:
    payload = _json_loads(get_setting(con, PERFORMANCE_SETTING), {})
    if not isinstance(payload, Mapping):
        payload = {}
    scores: dict[str, ModelPerformance] = {}
    for item in payload.get("scores") or []:
        if not isinstance(item, Mapping):
            continue
        provider = str(item.get("provider") or "").strip()
        model_id = str(item.get("model_id") or "").strip()
        if provider not in PROVIDERS or not model_id:
            continue

        def number(name: str) -> float | None:
            raw = item.get(name)
            if raw is None or isinstance(raw, bool):
                return None
            try:
                value = float(raw)
            except (TypeError, ValueError):
                return None
            return value if 0 <= value <= 100 else None

        score = ModelPerformance(
            provider=provider,
            model_id=model_id,
            writing=number("writing"),
            reasoning=number("reasoning"),
            instruction_following=number("instruction_following"),
            overall=number("overall"),
            confidence=str(item.get("confidence") or "unknown")
            if str(item.get("confidence") or "unknown") in _ALLOWED_CONFIDENCE
            else "unknown",
            evidence_note=str(item.get("evidence_note") or ""),
            evaluated_at=str(item.get("evaluated_at") or ""),
            source=str(item.get("source") or "runtime"),
        )
        scores[score.key] = score

    if catalog is not None:
        seed_at = str(payload.get("refreshed_at") or _iso(datetime.now()))
        for model in catalog.models:
            if model.key in scores:
                continue
            seed = _seed_score(model.provider, model.model_id, evaluated_at=seed_at)
            if seed is not None:
                scores[seed.key] = seed

    return PerformanceSnapshot(
        refreshed_at=str(payload.get("refreshed_at") or get_setting(con, PERFORMANCE_REFRESHED_AT_SETTING) or ""),
        scores=tuple(scores.values()),
        evaluator_provider=str(payload.get("evaluator_provider") or ""),
        evaluator_model_id=str(payload.get("evaluator_model_id") or ""),
    )


def performance_due(con: Any, *, now: datetime | None = None) -> bool:
    refreshed_at = _parse_dt(get_setting(con, PERFORMANCE_REFRESHED_AT_SETTING))
    if refreshed_at is None:
        return True
    return (_now(now) - refreshed_at).total_seconds() >= PERFORMANCE_TTL_SECONDS


def ranked_model_rows(
    catalog: ModelCatalogSnapshot,
    performance: PerformanceSnapshot,
) -> list[dict[str, Any]]:
    score_map = {item.key: item for item in performance.scores}
    rows: list[dict[str, Any]] = []
    for model in catalog.models:
        score = score_map.get(model.key)
        rows.append(
            {
                "key": model.key,
                "provider": model.provider,
                "provider_label": PROVIDER_LABELS.get(model.provider, model.provider),
                "model_id": model.model_id,
                "display_name": model.display_name,
                "writing": score.writing if score else None,
                "reasoning": score.reasoning if score else None,
                "instruction_following": score.instruction_following if score else None,
                "overall": score.overall if score else None,
                "confidence": score.confidence if score else "unknown",
                "zero_cost_reason": model.zero_cost_reason,
                "stale": model.stale,
                "evaluated_at": score.evaluated_at if score else "",
            }
        )

    def sort_value(value: object) -> float:
        return float(value) if isinstance(value, (int, float)) else -1.0

    rows.sort(
        key=lambda row: (
            -sort_value(row["writing"]),
            -sort_value(row["reasoning"]),
            -sort_value(row["instruction_following"]),
            -sort_value(row["overall"]),
            row["provider"],
            row["model_id"],
        )
    )
    return rows


def _openrouter_single_model_url(model_id: str) -> str:
    return OPENROUTER_MODEL_URL.format(
        model_path="/".join(
            urllib.parse.quote(part, safe=":@")
            for part in str(model_id).split("/")
        )
    )


def _preflight_openrouter(
    model_id: str,
    transport: HttpTransport,
    environ: Mapping[str, str],
) -> tuple[bool, str]:
    api_key = str(environ.get("OPENROUTER_API_KEY", "")).strip()
    if not api_key:
        return False, "OpenRouter API 키 없음"
    status, payload = _request_json(
        transport,
        method="GET",
        url=_openrouter_single_model_url(model_id),
        headers=_headers(api_key),
    )
    if status < 200 or status >= 300 or not isinstance(payload, Mapping):
        return False, f"OpenRouter 가격 확인 실패 HTTP {status}"
    row = payload.get("data")
    if not isinstance(row, Mapping) or _model_id(row) not in {model_id, ""}:
        return False, "OpenRouter 모델 정보 확인 실패"
    if not _openrouter_zero_cost(row):
        return False, "현재 OpenRouter 가격이 0원이 아님"
    return True, "현재 OpenRouter pricing 0원 확인"


def _preflight_groq(
    model_id: str,
    transport: HttpTransport,
    environ: Mapping[str, str],
) -> tuple[bool, str]:
    if str(environ.get("GROQ_PLAN", "")).strip().casefold() != "free":
        return False, "Groq Free 플랜 미확인"
    api_key = str(environ.get("GROQ_API_KEY", "")).strip()
    if not api_key:
        return False, "Groq API 키 없음"
    status, payload = _request_json(
        transport,
        method="GET",
        url=GROQ_MODELS_URL,
        headers=_headers(api_key),
    )
    if status < 200 or status >= 300:
        return False, f"Groq 모델 확인 실패 HTTP {status}"
    active_ids = {
        _model_id(row)
        for row in _rows(payload)
        if row.get("active") is not False and _model_id(row)
    }
    if model_id not in active_ids:
        return False, "Groq 현재 활성 모델 목록에 없음"
    return True, "Groq Free 플랜 + 현재 활성 모델 확인"


def _preflight_opencode(
    model_id: str,
    transport: HttpTransport,
    environ: Mapping[str, str],
) -> tuple[bool, str]:
    api_key = str(environ.get("OPENCODE_API_KEY", "")).strip()
    if not api_key:
        return False, "OpenCode API 키 없음"
    models, state = _fetch_opencode(transport, environ)
    if state.status != "ok":
        return False, f"OpenCode 가격 확인 실패: {state.status}"
    if model_id not in {item.model_id for item in models}:
        return False, "현재 OpenCode 공식 가격표에서 0원 모델이 아님"
    return True, "OpenCode 현재 공식 가격표 Free 확인"


def verify_zero_cost(
    provider: str,
    model_id: str,
    *,
    transport: HttpTransport | None = None,
    environ: Mapping[str, str] | None = None,
) -> tuple[bool, str]:
    active_transport = transport or UrllibHttpTransport()
    active_environ = environ if environ is not None else os.environ
    if provider == "openrouter":
        return _preflight_openrouter(model_id, active_transport, active_environ)
    if provider == "groq":
        return _preflight_groq(model_id, active_transport, active_environ)
    if provider == "opencode":
        return _preflight_opencode(model_id, active_transport, active_environ)
    return False, "지원하지 않는 Provider"


def _extract_chat_text(payload: object) -> str:
    if not isinstance(payload, Mapping):
        return ""
    choices = payload.get("choices")
    if isinstance(choices, list) and choices:
        first = choices[0]
        if isinstance(first, Mapping):
            message = first.get("message")
            if isinstance(message, Mapping):
                content = message.get("content")
                if isinstance(content, str):
                    return content.strip()
    output_text = payload.get("output_text")
    if isinstance(output_text, str):
        return output_text.strip()
    output = payload.get("output")
    if isinstance(output, list):
        parts: list[str] = []
        for item in output:
            if not isinstance(item, Mapping):
                continue
            content = item.get("content")
            if not isinstance(content, list):
                continue
            for part in content:
                if isinstance(part, Mapping) and isinstance(part.get("text"), str):
                    parts.append(str(part["text"]))
        return "\n".join(parts).strip()
    return ""


def _call_generation(
    provider: str,
    model_id: str,
    prompt: str,
    *,
    transport: HttpTransport,
    environ: Mapping[str, str],
) -> tuple[bool, str, str]:
    if provider == "openrouter":
        api_key = str(environ.get("OPENROUTER_API_KEY", "")).strip()
        url = OPENROUTER_CHAT_URL
    elif provider == "groq":
        api_key = str(environ.get("GROQ_API_KEY", "")).strip()
        url = GROQ_CHAT_URL
    elif provider == "opencode":
        api_key = str(environ.get("OPENCODE_API_KEY", "")).strip()
        url = OPENCODE_CHAT_URL
    else:
        return False, "", "지원하지 않는 Provider"

    payload = {
        "model": model_id,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.1,
    }
    status, response = _request_json(
        transport,
        method="POST",
        url=url,
        headers=_headers(api_key),
        payload=payload,
        timeout_seconds=120.0,
    )
    if 200 <= status < 300:
        text = _extract_chat_text(response)
        if text:
            return True, text, ""
        return False, "", "응답 본문이 비어 있음"

    # Some OpenCode models are Responses-API-only. Retry only on endpoint/shape
    # rejection, after the same zero-cost preflight already passed.
    if provider == "opencode" and status in {400, 404, 405, 422}:
        responses_payload = {
            "model": model_id,
            "input": prompt,
        }
        status, response = _request_json(
            transport,
            method="POST",
            url=OPENCODE_RESPONSES_URL,
            headers=_headers(api_key),
            payload=responses_payload,
            timeout_seconds=120.0,
        )
        if 200 <= status < 300:
            text = _extract_chat_text(response)
            if text:
                return True, text, ""
    return False, "", f"생성 실패 HTTP {status}"


def _priority_candidates(
    con: Any,
    catalog: ModelCatalogSnapshot,
) -> list[AutomaticWritingModel]:
    by_key = {item.key: item for item in catalog.models}
    return [by_key[key] for key in load_priority(con) if key in by_key]


def run_zero_cost_priority_fallback(
    con: Any,
    prompt: str,
    *,
    now: datetime | None = None,
    transport: HttpTransport | None = None,
    environ: Mapping[str, str] | None = None,
) -> FallbackResult:
    active_transport = transport or UrllibHttpTransport()
    active_environ = environ if environ is not None else os.environ
    catalog = refresh_model_catalog(
        con,
        force=False,
        now=now,
        transport=active_transport,
        environ=active_environ,
    )
    candidates = _priority_candidates(con, catalog)
    if not candidates:
        return FallbackResult(status="priority_not_configured")

    attempts: list[FallbackAttempt] = []
    for model in candidates:
        allowed, reason = verify_zero_cost(
            model.provider,
            model.model_id,
            transport=active_transport,
            environ=active_environ,
        )
        if not allowed:
            attempts.append(
                FallbackAttempt(model.provider, model.model_id, "skipped_not_zero_cost", reason)
            )
            continue
        try:
            ok, response_text, error = _call_generation(
                model.provider,
                model.model_id,
                str(prompt or ""),
                transport=active_transport,
                environ=active_environ,
            )
        except RuntimeError as exc:
            ok, response_text, error = False, "", str(exc)
        if ok:
            attempts.append(FallbackAttempt(model.provider, model.model_id, "success"))
            return FallbackResult(
                status="success",
                response_text=response_text,
                provider=model.provider,
                model_id=model.model_id,
                attempts=tuple(attempts),
            )
        attempts.append(FallbackAttempt(model.provider, model.model_id, "failed", error))
    return FallbackResult(status="all_failed", attempts=tuple(attempts))


def _performance_prompt(
    catalog: ModelCatalogSnapshot,
    existing: PerformanceSnapshot,
) -> str:
    existing_map = {item.key: item for item in existing.scores}
    rows = []
    for model in catalog.models:
        prior = existing_map.get(model.key)
        rows.append(
            {
                "provider": model.provider,
                "model_id": model.model_id,
                "prior": {
                    "writing": prior.writing,
                    "reasoning": prior.reasoning,
                    "instruction_following": prior.instruction_following,
                    "confidence": prior.confidence,
                }
                if prior
                else None,
            }
        )
    return (
        "아래는 현재 프로그램에서 비용 0원으로 확인된 텍스트 모델 목록입니다. "
        "블로그 정보성 글 자동작성용 비교 데이터를 갱신하세요. "
        "AI Workflow Helper의 평가 원칙처럼 글쓰기, 분석/추론, 지시준수를 각각 0~100으로 분리해 평가하고 "
        "직접적인 공개 벤치마크·모델 카드 근거가 부족하면 점수를 만들지 말고 null과 confidence=unknown을 사용하세요. "
        "코딩 성능만으로 글쓰기 점수를 높이지 마세요. 기존 점수는 참고일 뿐 그대로 맞추지 마세요. "
        "모든 입력 model_id를 정확히 한 번 반환하고 새 model_id를 만들지 마세요. "
        "출력은 설명 없이 JSON object 하나만 반환하세요. schema는 "
        '{"scores":[{"provider":"openrouter|groq|opencode","model_id":"...",'
        '"writing":0-100|null,"reasoning":0-100|null,"instruction_following":0-100|null,'
        '"confidence":"high|medium|low|unknown|provisional","evidence_note":"짧은 근거"}]}. '
        "입력: "
        + json.dumps(rows, ensure_ascii=False, separators=(",", ":"))
    )


def _extract_json_object(text: str) -> Mapping[str, Any] | None:
    cleaned = str(text or "").strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r"\s*```$", "", cleaned)
    try:
        value = json.loads(cleaned)
        return value if isinstance(value, Mapping) else None
    except json.JSONDecodeError:
        pass
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        value = json.loads(cleaned[start : end + 1])
        return value if isinstance(value, Mapping) else None
    except json.JSONDecodeError:
        return None


def _performance_number(value: object) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return round(number, 1) if 0 <= number <= 100 else None


def _parse_performance_response(
    text: str,
    catalog: ModelCatalogSnapshot,
    *,
    evaluated_at: str,
) -> dict[str, ModelPerformance]:
    payload = _extract_json_object(text)
    if payload is None or not isinstance(payload.get("scores"), list):
        return {}
    allowed = {item.key for item in catalog.models}
    result: dict[str, ModelPerformance] = {}
    for item in payload["scores"]:
        if not isinstance(item, Mapping):
            continue
        provider = str(item.get("provider") or "").strip()
        model_id = str(item.get("model_id") or "").strip()
        key = _score_key(provider, model_id)
        if key not in allowed or key in result:
            continue
        writing = _performance_number(item.get("writing"))
        reasoning = _performance_number(item.get("reasoning"))
        instruction = _performance_number(item.get("instruction_following"))
        confidence = str(item.get("confidence") or "unknown").strip().casefold()
        if confidence not in _ALLOWED_CONFIDENCE:
            confidence = "unknown"
        overall = None
        if writing is not None and reasoning is not None and instruction is not None:
            overall = round(0.45 * writing + 0.35 * reasoning + 0.20 * instruction, 1)
        result[key] = ModelPerformance(
            provider=provider,
            model_id=model_id,
            writing=writing,
            reasoning=reasoning,
            instruction_following=instruction,
            overall=overall,
            confidence=confidence,
            evidence_note=str(item.get("evidence_note") or "").strip(),
            evaluated_at=evaluated_at,
            source="runtime",
        )
    return result


def refresh_performance_if_due(
    con: Any,
    *,
    force: bool = False,
    now: datetime | None = None,
    transport: HttpTransport | None = None,
    environ: Mapping[str, str] | None = None,
) -> PerformanceRefreshResult:
    active_now = _now(now)
    active_transport = transport or UrllibHttpTransport()
    active_environ = environ if environ is not None else os.environ
    catalog = refresh_model_catalog(
        con,
        force=False,
        now=active_now,
        transport=active_transport,
        environ=active_environ,
    )
    existing = load_performance(con, catalog)
    if not force and not performance_due(con, now=active_now):
        return PerformanceRefreshResult("fresh", existing)
    if not catalog.models:
        return PerformanceRefreshResult("no_models", existing, message="현재 0원 모델 목록이 없습니다.")
    if not load_priority(con):
        return PerformanceRefreshResult(
            "priority_not_configured",
            existing,
            message="자동 작성 우선순위 1~4를 먼저 저장하세요.",
        )

    fallback = run_zero_cost_priority_fallback(
        con,
        _performance_prompt(catalog, existing),
        now=active_now,
        transport=active_transport,
        environ=active_environ,
    )
    if fallback.status != "success":
        return PerformanceRefreshResult(
            "failed",
            existing,
            attempts=fallback.attempts,
            message="성능 갱신에 실패해 이전 정상 데이터를 유지합니다.",
        )

    evaluated_at = _iso(active_now)
    parsed = _parse_performance_response(
        fallback.response_text,
        catalog,
        evaluated_at=evaluated_at,
    )
    if not parsed:
        return PerformanceRefreshResult(
            "invalid_response",
            existing,
            attempts=fallback.attempts,
            message="성능 응답을 검증하지 못해 이전 정상 데이터를 유지합니다.",
        )

    merged = {item.key: item for item in existing.scores}
    merged.update(parsed)
    snapshot = PerformanceSnapshot(
        refreshed_at=evaluated_at,
        scores=tuple(merged.values()),
        evaluator_provider=fallback.provider,
        evaluator_model_id=fallback.model_id,
    )
    set_setting(con, PERFORMANCE_SETTING, _serialize_performance(snapshot))
    set_setting(con, PERFORMANCE_REFRESHED_AT_SETTING, evaluated_at)
    return PerformanceRefreshResult(
        "refreshed",
        snapshot,
        attempts=fallback.attempts,
        message=f"{len(parsed)}개 모델의 성능 데이터를 갱신했습니다.",
    )
