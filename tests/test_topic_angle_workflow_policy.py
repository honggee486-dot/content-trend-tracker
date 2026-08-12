from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_background_refresh_integrates_gemini_topic_angles() -> None:
    source = (PROJECT_ROOT / "scripts" / "refresh_trends.py").read_text(encoding="utf-8")
    assert "prepare_missing_topic_angles" in source
    assert "execute_prepared_topic_angles" in source
    assert "finalize_prepared_topic_angles" in source
    assert "get_gemini_config" in source
    assert "_run_background_topic_angles" in source
    assert "generate_missing_topic_angles" not in source


def test_topic_angle_ui_distinguishes_saved_and_manual_states() -> None:
    source = (PROJECT_ROOT / "app.py").read_text(encoding="utf-8")
    assert "Gemini 글감 분석 · 제목·요약·작성 설정·확인 항목·방향 3개 저장됨" in source
    assert "Gemini 자동 방향 · 미생성 0/3" in source
    assert "수동 주제 방향 · 선택 사항" in source
    assert "Gemini 핵심 요약" in source
    assert "발행 전 확인할 사실" in source
    assert "규칙 기반 임시 방향" not in source


def test_dashboard_has_equal_three_action_buttons_and_separate_angle_action() -> None:
    source = (PROJECT_ROOT / "app.py").read_text(encoding="utf-8")
    assert "[1.433, 1.433, 1.433, 1.0, 1.0, 1.0, 1.12, 1.12]" in source
    assert '"angles",' in source
    assert "model_name=selected_auto_model" in source
    assert 'elif action == "angles":' in source
    assert 'elif action == "rebuild":' in source
    rebuild_section = source.split('elif action == "rebuild":', 1)[1].split(
        'elif action == "angles":', 1
    )[0]
    assert "run_topic_angles(" in rebuild_section
    assert "progress_start=" in rebuild_section
    assert "progress_span=" in rebuild_section

    helper_section = source.split("def run_topic_angles(", 1)[1].split("    try:", 1)[0]
    assert "prepare_missing_topic_angles" in helper_section
    assert "execute_prepared_topic_angles" in helper_section
    assert "finalize_prepared_topic_angles" in helper_section
    assert "progress_callback=" in helper_section


def test_dashboard_actions_snapshot_and_use_the_visible_gemini_model() -> None:
    source = (PROJECT_ROOT / "app.py").read_text(encoding="utf-8")

    assert 'TREND_REFRESH_MODEL_KEY = "trend_dashboard_pending_model"' in source
    assert "def queue_trend_dashboard_action(action: str, *, model_name: str)" in source
    assert "st.session_state[TREND_REFRESH_MODEL_KEY] = selected_model" in source
    assert "pending_model = normalize_model_id(" in source
    assert "auto_analysis_model=pending_model" in source
    assert source.count("model_name=selected_auto_model") == 3
    assert "config = replace(config, model=effective_auto_model)" in source
    assert '"model_name": effective_auto_model' in source
    assert "이미 완성된 제목·요약·작성 설정·방향은 API 사용량을 아끼기 위해" in source


def test_latest_refresh_runs_gemini_analysis_without_cancelling_collection() -> None:
    source = (PROJECT_ROOT / "app.py").read_text(encoding="utf-8")
    refresh_section = source.split('if action == "refresh":', 1)[1].split(
        'elif action == "rebuild":', 1
    )[0]

    assert "topic_angle_result = run_topic_angles(" in refresh_section
    assert 'refresh_result["topic_angles"] = topic_angle_payload(topic_angle_result)' in refresh_section
    assert 'topic_angle_warning = f"Gemini 주제 방향 생성 실패: {exc}"' in refresh_section
    assert '"topic_angle_detail": (' in refresh_section
    assert "0.05 + (0.70 * value)" in refresh_section


