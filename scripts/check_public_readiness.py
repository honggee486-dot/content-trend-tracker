from __future__ import annotations

import argparse
import fnmatch
import json
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Iterable


ALLOWED_EXACT_PATHS = {
    ".env.example",
    "data/.gitkeep",
}

CURRENT_BLOCK_PATTERNS = (
    ".env",
    ".env.*",
    ".streamlit/secrets.toml",
    "secrets*.toml",
    "credentials*.json",
    "credential*.json",
    "client_secret*.json",
    "service_account*.json",
    "service-account*.json",
    "oauth*.json",
    "token*.json",
    "cookies*.json",
    "cookie*.json",
    "session*.json",
    "storage_state*.json",
    "auth_state*.json",
    "*.pem",
    "*.key",
    "*.p12",
    "*.pfx",
    "*.cer",
    "*.crt",
    "id_rsa*",
    "id_ed25519*",
    "data/*",
    "*.duckdb",
    "*.duckdb.*",
    "*.db",
    "*.db-shm",
    "*.db-wal",
    "*.sqlite",
    "*.sqlite3",
    "*.sqlite-*",
    "*.parquet",
    "*.feather",
    "*.arrow",
    "*.log",
    "*.zip",
    "*.7z",
    "*.rar",
    "*.tar",
    "*.tar.gz",
    "*.tgz",
    "*.gz",
    "*.bz2",
    "*.xz",
)

HISTORY_BLOCK_PATTERNS = CURRENT_BLOCK_PATTERNS

HISTORY_REVIEW_PATTERNS = (
    "*.b64",
    "*.xz.b64",
    "*.part*",
    ".github/delta/*",
    ".github/payload/*",
    ".github/repair/*.b64",
)


def _normalize_path(path: str) -> str:
    normalized = path.replace("\\", "/")
    while normalized.startswith("./"):
        normalized = normalized[2:]
    return normalized.lstrip("/")


def _matches_any(path: str, patterns: Iterable[str]) -> bool:
    normalized = _normalize_path(path)
    if normalized in ALLOWED_EXACT_PATHS:
        return False
    basename = normalized.rsplit("/", 1)[-1]
    return any(
        fnmatch.fnmatchcase(normalized, pattern)
        or fnmatch.fnmatchcase(basename, pattern)
        for pattern in patterns
    )


def find_matching_paths(paths: Iterable[str], patterns: Iterable[str]) -> list[str]:
    return sorted(
        {
            _normalize_path(path)
            for path in paths
            if path and _matches_any(path, patterns)
        }
    )


