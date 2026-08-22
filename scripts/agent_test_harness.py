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
        "tests/test_trend_source_review_runtime.py",
        "tests/test_trend_source_review_visibility.py",
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
        "tests/test_scheduler_wake_status_ui.py",
        "tests/test_scheduler_quota_analysis_service.py",
    ),
    "latest-data": (
        "tests/test_short_database_connections.py",
        "tests/test_short_connection_structure.py",
        "tests/test_refresh_history_integration.py",
        "tests/test_post_collection_cleanup_runtime.py",
        "tests/test_dashboard_background_refresh_ui.py",
        "tests/test_dashboard_refresh_progress_service.py",
        "tests/test_dashboard_operation_status_ui.py",
        "tests/test_dashboard_refresh_stale_recovery.py",
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
        "tests/test_trend_source_visibility_diagnostic.py",
        "tests/test_trend_source_visibility_diagnostic_cli.py",
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
        "tests/test_content_workflow_writing_mode_recommendation.py",
        "tests/test_content_workflow_automatic_writing_models.py",
        "tests/test_content_quality_review_service.py",
        "tests/test_content_pack_capture_task_runtime.py",
        "tests/test_content_pack_seo_image_contract.py",
        "tests/test_content_workflow_representative_image.py",
        "tests/test_content_workflow_public_capture_executor.py",
        "tests/test_content_workflow_ui_runtime.py",
        "tests/test_chatgpt_request_workflow.py",
        "tests/test_adsense_candidate_service.py",
        "tests/test_trend_blog_recommendation_service.py",
        "tests/test_trend_candidate_blog_recommendation_ui.py",
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
            # 테스트 하네스는 실제 외부 호출·브라우저 스모크·인증 정보를 사용하지 않습니다.
            "CONTENT_TREND_BROWSER_SMOKE": "0",
            "GEMINI_API_KEY": "",
            "NAVER_CLIENT_ID": "",
            "NAVER_CLIENT_SECRET": "",
            "KAKAO_REST_API_KEY": "",
            "OPENROUTER_API_KEY": "",
            "GROQ_API_KEY": "",
            "OPENCODE_API_KEY": "",
            "CLOUDFLARE_API_TOKEN": "",
            "CLOUDFLARE_ACCOUNT_ID": "",
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


@dataclass(frozen=True)
class RoutingDecision:
    mode: str
    scenarios: tuple[str, ...]
    test_files: tuple[str, ...]
    reason: str


def classify_file(path_str: str) -> str | None:
    p = path_str.replace("\\", "/").strip().lstrip("/")
    if not p:
        return None
    p_lower = p.casefold()

    # 핵심 기반 변경 시 전체 fallback
    if p_lower in (
        "src/database.py",
        "app.py",
        "src/config.py",
        "src/services/topic_service.py",
        "requirements.txt",
    ):
        return "CORE_FALLBACK"

    # 하네스 및 적용 도구
    if (
        p_lower in ("agents.md", "apply_update.bat", "run_agent_harness.bat", "run_tests.bat")
        or p_lower.startswith("scripts/apply_update")
        or p_lower in ("scripts/check_harness.ps1", "scripts/agent_test_harness.py", "scripts/check_text_hygiene.py")
        or p_lower.startswith("tests/test_agent_test_harness")
        or p_lower.startswith("tests/test_agent_harness_")
        or p_lower.startswith("tests/test_apply_update_")
        or p_lower == "tests/test_repository_text_hygiene.py"
    ):
        return "harness"

    # 문서/위생 규칙
    if (
        p_lower.startswith("docs/")
        or p_lower.endswith(".md")
        or p_lower.endswith(".txt")
        or p_lower == ".gitignore"
        or p_lower == ".env.example"
    ):
        if not (p_lower.startswith("src/") or p_lower.startswith("scripts/") or p_lower.startswith("tests/")):
            return "DOC_ONLY"

    # 군집
    if (
        p_lower.startswith("src/services/trend_cluster_")
        or p_lower.startswith("src/services/trend_clustering_")
        or p_lower.startswith("src/services/trend_source_review_")
        or p_lower == "scripts/process_cluster_backlog.py"
        or p_lower.startswith("tests/test_trend_cluster_")
        or p_lower.startswith("tests/test_clustering_")
        or p_lower.startswith("tests/test_trend_source_review_")
        or p_lower.startswith("tests/test_trend_clustering_")
        or p_lower.startswith("tests/test_two_stage_clustering_")
    ):
        return "clustering"

    # 예약 작업·쿼터
    if (
        p_lower.startswith("src/services/scheduler_")
        or p_lower.startswith("tests/test_scheduler_")
    ):
        return "scheduler"

    # 최신 데이터 수집
    if (
        p_lower.startswith("src/services/post_collection_cleanup_")
        or p_lower.startswith("src/services/collection_history_")
        or p_lower.startswith("src/services/dashboard_refresh_progress_")
        or p_lower.startswith("src/adapters/")
        or p_lower in ("scripts/refresh_trends.py", "scripts/refresh_trends_dashboard.py")
        or p_lower in ("src/dashboard_background_refresh_ui.py", "src/dashboard_operation_status_ui.py")
        or p_lower.startswith("tests/test_short_database_")
        or p_lower.startswith("tests/test_short_connection_")
        or p_lower.startswith("tests/test_refresh_history_")
        or p_lower.startswith("tests/test_post_collection_")
        or p_lower.startswith("tests/test_dashboard_background_refresh_")
        or p_lower.startswith("tests/test_dashboard_refresh_progress_")
        or p_lower.startswith("tests/test_dashboard_operation_status_")
        or p_lower.startswith("tests/test_dashboard_refresh_stale_")
    ):
        return "latest-data"

    # 보존·정리 Policy
    if (
        p_lower == "src/services/data_maintenance_service.py"
        or p_lower == "tests/test_data_maintenance_service.py"
    ):
        return "cleanup"

    # Gemini 주제 방향
    if (
        p_lower.startswith("src/services/topic_angle_")
        or p_lower.startswith("src/services/post_clustering_topic_angle_")
        or p_lower.startswith("src/services/gemini_")
        or p_lower.startswith("tests/test_topic_angle_")
        or p_lower == "tests/test_background_refresh_topic_angles.py"
        or p_lower.startswith("tests/test_post_clustering_topic_angle_")
    ):
        return "topic-angles"

    # 읽기 전용 운영 진단
    if (
        p_lower in (
            "scripts/report_operation_diagnostics.py",
            "scripts/report_source_analysis_limits.py",
            "scripts/report_trend_source_visibility.py",
            "scripts/report_p2_diagnostics.py",
            "scripts/report_blogger_preflight.py",
        )
        or "diagnostic" in p_lower
        or p_lower == "tests/test_p2_diagnostic_bundle.py"
        or p_lower == "tests/test_blogger_preflight_cli.py"
    ):
        return "diagnostics"

    # 앱 supervisor·운영 로그
    if (
        p_lower.startswith("src/services/program_log_")
        or p_lower.startswith("src/services/source_collection_log_")
        or p_lower.startswith("src/services/app_supervisor_")
        or p_lower.startswith("src/services/web_update_")
        or p_lower.startswith("src/services/program_button_")
        or p_lower.startswith("src/services/operational_logs_")
        or p_lower in ("scripts/app_supervisor.ps1", "stop_app.bat", "run_app.bat")
        or p_lower.startswith("tests/test_program_log_")
        or p_lower.startswith("tests/test_source_collection_log_")
        or p_lower.startswith("tests/test_program_button_log_")
        or p_lower.startswith("tests/test_operational_logs_")
        or p_lower.startswith("tests/test_log_display_format_")
        or p_lower.startswith("tests/test_trend_auto_model_")
        or p_lower.startswith("tests/test_app_supervisor_")
        or p_lower.startswith("tests/test_web_update_")
    ):
        return "operations"

    # 제작 흐름·발행 보조
    if (
        p_lower.startswith("src/services/workflow_navigation_")
        or p_lower.startswith("src/services/content_pack_")
        or p_lower.startswith("src/services/content_quality_")
        or p_lower.startswith("src/services/draft_")
        or p_lower.startswith("src/services/fact_check_")
        or p_lower.startswith("src/services/publish_")
        or p_lower.startswith("src/services/adsense_candidate_")
        or p_lower.startswith("src/services/trend_blog_recommendation_")
        or p_lower == "src/trend_candidate_blog_recommendation_ui.py"
        or p_lower == "src/services/ai_result_parser.py"
        or p_lower.startswith("tests/test_browser_workflow_")
        or p_lower.startswith("tests/test_workflow_navigation_")
        or p_lower.startswith("tests/test_content_workflow_")
        or p_lower.startswith("tests/test_content_pack_")
        or p_lower.startswith("tests/test_content_quality_")
        or p_lower.startswith("tests/test_adsense_candidate_")
        or p_lower.startswith("tests/test_trend_blog_recommendation_")
        or p_lower.startswith("tests/test_trend_candidate_blog_recommendation_")
        or p_lower == "tests/test_chatgpt_request_workflow.py"
    ):
        return "workflow"

    return None


def resolve_routing(changed_files: Sequence[str]) -> RoutingDecision:
    files = [str(f or "").strip() for f in changed_files if str(f or "").strip()]
    if not files:
        return RoutingDecision(
            mode="fallback_all",
            scenarios=SCENARIO_ORDER,
            test_files=tuple(dict.fromkeys(f for s in SCENARIO_ORDER for f in SCENARIO_TESTS[s])),
            reason="변경 파일이 지정되지 않았습니다.",
        )

    categories: set[str] = set()
    unclassified: list[str] = []
    has_core = False

    for f in files:
        cat = classify_file(f)
        if cat == "CORE_FALLBACK":
            has_core = True
            break
        elif cat is None:
            unclassified.append(f)
        else:
            categories.add(cat)

    if has_core:
        return RoutingDecision(
            mode="fallback_all",
            scenarios=SCENARIO_ORDER,
            test_files=tuple(dict.fromkeys(f for s in SCENARIO_ORDER for f in SCENARIO_TESTS[s])),
            reason="핵심 기반 파일(DB/App/Config/Topic/Requirements) 변경으로 인해 전체 pytest로 진행합니다.",
        )

    if unclassified:
        return RoutingDecision(
            mode="fallback_all",
            scenarios=SCENARIO_ORDER,
            test_files=tuple(dict.fromkeys(f for s in SCENARIO_ORDER for f in SCENARIO_TESTS[s])),
            reason=f"분류되지 않은 파일이 포함되어 전체 pytest로 진행합니다: {', '.join(unclassified[:3])}",
        )

    if categories == {"DOC_ONLY"}:
        return RoutingDecision(
            mode="doc_only",
            scenarios=(),
            test_files=(
                "tests/test_repository_text_hygiene.py",
                "tests/test_agent_harness_launcher_contract.py",
            ),
            reason="문서 및 위생 규칙 변경으로 인해 위생 검사와 문서 계약 테스트를 실행합니다.",
        )

    # DOC_ONLY 제거 후 시나리오 조합 평가
    domain_scenarios = tuple(sorted(categories - {"DOC_ONLY"}))
    if not domain_scenarios:
        return RoutingDecision(
            mode="doc_only",
            scenarios=(),
            test_files=(
                "tests/test_repository_text_hygiene.py",
                "tests/test_agent_harness_launcher_contract.py",
            ),
            reason="문서 및 위생 규칙 변경으로 인해 위생 검사와 문서 계약 테스트를 실행합니다.",
        )

    if len(domain_scenarios) <= 2:
        matched_tests: list[str] = []
        for s in domain_scenarios:
            for tf in SCENARIO_TESTS[s]:
                if tf not in matched_tests:
                    matched_tests.append(tf)
        return RoutingDecision(
            mode="selective",
            scenarios=domain_scenarios,
            test_files=tuple(matched_tests),
            reason=f"변경 범위가 시나리오({', '.join(domain_scenarios)})에 해당합니다.",
        )

    return RoutingDecision(
        mode="fallback_all",
        scenarios=SCENARIO_ORDER,
        test_files=tuple(dict.fromkeys(f for s in SCENARIO_ORDER for f in SCENARIO_TESTS[s])),
        reason=f"변경 파일이 3개 이상의 영역({', '.join(domain_scenarios)})에 걸쳐 있어 전체 pytest로 진행합니다.",
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
    parser.add_argument(
        "--resolve-targets",
        nargs="*",
        help="변경 파일 목록을 입력받아 검증 라우팅(doc_only / selective / fallback_all)을 결정합니다.",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    if args.resolve_targets is not None:
        decision = resolve_routing(args.resolve_targets)
        # apply_update가 PowerShell 7/5.1에서 캡처하므로 라우팅 JSON은 ASCII-safe로 출력합니다.
        print(json.dumps(asdict(decision), ensure_ascii=True, indent=2))
        return 0
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
