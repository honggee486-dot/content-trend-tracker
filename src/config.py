from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = PROJECT_ROOT / "data"
EXPORTS_DIR = PROJECT_ROOT / "exports"
DEFAULT_DB_PATH = DATA_DIR / "content_trend_tracker.duckdb"
DEFAULT_YOUTUBE_DB_PATH = Path(
    r"C:\AIProjects\youtube-trend-tracker\data\youtube_trends.duckdb"
)
DEFAULT_YOUTUBE_PARQUET_PATH = Path(
    r"C:\AIProjects\youtube-trend-tracker\exports\content_trend_signals.parquet"
)

BACKGROUND_TOPIC_ANGLE_ITEMS_PER_REQUEST = 15
BACKGROUND_TOPIC_ANGLE_MAX_PARALLEL_REQUESTS = 1
BACKGROUND_TOPIC_ANGLE_TIMEOUT_SECONDS = 600
LEGACY_TOPIC_ANGLE_TIMEOUT_SECONDS = 360


@dataclass(frozen=True)
class GeminiConfig:
    api_key: str
    model: str
    app_id: str
    quota_scope_id: str
    timeout_seconds: int
    retry_wait_seconds: float
    retry_max_wait_seconds: float
    topic_angle_timeout_seconds: int = BACKGROUND_TOPIC_ANGLE_TIMEOUT_SECONDS
    topic_angle_batch_limit: int = BACKGROUND_TOPIC_ANGLE_ITEMS_PER_REQUEST
    topic_angle_max_parallel_requests: int = BACKGROUND_TOPIC_ANGLE_MAX_PARALLEL_REQUESTS
    topic_angle_request_stagger_seconds: float = 5.0
    topic_angle_min_opportunity_score: float = 50.0
    daily_request_reference_limit: int = 1500
    draft_thinking_level: str = "high"
    topic_angle_thinking_level: str = "medium"

TOPIC_STATUS_LABELS = {
    "candidate": "후보",
    "researching": "자료 확인",
    "ai_ready": "AI 요청 준비",
    "draft_complete": "초안 완성",
    "editing": "수정 중",
    "publish_ready": "발행 준비",
    "published": "발행 완료",
    "on_hold": "보류",
}

TOPIC_STATUS_OPTIONS = list(TOPIC_STATUS_LABELS)
PRIORITY_LABELS = {1: "낮음", 2: "보통", 3: "높음"}

DEFAULT_SETTINGS = {
    "youtube_parquet_path": str(DEFAULT_YOUTUBE_PARQUET_PATH),
    "youtube_db_path": str(DEFAULT_YOUTUBE_DB_PATH),
    "naver_write_url": "https://blog.naver.com/",
    "tistory_write_url": "https://www.tistory.com/",
    "default_audience": "주제에 관심은 있지만 전문 지식은 많지 않은 일반 독자",
    "default_purpose": "검색한 독자가 궁금한 내용을 정확하고 이해하기 쉽게 정리",
    "trend_seed_queries": "인공지능\nIT 신제품\n정부 정책\n생활 정보\n경제 이슈\n건강 정보\n자동차\n게임\n여행\n취업 채용",
    "trend_lookback_hours": "72",
    "trend_results_per_query": "10",
    "trend_portal_query_limit": "50",
    "trend_portal_pages_per_query": "2",
    "trend_refresh_interval_minutes": "180",
    "gemini_auto_analysis_model": "",
    "gemini_manual_draft_model": "",
    "gemini_model_catalog_json": "",
    "gemini_model_catalog_refreshed_at": "",
    "naver_search_workers": "6",
    "daum_search_workers": "4",
    "google_trends_enabled": "true",
    "google_trends_limit": "50",
    "wikipedia_pageviews_enabled": "true",
    "wikipedia_pageviews_limit": "50",
    "kakao_daum_daily_safety_limit": "50000",
    "kakao_daum_monthly_safety_limit": "3000000",
    "naver_search_daily_safety_limit": "25000",
    "naver_search_monthly_safety_limit": "775000",
    "data_cleanup_enabled": "true",
    "source_retention_days": "30",
    "sync_run_retention_days": "90",
    "api_usage_retention_months": "13",
    "trend_analysis_youtube_limit": "2000",
    "trend_analysis_naver_limit": "0",
    "trend_analysis_daum_limit": "0",
    "trend_analysis_google_limit": "500",
    "trend_analysis_wikipedia_limit": "500",
    "trend_ai_clustering_enabled": "true",
    "trend_ai_clustering_max_items": "4000",
    "trend_ai_clustering_batch_size": "200",
    "trend_ai_clustering_max_batches": "5",
}


def get_kakao_rest_api_key() -> str:
    return os.getenv("KAKAO_REST_API_KEY", "").strip()


def get_naver_api_credentials() -> tuple[str, str]:
    return (
        os.getenv("NAVER_CLIENT_ID", "").strip(),
        os.getenv("NAVER_CLIENT_SECRET", "").strip(),
    )


