from pathlib import Path

from scripts.check_public_readiness import (
    CURRENT_BLOCK_PATTERNS,
    HISTORY_REVIEW_PATTERNS,
    _mask_email,
    build_tracked_tree_report,
    find_matching_paths,
    main,
)


ROOT = Path(__file__).resolve().parents[1]


def test_public_readiness_blocks_sensitive_current_paths_but_allows_examples():
    paths = [
        ".env",
        ".env.production",
        ".env.example",
        "data/content_trend_tracker.duckdb",
        "data/private_snapshot.json",
        "data/.gitkeep",
        "logs/runtime.log",
        "credentials-prod.json",
        "certs/client.pem",
        "exports/items.parquet",
    ]

    matches = find_matching_paths(paths, CURRENT_BLOCK_PATTERNS)

    assert ".env" in matches
    assert ".env.production" in matches
    assert "data/content_trend_tracker.duckdb" in matches
    assert "data/private_snapshot.json" in matches
    assert "logs/runtime.log" in matches
    assert "credentials-prod.json" in matches
    assert "certs/client.pem" in matches
    assert "exports/items.parquet" in matches
    assert ".env.example" not in matches
    assert "data/.gitkeep" not in matches


def test_public_readiness_marks_encoded_history_for_manual_review():
    paths = [
        ".github/delta/two_stage.part00",
        ".github/payload/two_stage.part01",
        ".github/repair/agents.xz.b64",
        "src/services/trend_discovery_service.py",
    ]

    matches = find_matching_paths(paths, HISTORY_REVIEW_PATTERNS)

    assert ".github/delta/two_stage.part00" in matches
    assert ".github/payload/two_stage.part01" in matches
    assert ".github/repair/agents.xz.b64" in matches
    assert "src/services/trend_discovery_service.py" not in matches


def test_public_readiness_masks_personal_email():
    masked = _mask_email("developer@example.com")

    assert masked == "d***@e***.com"
    assert "developer" not in masked
    assert "example" not in masked


def test_tracked_tree_report_is_clean_for_current_repository():
    report = build_tracked_tree_report(ROOT)

    assert report["mode"] == "tracked_only"
    assert report["ready"] is True
    assert report["tracked_sensitive_paths"] == []
    assert report["blockers"] == []


def test_tracked_only_cli_can_pass_without_full_history_or_gitleaks():
    assert main(["--repo-root", str(ROOT), "--tracked-only", "--json"]) == 0


def test_ci_runs_public_tracked_tree_safety_check():
    text = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")

    assert "Public tracked-tree safety check" in text
    assert "python scripts/check_public_readiness.py --tracked-only" in text


def test_gitignore_covers_public_repository_sensitive_file_classes():
    text = (ROOT / ".gitignore").read_text(encoding="utf-8")

    required_entries = (
        ".env.*",
        "data/*",
        "*.duckdb",
        "*.db",
        "*.sqlite3",
        "*.parquet",
        "*.log",
        "*.pem",
        "*.key",
        "service-account*.json",
        "cookies*.json",
        "session*.json",
        "*.zip",
        ".vscode/",
        "Thumbs.db",
    )
    for entry in required_entries:
        assert entry in text


def test_env_example_has_no_person_specific_quota_scope():
    text = (ROOT / ".env.example").read_text(encoding="utf-8")

    assert "honggee" not in text.lower()
    assert "GEMINI_QUOTA_SCOPE_ID=content-trend-tracker-default" in text
    assert "NAVER_CLIENT_ID=\n" in text
    assert "NAVER_CLIENT_SECRET=\n" in text
    assert "KAKAO_REST_API_KEY=\n" in text
    assert "GEMINI_API_KEY=\n" in text
