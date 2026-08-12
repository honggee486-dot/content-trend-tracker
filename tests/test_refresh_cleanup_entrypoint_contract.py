from pathlib import Path
import subprocess
import sys


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_direct_refresh_main_installs_post_collection_cleanup_contract() -> None:
    code = """
from scripts import refresh_trends


def fake_lock(*args, **kwargs):
    cleanup = refresh_trends.run_automatic_cleanup_if_due
    refresh = refresh_trends.refresh_trend_sources_short_connections
    assert getattr(cleanup, '_post_collection_cleanup', False) is True
    assert getattr(refresh, '_post_collection_cleanup', False) is True
    return 0


refresh_trends.run_with_trend_refresh_lock = fake_lock
raise SystemExit(refresh_trends.main())
"""
    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stdout + result.stderr
