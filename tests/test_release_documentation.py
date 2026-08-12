from __future__ import annotations

from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _read(path: str) -> str:
    return (PROJECT_ROOT / path).read_text(encoding="utf-8")


def test_release_0_10_109_metadata_is_aligned() -> None:
    version = _read("VERSION").strip()
    changelog = _read("CHANGELOG.md")
    release_note = _read("docs/releases/0.10.109.md")
    context = _read("AI_CONTEXT.md")
    next_work = _read("docs/NEXT_WORK.md")
    ci = _read(".github/workflows/ci.yml")

    assert version == "0.10.109"
    assert changelog.startswith("## 0.10.109 - 2026-08-12\n")
    assert release_note.startswith("# 0.10.109 진단 신뢰성과 하네스 라우팅 안정화\n")
    assert "deterministic baseline" in release_note
    assert "`behind 0`, `ahead 1`" in release_note
    assert "현재 문서 기준 버전: `0.10.109`" in context
    assert "## 0.10.109 릴리스 범위" in next_work
    assert "workflow_dispatch:" in ci
    assert "contents: read" in ci
    assert "Public tracked-tree safety check" in ci


def test_release_0_10_107_changelog_and_note_are_aligned() -> None:
    changelog = _read("CHANGELOG.md")
    assert "## 0.10.107 - 2026-08-11\n" in changelog
    assert changelog.index("## 0.10.108") < changelog.index("## 0.10.107")
    assert changelog.index("## 0.10.107") < changelog.index("## 0.10.106")
    assert "representative_candidate_no" in changelog
    assert "tpm_wait_seconds" in changelog
    assert "Public 전환 준비" in changelog

    release_note = _read("docs/releases/0.10.107.md")
    assert release_note.startswith("# Release Bootstrap 0.10.107\n")
    assert "누락·검증 탈락 ID" in release_note
    assert "희소 응답 계약" in release_note
    assert "결정론적 군집 baseline" in release_note
    assert "Gitleaks" in release_note
    assert "`behind 0`, `ahead 1`" in release_note
    assert "실제 DB" in release_note

    historical_note = _read("docs/releases/0.10.106.md")
    assert historical_note.startswith("# 0.10.106 운영 진단 신뢰성과 군집 품질 안정화\n")
    assert "trend_lookback_hours" in historical_note
    assert "reconstruction_reliable" in historical_note


def test_ai_context_keeps_0_10_107_runtime_contract() -> None:
    context = _read("AI_CONTEXT.md")

    assert "- 현재 문서 기준 버전: `0.10.109`" in context
    assert "## 5. 0.10.107 운영·진단 계약" in context
    assert "누락·검증 탈락 ID만 최대 한 번 보강" in context
    assert "희소 응답 상호 배타·대표 후보 계약" in context
    assert "결정론적 군집 baseline 읽기 전용 비교 진단" in context
    assert "로컬 TPM 대기와 공급자 제한 분리 진단" in context
    assert "`trend_lookback_hours` 최근 시간 범위 전체" in context
    assert "`50,000`은 Gemini 요청당 후보 수가 아니라" in context
    assert "reconstruction_reliable=true" in context
    assert "`특징`, `선택`, `포인트`, `총정리`" in context
    assert "관리형 Streamlit 주소: `http://127.0.0.1:8518`" in context
    assert "`pwsh.exe`를 우선" in context
    assert "Windows PowerShell 5.1" in context
    assert "기존 DuckDB 데이터, 설정, 자료팩, 초안, 리비전" in context


def test_docs_keep_two_stage_grouping_and_token_logs() -> None:
    readme = _read("README.md")
    assert "## 1차 정리·Flash-Lite 2차 군집화" in readme
    assert "같은 정규 URL" in readme
    assert "안전한 완전 동일 제목" in readme
    assert "trend_clustering_job_batches" in readme
    assert "URL 중복 절감" in readme
    assert "1,000개당 예상 토큰" in readme
    assert "Gemini 요청과 점수 계산 중에는 유지하지 않습니다" in readme
    assert "## Gemini API 모델·기능별 사용 로그" in readme


def test_next_work_prioritizes_0_10_107_post_release_validation() -> None:
    next_work = _read("docs/NEXT_WORK.md")

    assert "## 0.10.107 릴리스 범위" in next_work
    assert "Gemini 주제 방향 부분 응답 보강" in next_work
    assert "`representative_candidate_no`" in next_work
    assert "결정론적 군집 baseline 읽기 전용 비교 진단" in next_work
    assert "`tpm_wait_seconds`" in next_work
    assert "공개 준비 검사가 `ready: True`" in next_work
    assert "## 0.10.106 릴리스 완료 상태" in next_work
    assert "`0.10.106`은 이 보완을 포함한 최종 단일 커밋" in next_work
    assert "후속 변경은 `work/0.10.107`에서 누적했다." in next_work
    assert "## 0.10.106 적용 후 우선 확인" in next_work
    assert "http://127.0.0.1:8518" in next_work
    assert "run_p2_diagnostics.bat" in next_work
    assert "reconstruction_reliable=true" in next_work
    assert "일반 편집 템플릿" in next_work
    assert "## 1·2차 통합 군집 안전성 실사용 검증 우선순위" in next_work
    assert "외부 스냅샷 1회" in next_work
    assert "`50,000` 내부 스캔 안전 상한" in next_work
    assert "토큰 기준으로 순차 분할" in next_work
    assert "Gemini 호출 없이 0배치 성공" in next_work
    assert "must_merge" in next_work
    assert "must_split" in next_work
    assert "option_id" in next_work
    assert "TPM 대기와 처리량" in next_work
    assert "DuckDB 잠금 없이" in next_work
