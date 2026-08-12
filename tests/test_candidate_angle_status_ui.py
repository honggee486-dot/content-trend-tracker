import pandas as pd

from src.config import BACKGROUND_TOPIC_ANGLE_ITEMS_PER_REQUEST, get_gemini_config
from src.ui import (
    _CANDIDATE_ANGLE_STATUS_CSS,
    _decorate_ranked_trends_with_angle_state,
    _install_candidate_angle_status_ui,
    _install_gemini_capacity_caption_ui,
    _rewrite_gemini_capacity_caption,
)


class _FakeQueryResult:
    def __init__(self, rows):
        self._rows = rows

    def fetchall(self):
        return self._rows


class _FakeConnection:
    def __init__(self, rows):
        self.rows = rows
        self.parameters = None

    def execute(self, _query, parameters):
        self.parameters = list(parameters)
        return _FakeQueryResult(self.rows)


class _FakeStreamlit:
    def __init__(self) -> None:
        self.captions: list[str] = []

    def caption(self, value: object, *_args, **_kwargs) -> None:
        self.captions.append(str(value))


def _rankings() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {"cluster_id": "cluster-ready", "판정": "추천", "주제": "완료 글감"},
            {"cluster_id": "cluster-partial", "판정": "검토", "주제": "부분 글감"},
            {"cluster_id": "cluster-empty", "판정": "보류", "주제": "대기 글감"},
        ]
    )


def test_topic_angle_request_defaults_to_15_and_is_capped_at_30(monkeypatch):
    monkeypatch.setenv("GEMINI_TOPIC_ANGLE_ITEMS_PER_REQUEST", "60")

    config = get_gemini_config()

    assert BACKGROUND_TOPIC_ANGLE_ITEMS_PER_REQUEST == 15
    assert config.topic_angle_batch_limit == 30


def test_candidate_rows_are_marked_only_after_three_angles():
    con = _FakeConnection(
        [
            ("cluster-ready", 3),
            ("cluster-partial", 2),
        ]
    )

    decorated = _decorate_ranked_trends_with_angle_state(con, _rankings())

    assert con.parameters == ["cluster-ready", "cluster-partial", "cluster-empty"]
    assert decorated["판정"].tolist() == [
        "추천 ai-ready",
        "검토 ai-pending",
        "보류 ai-pending",
    ]


def test_candidate_angle_wrapper_preserves_existing_rank_function():
    con = _FakeConnection([("cluster-ready", 3)])
    calls = []

    def original(connection, *, limit):
        calls.append((connection, limit))
        return _rankings().iloc[:1].copy()

    caller_globals = {"list_ranked_trends": original}
    _install_candidate_angle_status_ui(caller_globals)
    wrapped = caller_globals["list_ranked_trends"]

    result = wrapped(con, limit=10)

    assert calls == [(con, 10)]
    assert result.iloc[0]["판정"] == "추천 ai-ready"
    assert getattr(wrapped, "_candidate_angle_status_wrapper") is True


def test_candidate_angle_css_contains_ready_pending_and_legend_rules():
    assert "방향 API 저장 완료" in _CANDIDATE_ANGLE_STATUS_CSS
    assert ".status-tag.ai-ready" in _CANDIDATE_ANGLE_STATUS_CSS
    assert ".status-tag.ai-pending" in _CANDIDATE_ANGLE_STATUS_CSS
    assert ":has(.status-tag.ai-ready)" in _CANDIDATE_ANGLE_STATUS_CSS
    assert ":has(.status-tag.ai-pending)" in _CANDIDATE_ANGLE_STATUS_CSS


def test_gemini_capacity_caption_replaces_legacy_fixed_count_and_restores_streamlit():
    original_text = (
        "현재 자동 분석: 요청당 최대 30개 · 동시 요청 최대 1개 · "
        "버튼 1회 최대 30개. 기본 구성은 100개를 1회 요청으로 처리합니다."
    )
    rewritten = _rewrite_gemini_capacity_caption(original_text)

    assert "기본 구성은 100개" not in rewritten
    assert "현재 설정값 기준으로 위 버튼 1회 최대치가 적용됩니다." in rewritten

    fake_st = _FakeStreamlit()
    caller_globals: dict[str, object] = {"st": fake_st}

    def original_settings_renderer(_con) -> None:
        caller_globals["st"].caption(original_text)

    caller_globals["_render_gemini_model_settings"] = original_settings_renderer
    _install_gemini_capacity_caption_ui(caller_globals)
    wrapped = caller_globals["_render_gemini_model_settings"]

    wrapped(object())

    assert caller_globals["st"] is fake_st
    assert fake_st.captions == [rewritten]
    assert getattr(wrapped, "_gemini_capacity_caption_wrapper") is True