def test_api_call_buttons_are_marked_and_ai_import_prefers_chatgpt() -> None:
    source = (PROJECT_ROOT / "app.py").read_text(encoding="utf-8")

    assert 'API_BUTTON_ICON = ":material/api:"' in source
    assert source.count("icon=API_BUTTON_ICON") == 4
    assert "API 아이콘이 있는 버튼은 외부 API를 호출할 수 있습니다." in source
    assert "아래 요청서를 복사해 ChatGPT 또는 사용자가 선택한 AI에서 초안을 만들 수 있습니다." in source
    assert '"결과를 생성한 AI"' in source
    assert '["ChatGPT", "Gemini", "기타"]' in source
    assert "이 선택으로 API 호출 모델이 바뀌지는 않습니다." in source
    assert '"Gemini 기본 군집화 모델"' in source
    assert '"수동 최종 초안·검토 모델"' not in source


def test_topic_angle_env_uses_one_large_batch() -> None:
    example = (PROJECT_ROOT / ".env.example").read_text(encoding="utf-8")
    assert "GEMINI_TOPIC_ANGLE_ITEMS_PER_REQUEST=15" in example
    assert "상위 글감 최대 15개를 한 요청으로 처리합니다." in example
    assert "GEMINI_TOPIC_ANGLE_MAX_PARALLEL_REQUESTS=1" in example
    assert "GEMINI_TOPIC_ANGLE_REQUEST_STAGGER_SECONDS=5" in example
    assert "GEMINI_TOPIC_ANGLE_TIMEOUT_SECONDS=600" in example
    assert "GEMINI_TOPIC_ANGLE_MIN_OPPORTUNITY_SCORE=50" in example


def test_topic_angle_analysis_builds_saved_content_plan_and_prefills_request_form() -> None:
    app_source = (PROJECT_ROOT / "app.py").read_text(encoding="utf-8")
    service_source = (
        PROJECT_ROOT / "src" / "services" / "topic_angle_ai_service.py"
    ).read_text(encoding="utf-8")
    pack_source = (
        PROJECT_ROOT / "src" / "services" / "content_pack_service.py"
    ).read_text(encoding="utf-8")
    database_source = (PROJECT_ROOT / "src" / "database.py").read_text(encoding="utf-8")
    discovery_source = (
        PROJECT_ROOT / "src" / "services" / "trend_discovery_service.py"
    ).read_text(encoding="utf-8")

    assert 'TOPIC_ANGLE_FEATURE_VERSION = "6"' in service_source
    assert '"content_plan"' in service_source
    assert '"audience"' in service_source
    assert '"target_length"' in service_source
    assert '"timeliness"' in service_source
    assert '"evidence_plan"' in service_source
    assert '"primary_direction_reason"' in service_source
    assert '"search_intent"' in service_source
    assert '"reader_question"' in service_source
    assert '"demand_evidence"' in service_source
    assert '"evidence_source_ids"' in service_source
    assert '"score_breakdown"' in service_source
    assert "direction_score" in database_source
    assert "방향 점수 근거" in app_source
    assert 'content_plan_json' in database_source
    assert 'CREATE TABLE IF NOT EXISTS topic_content_preferences' in database_source
    assert 'def get_topic_content_defaults' in pack_source
    assert 'def save_topic_content_preferences' in pack_source
    assert 'link_topic_to_trend_cluster' in pack_source
    assert 'link_topic_to_trend_cluster(' in discovery_source
    assert 'get_topic_content_defaults(' in app_source
    assert '초기 Gemini 글감 분석에서 추천한 독자·목적·카테고리·분량·구성을' in app_source
    assert 'Gemini 추천 작성 설정' in app_source
    assert '게시 시급성' in app_source
    assert '필요한 공식 근거' in app_source
    assert '1순위 방향 추천 이유' in app_source


def test_top_navigation_restores_default_streamlit_deploy_and_toolbar() -> None:
    source = (PROJECT_ROOT / "app.py").read_text(encoding="utf-8")
    config_source = (PROJECT_ROOT / ".streamlit" / "config.toml").read_text(encoding="utf-8")
    assert 'toolbarMode = "developer"' in config_source
    assert "position: sticky;" in source
    assert "top: 0.18rem !important;" in source
    assert "height: 3.18rem !important;" in source
    assert "height: 2.55rem !important;" in source
    assert "[1.05] + [0.68] * len(NAVIGATION_ITEMS) + [0.78, 0.95]" in source
    assert "zip(menu_columns[1:-2], NAVIGATION_ITEMS)" in source
    assert "with menu_columns[-2].popover(" in source
    assert "text-overflow: ellipsis;" in source


