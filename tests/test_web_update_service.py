from __future__ import annotations

from pathlib import Path

import pytest

from src.services.web_update_service import (
    CommandOutput,
    WorkBranchCandidate,
    check_update_readiness,
    discover_work_branches,
    is_local_request,
    launch_update_and_restart,
    read_update_status,
    runtime_update_blockers,
    validate_work_branch_name,
)


def _candidate(**overrides) -> WorkBranchCandidate:
    values = {
        "branch_name": "work/0.10.106",
        "remote_ref": "origin/work/0.10.106",
        "commit_sha": "a" * 40,
        "committed_at": "2026-08-06T08:30:00+09:00",
        "ahead": 3,
        "behind": 0,
        "changed_files": 5,
        "eligible": True,
        "reason": "",
    }
    values.update(overrides)
    return WorkBranchCandidate(**values)


def test_work_branch_validation_accepts_version_and_suffix_only() -> None:
    assert validate_work_branch_name("origin/work/0.10.106") == "work/0.10.106"
    assert (
        validate_work_branch_name("work/0.10.107-app-version")
        == "work/0.10.107-app-version"
    )
    with pytest.raises(ValueError):
        validate_work_branch_name("main")
    with pytest.raises(ValueError):
        validate_work_branch_name("work/latest")
    with pytest.raises(ValueError):
        validate_work_branch_name("work/0.10.106;whoami")


def test_discovery_selects_highest_semantic_work_version_first(tmp_path: Path) -> None:
    (tmp_path / ".git").mkdir()

    def runner(root: Path, arguments, timeout: int) -> CommandOutput:
        assert root == tmp_path
        args = tuple(arguments)
        if args[:4] == ("git", "remote", "get-url", "origin"):
            return CommandOutput(
                "https://github.com/honggee486-dot/content-trend-tracker.git\n",
                "",
                0,
            )
        if args[:2] == ("git", "fetch"):
            return CommandOutput("", "", 0)
        if args[:2] == ("git", "for-each-ref"):
            return CommandOutput(
                "origin/work/0.10.106\t" + "b" * 40 + "\t2026-08-06T08:00:00+09:00\n"
                "origin/work/0.10.107-preview\t" + "c" * 40 + "\t2026-08-06T07:00:00+09:00\n"
                "origin/work/0.10.105\t" + "a" * 40 + "\t2026-08-06T09:00:00+09:00\n",
                "",
                0,
            )
        if args[:4] == ("git", "rev-list", "--left-right", "--count"):
            branch = args[-1]
            return CommandOutput("1 2\n" if "0.10.105" in branch else "0 4\n", "", 0)
        if args[:2] == ("git", "diff"):
            return CommandOutput("a.py\nb.py\n", "", 0)
        raise AssertionError(args)

    candidates = discover_work_branches(tmp_path, runner=runner)

    assert [row.branch_name for row in candidates] == [
        "work/0.10.107-preview",
        "work/0.10.106",
        "work/0.10.105",
    ]
    assert candidates[0].eligible is True
    assert candidates[-1].eligible is False
    assert "뒤처져" in candidates[-1].reason


def test_local_request_rejects_lan_or_forwarded_remote_access() -> None:
    assert is_local_request({"Host": "localhost:8501"}) is True
    assert is_local_request({"Host": "127.0.0.1:8501"}) is True
    assert is_local_request({"Host": "[::1]:8501"}) is True
    assert is_local_request({"Host": "192.168.0.10:8501"}) is False
    assert is_local_request(
        {"Host": "localhost:8501", "X-Forwarded-For": "192.168.0.20"}
    ) is False
    assert is_local_request({}) is False


