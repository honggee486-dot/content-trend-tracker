from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

from src.services.blogger_draft_service import BLOGGER_SCOPE
from src.services.blogger_preflight_service import build_blogger_preflight_report


def _status_loader(*, dependency_ready: bool = True):
    def loader(**_kwargs):
        return SimpleNamespace(dependency_ready=dependency_ready)

    return loader


def _write_client(path: Path, *, client_type: str = "installed") -> None:
    path.write_text(
        json.dumps(
            {
                client_type: {
                    "client_id": "client-id-secret-value",
                    "client_secret": "client-secret-value",
                    "auth_uri": "https://accounts.example/authorize",
                    "token_uri": "https://accounts.example/token",
                    "redirect_uris": ["http://localhost"],
                }
            }
        ),
        encoding="utf-8",
    )


def _write_token(
    path: Path,
    *,
    expiry: str = "2099-01-01T00:00:00Z",
    scopes=None,
    refresh_token: str = "refresh-secret-value",
) -> None:
    path.write_text(
        json.dumps(
            {
                "token": "access-secret-value",
                "refresh_token": refresh_token,
                "token_uri": "https://accounts.example/token",
                "client_id": "client-id-secret-value",
                "client_secret": "client-secret-value",
                "scopes": scopes if scopes is not None else [BLOGGER_SCOPE],
                "expiry": expiry,
            }
        ),
        encoding="utf-8",
    )


def test_valid_desktop_client_and_token_are_api_ready_without_secret_exposure(
    tmp_path: Path,
) -> None:
    client_path = tmp_path / "client.json"
    token_path = tmp_path / "token.json"
    _write_client(client_path)
    _write_token(token_path)

    report = build_blogger_preflight_report(
        client_secret_path=client_path,
        token_path=token_path,
        now=datetime(2026, 8, 3, tzinfo=timezone.utc),
        connection_status_loader=_status_loader(),
    )

    assert report.ready_for_authorization is True
    assert report.ready_for_api is True
    serialized = json.dumps(report.to_dict(), ensure_ascii=False)
    assert "client-id-secret-value" not in serialized
    assert "client-secret-value" not in serialized
    assert "access-secret-value" not in serialized
    assert "refresh-secret-value" not in serialized


def test_web_client_type_is_rejected_before_authorization(tmp_path: Path) -> None:
    client_path = tmp_path / "client.json"
    token_path = tmp_path / "token.json"
    _write_client(client_path, client_type="web")
    _write_token(token_path)

    report = build_blogger_preflight_report(
        client_secret_path=client_path,
        token_path=token_path,
        connection_status_loader=_status_loader(),
    )

    assert report.ready_for_authorization is False
    assert report.ready_for_api is False
    assert any(check.key == "client_type" and check.status == "fail" for check in report.checks)


def test_missing_token_keeps_authorization_ready_but_api_not_ready(tmp_path: Path) -> None:
    client_path = tmp_path / "client.json"
    _write_client(client_path)

    report = build_blogger_preflight_report(
        client_secret_path=client_path,
        token_path=tmp_path / "missing-token.json",
        connection_status_loader=_status_loader(),
    )

    assert report.ready_for_authorization is True
    assert report.ready_for_api is False
    assert any(check.key == "token_file" and check.status == "warning" for check in report.checks)


def test_expired_token_with_refresh_token_is_usable_with_warning(tmp_path: Path) -> None:
    client_path = tmp_path / "client.json"
    token_path = tmp_path / "token.json"
    _write_client(client_path)
    _write_token(token_path, expiry="2026-01-01T00:00:00Z")

    report = build_blogger_preflight_report(
        client_secret_path=client_path,
        token_path=token_path,
        now=datetime(2026, 8, 3, tzinfo=timezone.utc),
        connection_status_loader=_status_loader(),
    )

    assert report.ready_for_api is True
    assert any(check.key == "token_expiry" and check.status == "warning" for check in report.checks)


def test_missing_blogger_scope_requires_reauthorization(tmp_path: Path) -> None:
    client_path = tmp_path / "client.json"
    token_path = tmp_path / "token.json"
    _write_client(client_path)
    _write_token(token_path, scopes=["https://www.googleapis.com/auth/userinfo.email"])

    report = build_blogger_preflight_report(
        client_secret_path=client_path,
        token_path=token_path,
        connection_status_loader=_status_loader(),
    )

    assert report.ready_for_api is False
    assert any(check.key == "token_scope" and check.status == "fail" for check in report.checks)


def test_malformed_json_is_reported_without_file_content(tmp_path: Path) -> None:
    client_path = tmp_path / "client.json"
    token_path = tmp_path / "token.json"
    client_path.write_text('{"secret":"client-leak"', encoding="utf-8")
    token_path.write_text('{"secret":"token-leak"', encoding="utf-8")

    report = build_blogger_preflight_report(
        client_secret_path=client_path,
        token_path=token_path,
        connection_status_loader=_status_loader(),
    )

    serialized = json.dumps(report.to_dict(), ensure_ascii=False)
    assert report.ready_for_authorization is False
    assert report.ready_for_api is False
    assert "client-leak" not in serialized
    assert "token-leak" not in serialized
