from __future__ import annotations

import json
from pathlib import Path

from src.database import connect_database, init_database
from src.services.content_pack_service import (
    get_topic_content_defaults,
    link_topic_to_trend_cluster,
)
from src.services.content_pack_writing_mode_runtime import (
    render_writing_mode_recommendation,
)
from src.services.topic_angle_writing_mode_runtime import (
    WRITING_MODE_AUTO,
    WRITING_MODE_MANUAL,
    writing_mode_from_plan,
)
from src.services.topic_service import add_manual_topic
from src.services import topic_angle_ai_service


def _insert_profile(con, cluster_id: str, plan: dict) -> None:
    con.execute(
        """
        INSERT INTO trend_cluster_ai_profiles(
            cluster_id, canonical_title, display_title, summary,
            verification_points_json, content_plan_json,
            model_name, feature_version, created_at, updated_at
        ) VALUES (?, '원본 제목', '표시 제목', '요약', '[]', ?,
                  'test-model', '6', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
        """,
        [cluster_id, json.dumps(plan, ensure_ascii=False)],
    )


def test_topic_angle_schema_requires_auto_or_manual_recommendation() -> None:
    plan_schema = (
        topic_angle_ai_service.TOPIC_ANGLE_SCHEMA["properties"]["clusters"]["items"][
            "properties"
        ]["content_plan"]
    )
    assert plan_schema["properties"]["writing_mode_recommendation"]["enum"] == [
        "auto",
        "manual",
    ]
    assert "writing_mode_recommendation" in plan_schema["required"]
    assert "writing_mode_reason" in plan_schema["required"]


def test_ambiguous_or_missing_mode_defaults_to_manual() -> None:
    assert writing_mode_from_plan({})[0] == WRITING_MODE_MANUAL
    assert writing_mode_from_plan({"writing_mode_recommendation": "unknown"})[0] == WRITING_MODE_MANUAL
    assert writing_mode_from_plan(
        {"writing_mode_recommendation": "auto", "writing_mode_reason": ""}
    )[0] == WRITING_MODE_MANUAL


def test_explicit_auto_mode_is_preserved_with_reason() -> None:
    mode, reason = writing_mode_from_plan(
        {
            "writing_mode_recommendation": "auto",
            "writing_mode_reason": "일반 정보성 주제로 최신성 의존과 사실 검증 부담이 낮습니다.",
        }
    )
    assert mode == WRITING_MODE_AUTO
    assert "사실 검증 부담" in reason


def test_content_defaults_include_topic_angle_writing_mode(tmp_path: Path) -> None:
    db_path = tmp_path / "writing-mode.duckdb"
    init_database(db_path)
    with connect_database(db_path) as con:
        topic_id, _ = add_manual_topic(
            con,
            title="에어컨 전기요금 절약 원리",
            summary="일반 생활 정보",
            category="생활",
            memo="",
            priority=2,
        )
        cluster_id = "trend_writing_mode"
        link_topic_to_trend_cluster(con, topic_id=topic_id, cluster_id=cluster_id)
        _insert_profile(
            con,
            cluster_id,
            {
                "audience": "일반 독자",
                "purpose": "생활 정보를 이해하기 쉽게 설명",
                "category": "생활",
                "target_length": 2200,
                "title_rules": ["과장 금지", "본문 일치"],
                "outline": ["도입", "핵심", "방법", "주의", "정리"],
                "forbidden_expressions": ["무조건", "100%", "보장"],
                "timeliness": {},
                "evidence_plan": {},
                "primary_direction_reason": "검증 점수 1순위",
                "writing_mode_recommendation": "auto",
                "writing_mode_reason": "일반 정보성 주제로 자동 작성에 적합합니다.",
            },
        )
        defaults = get_topic_content_defaults(
            con,
            topic_id=topic_id,
            default_audience="기본 독자",
            default_purpose="기본 목적",
        )

    assert defaults["writing_mode_recommendation"] == "auto"
    assert defaults["writing_mode_reason"] == "일반 정보성 주제로 자동 작성에 적합합니다."
    assert defaults["writing_mode_source"] == "topic_angle_ai"
    assert defaults["primary_direction_reason"] == "검증 점수 1순위"


def test_legacy_content_defaults_are_manual_safe_default(tmp_path: Path) -> None:
    db_path = tmp_path / "legacy-writing-mode.duckdb"
    init_database(db_path)
    with connect_database(db_path) as con:
        topic_id, _ = add_manual_topic(
            con,
            title="기존 저장 주제",
            summary="이전 버전 분석",
            category="일반",
            memo="",
            priority=2,
        )
        cluster_id = "trend_legacy_mode"
        link_topic_to_trend_cluster(con, topic_id=topic_id, cluster_id=cluster_id)
        _insert_profile(
            con,
            cluster_id,
            {
                "audience": "일반 독자",
                "purpose": "설명",
                "category": "일반",
                "target_length": 2000,
            },
        )
        defaults = get_topic_content_defaults(
            con,
            topic_id=topic_id,
            default_audience="기본 독자",
            default_purpose="기본 목적",
        )

    assert defaults["writing_mode_recommendation"] == "manual"
    assert defaults["writing_mode_source"] == "legacy_safe_default"


class _FakeStreamlit:
    def __init__(self) -> None:
        self.session_state: dict[str, object] = {}
        self.messages: list[tuple[str, str]] = []

    def success(self, text, **kwargs):
        self.messages.append(("success", str(text)))

    def warning(self, text, **kwargs):
        self.messages.append(("warning", str(text)))

    def caption(self, text):
        self.messages.append(("caption", str(text)))

    def radio(self, label, options, *, index=0, key=None, **kwargs):
        assert key is not None
        current = self.session_state.get(key, options[index])
        self.session_state[key] = current
        return current


def test_ui_defaults_to_recommended_mode_and_allows_override() -> None:
    st = _FakeStreamlit()
    defaults = {
        "writing_mode_recommendation": "auto",
        "writing_mode_reason": "검증 부담이 낮습니다.",
        "writing_mode_source": "topic_angle_ai",
        "primary_direction_reason": "검증 점수 1순위입니다.",
    }
    selected = render_writing_mode_recommendation(
        defaults,
        topic_id="topic-1",
        st_module=st,
    )
    assert selected == "auto"
    assert ("success", "작성 방식 추천: 자동 작성") in st.messages

    st.session_state["content_pack_writing_mode_choice_topic-1"] = "manual"
    selected = render_writing_mode_recommendation(
        defaults,
        topic_id="topic-1",
        st_module=st,
    )
    assert selected == "manual"
    assert any("추천과 다른 방식" in text for level, text in st.messages if level == "caption")
