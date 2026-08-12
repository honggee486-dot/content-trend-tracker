from __future__ import annotations

from types import SimpleNamespace

from src.topic_angle_status_ui import (
    explain_topic_angle_status,
    format_runtime_batch_caption,
    install_topic_angle_status_explainer,
)


def _config(**overrides):
    values = {
        "api_key": "test-key",
        "topic_angle_min_opportunity_score": 50.0,
        "topic_angle_batch_limit": 15,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _cluster(**overrides):
    values = {
        "canonical_title": "자동 방향 상태 확인",
        "opportunity_score": 72.0,
        "recommendation_status": "recommended",
    }
    values.update(overrides)
    return values


def _items():
    return [
        {
            "source_item_id": "source_1",
            "source_type": "naver_news",
            "raw_title": "자동 방향 상태 확인 관련 공개 자료",
            "source_name": "테스트뉴스",
            "observed_at": "2026-08-02 06:00:00",
            "observation_count": 1,
            "metadata": {"item_title": "자동 방향 상태 확인 관련 공개 자료"},
        }
    ]


def test_explains_eligible_cluster_as_waiting() -> None:
    result = explain_topic_angle_status(
        cluster=_cluster(),
        items=_items(),
        stored_angle_count=0,
        config=_config(),
    )

    assert result.state == "pending"
    assert result.status_text == "Gemini 자동 방향 · 생성 대기 중 0/3"
    assert "자동 생성 조건을 모두 충족" in result.caption_text
    assert "실행당 최대 15개" in result.caption_text
    assert result.blockers == ()


def test_explains_all_unmet_generation_conditions() -> None:
    result = explain_topic_angle_status(
        cluster=_cluster(
            canonical_title="",
            opportunity_score=42.0,
            recommendation_status="hold",
        ),
        items=[],
        stored_angle_count=0,
        config=_config(api_key=""),
    )

    assert result.state == "blocked"
    assert result.status_text == "Gemini 자동 방향 · 조건 미충족 0/3"
    assert "Gemini API 키" in result.caption_text
    assert "42.0점" in result.caption_text
    assert "보류" in result.caption_text
    assert "유효한 원문 근거" in result.caption_text
    assert len(result.blockers) == 5


def test_explains_partial_result_as_regeneration_waiting() -> None:
    result = explain_topic_angle_status(
        cluster=_cluster(),
        items=_items(),
        stored_angle_count=2,
        config=_config(topic_angle_batch_limit=10),
    )

    assert result.state == "pending"
    assert result.status_text == "Gemini 자동 방향 · 재생성 대기 중 2/3"
    assert "실행당 최대 10개" in result.caption_text


def test_runtime_batch_caption_uses_current_configured_limit() -> None:
    original = (
        "자동·예약 분석 모델: gemini-3.6-flash · "
        "실행당 새 분석 대상 상위 15개 · 3시간 주기라면 하루 약 8회 실행됩니다."
    )

    rendered = format_runtime_batch_caption(
        original,
        config=_config(topic_angle_batch_limit=20),
    )

    assert "실행당 새 분석 대상 상위 20개" in rendered
    assert "상위 15개" not in rendered


class _FakeStreamlit:
    def __init__(self) -> None:
        self.warnings: list[str] = []
        self.captions: list[str] = []

    def warning(self, value, *args, **kwargs):
        self.warnings.append(str(value))

    def caption(self, value, *args, **kwargs):
        self.captions.append(str(value))


def test_installer_replaces_generic_detail_message() -> None:
    fake = _FakeStreamlit()
    install_topic_angle_status_explainer(fake)

    def render_like_trend_detail() -> None:
        cluster = _cluster()
        items = _items()
        stored_angle_count = 0
        gemini_config = _config()
        fake.warning("Gemini 자동 방향 · 미생성 0/3", icon="pending")
        fake.caption(
            "자동 생성된 분석 정보가 없습니다. ‘주제 방향 자동 생성’을 실행하세요."
        )

    render_like_trend_detail()

    assert fake.warnings == ["Gemini 자동 방향 · 생성 대기 중 0/3"]
    assert len(fake.captions) == 1
    assert "자동 생성 조건을 모두 충족" in fake.captions[0]


def test_installer_rewrites_fixed_model_caption_from_environment(
    monkeypatch,
) -> None:
    monkeypatch.setenv("GEMINI_TOPIC_ANGLE_ITEMS_PER_REQUEST", "20")
    fake = _FakeStreamlit()
    install_topic_angle_status_explainer(fake)

    fake.caption(
        "자동·예약 분석 모델: gemini-3.6-flash · "
        "실행당 새 분석 대상 상위 15개 · 3시간 주기라면 하루 약 8회 실행됩니다."
    )

    assert len(fake.captions) == 1
    assert "실행당 새 분석 대상 상위 20개" in fake.captions[0]
