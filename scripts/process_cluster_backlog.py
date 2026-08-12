from __future__ import annotations

import argparse
from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.services.program_log_runtime import install_program_logging_contract
from src.services.program_log_correlation_runtime import (
    install_program_log_correlation_contract,
)
from src.services.topic_angle_candidate_diagnostic_service import (
    install_topic_angle_candidate_diagnostic_contract,
)
from src.services.trend_cluster_runtime_contract import (
    install_trend_cluster_runtime_contract,
)

# Gemini 호출 함수를 다른 군집 모듈이 가져오기 전에 중앙 운영 로그를 먼저 설치합니다.
install_program_logging_contract()
install_topic_angle_candidate_diagnostic_contract()
install_program_log_correlation_contract()
install_trend_cluster_runtime_contract()

from src.database import init_database
import src.services.trend_clustering_job_service as clustering_jobs
from src.services.trend_cluster_progress_detail_runtime import (
    install_cluster_progress_detail_contract,
)
from src.services.post_clustering_topic_angle_service import (
    run_topic_angles_after_clustering,
)
from src.services.trend_cluster_persistence_safety_service import (
    finalize_prepared_trend_rankings_safely,
)

# 별도 프로세스에서도 앱과 같은 요청별 진행 로그와 단계 설명을 기록합니다.
install_cluster_progress_detail_contract(clustering_jobs)


# 백그라운드 군집 저장은 동일 cluster_id가 한 계산에 중복돼도
# 기존 데이터와 원문 연결을 잃지 않고 멱등하게 정리한 뒤 반영합니다.
clustering_jobs.finalize_prepared_trend_rankings = (
    finalize_prepared_trend_rankings_safely
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="최근 미처리 자료를 1차 군집 후 Flash-Lite 2차 군집으로 처리합니다."
    )
    parser.add_argument("--job-id", required=True)
    parser.add_argument("--db-path", required=True)
    parser.add_argument("--lookback-hours", type=int, default=72)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    job_id = str(args.job_id)
    db_path = Path(args.db_path).resolve()
    init_database(db_path)
    exit_code = clustering_jobs.run_clustering_job(
        job_id,
        db_path=db_path,
        project_root=PROJECT_ROOT,
        lookback_hours=max(6, int(args.lookback_hours)),
    )
    if exit_code == 0:
        # Gemini 방향 생성 실패는 성공한 군집 결과를 취소하지 않습니다.
        run_topic_angles_after_clustering(
            job_id,
            db_path=db_path,
        )
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
