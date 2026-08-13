from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys

from scripts.report_blogger_preflight import main
from src.services.blogger_draft_service import BLOGGER_SCOPE


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _write_client(path: Path) -> None:
    path.write_text(
        json.dumps(
            {
                "installed": {
                    "client_id": "cli-client-secret-value",
                    "client_secret": "cli-client-secret-key",
                    "auth_uri": "https://accounts.example/authorize",
                    "token_uri": "https://accounts.example/token",
                    "redirect_uris": ["http://localhost"],
                }
            }
        ),
        encoding="utf-8",
    )


def _write_token(path: Path) -> None:
    path.write_text(
        json.dumps(
            {
                "token": "cli-access-secret-value",
                "refresh_token": "cli-refresh-secret-value",
                "token_uri": "https://accounts.example/token",
                "client_id": "cli-client-secret-value",
                "client_secret": "cli-client-secret-key",
                "scopes": [BLOGGER_SCOPE],
                "expiry": "2099-01-01T00:00:00Z",
            }
        ),
        encoding="utf-8",
    )


def test_json_cli_reports_ready_without_secret_values(
    tmp_path: Path,
    capsys,
) -> None:
    client_path = tmp_path / "client.json"
    token_path = tmp_path / "token.json"
    _write_client(client_path)
    _write_token(token_path)

    exit_code = main(
        [
            "--client",
            str(client_path),
            "--token",
            str(token_path),
            "--json",
        ]
    )
    captured = capsys.readouterr()

    assert exit_code == 0, captured.err
    report = json.loads(captured.out)
    assert report["ready_for_authorization"] is True
    assert report["ready_for_api"] is True
    assert "cli-client-secret-value" not in captured.out
    assert "cli-client-secret-key" not in captured.out
    assert "cli-access-secret-value" not in captured.out
    assert "cli-refresh-secret-value" not in captured.out


def test_cli_uses_exit_code_three_when_account_connection_is_needed(
    tmp_path: Path,
    capsys,
) -> None:
    client_path = tmp_path / "client.json"
    _write_client(client_path)

    exit_code = main(
        [
            "--client",
            str(client_path),
            "--token",
            str(tmp_path / "missing-token.json"),
        ]
    )
    captured = capsys.readouterr()

    assert exit_code == 3
    assert "계정 연결" in captured.out
    assert "네트워크 요청: 없음" in captured.out
    assert "DuckDB 쓰기: 없음" in captured.out


def test_blogger_preflight_batch_preserves_windows_contract() -> None:
    batch_path = PROJECT_ROOT / "run_blogger_preflight.bat"
    batch_bytes = batch_path.read_bytes()
    batch_text = batch_bytes.decode("utf-8")

    assert b"\r\n" in batch_bytes
    assert 'set "PYTHON_EXE=%~dp0.venv\\Scripts\\python.exe"' in batch_text
    assert 'set "PYTHON_EXE=%~dp0venv\\Scripts\\python.exe"' in batch_text
    assert (
        '"%PYTHON_EXE%" "%~dp0scripts\\report_blogger_preflight.py" %*'
        in batch_text
    )
    assert "exit /b %EXIT_CODE%" in batch_text
    assert "pause" not in batch_text.lower()
    assert "chcp" not in batch_text.lower()
