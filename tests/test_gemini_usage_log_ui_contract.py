from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_settings_exposes_model_and_feature_usage_logs() -> None:
    ui_source = (PROJECT_ROOT / "src" / "gemini_stability_ui.py").read_text(
        encoding="utf-8"
    )
    wrapper_source = (PROJECT_ROOT / "src" / "database_backup_ui.py").read_text(
        encoding="utf-8"
    )
    service_source = (
        PROJECT_ROOT / "src" / "services" / "gemini_usage_log_service.py"
    ).read_text(encoding="utf-8")

    assert "Gemini API 모델·기능별 사용 로그" in ui_source
    assert "3.6 Flash 호출" in ui_source
    assert "AI 군집화 호출" in ui_source
    assert "Google AI Studio의 공식 남은 RPM·RPD·결제 사용량" in ui_source
    assert "render_gemini_usage_log_panel" in wrapper_source
    assert "trend_topic_angle_batch_v1" in service_source
    assert "trend_cluster_grouping_v3" in service_source
    assert "trend_cluster_review_v1" in service_source
    assert "cache_hit = FALSE" in service_source
