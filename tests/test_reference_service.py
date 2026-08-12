from pathlib import Path

import pytest

from src.database import connect_database, init_database
from src.services.reference_service import (
    add_topic_reference,
    archive_topic_reference,
    list_topic_references,
    update_topic_reference,
)
from src.services.topic_service import add_manual_topic


def test_reference_crud_and_archive(tmp_path: Path) -> None:
    db_path = tmp_path / "main.duckdb"
    init_database(db_path)

    with connect_database(db_path) as con:
        topic_id, _ = add_manual_topic(con, title="전기요금 제도")
        reference_id, created = add_topic_reference(
            con,
            topic_id=topic_id,
            reference_type="official_agency",
            title="전기요금 안내",
            publisher="한국전력공사",
            url="https://example.com/power/",
            published_at="2026-07-14",
            memo="요금 기준 확인",
        )
        assert created

        references = list_topic_references(con, topic_id)
        assert len(references) == 1
        assert references[0]["reference_id"] == reference_id
        assert references[0]["reference_type_label"] == "공식 기관"

        update_topic_reference(
            con,
            reference_id=reference_id,
            reference_type="public_data",
            title="전기요금 공개 자료",
            publisher="공공데이터포털",
            url="https://example.com/power-data",
            published_at="2026년 7월",
            memo="통계 표 확인",
        )
        updated = list_topic_references(con, topic_id)[0]
        assert updated["title"] == "전기요금 공개 자료"
        assert updated["reference_type_label"] == "공공데이터"

        archive_topic_reference(con, reference_id)
        assert list_topic_references(con, topic_id) == []
        assert len(list_topic_references(con, topic_id, include_archived=True)) == 1


def test_same_url_updates_existing_reference(tmp_path: Path) -> None:
    db_path = tmp_path / "main.duckdb"
    init_database(db_path)

    with connect_database(db_path) as con:
        topic_id, _ = add_manual_topic(con, title="지원금")
        reference_id, created = add_topic_reference(
            con,
            topic_id=topic_id,
            reference_type="official_agency",
            title="기존 제목",
            url="https://example.com/support/",
        )
        assert created

        same_id, created_again = add_topic_reference(
            con,
            topic_id=topic_id,
            reference_type="official_agency",
            title="갱신 제목",
            url="https://example.com/support",
        )
        assert not created_again
        assert same_id == reference_id
        assert list_topic_references(con, topic_id)[0]["title"] == "갱신 제목"


def test_reference_requires_valid_url(tmp_path: Path) -> None:
    db_path = tmp_path / "main.duckdb"
    init_database(db_path)

    with connect_database(db_path) as con:
        topic_id, _ = add_manual_topic(con, title="테스트")
        with pytest.raises(ValueError, match="http"):
            add_topic_reference(
                con,
                topic_id=topic_id,
                reference_type="user_reference",
                title="잘못된 주소",
                url="example.com/no-scheme",
            )
