from __future__ import annotations

from datetime import datetime
from pathlib import Path

import duckdb
import pytest

from src.database import connect_database, get_setting, init_database, set_setting
from src.services.database_backup_service import (
    DatabaseBackupError,
    check_database_integrity,
    create_database_backup,
    list_database_backups,
    restore_database_backup,
)
from src.services.trend_refresh_lock_service import acquire_trend_refresh_lock


def _initialized_database(tmp_path: Path) -> Path:
    db_path = tmp_path / "data" / "content_trend_tracker.duckdb"
    init_database(db_path)
    return db_path


def test_database_backup_is_reopened_and_preserves_user_rows(tmp_path: Path) -> None:
    db_path = _initialized_database(tmp_path)
    backup_dir = tmp_path / "backups"
    with connect_database(db_path) as con:
        set_setting(con, "backup_test_marker", "before-backup")

    result = create_database_backup(
        db_path,
        backup_dir=backup_dir,
        project_root=tmp_path,
        now=datetime(2026, 7, 31, 9, 15, 0),
    )

    assert result.backup.path.is_file()
    assert result.backup.kind == "manual"
    assert result.integrity.is_valid is True
    assert result.integrity.error_count == 0
    assert list_database_backups(backup_dir=backup_dir)[0].path == result.backup.path
    with connect_database(result.backup.path, read_only=True) as con:
        assert get_setting(con, "backup_test_marker", "") == "before-backup"


def test_restore_creates_pre_restore_backup_and_restores_selected_state(
    tmp_path: Path,
) -> None:
    db_path = _initialized_database(tmp_path)
    backup_dir = tmp_path / "backups"
    with connect_database(db_path) as con:
        set_setting(con, "restore_test_marker", "selected-backup-state")
    selected = create_database_backup(
        db_path,
        backup_dir=backup_dir,
        project_root=tmp_path,
        now=datetime(2026, 7, 31, 9, 20, 0),
    ).backup.path

    with connect_database(db_path) as con:
        set_setting(con, "restore_test_marker", "current-state-before-restore")

    result = restore_database_backup(
        selected,
        db_path,
        backup_dir=backup_dir,
        project_root=tmp_path,
        now=datetime(2026, 7, 31, 9, 25, 0),
    )

    assert result.integrity.is_valid is True
    assert result.pre_restore_backup.is_file()
    assert "_pre_restore_" in result.pre_restore_backup.name
    with connect_database(db_path, read_only=True) as con:
        assert get_setting(con, "restore_test_marker", "") == "selected-backup-state"
    with connect_database(result.pre_restore_backup, read_only=True) as con:
        assert get_setting(con, "restore_test_marker", "") == "current-state-before-restore"


def test_integrity_report_warns_about_orphaned_topic_source_links(
    tmp_path: Path,
) -> None:
    db_path = _initialized_database(tmp_path)
    with connect_database(db_path) as con:
        con.execute(
            """
            INSERT INTO topic_source_links(
                topic_id, source_item_id, match_type, match_score, linked_at
            ) VALUES ('missing-topic', 'missing-source', 'manual', 1.0, ?)
            """,
            [datetime(2026, 7, 31, 9, 30, 0)],
        )

    report = check_database_integrity(db_path)

    assert report.is_valid is True
    assert report.warning_count == 2
    issue_codes = {issue.code for issue in report.issues}
    assert "orphan_topic_source_topic" in issue_codes
    assert "orphan_topic_source_item" in issue_codes
    assert all(issue.severity == "warning" for issue in report.issues)


def test_integrity_report_blocks_database_without_required_tables(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "incomplete.duckdb"
    with duckdb.connect(str(db_path)) as con:
        con.execute("CREATE TABLE only_table(value INTEGER)")

    report = check_database_integrity(db_path)

    assert report.is_valid is False
    assert report.error_count >= 1
    assert any(issue.code == "required_tables_missing" for issue in report.issues)


def test_backup_is_blocked_while_collection_lock_is_active(tmp_path: Path) -> None:
    db_path = _initialized_database(tmp_path)
    lock_attempt = acquire_trend_refresh_lock(tmp_path, launcher="active-test")
    assert lock_attempt.acquired is True
    assert lock_attempt.lock is not None
    try:
        with pytest.raises(DatabaseBackupError, match="이미 실행 중"):
            create_database_backup(
                db_path,
                backup_dir=tmp_path / "backups",
                project_root=tmp_path,
            )
    finally:
        lock_attempt.lock.release()


def test_restore_rejects_files_outside_project_backup_directory(tmp_path: Path) -> None:
    db_path = _initialized_database(tmp_path)
    outside = tmp_path / "outside.duckdb"
    outside.write_bytes(db_path.read_bytes())

    with pytest.raises(DatabaseBackupError, match="백업 폴더 밖"):
        restore_database_backup(
            outside,
            db_path,
            backup_dir=tmp_path / "backups",
            project_root=tmp_path,
        )


class _FakeStreamlit:
    def __init__(self) -> None:
        self.events: list[str] = []

    def caption(self, value: object, *_args, **_kwargs) -> None:
        self.events.append(f"caption:{value}")

    def divider(self) -> None:
        self.events.append("divider")


def test_quality_diagnostics_are_grouped_in_order_without_settings_wrapper(
    monkeypatch,
) -> None:
    import src.database_backup_ui as backup_ui

    fake_st = _FakeStreamlit()
    monkeypatch.setattr(
        backup_ui,
        "_render_gemini_stability",
        lambda *, st_module: st_module.events.append("stability"),
    )
    monkeypatch.setattr(
        backup_ui,
        "_render_source_diversity",
        lambda *, st_module: st_module.events.append("diversity"),
    )

    backup_ui.render_quality_diagnostic_panels(st_module=fake_st)

    assert fake_st.events == ["stability", "divider", "diversity"]
