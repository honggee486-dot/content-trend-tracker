from __future__ import annotations

from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.services.program_log_runtime import install_program_logging_contract
from src.services.program_log_run_lifecycle import (
    install_program_log_run_lifecycle,
)
from src.services.source_collection_log_runtime import (
    install_source_collection_logging,
)
from src.services.post_collection_cleanup_runtime import (
    install_post_collection_cleanup_contract,
)
from src.services.program_log_correlation_runtime import (
    install_program_log_correlation_contract,
)
from src.services.topic_angle_candidate_diagnostic_service import (
    install_topic_angle_candidate_diagnostic_contract,
)
from src.services.trend_cluster_runtime_contract import (
    install_trend_cluster_runtime_contract,
)
from src.services.trend_stage_program_log_runtime import (
    install_precise_trend_stage_logging,
)

# 예약 수집에서도 실제 Gemini 전송·출처 요청·정리·주제 방향 단계를 동일하게 기록합니다.
install_program_logging_contract()
install_topic_angle_candidate_diagnostic_contract()
install_program_log_run_lifecycle()
install_source_collection_logging()
install_post_collection_cleanup_contract()
install_program_log_correlation_contract()
install_trend_cluster_runtime_contract()
install_precise_trend_stage_logging()

import src.services.trend_discovery_service as trend_discovery
from scripts import refresh_trends as base_refresh
from src.services.scheduled_topic_angle_log_service import (
    run_refresh_body_with_topic_angle_log,
)
from src.services.topic_angle_backlog_resume_service import (
    resume_deferred_topic_angles,
)
from src.services.trend_cluster_persistence_safety_service import (
    finalize_prepared_trend_rankings_safely,
)


_original_finalizer = trend_discovery.finalize_prepared_trend_rankings


def _safe_finalizer(con, calculation):
    return finalize_prepared_trend_rankings_safely(
        con,
        calculation,
        finalizer=_original_finalizer,
    )


# 예약 수집이 내부에서 순위를 저장할 때도 백그라운드 군집 작업과
# 동일한 기본키 중복 정리 계약을 사용합니다.
trend_discovery.finalize_prepared_trend_rankings = _safe_finalizer


def main() -> int:
    original_runner = base_refresh._run_refresh_body

    def resumed_runner(
        collection_run_id: str | None = None,
    ) -> tuple[int, dict[str, object]]:
        exit_code, result = original_runner(collection_run_id)
        result, topic_angle_warning = resume_deferred_topic_angles(
            result,
            runner=base_refresh._run_background_topic_angles,
            db_path=base_refresh.DEFAULT_DB_PATH,
        )
        if topic_angle_warning:
            print(f"주의: {topic_angle_warning}")
        return exit_code, result

    def logged_runner(
        collection_run_id: str | None = None,
    ) -> tuple[int, dict[str, object]]:
        return run_refresh_body_with_topic_angle_log(
            resumed_runner,
            collection_run_id,
            db_path=base_refresh.DEFAULT_DB_PATH,
        )

    base_refresh._run_refresh_body = logged_runner
    try:
        return base_refresh.main()
    finally:
        base_refresh._run_refresh_body = original_runner


if __name__ == "__main__":
    raise SystemExit(main())
