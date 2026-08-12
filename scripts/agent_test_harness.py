from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path
from time import perf_counter
from typing import Iterable, Sequence

PROJECT_ROOT = Path(__file__).resolve().parents[1]

SCENARIO_TESTS: dict[str, tuple[str, ...]] = {
    "clustering": (
        "tests/test_trend_cluster_sequential_execution.py",
        "tests/test_trend_cluster_sparse_orchestrator.py",
        "tests/test_trend_cluster_sparse_protocol.py",
        "tests/test_trend_cluster_token_runtime.py",
        "tests/test_trend_cluster_runtime_contract.py",
        "tests/test_trend_cluster_job_runtime_contract.py",
        "tests/test_trend_cluster_existing_index.py",
        "tests/test_clustering_job_status_ui.py",
        "tests/test_trend_cluster_live_progress.py",
        "tests/test_trend_cluster_progress_detail_runtime.py",
        "tests/test_trend_clustering_stale_display_runtime.py",
        "tests/test_trend_clustering_quality_sample.py",
        "tests/test_trend_clustering_deterministic_baseline_service.py",
        "tests/test_trend_clustering_quality_runtime.py",
        "tests/test_trend_clustering_sparse_response_docs.py",
        "tests/test_trend_clustering_deterministic_baseline_docs.py",
    ),
    "scheduler": (
        "tests/test_scheduler_service.py",
        "tests/test_scheduler_quota_analysis_service.py",
    ),
    "latest-data": (
        "tests/test_short_database_connections.py",
        "tests/test_short_connection_structure.py",
        "tests/test_refresh_history_integration.py",
        "tests/test_post_collection_cleanup_runtime.py",
        "tests/test_dashboard_background_refresh_ui.py",
    ),
    "cleanup": (
        "tests/test_data_maintenance_service.py",
    ),
    "topic-angles": (
        "tests/test_topic_angle_ai_service.py",
        "tests/test_topic_angle_partial_recovery_runtime.py",
        "tests/test_background_refresh_topic_angles.py",
        "tests/test_topic_angle_timeout_and_queue_collapse.py",
        "tests/test_post_clustering_topic_angle_service.py",
        "tests/test_post_clustering_topic_angle_launcher.py",
    ),
    "diagnostics": (
        "tests/test_operation_diagnostic_cli.py",
        "tests/test_operation_diagnostic_failure_samples.py",
        "tests/test_operation_diagnostic_report.py",
        "tests/test_operation_diagnostic_snapshot_reuse.py",
        "tests/test_operation_diagnostic_throttle_action.py",
        "tests/test_source_analysis_limit_diagnostic.py",
        "tests/test_source_analysis_limit_diagnostic_cli.py",
        "tests/test_p2_diagnostic_bundle.py",
    ),
    "operations": (
        "tests/test_program_log_service.py",
        "tests/test_program_log_runtime.py",
        "tests/test_program_log_correlation_runtime.py",
        "tests/test_program_log_run_lifecycle.py",
        "tests/test_source_collection_log_runtime.py",
        "tests/test_program_button_log_ui.py",
        "tests/test_operational_logs_ui.py",
        "tests/test_log_display_format_ui.py",
        "tests/test_trend_auto_model_ui.py",
        "tests/test_app_supervisor_contract.py",
        "tests/test_web_update_confirmation_ui.py",
        "tests/test_web_update_launch_runtime.py",
    ),
    "workflow": (
        "tests/test_browser_workflow_regression_contract.py",
        "tests/test_workflow_navigation_state.py",
        "tests/test_content_workflow_scenarios.py",
        "tests/test_chatgpt_request_workflow.py",
    ),
    "harness": (
        "tests/test_agent_test_harness.py",
        "tests/test_agent_harness_launcher_contract.py",
        "tests/test_apply_update_and_restart_contract.py",
        "tests/test_apply_update_launcher_stability.py",
        "tests/test_apply_update_tool.py",
        "tests/test_apply_update_work_branch_mode.py",
        "tests/test_repository_text_hygiene.py",
    ),
}
SCENARIO_ORDER = (
    "clustering",
    "scheduler",
    "latest-data",
    "cleanup",
    "topic-angles",
    "diagnostics",
    "operations",
    "workflow",
    "harness",
)
SCENARIO_ALIASES = {
    "refresh": "latest-data",
    "collection": "latest-data",
    "maintenance": "cleanup",
    "angles": "topic-angles",
    "topic-angle": "topic-angles",
    "logs": "operations",
    "logging": "operations",
    "content-workflow": "workflow",
    "workflow-ui": "workflow",
    "dev-harness": "harness",
}