def _env_int(name: str, default: int, *, minimum: int, maximum: int) -> int:
    raw = os.getenv(name, "").strip()
    try:
        value = int(raw) if raw else int(default)
    except ValueError:
        value = int(default)
    return min(max(value, minimum), maximum)


def _env_float(
    name: str,
    default: float,
    *,
    minimum: float,
    maximum: float,
) -> float:
    raw = os.getenv(name, "").strip()
    try:
        value = float(raw) if raw else float(default)
    except ValueError:
        value = float(default)
    return min(max(value, minimum), maximum)


def _env_choice(name: str, default: str, allowed: set[str]) -> str:
    value = os.getenv(name, default).strip().casefold() or default
    return value if value in allowed else default


def _topic_angle_timeout_seconds() -> int:
    """기존 기본값 360초는 600초로 올리고 사용자가 정한 다른 값은 보존합니다."""
    raw = os.getenv("GEMINI_TOPIC_ANGLE_TIMEOUT_SECONDS", "").strip()
    try:
        value = int(raw) if raw else BACKGROUND_TOPIC_ANGLE_TIMEOUT_SECONDS
    except ValueError:
        value = BACKGROUND_TOPIC_ANGLE_TIMEOUT_SECONDS
    if value == LEGACY_TOPIC_ANGLE_TIMEOUT_SECONDS:
        value = BACKGROUND_TOPIC_ANGLE_TIMEOUT_SECONDS
    return min(max(value, 30), 900)


def get_gemini_config(*, model: str | None = None) -> GeminiConfig:
    configured_model = str(model or "").strip()
    if not configured_model:
        configured_model = os.getenv("GEMINI_MODEL", "gemini-3.6-flash").strip()
    configured_model = configured_model or "gemini-3.6-flash"
    return GeminiConfig(
        api_key=os.getenv("GEMINI_API_KEY", "").strip(),
        model=configured_model,
        app_id=os.getenv("GEMINI_APP_ID", "content-trend-tracker").strip()
        or "content-trend-tracker",
        quota_scope_id=os.getenv(
            "GEMINI_QUOTA_SCOPE_ID", "honggee-gemini-main"
        ).strip()
        or "honggee-gemini-main",
        timeout_seconds=_env_int(
            "GEMINI_TIMEOUT_SECONDS", 60, minimum=5, maximum=300
        ),
        retry_wait_seconds=_env_float(
            "GEMINI_RETRY_WAIT_SECONDS", 2.0, minimum=0.5, maximum=30.0
        ),
        retry_max_wait_seconds=_env_float(
            "GEMINI_RETRY_MAX_WAIT_SECONDS", 30.0, minimum=0.0, maximum=120.0
        ),
        topic_angle_timeout_seconds=_topic_angle_timeout_seconds(),
        topic_angle_batch_limit=_env_int(
            "GEMINI_TOPIC_ANGLE_ITEMS_PER_REQUEST",
            _env_int(
                "GEMINI_TOPIC_ANGLE_BATCH_LIMIT",
                BACKGROUND_TOPIC_ANGLE_ITEMS_PER_REQUEST,
                minimum=1,
                maximum=30,
            ),
            minimum=1,
            maximum=30,
        ),
        topic_angle_max_parallel_requests=_env_int(
            "GEMINI_TOPIC_ANGLE_MAX_PARALLEL_REQUESTS",
            BACKGROUND_TOPIC_ANGLE_MAX_PARALLEL_REQUESTS,
            minimum=1,
            maximum=4,
        ),
        topic_angle_request_stagger_seconds=_env_float(
            "GEMINI_TOPIC_ANGLE_REQUEST_STAGGER_SECONDS",
            5.0,
            minimum=0.0,
            maximum=60.0,
        ),
        topic_angle_min_opportunity_score=_env_float(
            "GEMINI_TOPIC_ANGLE_MIN_OPPORTUNITY_SCORE",
            _env_float(
                "GEMINI_TOPIC_ANGLE_MIN_TREND_SCORE",
                50.0,
                minimum=0.0,
                maximum=100.0,
            ),
            minimum=0.0,
            maximum=100.0,
        ),
        daily_request_reference_limit=_env_int(
            "GEMINI_DAILY_REQUEST_REFERENCE_LIMIT",
            1500,
            minimum=1,
            maximum=1000000,
        ),
        draft_thinking_level=_env_choice(
            "GEMINI_DRAFT_THINKING_LEVEL",
            "high",
            {"minimal", "low", "medium", "high"},
        ),
        topic_angle_thinking_level=_env_choice(
            "GEMINI_TOPIC_ANGLE_THINKING_LEVEL",
            "medium",
            {"minimal", "low", "medium", "high"},
        ),
    )


def ensure_project_directories() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    EXPORTS_DIR.mkdir(parents=True, exist_ok=True)
