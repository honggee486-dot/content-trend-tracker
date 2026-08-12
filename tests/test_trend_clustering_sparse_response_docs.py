from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_docs_record_mutually_exclusive_sparse_response_contract() -> None:
    next_work = (PROJECT_ROOT / "docs" / "NEXT_WORK.md").read_text(encoding="utf-8")
    runtime = (PROJECT_ROOT / "docs" / "TREND_CLUSTERING_RUNTIME.md").read_text(
        encoding="utf-8"
    )

    assert "`duplicate_candidate_no=24`" in next_work
    assert "`existing_links`·`new_groups`·`uncertain_nos`" in next_work
    assert "상호 배타" in next_work
    assert "`representative_candidate_no`" in next_work
    assert "입력 제목 문자열 대신" in next_work
    assert "서로 배타적" in runtime
    assert "`representative_candidate_no`" in runtime
    assert "입력 `title`·`examples`·`existing_options` 문자열의 반복 출력만 제거" in runtime
    assert "대표 제목 필드와 품질 계약은 유지" in runtime
