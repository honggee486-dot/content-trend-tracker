from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts.agent_test_harness import (
    PROJECT_ROOT,
    SCENARIO_ORDER,
    SCENARIO_TESTS,
    build_pytest_command,
    main,
    normalize_scenarios,
    resolve_routing,
    run_harness,
)


def test_requested_operating_scenarios_have_dedicated_safe_test_sets() -> None:
    assert SCENARIO_ORDER == (
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
    assert "tests/test_scheduler_service.py" in SCENARIO_TESTS["scheduler"]
    assert "tests/test_short_database_connections.py" in SCENARIO_TESTS["latest-data"]
    assert (
        "tests/test_post_collection_cleanup_runtime.py"
        in SCENARIO_TESTS["latest-data"]
    )
    assert "tests/test_data_maintenance_service.py" in SCENARIO_TESTS["cleanup"]
    assert "tests/test_topic_angle_ai_service.py" in SCENARIO_TESTS["topic-angles"]
    assert SCENARIO_TESTS["diagnostics"] == (
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
    )
    assert "tests/test_program_log_service.py" in SCENARIO_TESTS["operations"]
    assert "tests/test_operational_logs_ui.py" in SCENARIO_TESTS["operations"]
    assert "tests/test_app_supervisor_contract.py" in SCENARIO_TESTS["operations"]
    assert (
        "tests/test_program_log_correlation_runtime.py"
        in SCENARIO_TESTS["operations"]
    )
    assert (
        "tests/test_web_update_confirmation_ui.py"
        in SCENARIO_TESTS["operations"]
    )
    assert (
        "tests/test_trend_cluster_sequential_execution.py"
        in SCENARIO_TESTS["clustering"]
    )
    assert "tests/test_trend_cluster_sparse_protocol.py" in SCENARIO_TESTS["clustering"]
    assert "tests/test_trend_cluster_sparse_orchestrator.py" in SCENARIO_TESTS["clustering"]
    assert (
        "tests/test_trend_clustering_sparse_response_docs.py"
        in SCENARIO_TESTS["clustering"]
    )
    assert (
        "tests/test_trend_clustering_deterministic_baseline_service.py"
        in SCENARIO_TESTS["clustering"]
    )
    assert "tests/test_trend_clustering_quality_runtime.py" in SCENARIO_TESTS["clustering"]
    assert (
        "tests/test_trend_clustering_deterministic_baseline_docs.py"
        in SCENARIO_TESTS["clustering"]
    )
    assert "tests/test_trend_cluster_existing_index.py" in SCENARIO_TESTS["clustering"]
    assert "tests/test_clustering_job_status_ui.py" in SCENARIO_TESTS["clustering"]
    assert (
        "tests/test_trend_clustering_stale_display_runtime.py"
        in SCENARIO_TESTS["clustering"]
    )
    assert (
        "tests/test_trend_clustering_quality_sample.py"
        in SCENARIO_TESTS["clustering"]
    )
    assert (
        "tests/test_browser_workflow_regression_contract.py"
        in SCENARIO_TESTS["workflow"]
    )
    assert "tests/test_workflow_navigation_state.py" in SCENARIO_TESTS["workflow"]
    assert "tests/test_adsense_candidate_service.py" in SCENARIO_TESTS["workflow"]
    assert "tests/test_trend_blog_recommendation_service.py" in SCENARIO_TESTS["workflow"]
    assert (
        "tests/test_trend_candidate_blog_recommendation_ui.py"
        in SCENARIO_TESTS["workflow"]
    )
    assert "tests/test_agent_harness_launcher_contract.py" in SCENARIO_TESTS["harness"]
    assert "tests/test_apply_update_work_branch_mode.py" in SCENARIO_TESTS["harness"]


def test_all_scenario_test_files_exist_in_repository_checkout() -> None:
    missing = sorted(
        path
        for paths in SCENARIO_TESTS.values()
        for path in paths
        if not (PROJECT_ROOT / path).is_file()
    )

    assert missing == []


def test_scenario_aliases_and_all_are_normalized_in_stable_order() -> None:
    assert normalize_scenarios(["all"]) == SCENARIO_ORDER
    assert normalize_scenarios(
        ["refresh", "maintenance", "angles", "logs", "content-workflow", "dev-harness"]
    ) == (
        "latest-data",
        "cleanup",
        "topic-angles",
        "operations",
        "workflow",
        "harness",
    )
    assert normalize_scenarios(["diagnostics", "diagnostics"]) == ("diagnostics",)
    assert normalize_scenarios(["scheduler", "scheduler"]) == ("scheduler",)
    with pytest.raises(ValueError):
        normalize_scenarios(["real-api"])


def test_pytest_command_uses_isolated_basetemp_and_no_cache(tmp_path: Path) -> None:
    command = build_pytest_command(
        "scheduler",
        python_executable="python-test",
        basetemp=tmp_path / "pytest",
    )

    assert command[:4] == ["python-test", "-m", "pytest", "-q"]
    assert "-p" in command
    assert "no:cacheprovider" in command
    assert f"--basetemp={tmp_path / 'pytest'}" in command
    scheduler_tests = list(SCENARIO_TESTS["scheduler"])
    assert command[-len(scheduler_tests):] == scheduler_tests


def test_resolve_routing_returns_doc_only_for_markdown_and_docs() -> None:
    decision = resolve_routing(["docs/AGENT_TEST_HARNESS.md", "README.md"])
    assert decision.mode == "doc_only"
    assert decision.scenarios == ()
    assert "tests/test_repository_text_hygiene.py" in decision.test_files


def test_resolve_routing_returns_selective_for_single_domain() -> None:
    decision = resolve_routing(["scripts/report_operation_diagnostics.py"])
    assert decision.mode == "selective"
    assert decision.scenarios == ("diagnostics",)
    assert "tests/test_operation_diagnostic_cli.py" in decision.test_files


def test_resolve_routing_keeps_adsense_blog_delta_in_workflow() -> None:
    decision = resolve_routing(
        [
            "src/services/adsense_candidate_service.py",
            "src/trend_candidate_blog_recommendation_ui.py",
            "tests/test_adsense_candidate_service.py",
            "tests/test_trend_candidate_blog_recommendation_ui.py",
        ]
    )

    assert decision.mode == "selective"
    assert decision.scenarios == ("workflow",)
    assert "tests/test_adsense_candidate_service.py" in decision.test_files
    assert "tests/test_trend_candidate_blog_recommendation_ui.py" in decision.test_files


def test_resolve_targets_cli_emits_ascii_safe_json(capsys: pytest.CaptureFixture[str]) -> None:
    exit_code = main(
        ["--resolve-targets", "src/services/adsense_candidate_service.py"]
    )
    output = capsys.readouterr().out

    assert exit_code == 0
    output.encode("ascii")
    payload = json.loads(output)
    assert payload["mode"] == "selective"
    assert payload["scenarios"] == ["workflow"]
    assert "변경 범위" in payload["reason"]


def test_resolve_routing_returns_fallback_all_for_core_files_and_multi_domain() -> None:
    db_decision = resolve_routing(["src/database.py"])
    assert db_decision.mode == "fallback_all"
    assert "src/database.py" in db_decision.reason or "DB" in db_decision.reason

    multi_decision = resolve_routing(
        [
            "src/services/trend_cluster_ai_review_service.py",
            "src/services/scheduler_service.py",
            "src/services/program_log_service.py",
        ]
    )
    assert multi_decision.mode == "fallback_all"


def test_harness_runs_scenarios_sequentially_with_safe_environment() -> None:
    calls: list[dict] = []

    def fake_run(command, **kwargs):
        calls.append({"command": list(command), **kwargs})
        return SimpleNamespace(returncode=0)

    results = run_harness(
        [
            "scheduler",
            "cleanup",
            "topic-angles",
            "diagnostics",
            "operations",
            "workflow",
            "harness",
        ],
        python_executable="python-test",
        command_runner=fake_run,
    )

    assert [row.scenario for row in results] == [
        "scheduler",
        "cleanup",
        "topic-angles",
        "diagnostics",
        "operations",
        "workflow",
        "harness",
    ]
    assert all(row.status == "passed" for row in results)
    assert len(calls) == 7
    for call in calls:
        assert call["env"]["CONTENT_TREND_AGENT_HARNESS"] == "1"
        assert call["env"]["GEMINI_API_KEY"] == ""
        assert call["env"]["NAVER_CLIENT_ID"] == ""
        assert call["env"]["KAKAO_REST_API_KEY"] == ""
        assert call["stdin"] is not None
        assert call["check"] is False
