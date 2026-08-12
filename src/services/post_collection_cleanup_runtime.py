from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from functools import wraps
from pathlib import Path
import threading
from typing import Any, Mapping

from src.config import DEFAULT_DB_PATH
from src.database import connect_database, get_setting
from src.services.program_log_context import program_log_correlation


_RESULT_FIELDS = (
    "source_items_deleted",
    "cluster_links_deleted",
    "empty_clusters_deleted",
    "sync_runs_deleted",
    "collection_runs_deleted",
    "api_usage_rows_deleted",
    "total_rows_deleted",
)
_STATE = threading.local()


class DeferredCleanupResult:
    """수집 뒤 실제 정리 결과가 채워지는 호환 결과 객체입니다."""

    def __init__(self) -> None:
        self._result: Any | None = None
        self._executed = False

    def bind(self, result: Any | None) -> None:
        self._result = result
        self._executed = True

    @property
    def executed(self) -> bool:
        return bool(self._executed)

    def __getattr__(self, name: str) -> Any:
        if name in _RESULT_FIELDS:
            if self._result is None:
                return 0
            return getattr(self._result, name, 0)
        if name == "checkpoint_completed":
            return bool(getattr(self._result, name, False)) if self._result else False
        if name == "finished_at":
            return getattr(self._result, name, None) if self._result else None
        raise AttributeError(name)


@dataclass
class _PendingCleanup:
    database_path: Path
    call_kwargs: dict[str, Any]
    original: Any
    proxy: DeferredCleanupResult | None


def _pending_map() -> dict[str, _PendingCleanup]:
    value = getattr(_STATE, "pending", None)
    if not isinstance(value, dict):
        value = {}
        _STATE.pending = value
    return value


def _database_path_from_connection(con: Any) -> Path:
    try:
        rows = con.execute("PRAGMA database_list").fetchall()
        for row in rows:
            if len(row) >= 3 and str(row[2] or "").strip():
                return Path(str(row[2])).resolve()
    except Exception:
        pass
    return Path(DEFAULT_DB_PATH).resolve()


def _database_path_from_call(
    args: tuple[Any, ...],
    kwargs: Mapping[str, Any],
) -> Path:
    value = kwargs.get("db_path")
    if value is None and args and isinstance(args[0], (str, Path)):
        value = args[0]
    return Path(value or DEFAULT_DB_PATH).resolve()


def _cleanup_is_due(con: Any, kwargs: Mapping[str, Any]) -> tuple[bool, datetime]:
    current = kwargs.get("now")
    if not isinstance(current, datetime):
        current = datetime.now()
    enabled = bool(kwargs.get("enabled"))
    if not enabled:
        return False, current
    last_date = get_setting(con, "data_cleanup_last_date", "")
    return str(last_date or "") != current.strftime("%Y-%m-%d"), current


def _discard_pending(database_path: Path) -> None:
    _pending_map().pop(str(database_path.resolve()), None)


def _flush_pending(
    database_path: Path,
    *,
    con: Any | None = None,
    correlation_id: str = "",
) -> Any | None:
    key = str(database_path.resolve())
    pending = _pending_map().pop(key, None)
    if pending is None:
        return None

    with program_log_correlation(correlation_id):
        if con is not None:
            result = pending.original(con, **pending.call_kwargs)
        else:
            with connect_database(pending.database_path) as connection:
                result = pending.original(connection, **pending.call_kwargs)
    if pending.proxy is not None:
        pending.proxy.bind(result)
    return result


def install_post_collection_cleanup_contract() -> None:
    """자동 정리를 출처 저장 완료 뒤, 순위 준비 직전에 실행합니다."""
    from src.services import data_maintenance_service as maintenance
    from src.services import trend_discovery_service as discovery

    cleanup = getattr(maintenance, "run_automatic_cleanup_if_due", None)
    if callable(cleanup) and not getattr(cleanup, "_post_collection_cleanup", False):
        original_cleanup = cleanup

        @wraps(original_cleanup)
        def deferred_cleanup(con, *args, **kwargs):
            database_path = _database_path_from_connection(con)
            due, current = _cleanup_is_due(con, kwargs)
            call_kwargs = dict(kwargs)
            call_kwargs["now"] = current
            proxy = DeferredCleanupResult() if due else None
            _pending_map()[str(database_path)] = _PendingCleanup(
                database_path=database_path,
                call_kwargs=call_kwargs,
                original=original_cleanup,
                proxy=proxy,
            )
            return proxy

        deferred_cleanup._post_collection_cleanup = True  # type: ignore[attr-defined]
        maintenance.run_automatic_cleanup_if_due = deferred_cleanup

    prepare = getattr(discovery, "prepare_trend_ranking_rebuild", None)
    if callable(prepare) and not getattr(prepare, "_post_collection_cleanup", False):
        original_prepare = prepare

        @wraps(original_prepare)
        def prepare_after_cleanup(con, *args, **kwargs):
            database_path = _database_path_from_connection(con)
            _flush_pending(database_path, con=con)
            return original_prepare(con, *args, **kwargs)

        prepare_after_cleanup._post_collection_cleanup = True  # type: ignore[attr-defined]
        discovery.prepare_trend_ranking_rebuild = prepare_after_cleanup

    refresh = getattr(discovery, "refresh_trend_sources_short_connections", None)
    if callable(refresh) and not getattr(refresh, "_post_collection_cleanup", False):
        original_refresh = refresh

        @wraps(original_refresh)
        def refresh_with_cleanup_boundary(*args, **kwargs):
            database_path = _database_path_from_call(args, kwargs)
            correlation_id = str(kwargs.get("collection_run_id") or "")
            try:
                result = original_refresh(*args, **kwargs)
            except Exception:
                # 수집이 완료되지 않았으면 오래된 자료도 건드리지 않습니다.
                _discard_pending(database_path)
                raise
            ranking = result.get("ranking") if isinstance(result, Mapping) else None
            ranking_status = (
                str(ranking.get("status") or "") if isinstance(ranking, Mapping) else ""
            )
            if ranking_status == "skipped_source_failure":
                # 출처 오류가 정상 반환으로 격리된 경우에도 기존 보존 자료를 유지합니다.
                _discard_pending(database_path)
                return result
            # 정상 구현에서는 순위 준비 직전에 이미 실행됩니다. 순위 생략 경로가
            # 생겨도 수집 성공 뒤 정리가 누락되지 않도록 마지막 안전 경계를 둡니다.
            _flush_pending(
                database_path,
                correlation_id=correlation_id,
            )
            return result

        refresh_with_cleanup_boundary._post_collection_cleanup = True  # type: ignore[attr-defined]
        discovery.refresh_trend_sources_short_connections = refresh_with_cleanup_boundary