def _run_git(repo_root: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", *args],
        cwd=repo_root,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    return completed.stdout


def tracked_paths(repo_root: Path) -> list[str]:
    output = subprocess.run(
        ["git", "ls-files", "-z"],
        cwd=repo_root,
        check=True,
        capture_output=True,
    ).stdout
    return [
        item.decode("utf-8", errors="replace")
        for item in output.split(b"\0")
        if item
    ]


def history_paths(repo_root: Path) -> list[str]:
    output = _run_git(repo_root, "rev-list", "--objects", "--all")
    paths: list[str] = []
    for line in output.splitlines():
        if " " not in line:
            continue
        _, path = line.split(" ", 1)
        if path:
            paths.append(path)
    return paths


def pull_audit_ref_count(repo_root: Path) -> int:
    output = _run_git(
        repo_root,
        "for-each-ref",
        "--format=%(refname)",
        "refs/public-audit/pull/",
        "refs/pull/",
    )
    return sum(1 for line in output.splitlines() if line.strip())


def _mask_email(email: str) -> str:
    email = email.strip()
    if "@" not in email:
        return "***"
    local, domain = email.split("@", 1)
    local_masked = (local[:1] + "***") if local else "***"
    domain_parts = domain.split(".")
    if domain_parts:
        first = domain_parts[0]
        domain_parts[0] = (first[:1] + "***") if first else "***"
    return f"{local_masked}@{'.'.join(domain_parts)}"


def personal_commit_emails(repo_root: Path) -> list[str]:
    output = _run_git(repo_root, "log", "--all", "--format=%ae")
    emails = sorted({line.strip() for line in output.splitlines() if line.strip()})
    return [
        _mask_email(email)
        for email in emails
        if not email.lower().endswith("@users.noreply.github.com")
        and not email.lower().endswith("@noreply.github.com")
    ]


def run_gitleaks(repo_root: Path) -> dict[str, object]:
    executable = shutil.which("gitleaks")
    if not executable:
        return {
            "status": "missing",
            "message": "gitleaks executable was not found on PATH",
        }

    command = [
        executable,
        "git",
        "--no-banner",
        "--no-color",
        "--redact",
        "--max-archive-depth=3",
        "--max-decode-depth=3",
        "--log-opts=--all",
        str(repo_root),
    ]
    completed = subprocess.run(
        command,
        cwd=repo_root,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if completed.returncode == 0:
        return {"status": "passed", "returncode": 0}
    if completed.returncode == 1:
        return {
            "status": "findings",
            "returncode": 1,
            "message": "Gitleaks reported one or more redacted findings",
        }
    return {
        "status": "error",
        "returncode": completed.returncode,
        "message": "Gitleaks could not complete the scan",
    }


def build_tracked_tree_report(repo_root: Path) -> dict[str, object]:
    """현재 추적 트리만 빠르게 검사하는 Public 저장소 지속 안전 게이트입니다."""
    tracked_sensitive = find_matching_paths(
        tracked_paths(repo_root),
        CURRENT_BLOCK_PATTERNS,
    )
    blockers = ["tracked_sensitive_paths"] if tracked_sensitive else []
    return {
        "mode": "tracked_only",
        "ready": not blockers,
        "blockers": blockers,
        "tracked_sensitive_paths": tracked_sensitive,
    }


def build_report(
    repo_root: Path,
    *,
    run_secret_scan: bool = True,
    acknowledge_history_review: bool = False,
) -> dict[str, object]:
    tracked = tracked_paths(repo_root)
    history = history_paths(repo_root)
    tracked_sensitive = find_matching_paths(tracked, CURRENT_BLOCK_PATTERNS)
    history_sensitive = find_matching_paths(history, HISTORY_BLOCK_PATTERNS)
    history_review = find_matching_paths(history, HISTORY_REVIEW_PATTERNS)
    pull_refs = pull_audit_ref_count(repo_root)
    personal_emails = personal_commit_emails(repo_root)
    gitleaks = (
        run_gitleaks(repo_root)
        if run_secret_scan
        else {"status": "skipped", "message": "secret scan was skipped"}
    )

    blockers: list[str] = []
    if tracked_sensitive:
        blockers.append("tracked_sensitive_paths")
    if history_sensitive:
        blockers.append("history_sensitive_paths")
    if pull_refs == 0:
        blockers.append("pull_refs_not_fetched")
    if gitleaks.get("status") != "passed":
        blockers.append("gitleaks_not_passed")
    if history_review and not acknowledge_history_review:
        blockers.append("history_review_required")

    return {
        "mode": "full",
        "ready": not blockers,
        "blockers": blockers,
        "tracked_sensitive_paths": tracked_sensitive,
        "history_sensitive_paths": history_sensitive,
        "history_review_paths": history_review,
        "history_review_acknowledged": acknowledge_history_review,
        "pull_audit_ref_count": pull_refs,
        "personal_commit_emails": personal_emails,
        "gitleaks": gitleaks,
    }


def _print_tracked_tree_human(report: dict[str, object]) -> None:
    print("[PUBLIC TRACKED TREE SAFETY]")
    print(f"ready: {report['ready']}")
    tracked_sensitive = report["tracked_sensitive_paths"]
    print(f"tracked sensitive paths: {len(tracked_sensitive)}")
    for path in tracked_sensitive:
        print(f"  - {path}")
    blockers = report["blockers"]
    if blockers:
        print("blockers:")
        for blocker in blockers:
            print(f"  - {blocker}")


def _print_human(report: dict[str, object]) -> None:
    print("[PUBLIC READINESS]")
    print(f"ready: {report['ready']}")
    print(f"pull audit refs: {report['pull_audit_ref_count']}")

    tracked_sensitive = report["tracked_sensitive_paths"]
    history_sensitive = report["history_sensitive_paths"]
    history_review = report["history_review_paths"]
    personal_emails = report["personal_commit_emails"]

    print(f"tracked sensitive paths: {len(tracked_sensitive)}")
    for path in tracked_sensitive:
        print(f"  - {path}")
    print(f"history sensitive paths: {len(history_sensitive)}")
    for path in history_sensitive:
        print(f"  - {path}")
    print(f"history review paths: {len(history_review)}")
    for path in history_review:
        print(f"  - {path}")
    print(f"personal commit emails: {len(personal_emails)}")
    for email in personal_emails:
        print(f"  - {email}")

    gitleaks = report["gitleaks"]
    print(f"gitleaks: {gitleaks.get('status')}")
    if gitleaks.get("message"):
        print(f"  {gitleaks['message']}")

    blockers = report["blockers"]
    if blockers:
        print("blockers:")
        for blocker in blockers:
            print(f"  - {blocker}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Check whether the repository is safe for public visibility without exposing secret values."
    )
    parser.add_argument(
        "--repo-root",
        type=Path,
        default=Path(__file__).resolve().parents[1],
        help="repository root; defaults to the parent of scripts/",
    )
    parser.add_argument(
        "--paths-only",
        action="store_true",
        help="skip Gitleaks; this can never produce a ready=true full-audit result",
    )
    parser.add_argument(
        "--tracked-only",
        action="store_true",
        help="check only currently tracked sensitive paths for continuous Public-repository CI",
    )
    parser.add_argument(
        "--acknowledge-history-review",
        action="store_true",
        help="acknowledge that listed encoded/archive history paths were manually reviewed",
    )
    parser.add_argument("--json", action="store_true", help="print JSON output")
    args = parser.parse_args(argv)

    repo_root = args.repo_root.resolve()
    try:
        if args.tracked_only:
            report = build_tracked_tree_report(repo_root)
        else:
            report = build_report(
                repo_root,
                run_secret_scan=not args.paths_only,
                acknowledge_history_review=args.acknowledge_history_review,
            )
    except (OSError, subprocess.CalledProcessError) as exc:
        print(f"[ERROR] public readiness check failed: {exc}", file=sys.stderr)
        return 2

    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    elif args.tracked_only:
        _print_tracked_tree_human(report)
    else:
        _print_human(report)

    return 0 if report["ready"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
