from __future__ import annotations

from functools import wraps
from typing import Any

from src.services.program_log_context import (
    begin_program_log_correlation,
    end_program_log_correlation,
)


def install_program_log_run_lifecycle() -> None:
    """수집 실행 시작부터 종료까지 같은 운영 로그 실행 ID를 유지합니다."""
    from src.services import collection_history_service as module

    start = getattr(module, "start_collection_run", None)
    if callable(start) and not getattr(start, "_program_log_run_lifecycle", False):
        original_start = start

        @wraps(original_start)
        def start_with_context(*args: Any, **kwargs: Any):
            end_program_log_correlation()
            run_id = original_start(*args, **kwargs)
            begin_program_log_correlation(run_id)
            return run_id

        start_with_context._program_log_run_lifecycle = True  # type: ignore[attr-defined]
        module.start_collection_run = start_with_context

    finish = getattr(module, "finish_collection_run", None)
    if callable(finish) and not getattr(finish, "_program_log_run_lifecycle", False):
        original_finish = finish

        @wraps(original_finish)
        def finish_with_context(con: Any, run_id: Any, *args: Any, **kwargs: Any):
            try:
                return original_finish(con, run_id, *args, **kwargs)
            finally:
                end_program_log_correlation(run_id)

        finish_with_context._program_log_run_lifecycle = True  # type: ignore[attr-defined]
        module.finish_collection_run = finish_with_context
