from __future__ import annotations

from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_blogger_preflight_panel_states_privacy_boundary() -> None:
    source = (PROJECT_ROOT / "src" / "blogger_preflight_ui.py").read_text(
        encoding="utf-8"
    )

    assert "Blogger OAuth·API 사전점검" in source
    assert "비밀값은 화면에 표시하지 않으며" in source
    assert "네트워크 요청·DB 쓰기를 수행하지 않습니다" in source
    assert "build_blogger_preflight_report" in source
    assert "pd.DataFrame(_check_rows(report))" in source


def test_blogger_actions_are_gated_by_preflight_readiness() -> None:
    source = (PROJECT_ROOT / "src" / "blogger_draft_ui.py").read_text(
        encoding="utf-8"
    )

    assert "preflight = render_blogger_preflight" in source
    assert "disabled=not preflight.ready_for_authorization" in source
    assert "disabled=not preflight.ready_for_api" in source
    assert "if binding and preflight.ready_for_api:" in source


def test_preflight_cli_only_prints_sanitized_report() -> None:
    source = (PROJECT_ROOT / "scripts" / "report_blogger_preflight.py").read_text(
        encoding="utf-8"
    )

    assert "OAuth 비밀값 출력: 없음" in source
    assert "report.to_dict()" in source
    assert ".read_text(" not in source
    assert "print(args.client" not in source
    assert "print(args.token" not in source