def test_topic_angle_target_uses_opportunity_score_not_trend_score() -> None:
    source = (PROJECT_ROOT / "src" / "services" / "topic_angle_ai_service.py").read_text(
        encoding="utf-8"
    )
    assert "COALESCE(tc.opportunity_score, 0) >= ?" in source
    assert "ORDER BY tc.opportunity_score DESC, tc.trend_score DESC" in source
    assert "COALESCE(tc.trend_score, 0) >= ?" not in source


def test_gemini_progress_prefers_clear_request_status_over_estimated_percent() -> None:
    app_source = (PROJECT_ROOT / "app.py").read_text(encoding="utf-8")
    service_source = (
        PROJECT_ROOT / "src" / "services" / "topic_angle_ai_service.py"
    ).read_text(encoding="utf-8")
    assert 'message if "Gemini" in message else f"진행률 {percent}% · {message}"' in app_source
    assert "응답 완료 {successful_items:,}개" in service_source
    assert "처리 중 {processing_items:,}개" in service_source
    assert "제한까지 최대" in service_source
    assert "남은 최대 상한" not in service_source


def test_gemini_model_catalog_and_purpose_selection_are_wired() -> None:
    app_source = (PROJECT_ROOT / "app.py").read_text(encoding="utf-8")
    refresh_source = (PROJECT_ROOT / "scripts" / "refresh_trends.py").read_text(
        encoding="utf-8"
    )
    service_source = (
        PROJECT_ROOT / "src" / "services" / "gemini_model_service.py"
    ).read_text(encoding="utf-8")

    assert "def _render_auto_analysis_model_selector" in app_source
    assert "def _render_gemini_model_settings" in app_source
    assert "모델 목록 새로고침" in app_source
    assert "MODEL_PURPOSE_AUTO" in app_source
    assert "MODEL_PURPOSE_DATA_REVIEW" in app_source
    assert "build_gemini_config_for_purpose" in refresh_source
    assert "limit=items_per_request" in refresh_source
    assert "items_per_request = max(1, int(config.topic_angle_batch_limit))" in refresh_source
    assert "BACKGROUND_TOPIC_ANGLE_ITEMS_PER_REQUEST" not in refresh_source
    assert "topic_angle_batch_limit=BACKGROUND_TOPIC_ANGLE_ITEMS_PER_REQUEST" not in refresh_source
    assert 'GEMINI_MODELS_URL = "https://generativelanguage.googleapis.com/v1beta/models"' in service_source
    assert '"generateContent" not in methods' in service_source
    assert 'headers={"x-goog-api-key": key}' in service_source


def test_model_catalog_network_request_precedes_settings_db_connection() -> None:
    source = (PROJECT_ROOT / "app.py").read_text(encoding="utf-8")
    section = source.split("def render_settings() -> None:", 1)[1]

    assert section.index("fetch_gemini_model_catalog(") < section.index(
        "with db_connection() as con:"
    )


def test_completed_v5_profiles_are_not_mass_reprocessed() -> None:
    source = (PROJECT_ROOT / "src" / "services" / "topic_angle_ai_service.py").read_text(
        encoding="utf-8"
    )
    missing_section = source.split("def _missing_clusters(", 1)[1].split(
        "def _build_request(", 1
    )[0]
    assert "tcp.feature_version" not in missing_section
    assert "content_plan_json" in missing_section


def test_new_install_defaults_use_15_items_and_180_minutes() -> None:
    config_source = (PROJECT_ROOT / "src" / "config.py").read_text(encoding="utf-8")
    app_source = (PROJECT_ROOT / "app.py").read_text(encoding="utf-8")
    assert "BACKGROUND_TOPIC_ANGLE_ITEMS_PER_REQUEST = 15" in config_source
    assert '"trend_refresh_interval_minutes": "180"' in config_source
    assert "180으로 설정하면 3시간마다 실행됩니다." in app_source
