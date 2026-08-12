from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.config import DEFAULT_DB_PATH
from src.database import connect_database, get_setting

_TRIAL_MAX_BATCHES = 1


def _create_trial_job(con, clustering_jobs: Any) -> dict[str, Any]:
    result = dict(
        clustering_jobs.create_clustering_job(
            con,
            launcher="p2_diagnostic_trial",
        )
    )
    if not bool(result.get("created")):
        return result

    job_id = str(result.get("job_id") or "").strip()
    if not job_id:
        raise RuntimeError("진단용 군집 작업 ID를 만들지 못했습니다.")

    updated = con.execute(
        """
        UPDATE trend_clustering_jobs
        SET max_batches = ?
        WHERE job_id = ?
          AND status = 'queued'
        RETURNING max_batches
        """,
        [_TRIAL_MAX_BATCHES, job_id],
    ).fetchone()
    if updated is None or int(updated[0] or 0) != _TRIAL_MAX_BATCHES:
        raise RuntimeError("진단용 군집 작업을 1배치로 제한하지 못했습니다.")

    result["max_batches"] = _TRIAL_MAX_BATCHES
    return result


def _job_summary(con, job_id: str) -> dict[str, Any]:
    row = con.execute(
        """
        SELECT job_id, status, model_name, batch_size, max_batches,
               completed_batches, processed_units, processed_source_items,
               remaining_items, total_tokens, error_message,
               created_at, started_at, finished_at
        FROM trend_clustering_jobs
        WHERE job_id = ?
        """,
        [job_id],
    ).fetchone()
    if row is None:
        return {"job_id": job_id, "status": "missing"}

    columns = [
        "job_id",
        "status",
        "model_name",
        "batch_size",
        "max_batches",
        "completed_batches",
        "processed_units",
        "processed_source_items",
        "remaining_items",
        "total_tokens",
        "error_message",
        "created_at",
        "started_at",
        "finished_at",
    ]
    payload = dict(zip(columns, row))
    for key in ("created_at", "started_at", "finished_at"):
        value = payload.get(key)
        payload[key] = value.isoformat(sep=" ") if value is not None else None
    return payload


def _install_runtime_contracts():
    from src.services.program_log_runtime import install_program_logging_contract
    from src.services.program_log_correlation_runtime import (
        install_program_log_correlation_contract,
    )
    from src.services.trend_cluster_runtime_contract import (
        install_trend_cluster_runtime_contract,
    )

    install_program_logging_contract()
    install_program_log_correlation_contract()
    install_trend_cluster_runtime_contract()

    import src.services.trend_clustering_job_service as clustering_jobs
    from src.services.trend_cluster_progress_detail_runtime import (
        install_cluster_progress_detail_contract,
    )
    from src.services.trend_cluster_persistence_safety_service import (
        finalize_prepared_trend_rankings_safely,
    )

    install_cluster_progress_detail_contract(clustering_jobs)
    clustering_jobs.finalize_prepared_trend_rankings = (
        finalize_prepared_trend_rankings_safely
    )
    return clustering_jobs


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "P2 군집 운영 표본을 위해 실제 DB와 Gemini를 사용하되 "
            "외부 군집 배치를 최대 1회로 제한합니다."
        )
    )
    parser.add_argument(
        "--db-path",
        type=Path,
        default=DEFAULT_DB_PATH,
        help="진단 표본을 만들 실제 DuckDB 경로",
    )
    parser.add_argument(
        "--lookback-hours",
        type=int,
        default=None,
        help="생략하면 현재 trend_lookback_hours 설정을 사용",
    )
    parser.add_argument(
        "--confirm",
        action="store_true",
        help="실제 DB 저장과 Gemini 호출을 명시적으로 허용",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if not args.confirm:
        print(
            "이 명령은 실제 DuckDB에 군집 결과를 저장하고 Gemini를 호출할 수 있습니다. "
            "--confirm을 붙여 다시 실행하세요.",
            file=sys.stderr,
        )
        return 2

    db_path = args.db_path.expanduser().resolve()
    if not db_path.is_file():
        print(f"DB 파일을 찾을 수 없습니다: {db_path}", file=sys.stderr)
        return 2

    clustering_jobs = _install_runtime_contracts()

    try:
        with connect_database(db_path) as con:
            lookback_hours = (
                max(6, int(args.lookback_hours))
                if args.lookback_hours is not None
                else max(
                    6,
                    int(get_setting(con, "trend_lookback_hours", "72") or 72),
                )
            )
            created = _create_trial_job(con, clustering_jobs)
    except Exception as exc:
        print(f"P2 군집 표본 작업을 준비하지 못했습니다: {exc}", file=sys.stderr)
        return 1

    if not bool(created.get("created")):
        print(
            str(created.get("message") or "이미 군집 작업이 실행 중입니다."),
            file=sys.stderr,
        )
        return 3

    job_id = str(created["job_id"])
    print(
        "P2 군집 표본 실행: 실제 DB 저장·Gemini 호출 가능 · "
        f"외부 배치 최대 {_TRIAL_MAX_BATCHES}회 · "
        f"1차 군집 단위 최대 {int(created.get('batch_size') or 0):,}개"
    )

    try:
        exit_code = clustering_jobs.run_clustering_job(
            job_id,
            db_path=db_path,
            project_root=PROJECT_ROOT,
            lookback_hours=lookback_hours,
        )
    except Exception as exc:
        print(f"P2 군집 표본 실행 중 예외가 발생했습니다: {exc}", file=sys.stderr)
        return 1

    try:
        with connect_database(db_path, read_only=True) as con:
            summary = _job_summary(con, job_id)
    except Exception as exc:
        print(f"군집 표본은 실행됐지만 결과를 다시 읽지 못했습니다: {exc}", file=sys.stderr)
        return 1 if exit_code == 0 else int(exit_code)

    print(json.dumps(summary, ensure_ascii=False, indent=2))
    status = str(summary.get("status") or "")
    if status == "skipped_overlap":
        print(
            "다른 군집 작업과 겹쳐 표본을 만들지 않았습니다. "
            "현재 군집 작업이 끝난 뒤 다시 실행하세요.",
            file=sys.stderr,
        )
        return 3
    if exit_code == 0:
        print(
            "표본 실행이 끝났습니다. 이제 .\\run_p2_diagnostics.bat --json 을 "
            "다시 실행해 trend_clustering 결과를 확인하세요."
        )
    return int(exit_code)


if __name__ == "__main__":
    raise SystemExit(main())
