from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.services.blogger_draft_service import (
    DEFAULT_CLIENT_SECRET_PATH,
    DEFAULT_TOKEN_PATH,
)
from src.services.blogger_preflight_service import build_blogger_preflight_report


_STATUS_LABELS = {
    "pass": "통과",
    "warning": "확인 필요",
    "fail": "실패",
}


def _print_human(report) -> None:
    print("Blogger OAuth·API 사전점검")
    print("=" * 44)
    print(report.summary)
    print()
    for check in report.checks:
        label = _STATUS_LABELS.get(check.status, check.status)
        print(f"[{label}] {check.label}: {check.message}")
    print()
    print("네트워크 요청: 없음")
    print("DuckDB 쓰기: 없음")
    print("OAuth 비밀값 출력: 없음")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="비밀값을 출력하지 않고 Blogger OAuth·API 준비 상태를 검사합니다."
    )
    parser.add_argument(
        "--client",
        type=Path,
        default=DEFAULT_CLIENT_SECRET_PATH,
        help="데스크톱 앱 OAuth 클라이언트 JSON 경로",
    )
    parser.add_argument(
        "--token",
        type=Path,
        default=DEFAULT_TOKEN_PATH,
        help="로컬 OAuth 토큰 JSON 경로",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="사람용 요약 대신 개인정보 제한 JSON을 출력",
    )
    args = parser.parse_args()

    report = build_blogger_preflight_report(
        client_secret_path=args.client.expanduser().resolve(),
        token_path=args.token.expanduser().resolve(),
    )
    if args.json:
        print(json.dumps(report.to_dict(), ensure_ascii=False, indent=2))
    else:
        _print_human(report)

    if report.ready_for_api:
        return 0
    if report.ready_for_authorization:
        return 3
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
