from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_app_exposes_two_stage_background_trial_settings() -> None:
    app = (ROOT / "app.py").read_text(encoding="utf-8")

    assert "Gemini 기본 군집화 모델" in app
    assert "Gemini를 기본 주제 군집 엔진으로 사용" in app
    assert "2차 군집 후보를 찾을 최근 미처리 원문" in app
    assert "Gemini 요청 1회당 1차 군집" in app
    assert "백그라운드 작업 1회당 최대 Gemini 요청" in app
    assert "최근 4,000개" in app
    assert "기본값은 200개" in app
    assert "기본값은 시험용 5회" in app


def test_service_orders_first_stage_then_flash_lite_then_scoring() -> None:
    service = (ROOT / "src" / "services" / "trend_discovery_service.py").read_text(
        encoding="utf-8"
    )

    first_stage = service.index("first_stage_candidates, first_stage_stats")
    classify = service.index("execution = classify_cluster_batch", first_stage)
    score = service.index("scored = _score_cluster", classify)
    assert first_stage < classify < score
    assert "DEFAULT_AI_CLUSTERING_MAX_ITEMS = 4000" in service
    assert "DEFAULT_AI_CLUSTERING_BATCH_SIZE = 200" in service
    assert "DEFAULT_AI_CLUSTERING_MAX_BATCHES = 5" in service
    assert "same_url" in service
    assert "same_title" in service
    assert "second_stage_ready" in service
    assert "_build_ai_cluster_review_candidates" not in service
    assert "_title_redundancy_penalty" not in service


def test_background_worker_closes_db_before_gemini_calculation() -> None:
    worker = (
        ROOT / "src" / "services" / "trend_clustering_job_service.py"
    ).read_text(encoding="utf-8")
    prepare_open = worker.index("with connect_database(db_path) as con:", worker.index("for batch_number"))
    calculate = worker.index("calculation = calculate_prepared_trend_rankings", prepare_open)
    finalize_open = worker.index("with connect_database(db_path) as con:", calculate)
    assert prepare_open < calculate < finalize_open
    assert "range(1, max_batches + 1)" in worker


def test_background_status_exposes_first_stage_savings_and_token_projection() -> None:
    app = (ROOT / "app.py").read_text(encoding="utf-8")
    database = (ROOT / "src" / "database.py").read_text(encoding="utf-8")

    assert "URL 중복 절감" in app
    assert "동일 제목 병합" in app
    assert "1,000개당 예상 토큰" in app
    assert "scanned_pending_items" in database
    assert "url_merged_items" in database
    assert "title_merged_groups" in database
    assert "deferred_units" in database