def test_runtime_blockers_detect_collection_and_clustering_locks(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "localappdata"))
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    (data_dir / "trend_refresh.lock").write_text("locked", encoding="utf-8")
    (data_dir / "trend_clustering.lock").write_text("locked", encoding="utf-8")

    blockers = runtime_update_blockers(
        tmp_path,
        data_dir / "missing.duckdb",
    )

    assert any("수집 작업" in blocker for blocker in blockers)
    assert any("2단계 군집 작업" in blocker for blocker in blockers)


def test_readiness_requires_clean_tree_and_not_already_applied(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "localappdata"))
    (tmp_path / ".git").mkdir()
    (tmp_path / "scripts").mkdir()
    (tmp_path / "apply_update.bat").write_text("@echo off\n", encoding="utf-8")
    (tmp_path / "scripts" / "apply_update_and_restart.ps1").write_text(
        "exit 0\n",
        encoding="utf-8",
    )
    db_path = tmp_path / "data" / "missing.duckdb"

    def clean_runner(root: Path, arguments, timeout: int) -> CommandOutput:
        args = tuple(arguments)
        if args[1:3] == ("status", "--porcelain=v1"):
            return CommandOutput("", "", 0)
        if args[1:4] == ("symbolic-ref", "--quiet", "--short"):
            return CommandOutput("work/0.10.105\n", "", 0)
        if args[1:3] == ("rev-parse", "HEAD^{commit}"):
            return CommandOutput("d" * 40 + "\n", "", 0)
        raise AssertionError(args)

    readiness = check_update_readiness(
        _candidate(),
        tmp_path,
        db_path,
        runner=clean_runner,
    )
    assert readiness.ready is True

    def dirty_runner(root: Path, arguments, timeout: int) -> CommandOutput:
        args = tuple(arguments)
        if args[1:3] == ("status", "--porcelain=v1"):
            return CommandOutput("?? local.txt\n", "", 0)
        return clean_runner(root, arguments, timeout)

    dirty = check_update_readiness(
        _candidate(),
        tmp_path,
        db_path,
        runner=dirty_runner,
    )
    assert dirty.ready is False
    assert "미커밋" in " ".join(dirty.blockers)

    applied = check_update_readiness(
        _candidate(branch_name="work/0.10.105", commit_sha="d" * 40),
        tmp_path,
        db_path,
        runner=clean_runner,
    )
    assert applied.ready is False
    assert applied.already_applied is True


def test_launch_uses_argument_list_without_shell(tmp_path: Path, monkeypatch) -> None:
    (tmp_path / "scripts").mkdir()
    (tmp_path / "scripts" / "apply_update_and_restart.ps1").write_text(
        "exit 0\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "localappdata"))
    captured = {}

    class Process:
        pid = 54321

    def fake_popen(command, **kwargs):
        captured["command"] = command
        captured["kwargs"] = kwargs
        return Process()

    pid = launch_update_and_restart(
        _candidate(),
        tmp_path,
        parent_pid=12345,
        popen_factory=fake_popen,
        powershell_executable="pwsh.exe",
    )

    assert pid == 54321
    assert isinstance(captured["command"], list)
    assert captured["command"][0] == "pwsh.exe"
    assert "work/0.10.106" in captured["command"]
    assert "a" * 40 in captured["command"]
    assert captured["kwargs"]["shell"] is False
    assert captured["kwargs"]["stdin"] is not None


def test_launch_failure_is_recorded_in_external_status(
    tmp_path: Path,
    monkeypatch,
) -> None:
    (tmp_path / "scripts").mkdir()
    (tmp_path / "scripts" / "apply_update_and_restart.ps1").write_text(
        "exit 0\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "localappdata"))

    def failed_popen(command, **kwargs):
        raise OSError("process start failed")

    with pytest.raises(OSError):
        launch_update_and_restart(
            _candidate(),
            tmp_path,
            parent_pid=12345,
            popen_factory=failed_popen,
            powershell_executable="pwsh.exe",
        )

    status = read_update_status()
    assert status["status"] == "failed"
    assert status["stage"] == "launch_failed"
    assert "process start failed" in status["message"]
