from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace

from src.database import init_database


class _FakeStreamlit:
    def __init__(self) -> None:
        self.captions: list[str] = []
        self.metric_help: list[str] = []
        self.notices: list[tuple[str, str]] = []
        self.markdown_texts: list[str] = []

    def caption(self, value: object, *_args, **_kwargs) -> None:
        self.captions.append(str(value))

    def expander(self, *_args, **_kwargs):
        return nullcontext()

    def columns(self, count: int):
        return [SimpleNamespace(metric=self._record_metric) for _ in range(count)]

    def _record_metric(self, *_args, help=None, **_kwargs) -> None:
        if help is not None:
            self.metric_help.append(str(help))

    def info(self, value: object, *_args, **_kwargs) -> None:
        self.notices.append(("info", str(value)))

    def warning(self, value: object, *_args, **_kwargs) -> None:
        self.notices.append(("warning", str(value)))

    def success(self, value: object, *_args, **_kwargs) -> None:
        self.notices.append(("success", str(value)))

    def markdown(self, value: object, *_args, **_kwargs) -> None:
        self.markdown_texts.append(str(value))

    def dataframe(self, *_args, **_kwargs) -> None:
        pass

    def divider(self, *_args, **_kwargs) -> None:
        pass


def test_stability_panel_copy_uses_current_runtime_settings(monkeypatch) -> None:
    import src.gemini_stability_ui as stability_ui

    run_window = SimpleNamespace(
        run_count=5,
        requested_clusters=125,
        generated_clusters=125,
        save_rate=1.0,
        partial_runs=0,
        failed_runs=0,
        request_count=5,
        retry_count=0,
        average_duration_ms=10_000,
    )
    calls = SimpleNamespace(
        validation_failure_count=0,
        attempt_count=5,
        average_generation_tokens=20_000,
        maximum_generation_tokens=22_000,
        near_limit_count=0,
        max_tokens_count=1,
        recorded_requested_item_count=5,
        average_requested_item_count=25.0,
        maximum_requested_item_count=25,
        thinking_level_counts=(("high", 5),),
        finish_reason_counts=(("MAX_TOKENS", 1), ("STOP", 4)),
        missing_finish_reason_count=0,
        rate_limit_affected_request_count=2,
        retry_recovered_request_count=1,
        rate_limited_final_request_count=1,
        ungrouped_retry_attempt_count=0,
        retrying_attempt_count=3,
        rate_limit_attempt_count=3,
        quota_exhausted_count=0,
        timeout_count=0,
        network_error_count=0,
        server_error_count=0,
        invalid_request_count=0,
        other_failure_count=0,
        retry_wait_total_seconds=4.0,
        retry_wait_average_seconds=2.0,
        retry_wait_max_seconds=2.0,
    )
    recommendation = SimpleNamespace(
        evaluation_status="유지 권장",
        current_items_per_request=25,
        recommended_items_per_request=25,
        recommendation_label="25개 유지",
        thinking_recommendation="high 유지",
        reasons=("안정적입니다.",),
        recent_10=run_window,
        recent_30=run_window,
        calls=calls,
    )
    captured: dict[str, object] = {}

    def fake_recommendation(
        _con,
        *,
        app_id,
        current_items_per_request,
        current_thinking_level,
    ):
        captured.update(
            app_id=app_id,
            current_items=current_items_per_request,
            current_thinking=current_thinking_level,
        )
        return recommendation

    fake_st = _FakeStreamlit()
    monkeypatch.setattr(
        stability_ui,
        "get_gemini_stability_recommendation",
        fake_recommendation,
    )

    stability_ui.render_gemini_stability_panel(
        object(),
        app_id="stability-ui-app",
        current_items_per_request=25,
        current_thinking_level="high",
        st_module=fake_st,
    )

    assert captured == {
        "app_id": "stability-ui-app",
        "current_items": 25,
        "current_thinking": "high",
    }
    assert "현재 요청당 25개" in fake_st.metric_help[0]
    assert fake_st.notices == [
        ("success", "현재 요청당 25개 설정을 유지해도 되는 기록 상태입니다.")
    ]
    assert any("오류·재시도 세부 현황" in text for text in fake_st.markdown_texts)
    assert any("사고 수준 추천: high 유지" in text for text in fake_st.captions)
    assert any("실제 요청 글감 수 기록 5회" in text for text in fake_st.captions)
    assert any("MAX_TOKENS 1회" in text for text in fake_st.captions)



