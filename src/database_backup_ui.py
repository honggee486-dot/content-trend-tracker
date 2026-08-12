"""설정 탭 안에 품질 진단과 DuckDB 백업·복구를 분리해 표시합니다."""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import streamlit as st

from src.config import (
    DEFAULT_DB_PATH,
    PROJECT_ROOT,
    get_gemini_config,
)
from src.database import connect_database
from src.gemini_stability_ui import (
    render_gemini_stability_panel,
    render_gemini_usage_log_panel,
)
from src.source_diversity_ui import render_source_diversity_panel
from src.services.database_backup_service import (
    DatabaseBackupError,
    DatabaseIntegrityReport,
    check_database_integrity,
    create_database_backup,
    get_database_backup_dir,
    list_database_backups,
    restore_database_backup,
)


_CURRENT_REPORT_KEY = "database_backup_current_integrity_report"
_SELECTED_REPORT_KEY = "database_backup_selected_integrity_report"
_FLASH_KEY = "database_backup_flash_message"


def _format_file_size(size_bytes: int) -> str:
    size = max(0, int(size_bytes or 0))
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size < 1024 or unit == "TB":
            return f"{size:.1f} {unit}" if unit != "B" else f"{size} B"
        size /= 1024
    return f"{size:.1f} TB"


def _render_integrity_report(report: DatabaseIntegrityReport, *, st_module=st) -> None:
    if report.is_valid:
        st_module.success(
            f"무결성 검사 통과 · 테이블 {report.table_count:,}개 · "
            f"파일 {_format_file_size(report.database_size_bytes)}"
        )
    else:
        st_module.error(
            f"무결성 검사 실패 · 오류 {report.error_count:,}개 · "
            f"경고 {report.warning_count:,}개"
        )

    if report.issues:
        issue_rows = [
            {
                "등급": "오류" if issue.severity == "error" else "주의",
                "코드": issue.code,
                "건수": int(issue.count),
                "내용": issue.message,
            }
            for issue in report.issues
        ]
        st_module.dataframe(
            pd.DataFrame(issue_rows),
            hide_index=True,
            width="stretch",
        )

    if report.row_counts:
        row_frame = pd.DataFrame(
            [
                {"사용자 작업 테이블": table_name, "행 수": int(row_count)}
                for table_name, row_count in report.row_counts
            ]
        )
        st_module.dataframe(row_frame, hide_index=True, width="stretch")


def _render_gemini_stability(con=None, *, st_module=st) -> None:
    config = get_gemini_config()

    def _render(active_con) -> None:
        render_gemini_stability_panel(
            active_con,
            app_id=config.app_id,
            current_items_per_request=config.topic_angle_batch_limit,
            current_thinking_level=config.topic_angle_thinking_level,
            st_module=st_module,
        )
        render_gemini_usage_log_panel(
            active_con,
            app_id=config.app_id,
            st_module=st_module,
        )

    try:
        if con is not None:
            _render(con)
            return
        with connect_database(DEFAULT_DB_PATH, read_only=True) as diagnostic_con:
            _render(diagnostic_con)
    except Exception as exc:
        st_module.caption(f"Gemini 진단과 사용 로그를 불러오지 못했습니다: {exc}")


def _render_source_diversity(con=None, *, st_module=st) -> None:
    try:
        if con is not None:
            render_source_diversity_panel(con, st_module=st_module)
            return
        with connect_database(DEFAULT_DB_PATH, read_only=True) as diagnostic_con:
            render_source_diversity_panel(diagnostic_con, st_module=st_module)
    except Exception as exc:
        st_module.caption(f"수집 출처 다양성 진단을 불러오지 못했습니다: {exc}")


def render_quality_diagnostic_panels(con=None, *, st_module=st) -> None:
    """Render SELECT-only diagnostics and reuse an active app connection when present."""
    if con is None:
        _render_gemini_stability(st_module=st_module)
        st_module.divider()
        _render_source_diversity(st_module=st_module)
        return
    _render_gemini_stability(con, st_module=st_module)
    st_module.divider()
    _render_source_diversity(con, st_module=st_module)


