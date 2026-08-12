from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping

from src.services.blogger_draft_service import (
    BLOGGER_SCOPE,
    DEFAULT_CLIENT_SECRET_PATH,
    DEFAULT_TOKEN_PATH,
    get_blogger_connection_status,
)


_STATUS_ORDER = {"pass": 0, "warning": 1, "fail": 2}


@dataclass(frozen=True)
class BloggerPreflightCheck:
    key: str
    label: str
    status: str
    message: str


@dataclass(frozen=True)
class BloggerPreflightReport:
    ready_for_authorization: bool
    ready_for_api: bool
    summary: str
    checks: tuple[BloggerPreflightCheck, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "ready_for_authorization": self.ready_for_authorization,
            "ready_for_api": self.ready_for_api,
            "summary": self.summary,
            "checks": [asdict(check) for check in self.checks],
        }


def _check(
    key: str,
    label: str,
    status: str,
    message: str,
) -> BloggerPreflightCheck:
    if status not in _STATUS_ORDER:
        raise ValueError(f"지원하지 않는 사전점검 상태입니다: {status}")
    return BloggerPreflightCheck(
        key=key,
        label=label,
        status=status,
        message=message,
    )


def _read_json_object(path: Path) -> tuple[Mapping[str, Any] | None, str | None]:
    try:
        raw = path.read_text(encoding="utf-8-sig")
        payload = json.loads(raw)
    except FileNotFoundError:
        return None, "missing"
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None, "invalid-json"
    if not isinstance(payload, Mapping):
        return None, "invalid-object"
    return payload, None


def _non_empty_string(mapping: Mapping[str, Any], key: str) -> bool:
    return bool(str(mapping.get(key) or "").strip())