@dataclass(frozen=True)
class ScenarioResult:
    scenario: str
    status: str
    exit_code: int
    duration_seconds: float
    test_files: tuple[str, ...]


def normalize_scenarios(values: Iterable[str]) -> tuple[str, ...]:
    requested = [str(value or "").strip().casefold() for value in values]
    if not requested or "all" in requested:
        return SCENARIO_ORDER
    normalized: list[str] = []
    for value in requested:
        scenario = SCENARIO_ALIASES.get(value, value)
        if scenario not in SCENARIO_TESTS:
            allowed = ", ".join(("all", *SCENARIO_ORDER))
            raise ValueError(f"알 수 없는 시나리오입니다: {value}. 허용값: {allowed}")
        if scenario not in normalized:
            normalized.append(scenario)
    return tuple(normalized)


def _safe_environment(temporary_root: Path) -> dict[str, str]:
    environment = dict(os.environ)
    environment.update(
        {
            "PYTHONUTF8": "1",
            "PYTHONIOENCODING": "utf-8",
            "PYTHONPYCACHEPREFIX": str(temporary_root / "pycache"),
            "CONTENT_TREND_AGENT_HARNESS": "1",
            # 테스트 하네스는 실제 API·인증 정보를 사용하지 않습니다.
            "GEMINI_API_KEY": "",
            "NAVER_CLIENT_ID": "",
            "NAVER_CLIENT_SECRET": "",
            "KAKAO_REST_API_KEY": "",
        }
    )
    return environment


def build_pytest_command(
    scenario: str,
    *,
    python_executable: str | Path = sys.executable,
    basetemp: str | Path,
) -> list[str]:
    if scenario not in SCENARIO_TESTS:
        raise ValueError(f"알 수 없는 시나리오입니다: {scenario}")
    return [
        str(python_executable),
        "-m",
        "pytest",
        "-q",
        "-p",
        "no:cacheprovider",
        f"--basetemp={Path(basetemp)}",
        *SCENARIO_TESTS[scenario],
    ]


def run_scenario(
    scenario: str,
    *,
    python_executable: str | Path = sys.executable,
    command_runner=subprocess.run,
) -> ScenarioResult:
    started = perf_counter()
    with tempfile.TemporaryDirectory(prefix=f"content-trend-agent-{scenario}-") as temp:
        temporary_root = Path(temp)
        command = build_pytest_command(
            scenario,
            python_executable=python_executable,
            basetemp=temporary_root / "pytest",
        )
        completed = command_runner(
            command,
            cwd=str(PROJECT_ROOT),
            env=_safe_environment(temporary_root),
            stdin=subprocess.DEVNULL,
            check=False,
        )
    exit_code = int(completed.returncode)
    return ScenarioResult(
        scenario=scenario,
        status="passed" if exit_code == 0 else "failed",
        exit_code=exit_code,
        duration_seconds=round(perf_counter() - started, 3),
        test_files=SCENARIO_TESTS[scenario],
    )


def run_harness(
    scenarios: Sequence[str],
    *,
    python_executable: str | Path = sys.executable,
    command_runner=subprocess.run,
) -> tuple[ScenarioResult, ...]:
    return tuple(
        run_scenario(
            scenario,
            python_executable=python_executable,
            command_runner=command_runner,
        )
        for scenario in normalize_scenarios(scenarios)
    )


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "실제 DB·외부 API·Windows 작업 스케줄러를 변경하지 않고 운영 흐름을 "
            "임시 pytest 환경에서 검증합니다."
        )
    )
    parser.add_argument(
        "scenario",
        nargs="*",
        default=["all"],
        help=(
            "all, clustering, scheduler, latest-data, cleanup, topic-angles, "
            "diagnostics, operations, workflow, harness 중 하나 이상"
        ),
    )
    parser.add_argument(
        "--list",
        action="store_true",
        help="지원 시나리오와 테스트 파일만 표시합니다.",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    if args.list:
        print(
            json.dumps(
                {name: list(SCENARIO_TESTS[name]) for name in SCENARIO_ORDER},
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0
    try:
        scenarios = normalize_scenarios(args.scenario)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2

    results = run_harness(scenarios)
    summary = {
        "status": "passed" if all(row.exit_code == 0 for row in results) else "failed",
        "safe_mode": {
            "real_database": False,
            "external_api": False,
            "windows_scheduler_write": False,
        },
        "results": [asdict(row) for row in results],
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if summary["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