def render_database_backup_panel(*, st_module=st) -> None:
    st_module.subheader("데이터베이스 백업·안전 복구")
    st_module.caption(
        "백업은 CHECKPOINT 후 DB 연결을 닫고 복사본을 다시 열어 검사한 뒤 확정합니다. "
        "복구 전에는 현재 DB를 자동으로 pre_restore 백업하며, 수집 잠금·WAL·파일 교체에 "
        "문제가 있으면 기존 DB를 변경하지 않고 중단합니다."
    )

    flash = st_module.session_state.pop(_FLASH_KEY, None)
    if flash:
        st_module.success(str(flash))

    backup_dir = get_database_backup_dir(PROJECT_ROOT)
    backups = list_database_backups(backup_dir=backup_dir)
    current_size = DEFAULT_DB_PATH.stat().st_size if DEFAULT_DB_PATH.is_file() else 0
    latest = backups[0] if backups else None

    metric_columns = st_module.columns(3)
    metric_columns[0].metric(
        "현재 DB 크기",
        _format_file_size(current_size),
        help="data/content_trend_tracker.duckdb 파일 크기입니다.",
        border=True,
    )
    metric_columns[1].metric(
        "보관 백업",
        f"{len(backups):,}개",
        help="수동 백업 최대 10개와 복구 전 자동 백업 최대 5개를 보관합니다.",
        border=True,
    )
    metric_columns[2].metric(
        "최근 백업",
        latest.created_at.strftime("%Y-%m-%d %H:%M") if latest else "기록 없음",
        help="프로젝트 backups 폴더에서 확인한 가장 최근 백업입니다.",
        border=True,
    )

    action_columns = st_module.columns(2)
    if action_columns[0].button(
        "현재 DB 무결성 검사",
        key="database_backup_check_current",
        width="stretch",
    ):
        with st_module.spinner("현재 DuckDB를 읽기 전용으로 검사하고 있습니다..."):
            st_module.session_state[_CURRENT_REPORT_KEY] = check_database_integrity(
                DEFAULT_DB_PATH
            )

    if action_columns[1].button(
        "수동 백업 만들기",
        key="database_backup_create",
        type="primary",
        width="stretch",
    ):
        try:
            with st_module.spinner("CHECKPOINT와 복사본 무결성 검사를 진행하고 있습니다..."):
                result = create_database_backup(
                    DEFAULT_DB_PATH,
                    project_root=PROJECT_ROOT,
                )
            st_module.session_state[_CURRENT_REPORT_KEY] = result.integrity
            st_module.session_state[_FLASH_KEY] = (
                f"백업을 만들었습니다: {result.backup.path.name} "
                f"({_format_file_size(result.backup.size_bytes)})"
            )
            st_module.rerun()
        except DatabaseBackupError as exc:
            st_module.error(str(exc))
        except Exception as exc:
            st_module.error(f"백업을 완료하지 못했습니다: {exc}")

    current_report = st_module.session_state.get(_CURRENT_REPORT_KEY)
    if isinstance(current_report, DatabaseIntegrityReport):
        with st_module.expander("현재 DB 무결성 결과", expanded=not current_report.is_valid):
            _render_integrity_report(current_report, st_module=st_module)

    backups = list_database_backups(backup_dir=backup_dir)
    if not backups:
        st_module.info("아직 복구에 사용할 백업이 없습니다. 먼저 수동 백업을 만드세요.")
        st_module.caption(f"백업 저장 위치: {backup_dir}")
        return

    backup_frame = pd.DataFrame(
        [
            {
                "백업 파일": backup.path.name,
                "종류": "복구 전 자동" if backup.kind == "pre_restore" else "수동",
                "생성 시각": backup.created_at.strftime("%Y-%m-%d %H:%M:%S"),
                "크기": _format_file_size(backup.size_bytes),
            }
            for backup in backups
        ]
    )
    with st_module.expander("보관된 백업 목록", expanded=False):
        st_module.dataframe(backup_frame, hide_index=True, width="stretch")
        st_module.caption(f"백업 저장 위치: {backup_dir}")

    paths = [str(item.path) for item in backups]
    labels = {
        str(item.path): (
            f"{item.created_at:%Y-%m-%d %H:%M:%S} · "
            f"{'복구 전 자동' if item.kind == 'pre_restore' else '수동'} · "
            f"{_format_file_size(item.size_bytes)}"
        )
        for item in backups
    }
    selected_path = st_module.selectbox(
        "검사·복구할 백업",
        paths,
        format_func=lambda value: labels.get(str(value), Path(str(value)).name),
        key="database_backup_selected_path",
    )

    if st_module.button(
        "선택 백업 무결성 검사",
        key="database_backup_check_selected",
        width="stretch",
    ):
        with st_module.spinner("선택한 백업을 읽기 전용으로 검사하고 있습니다..."):
            st_module.session_state[_SELECTED_REPORT_KEY] = check_database_integrity(
                selected_path
            )

    selected_report = st_module.session_state.get(_SELECTED_REPORT_KEY)
    if isinstance(selected_report, DatabaseIntegrityReport):
        with st_module.expander(
            "선택 백업 무결성 결과",
            expanded=not selected_report.is_valid,
        ):
            _render_integrity_report(selected_report, st_module=st_module)

    with st_module.expander("선택한 백업으로 복구", expanded=False):
        st_module.warning(
            "복구하면 현재 DB가 선택한 백업 시점으로 바뀝니다. 복구 직전에 현재 DB를 "
            "자동 백업하며 실패하면 그 백업으로 원상 복구를 시도합니다. 앱이나 예약 수집이 "
            "DB를 사용 중이면 복구하지 않습니다."
        )
        confirmed = st_module.checkbox(
            "현재 DB가 선택한 백업 내용으로 교체되는 것을 확인했습니다.",
            key="database_backup_restore_confirmed",
        )
        confirmation_text = st_module.text_input(
            "확인을 위해 `복구`를 입력하세요.",
            key="database_backup_restore_text",
        )
        restore_enabled = bool(confirmed and confirmation_text.strip() == "복구")
        if st_module.button(
            "안전 복구 실행",
            key="database_backup_restore",
            type="primary",
            disabled=not restore_enabled,
            width="stretch",
        ):
            try:
                with st_module.spinner("현재 DB를 보존하고 선택한 백업을 복구하고 있습니다..."):
                    result = restore_database_backup(
                        selected_path,
                        DEFAULT_DB_PATH,
                        backup_dir=backup_dir,
                        project_root=PROJECT_ROOT,
                    )
                st_module.session_state.pop(_CURRENT_REPORT_KEY, None)
                st_module.session_state.pop(_SELECTED_REPORT_KEY, None)
                st_module.session_state[_FLASH_KEY] = (
                    f"복구를 완료했습니다: {result.restored_from.name} · "
                    f"복구 전 백업 {result.pre_restore_backup.name}"
                )
                st_module.rerun()
            except DatabaseBackupError as exc:
                st_module.error(str(exc))
            except Exception as exc:
                st_module.error(f"복구를 완료하지 못했습니다: {exc}")