def _parse_expiry(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    normalized = text[:-1] + "+00:00" if text.endswith("Z") else text
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _client_secret_checks(path: Path) -> tuple[list[BloggerPreflightCheck], bool]:
    if not path.is_file():
        return [
            _check(
                "client_file",
                "OAuth 클라이언트 파일",
                "fail",
                "데스크톱 앱 OAuth 클라이언트 JSON 파일이 없습니다.",
            )
        ], False

    payload, error = _read_json_object(path)
    if error:
        return [
            _check(
                "client_file",
                "OAuth 클라이언트 파일",
                "fail",
                "OAuth 클라이언트 JSON을 안전하게 해석할 수 없습니다.",
            )
        ], False

    installed = payload.get("installed") if payload else None
    if not isinstance(installed, Mapping):
        return [
            _check(
                "client_type",
                "OAuth 클라이언트 유형",
                "fail",
                "`데스크톱 앱` 유형의 OAuth 클라이언트 JSON이 아닙니다.",
            )
        ], False

    required_fields = ("client_id", "client_secret", "auth_uri", "token_uri")
    missing_fields = [field for field in required_fields if not _non_empty_string(installed, field)]
    if missing_fields:
        return [
            _check(
                "client_fields",
                "OAuth 클라이언트 필수 항목",
                "fail",
                "데스크톱 OAuth 클라이언트 JSON의 필수 항목이 누락되었습니다.",
            )
        ], False

    redirect_uris = installed.get("redirect_uris")
    redirect_values = (
        [str(value or "").strip() for value in redirect_uris]
        if isinstance(redirect_uris, list)
        else []
    )
    loopback_ready = any(
        value.startswith("http://localhost") or value.startswith("http://127.0.0.1")
        for value in redirect_values
    )
    checks = [
        _check(
            "client_type",
            "OAuth 클라이언트 유형",
            "pass",
            "데스크톱 앱 OAuth 클라이언트 구조를 확인했습니다.",
        ),
        _check(
            "client_fields",
            "OAuth 클라이언트 필수 항목",
            "pass",
            "필수 인증 항목이 존재합니다. 값은 표시하지 않았습니다.",
        ),
        _check(
            "client_redirect",
            "로컬 승인 리디렉션",
            "pass" if loopback_ready else "warning",
            (
                "로컬 브라우저 승인용 loopback 리디렉션을 확인했습니다."
                if loopback_ready
                else "loopback 리디렉션이 JSON에 명시되지 않았습니다. 실제 승인 시 확인하세요."
            ),
        ),
    ]
    return checks, True


def _token_checks(
    path: Path,
    *,
    now: datetime,
) -> tuple[list[BloggerPreflightCheck], bool]:
    if not path.is_file():
        return [
            _check(
                "token_file",
                "Google 계정 연결 토큰",
                "warning",
                "로컬 토큰이 없습니다. OAuth 클라이언트 준비 후 계정을 연결하세요.",
            )
        ], False

    payload, error = _read_json_object(path)
    if error:
        return [
            _check(
                "token_file",
                "Google 계정 연결 토큰",
                "fail",
                "로컬 OAuth 토큰 JSON을 안전하게 해석할 수 없습니다. 다시 연결하세요.",
            )
        ], False

    required_fields = ("token_uri", "client_id", "client_secret")
    missing_fields = [field for field in required_fields if not _non_empty_string(payload, field)]
    if missing_fields:
        return [
            _check(
                "token_fields",
                "OAuth 토큰 필수 항목",
                "fail",
                "로컬 OAuth 토큰의 필수 항목이 누락되었습니다. 다시 연결하세요.",
            )
        ], False

    scopes_value = payload.get("scopes")
    if isinstance(scopes_value, str):
        scopes = {item for item in scopes_value.split() if item}
    elif isinstance(scopes_value, list):
        scopes = {str(item or "").strip() for item in scopes_value if str(item or "").strip()}
    else:
        scopes = set()
    scope_ready = BLOGGER_SCOPE in scopes

    refresh_ready = _non_empty_string(payload, "refresh_token")
    token_ready = _non_empty_string(payload, "token")
    expiry = _parse_expiry(payload.get("expiry"))
    now_utc = now.astimezone(timezone.utc)
    expired = expiry is not None and expiry <= now_utc

    checks = [
        _check(
            "token_fields",
            "OAuth 토큰 필수 항목",
            "pass",
            "토큰 필수 항목이 존재합니다. 값은 표시하지 않았습니다.",
        ),
        _check(
            "token_scope",
            "Blogger 권한 범위",
            "pass" if scope_ready else "fail",
            (
                "Blogger API 권한 범위를 확인했습니다."
                if scope_ready
                else "Blogger API 권한 범위가 없어 계정을 다시 연결해야 합니다."
            ),
        ),
    ]

    if expired and refresh_ready:
        checks.append(
            _check(
                "token_expiry",
                "OAuth 토큰 만료",
                "warning",
                "액세스 토큰은 만료됐지만 갱신 토큰이 있어 첫 API 호출 때 갱신할 수 있습니다.",
            )
        )
    elif expired:
        checks.append(
            _check(
                "token_expiry",
                "OAuth 토큰 만료",
                "fail",
                "토큰이 만료됐고 갱신 토큰이 없어 계정을 다시 연결해야 합니다.",
            )
        )
    elif token_ready or refresh_ready:
        checks.append(
            _check(
                "token_expiry",
                "OAuth 토큰 상태",
                "pass",
                "API 호출에 사용할 로컬 토큰 상태를 확인했습니다.",
            )
        )
    else:
        checks.append(
            _check(
                "token_expiry",
                "OAuth 토큰 상태",
                "fail",
                "사용 가능한 액세스 토큰 또는 갱신 토큰이 없습니다.",
            )
        )

    usable = scope_ready and (token_ready or refresh_ready) and not (expired and not refresh_ready)
    return checks, usable


def build_blogger_preflight_report(
    *,
    client_secret_path: Path = DEFAULT_CLIENT_SECRET_PATH,
    token_path: Path = DEFAULT_TOKEN_PATH,
    now: datetime | None = None,
    connection_status_loader: Callable[..., Any] = get_blogger_connection_status,
) -> BloggerPreflightReport:
    reference_time = now or datetime.now(timezone.utc)
    status = connection_status_loader(
        client_secret_path=client_secret_path,
        token_path=token_path,
    )
    checks = [
        _check(
            "dependencies",
            "Blogger API Python 의존성",
            "pass" if status.dependency_ready else "fail",
            (
                "필요한 Google API Python 패키지가 설치되어 있습니다."
                if status.dependency_ready
                else "requirements.txt의 Google API 패키지를 설치해야 합니다."
            ),
        )
    ]

    client_checks, client_ready = _client_secret_checks(client_secret_path)
    token_checks, token_ready = _token_checks(token_path, now=reference_time)
    checks.extend(client_checks)
    checks.extend(token_checks)

    ready_for_authorization = bool(status.dependency_ready and client_ready)
    ready_for_api = bool(ready_for_authorization and token_ready)
    if ready_for_api:
        summary = "Blogger API 호출 사전점검을 통과했습니다."
    elif ready_for_authorization:
        summary = "OAuth 클라이언트는 준비됐지만 Google 계정 연결 또는 토큰 재연결이 필요합니다."
    else:
        summary = "Blogger OAuth 설정을 먼저 보완해야 합니다."

    return BloggerPreflightReport(
        ready_for_authorization=ready_for_authorization,
        ready_for_api=ready_for_api,
        summary=summary,
        checks=tuple(sorted(checks, key=lambda item: (_STATUS_ORDER[item.status], item.label))),
    )
