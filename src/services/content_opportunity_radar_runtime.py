from __future__ import annotations

from functools import wraps
from typing import Any

from src.services.content_opportunity_radar_service import (
    get_opportunity_summary,
    refresh_opportunity_radar,
)


def install_content_opportunity_radar_contract(discovery_module: Any | None = None) -> None:
    """순위 저장 뒤 레이더 관측을 남기되 실패가 기존 순위 성공을 취소하지 않게 합니다."""
    if discovery_module is None:
        from src.services import trend_discovery_service as discovery_module

    original = getattr(discovery_module, "finalize_prepared_trend_rankings", None)
    if not callable(original) or getattr(
        original,
        "_content_opportunity_radar_contract",
        False,
    ):
        return

    @wraps(original)
    def finalize_with_opportunity_radar(con, calculation):
        result = dict(original(con, calculation))
        if bool(result.get("reused")):
            try:
                result["opportunity_radar"] = {
                    "status": "reused",
                    "counts": get_opportunity_summary(con),
                }
            except Exception as exc:
                result["opportunity_radar"] = {
                    "status": "failed",
                    "error": str(exc)[:500],
                }
            return result

        observed_at = None
        cluster_rows = list(getattr(calculation, "cluster_rows", ()) or ())
        if cluster_rows:
            observed_at = cluster_rows[0].get("calculated_at")
        try:
            result["opportunity_radar"] = refresh_opportunity_radar(
                con,
                observed_at=observed_at,
            )
        except Exception as exc:
            # 레이더는 파생 관찰 계층입니다. 여기서 실패해도 이미 COMMIT된 순위·수집
            # 성공을 되돌리거나 다른 출처의 성공을 실패로 바꾸지 않습니다.
            result["opportunity_radar"] = {
                "status": "failed",
                "error": str(exc)[:500],
            }
        return result

    finalize_with_opportunity_radar._content_opportunity_radar_contract = True  # type: ignore[attr-defined]
    discovery_module.finalize_prepared_trend_rankings = finalize_with_opportunity_radar
