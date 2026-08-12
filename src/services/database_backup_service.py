"""DuckDB 백업, 복구와 읽기 전용 무결성 검사를 제공합니다."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import os
from pathlib import Path
import shutil
from typing import Iterable
from uuid import uuid4

import duckdb

from src.config import DEFAULT_DB_PATH, PROJECT_ROOT
from src.database import connect_database, init_database
from src.services.trend_refresh_lock_service import acquire_trend_refresh_lock


BACKUP_DIR_NAME = "backups"
BACKUP_FILENAME_PREFIX = "content_trend_tracker"
MANUAL_BACKUP_KEEP_COUNT = 10
PRE_RESTORE_BACKUP_KEEP_COUNT = 5
_MIN_FREE_SPACE_BYTES = 10 * 1024 * 1024

REQUIRED_TABLES = (
    "app_settings",
    "topics",
    "source_items",
    "topic_source_links",
    "content_packs",
    "generation_sessions",
    "drafts",
    "draft_revisions",
    "fact_check_items",
    "publish_records",
)

CRITICAL_ROW_COUNT_TABLES = (
    "topics",
    "topic_source_links",
    "topic_references",
    "content_packs",
    "generation_sessions",
    "drafts",
    "draft_revisions",
    "fact_check_items",
    "publish_records",
)


class DatabaseBackupError(RuntimeError):
    """백업 또는 복구를 안전하게 완료하지 못한 경우 발생합니다."""


@dataclass(frozen=True)
class IntegrityIssue:
    code: str
    severity: str
    message: str
    count: int = 0


@dataclass(frozen=True)
class DatabaseIntegrityReport:
    database_path: Path
    checked_at: datetime
    database_size_bytes: int
    table_count: int
    row_counts: tuple[tuple[str, int], ...]
    issues: tuple[IntegrityIssue, ...]

    @property
    def is_valid(self) -> bool:
        return not any(issue.severity == "error" for issue in self.issues)

    @property
    def error_count(self) -> int:
        return sum(1 for issue in self.issues if issue.severity == "error")

    @property
    def warning_count(self) -> int:
        return sum(1 for issue in self.issues if issue.severity == "warning")


@dataclass(frozen=True)
class DatabaseBackupInfo:
    path: Path
    kind: str
    created_at: datetime
    size_bytes: int


@dataclass(frozen=True)
class DatabaseBackupResult:
    backup: DatabaseBackupInfo
    integrity: DatabaseIntegrityReport
    removed_backups: tuple[Path, ...]


@dataclass(frozen=True)
class DatabaseRestoreResult:
    restored_from: Path
    pre_restore_backup: Path
    integrity: DatabaseIntegrityReport
    finished_at: datetime


def get_database_backup_dir(
    project_root: str | Path = PROJECT_ROOT,
) -> Path:
    return Path(project_root).resolve() / BACKUP_DIR_NAME


def _backup_kind_from_name(path: Path) -> str:
    name = path.name.casefold()
    if "_pre_restore_" in name:
        return "pre_restore"
    return "manual"


def list_database_backups(
    *,
    backup_dir: str | Path | None = None,
    project_root: str | Path = PROJECT_ROOT,
) -> list[DatabaseBackupInfo]:
    directory = (
        Path(backup_dir).resolve()
        if backup_dir is not None
        else get_database_backup_dir(project_root)
    )
    if not directory.is_dir():
        return []

    backups: list[DatabaseBackupInfo] = []
    pattern = f"{BACKUP_FILENAME_PREFIX}_*.duckdb"
    for path in directory.glob(pattern):
        if not path.is_file():
            continue
        stat = path.stat()
        backups.append(
            DatabaseBackupInfo(
                path=path.resolve(),
                kind=_backup_kind_from_name(path),
                created_at=datetime.fromtimestamp(stat.st_mtime),
                size_bytes=int(stat.st_size),
            )
        )
    return sorted(backups, key=lambda item: item.created_at, reverse=True)


def _table_names(con: duckdb.DuckDBPyConnection) -> set[str]:
    return {
        str(row[0])
        for row in con.execute(
            """
            SELECT table_name
            FROM information_schema.tables
            WHERE table_schema = 'main' AND table_type = 'BASE TABLE'
            """
        ).fetchall()
    }


def _count_query(
    con: duckdb.DuckDBPyConnection,
    query: str,
    parameters: Iterable[object] | None = None,
) -> int:
    row = con.execute(query, list(parameters or [])).fetchone()
    return int(row[0] or 0) if row else 0


def _relationship_issues(
    con: duckdb.DuckDBPyConnection,
    tables: set[str],
) -> list[IntegrityIssue]:
    checks = (
        (
            "orphan_topic_source_topic",
            {"topic_source_links", "topics"},
            """
            SELECT COUNT(*)
            FROM topic_source_links l
            LEFT JOIN topics t ON t.topic_id = l.topic_id
            WHERE t.topic_id IS NULL
            """,
            "주제가 없는 원문 연결이 있습니다.",
        ),
        (
            "orphan_topic_source_item",
            {"topic_source_links", "source_items"},
            """
            SELECT COUNT(*)
            FROM topic_source_links l
            LEFT JOIN source_items s ON s.source_item_id = l.source_item_id
            WHERE s.source_item_id IS NULL
            """,
            "원문이 없는 주제 연결이 있습니다.",
        ),
        (
            "orphan_content_pack_topic",
            {"content_packs", "topics"},
            """
            SELECT COUNT(*)
            FROM content_packs p
            LEFT JOIN topics t ON t.topic_id = p.topic_id
            WHERE t.topic_id IS NULL
            """,
            "주제가 없는 자료팩이 있습니다.",
        ),
        (
            "orphan_generation_topic",
            {"generation_sessions", "topics"},
            """
            SELECT COUNT(*)
            FROM generation_sessions g
            LEFT JOIN topics t ON t.topic_id = g.topic_id
            WHERE t.topic_id IS NULL
            """,
            "주제가 없는 AI 생성 기록이 있습니다.",
        ),
        (
            "orphan_generation_pack",
            {"generation_sessions", "content_packs"},
            """
            SELECT COUNT(*)
            FROM generation_sessions g
            LEFT JOIN content_packs p ON p.content_pack_id = g.content_pack_id
            WHERE p.content_pack_id IS NULL
            """,
            "자료팩이 없는 AI 생성 기록이 있습니다.",
        ),
        (
            "orphan_draft_topic",
            {"drafts", "topics"},
            """
            SELECT COUNT(*)
            FROM drafts d
            LEFT JOIN topics t ON t.topic_id = d.topic_id
            WHERE t.topic_id IS NULL
            """,
            "주제가 없는 초안이 있습니다.",
        ),
        (
            "orphan_draft_generation",
            {"drafts", "generation_sessions"},
            """
            SELECT COUNT(*)
            FROM drafts d
            LEFT JOIN generation_sessions g ON g.generation_id = d.generation_id
            WHERE d.generation_id IS NOT NULL AND g.generation_id IS NULL
            """,
            "AI 생성 기록이 없는 초안이 있습니다.",
        ),
        (
            "orphan_revision_draft",
            {"draft_revisions", "drafts"},
            """
            SELECT COUNT(*)
            FROM draft_revisions r
            LEFT JOIN drafts d ON d.draft_id = r.draft_id
            WHERE d.draft_id IS NULL
            """,
            "초안이 없는 리비전이 있습니다.",
        ),
        (
            "orphan_fact_check_draft",
            {"fact_check_items", "drafts"},
            """
            SELECT COUNT(*)
            FROM fact_check_items f
            LEFT JOIN drafts d ON d.draft_id = f.draft_id
            WHERE d.draft_id IS NULL
            """,
            "초안이 없는 사실 확인 항목이 있습니다.",
        ),
        (
            "orphan_publish_draft",
            {"publish_records", "drafts"},
            """
            SELECT COUNT(*)
            FROM publish_records p
            LEFT JOIN drafts d ON d.draft_id = p.draft_id
            WHERE d.draft_id IS NULL
            """,
            "초안이 없는 발행 기록이 있습니다.",
        ),
    )

    issues: list[IntegrityIssue] = []
    for code, required, query, message in checks:
        if not required.issubset(tables):
            continue
        count = _count_query(con, query)
        if count:
            issues.append(
                IntegrityIssue(
                    code=code,
                    severity="warning",
                    message=message,
                    count=count,
                )
            )
    return issues


def check_database_integrity(
    db_path: str | Path = DEFAULT_DB_PATH,
    *,
    now: datetime | None = None,
) -> DatabaseIntegrityReport:
    path = Path(db_path).resolve()
    checked_at = now or datetime.now()
    issues: list[IntegrityIssue] = []
    row_counts: list[tuple[str, int]] = []
    table_count = 0
    size_bytes = path.stat().st_size if path.is_file() else 0

    if not path.is_file():
        return DatabaseIntegrityReport(
            database_path=path,
            checked_at=checked_at,
            database_size_bytes=0,
            table_count=0,
            row_counts=(),
            issues=(
                IntegrityIssue(
                    code="database_missing",
                    severity="error",
                    message="DuckDB 파일을 찾을 수 없습니다.",
                ),
            ),
        )
    if size_bytes <= 0:
        issues.append(
            IntegrityIssue(
                code="database_empty",
                severity="error",
                message="DuckDB 파일 크기가 0바이트입니다.",
            )
        )

    try:
        with duckdb.connect(str(path), read_only=True) as con:
            con.execute("SELECT 1").fetchone()
            tables = _table_names(con)
            table_count = len(tables)
            missing = sorted(set(REQUIRED_TABLES) - tables)
            if missing:
                issues.append(
                    IntegrityIssue(
                        code="required_tables_missing",
                        severity="error",
                        message="필수 테이블이 없습니다: " + ", ".join(missing),
                        count=len(missing),
                    )
                )

            for table_name in CRITICAL_ROW_COUNT_TABLES:
                if table_name not in tables:
                    continue
                row_counts.append(
                    (table_name, _count_query(con, f'SELECT COUNT(*) FROM "{table_name}"'))
                )

            issues.extend(_relationship_issues(con, tables))
    except Exception as exc:
        issues.append(
            IntegrityIssue(
                code="database_open_failed",
                severity="error",
                message=f"DuckDB를 읽기 전용으로 열어 검사하지 못했습니다: {exc}",
            )
        )

    return DatabaseIntegrityReport(
        database_path=path,
        checked_at=checked_at,
        database_size_bytes=size_bytes,
        table_count=table_count,
        row_counts=tuple(row_counts),
        issues=tuple(issues),
    )


def _safe_backup_path(path: str | Path, backup_dir: Path) -> Path:
    resolved = Path(path).resolve()
    directory = backup_dir.resolve()
    try:
        resolved.relative_to(directory)
    except ValueError as exc:
        raise DatabaseBackupError("프로젝트 백업 폴더 밖의 파일은 복구할 수 없습니다.") from exc
    if resolved.suffix.casefold() != ".duckdb" or not resolved.name.startswith(
        f"{BACKUP_FILENAME_PREFIX}_"
    ):
        raise DatabaseBackupError("프로그램이 만든 DuckDB 백업 파일만 복구할 수 있습니다.")
    if not resolved.is_file():
        raise DatabaseBackupError("선택한 백업 파일을 찾을 수 없습니다.")
    return resolved


def _assert_disk_space(target_dir: Path, source_size: int) -> None:
    target_dir.mkdir(parents=True, exist_ok=True)
    free_bytes = int(shutil.disk_usage(target_dir).free)
    required = max(_MIN_FREE_SPACE_BYTES, int(source_size * 1.10))
    if free_bytes < required:
        raise DatabaseBackupError(
            "백업 파일을 안전하게 만들 디스크 여유 공간이 부족합니다. "
            f"필요 약 {required:,}바이트, 사용 가능 {free_bytes:,}바이트입니다."
        )


def _checkpoint_and_close(db_path: Path) -> None:
    try:
        with connect_database(db_path) as con:
            con.execute("CHECKPOINT")
    except Exception as exc:
        raise DatabaseBackupError(
            "DuckDB CHECKPOINT를 완료하지 못했습니다. 앱·수집 프로세스가 DB를 사용 중인지 확인하세요. "
            f"원인: {exc}"
        ) from exc


def _assert_wal_clear(db_path: Path) -> None:
    wal_path = Path(f"{db_path}.wal")
    if not wal_path.exists():
        return
    try:
        size = int(wal_path.stat().st_size)
    except OSError as exc:
        raise DatabaseBackupError(f"DuckDB WAL 상태를 확인하지 못했습니다: {exc}") from exc
    if size > 0:
        raise DatabaseBackupError(
            "CHECKPOINT 후에도 비어 있지 않은 DuckDB WAL이 남아 있어 백업·복구를 중단했습니다. "
            "앱과 예약 수집을 모두 종료한 뒤 다시 시도하세요."
        )
    try:
        wal_path.unlink()
    except FileNotFoundError:
        pass
    except OSError as exc:
        raise DatabaseBackupError(f"빈 DuckDB WAL 파일을 정리하지 못했습니다: {exc}") from exc


def _backup_filename(kind: str, now: datetime) -> str:
    safe_kind = "pre_restore" if kind == "pre_restore" else "manual"
    stamp = now.strftime("%Y%m%d_%H%M%S_%f")
    return f"{BACKUP_FILENAME_PREFIX}_{safe_kind}_{stamp}.duckdb"


def _prune_database_backups(
    backup_dir: Path,
    *,
    manual_keep: int = MANUAL_BACKUP_KEEP_COUNT,
    pre_restore_keep: int = PRE_RESTORE_BACKUP_KEEP_COUNT,
    preserve_paths: Iterable[Path] = (),
) -> tuple[Path, ...]:
    removed: list[Path] = []
    preserved = {Path(path).resolve() for path in preserve_paths}
    backups = list_database_backups(backup_dir=backup_dir)
    for kind, keep_count in (
        ("manual", max(1, int(manual_keep))),
        ("pre_restore", max(1, int(pre_restore_keep))),
    ):
        kept = 0
        for backup in [item for item in backups if item.kind == kind]:
            if backup.path in preserved:
                kept += 1
                continue
            if kept < keep_count:
                kept += 1
                continue
            try:
                backup.path.unlink()
                removed.append(backup.path)
            except FileNotFoundError:
                pass
    return tuple(removed)


def _create_database_backup_locked(
    *,
    db_path: Path,
    backup_dir: Path,
    kind: str,
    now: datetime,
    manual_keep: int,
    pre_restore_keep: int,
    preserve_paths: Iterable[Path] = (),
) -> DatabaseBackupResult:
    if not db_path.is_file():
        raise DatabaseBackupError("백업할 DuckDB 파일을 찾을 수 없습니다.")

    source_size = int(db_path.stat().st_size)
    _assert_disk_space(backup_dir, source_size)
    _checkpoint_and_close(db_path)
    _assert_wal_clear(db_path)

    final_path = backup_dir / _backup_filename(kind, now)
    temp_path = backup_dir / f".{final_path.name}.{uuid4().hex}.partial"
    try:
        shutil.copy2(db_path, temp_path)
        integrity = check_database_integrity(temp_path, now=now)
        if not integrity.is_valid:
            details = "; ".join(issue.message for issue in integrity.issues)
            raise DatabaseBackupError(f"복사한 백업의 무결성 검사가 실패했습니다: {details}")
        os.replace(temp_path, final_path)
    finally:
        try:
            temp_path.unlink()
        except FileNotFoundError:
            pass

    stat = final_path.stat()
    backup = DatabaseBackupInfo(
        path=final_path.resolve(),
        kind=_backup_kind_from_name(final_path),
        created_at=datetime.fromtimestamp(stat.st_mtime),
        size_bytes=int(stat.st_size),
    )
    removed = _prune_database_backups(
        backup_dir,
        manual_keep=manual_keep,
        pre_restore_keep=pre_restore_keep,
        preserve_paths=(*preserve_paths, backup.path),
    )
    return DatabaseBackupResult(
        backup=backup,
        integrity=integrity,
        removed_backups=removed,
    )


def create_database_backup(
    db_path: str | Path = DEFAULT_DB_PATH,
    *,
    backup_dir: str | Path | None = None,
    project_root: str | Path = PROJECT_ROOT,
    kind: str = "manual",
    now: datetime | None = None,
    manual_keep: int = MANUAL_BACKUP_KEEP_COUNT,
    pre_restore_keep: int = PRE_RESTORE_BACKUP_KEEP_COUNT,
) -> DatabaseBackupResult:
    root = Path(project_root).resolve()
    source = Path(db_path).resolve()
    directory = (
        Path(backup_dir).resolve()
        if backup_dir is not None
        else get_database_backup_dir(root)
    )
    attempt = acquire_trend_refresh_lock(root, launcher=f"database-{kind}-backup")
    if not attempt.acquired or attempt.lock is None:
        raise DatabaseBackupError(attempt.message or "수집 실행 잠금을 획득하지 못했습니다.")
    try:
        return _create_database_backup_locked(
            db_path=source,
            backup_dir=directory,
            kind=kind,
            now=now or datetime.now(),
            manual_keep=manual_keep,
            pre_restore_keep=pre_restore_keep,
        )
    finally:
        attempt.lock.release()


def restore_database_backup(
    backup_path: str | Path,
    db_path: str | Path = DEFAULT_DB_PATH,
    *,
    backup_dir: str | Path | None = None,
    project_root: str | Path = PROJECT_ROOT,
    now: datetime | None = None,
    manual_keep: int = MANUAL_BACKUP_KEEP_COUNT,
    pre_restore_keep: int = PRE_RESTORE_BACKUP_KEEP_COUNT,
) -> DatabaseRestoreResult:
    root = Path(project_root).resolve()
    target = Path(db_path).resolve()
    directory = (
        Path(backup_dir).resolve()
        if backup_dir is not None
        else get_database_backup_dir(root)
    )
    selected = _safe_backup_path(backup_path, directory)
    source_integrity = check_database_integrity(selected)
    if not source_integrity.is_valid:
        details = "; ".join(issue.message for issue in source_integrity.issues)
        raise DatabaseBackupError(f"선택한 백업을 복구에 사용할 수 없습니다: {details}")

    attempt = acquire_trend_refresh_lock(root, launcher="database-restore")
    if not attempt.acquired or attempt.lock is None:
        raise DatabaseBackupError(attempt.message or "수집 실행 잠금을 획득하지 못했습니다.")

    current = now or datetime.now()
    pre_restore_path: Path | None = None
    replacement_started = False
    restore_temp = target.with_name(f".{target.name}.{uuid4().hex}.restore.partial")
    rollback_temp = target.with_name(f".{target.name}.{uuid4().hex}.rollback.partial")
    try:
        pre_restore = _create_database_backup_locked(
            db_path=target,
            backup_dir=directory,
            kind="pre_restore",
            now=current,
            manual_keep=manual_keep,
            pre_restore_keep=pre_restore_keep,
            preserve_paths=(selected,),
        )
        pre_restore_path = pre_restore.backup.path

        _assert_disk_space(target.parent, int(selected.stat().st_size))
        shutil.copy2(selected, restore_temp)
        staged_integrity = check_database_integrity(restore_temp, now=current)
        if not staged_integrity.is_valid:
            details = "; ".join(issue.message for issue in staged_integrity.issues)
            raise DatabaseBackupError(f"복구 임시 파일의 무결성 검사가 실패했습니다: {details}")

        _checkpoint_and_close(target)
        _assert_wal_clear(target)
        os.replace(restore_temp, target)
        replacement_started = True

        init_database(target)
        restored_integrity = check_database_integrity(target, now=current)
        if not restored_integrity.is_valid:
            details = "; ".join(issue.message for issue in restored_integrity.issues)
            raise DatabaseBackupError(f"복구 후 무결성 검사가 실패했습니다: {details}")

        return DatabaseRestoreResult(
            restored_from=selected,
            pre_restore_backup=pre_restore_path,
            integrity=restored_integrity,
            finished_at=current,
        )
    except Exception as exc:
        if replacement_started and pre_restore_path is not None and pre_restore_path.is_file():
            try:
                shutil.copy2(pre_restore_path, rollback_temp)
                rollback_integrity = check_database_integrity(rollback_temp, now=current)
                if not rollback_integrity.is_valid:
                    raise DatabaseBackupError("복구 실패 후 원본 백업의 무결성 검사도 실패했습니다.")
                os.replace(rollback_temp, target)
                init_database(target)
            except Exception as rollback_exc:
                raise DatabaseBackupError(
                    "복구가 실패했고 자동 원상 복구도 완료하지 못했습니다. "
                    f"복구 오류: {exc}; 원상 복구 오류: {rollback_exc}; "
                    f"보존 백업: {pre_restore_path}"
                ) from rollback_exc
        if isinstance(exc, DatabaseBackupError):
            raise
        raise DatabaseBackupError(f"DuckDB 복구를 완료하지 못했습니다: {exc}") from exc
    finally:
        for temp_path in (restore_temp, rollback_temp):
            try:
                temp_path.unlink()
            except FileNotFoundError:
                pass
        attempt.lock.release()
