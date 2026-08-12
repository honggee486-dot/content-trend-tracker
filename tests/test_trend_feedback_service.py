from datetime import datetime, timedelta
from pathlib import Path

from src.database import connect_database, init_database
from src.services.trend_feedback_service import (
    build_cluster_diagnostics,
    clear_trend_feedback,
    get_trend_feedback,
    get_trend_feedback_summary,
    list_trend_feedback_map,
    save_trend_feedback,
)


def _item(
    source_item_id: str,
    title: str,
    *,
    url: str,
    source_type: str = "naver_news",
    source_name: str = "뉴스A",
    hours_ago: int = 1,
):
    observed_at = datetime.now() - timedelta(hours=hours_ago)
    return {
        "source_item_id": source_item_id,
        "source_type": source_type,
        "raw_title": title,
        "source_url": url,
        "normalized_url": "",
        "source_name": source_name,
        "published_at": observed_at,
        "observed_at": observed_at,
        "metadata": {"item_title": title},
    }


def test_cluster_diagnostics_deduplicates_urls_and_explains_repeated_subject() -> None:
    items = [
        _item(
            "one",
            "GPT-5.6 새 기능 공개",
            url="https://example.com/a?utm_source=x",
            source_name="뉴스A",
        ),
        _item(
            "one-copy",
            "GPT-5.6 새 기능 공개",
            url="https://example.com/a?utm_source=y",
            source_name="뉴스A",
        ),
        _item(
            "two",
            "GPT-5.6 기능 업데이트 분석",
            url="https://example.org/b",
            source_type="naver_blog",
            source_name="블로그B",
            hours_ago=2,
        ),
    ]

    diagnostics = build_cluster_diagnostics(
        {"cluster_id": "trend_1", "canonical_title": "GPT-5.6 업데이트"},
        items,
    )

    assert diagnostics["raw_item_count"] == 3
    assert diagnostics["unique_evidence_count"] == 2
    assert diagnostics["duplicate_count"] == 1
    assert diagnostics["publisher_count"] == 2
    assert diagnostics["source_type_count"] == 2
    assert "gpt-5.6" in diagnostics["repeated_terms"]
    assert "원문 2건" in diagnostics["binding_reason"]


def test_cluster_diagnostics_warns_when_single_item_has_no_independent_support() -> None:
    diagnostics = build_cluster_diagnostics(
        {"cluster_id": "trend_single", "canonical_title": "단일 후보"},
        [
            _item(
                "single",
                "단일 후보",
                url="https://example.com/single",
            )
        ],
    )

    assert diagnostics["unique_evidence_count"] == 1
    assert diagnostics["warnings"]
    assert any("1건 이하" in warning for warning in diagnostics["warnings"])


def test_feedback_is_upserted_summarized_and_cleared(tmp_path: Path) -> None:
    db_path = tmp_path / "feedback.duckdb"
    init_database(db_path)
    diagnostics = {
        "raw_item_count": 4,
        "unique_evidence_count": 3,
        "source_type_count": 2,
        "publisher_count": 3,
    }

    with connect_database(db_path) as con:
        first = save_trend_feedback(
            con,
            cluster_id="trend_feedback",
            canonical_title="테스트 글감",
            feedback_type="ambiguous",
            note="근거가 조금 약함",
            diagnostics=diagnostics,
        )
        second = save_trend_feedback(
            con,
            cluster_id="trend_feedback",
            canonical_title="테스트 글감 수정",
            feedback_type="good",
            note="원문 확인 완료",
            diagnostics=diagnostics,
        )

        assert first["feedback_id"] == second["feedback_id"]
        assert second["feedback_type"] == "good"
        assert second["canonical_title"] == "테스트 글감 수정"
        assert get_trend_feedback(con, "trend_feedback")["note"] == "원문 확인 완료"
        assert list_trend_feedback_map(con, ["trend_feedback"])["trend_feedback"]["feedback_type"] == "good"
        assert get_trend_feedback_summary(con)["good"] == 1
        assert clear_trend_feedback(con, "trend_feedback") is True
        assert get_trend_feedback(con, "trend_feedback") is None
        assert clear_trend_feedback(con, "trend_feedback") is False
