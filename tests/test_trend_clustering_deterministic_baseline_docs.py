from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_deterministic_baseline_docs_keep_read_only_adoption_gate() -> None:
    baseline = (PROJECT_ROOT / "docs" / "DETERMINISTIC_CLUSTERING_BASELINE.md").read_text(
        encoding="utf-8"
    )
    next_work = (PROJECT_ROOT / "docs" / "NEXT_WORK.md").read_text(encoding="utf-8")

    assert "trend_clustering.deterministic_baseline" in baseline
    assert "read_only=True" in baseline
    assert "quality_sample_unreliable" in baseline
    assert "comparison_complete=false" in baseline
    assert "정답률이 아니다" in baseline
    assert "ambiguous-only Gemini" in baseline
    assert "현재 군집 결과, Gemini 호출 방식, 설정, DB 스키마를 변경하지 않는다" in baseline
    assert "deterministic baseline" in next_work
    assert "생산 군집에 채택하지 않았다" in next_work