def test_database_settings_opens_stability_data_read_only(
    tmp_path: Path,
    monkeypatch,
) -> None:
    import src.database_backup_ui as backup_ui

    db_path = tmp_path / "stability-ui.duckdb"
    init_database(db_path)
    captured: dict[str, object] = {}

    def fake_panel(
        con,
        *,
        app_id,
        current_items_per_request,
        current_thinking_level,
        st_module,
    ) -> None:
        captured["select_one"] = int(con.execute("SELECT 1").fetchone()[0])
        captured["app_id"] = app_id
        captured["current_items"] = current_items_per_request
        captured["current_thinking"] = current_thinking_level
        captured["st_module"] = st_module

    fake_st = _FakeStreamlit()
    monkeypatch.setattr(backup_ui, "DEFAULT_DB_PATH", db_path)
    monkeypatch.setattr(
        backup_ui,
        "get_gemini_config",
        lambda: SimpleNamespace(
            app_id="stability-ui-app",
            topic_angle_batch_limit=25,
            topic_angle_thinking_level="high",
        ),
    )
    monkeypatch.setattr(backup_ui, "render_gemini_stability_panel", fake_panel)
    monkeypatch.setattr(
        backup_ui,
        "render_gemini_usage_log_panel",
        lambda *_args, **_kwargs: None,
    )

    backup_ui._render_gemini_stability(st_module=fake_st)

    assert captured == {
        "select_one": 1,
        "app_id": "stability-ui-app",
        "current_items": 25,
        "current_thinking": "high",
        "st_module": fake_st,
    }
    assert fake_st.captions == []


def test_database_settings_isolates_stability_panel_errors(
    tmp_path: Path,
    monkeypatch,
) -> None:
    import src.database_backup_ui as backup_ui

    db_path = tmp_path / "stability-ui-error.duckdb"
    init_database(db_path)
    fake_st = _FakeStreamlit()

    monkeypatch.setattr(backup_ui, "DEFAULT_DB_PATH", db_path)
    monkeypatch.setattr(
        backup_ui,
        "get_gemini_config",
        lambda: SimpleNamespace(
            app_id="stability-ui-app",
            topic_angle_batch_limit=25,
            topic_angle_thinking_level="high",
        ),
    )
    monkeypatch.setattr(
        backup_ui,
        "render_gemini_stability_panel",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("test failure")),
    )

    backup_ui._render_gemini_stability(st_module=fake_st)

    assert fake_st.captions == [
        "Gemini 진단과 사용 로그를 불러오지 못했습니다: test failure"
    ]



def test_quality_diagnostics_reuse_existing_app_connection(monkeypatch) -> None:
    import src.database_backup_ui as backup_ui

    active_con = object()
    captured: list[tuple[str, object]] = []
    fake_st = _FakeStreamlit()

    monkeypatch.setattr(
        backup_ui,
        "get_gemini_config",
        lambda: SimpleNamespace(
            app_id="diagnostic-reuse-app",
            topic_angle_batch_limit=15,
            topic_angle_thinking_level="medium",
        ),
    )

    def unexpected_connect(*_args, **_kwargs):
        raise AssertionError("기존 앱 연결이 있는데 새 DuckDB 연결을 열면 안 됩니다.")

    monkeypatch.setattr(backup_ui, "connect_database", unexpected_connect)
    monkeypatch.setattr(
        backup_ui,
        "render_gemini_stability_panel",
        lambda con, **_kwargs: captured.append(("stability", con)),
    )
    monkeypatch.setattr(
        backup_ui,
        "render_gemini_usage_log_panel",
        lambda con, **_kwargs: captured.append(("usage", con)),
    )
    monkeypatch.setattr(
        backup_ui,
        "render_source_diversity_panel",
        lambda con, **_kwargs: captured.append(("diversity", con)),
    )

    backup_ui.render_quality_diagnostic_panels(active_con, st_module=fake_st)

    assert captured == [
        ("stability", active_con),
        ("usage", active_con),
        ("diversity", active_con),
    ]
    assert fake_st.captions == []
