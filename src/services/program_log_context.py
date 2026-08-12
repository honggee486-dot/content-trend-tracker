from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from typing import Iterator


_CURRENT_CORRELATION_ID: ContextVar[str] = ContextVar(
    "content_trend_program_log_correlation_id",
    default="",
)


def current_program_log_correlation_id() -> str:
    return str(_CURRENT_CORRELATION_ID.get() or "").strip()


def begin_program_log_correlation(correlation_id: object) -> None:
    _CURRENT_CORRELATION_ID.set(str(correlation_id or "").strip())


def end_program_log_correlation(correlation_id: object = "") -> None:
    expected = str(correlation_id or "").strip()
    current = current_program_log_correlation_id()
    if not expected or not current or current == expected:
        _CURRENT_CORRELATION_ID.set("")


@contextmanager
def program_log_correlation(correlation_id: object) -> Iterator[None]:
    value = str(correlation_id or "").strip()
    token = _CURRENT_CORRELATION_ID.set(value)
    try:
        yield
    finally:
        _CURRENT_CORRELATION_ID.reset(token)
