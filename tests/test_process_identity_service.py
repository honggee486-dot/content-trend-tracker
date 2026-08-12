from __future__ import annotations

from src.services.process_identity_service import process_identity_matches


def test_dead_process_never_matches() -> None:
    assert process_identity_matches(
        123,
        "windows-filetime:1",
        is_process_alive=lambda _pid: False,
        identity_reader=lambda _pid: "windows-filetime:1",
    ) is False


def test_legacy_lock_without_identity_uses_live_pid_compatibility() -> None:
    assert process_identity_matches(
        123,
        "",
        is_process_alive=lambda _pid: True,
        identity_reader=lambda _pid: "windows-filetime:2",
    ) is True


def test_matching_identity_confirms_same_process() -> None:
    assert process_identity_matches(
        123,
        "windows-filetime:2",
        is_process_alive=lambda _pid: True,
        identity_reader=lambda _pid: "windows-filetime:2",
    ) is True


def test_mismatched_identity_rejects_reused_pid() -> None:
    assert process_identity_matches(
        123,
        "windows-filetime:1",
        is_process_alive=lambda _pid: True,
        identity_reader=lambda _pid: "windows-filetime:2",
    ) is False


def test_unavailable_identity_keeps_live_process_conservatively() -> None:
    assert process_identity_matches(
        123,
        "windows-filetime:1",
        is_process_alive=lambda _pid: True,
        identity_reader=lambda _pid: "",
    ) is True


def test_identity_reader_failure_keeps_live_process_conservatively() -> None:
    def fail(_pid: int) -> str:
        raise PermissionError("process query denied")

    assert process_identity_matches(
        123,
        "windows-filetime:1",
        is_process_alive=lambda _pid: True,
        identity_reader=fail,
    ) is True
