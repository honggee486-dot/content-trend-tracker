from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_current_operating_docs_match_gemini_fifteen_item_default() -> None:
    readme = (PROJECT_ROOT / "README.md").read_text(encoding="utf-8")
    context = (PROJECT_ROOT / "AI_CONTEXT.md").read_text(encoding="utf-8")
    example = (PROJECT_ROOT / ".env.example").read_text(encoding="utf-8")
    config = (PROJECT_ROOT / "src" / "config.py").read_text(encoding="utf-8")

    assert "요청당 기본값: **글감 15개**, 허용 상한: **30개**" in readme
    assert "현재 문서 기준 버전: `0.10.110`" in context
    assert "요청당 기본값 `15개`·허용 상한 `30개`" in context
    assert "GEMINI_TOPIC_ANGLE_ITEMS_PER_REQUEST=15" in example
    assert "BACKGROUND_TOPIC_ANGLE_ITEMS_PER_REQUEST = 15" in config
    assert config.count("maximum=30,") == 2
    assert "maximum=25," not in config


def test_current_docs_keep_scheduler_backup_and_safety_baseline() -> None:
    readme = (PROJECT_ROOT / "README.md").read_text(encoding="utf-8")
    context = (PROJECT_ROOT / "AI_CONTEXT.md").read_text(encoding="utf-8")
    gitignore = (PROJECT_ROOT / ".gitignore").read_text(encoding="utf-8")
    safe_zip = (PROJECT_ROOT / "make_safe_upload_zip.bat").read_text(
        encoding="utf-8"
    )

    assert "자동 수집 기본 입력값은 **180분**" in readme
    assert "자동 수집 기본 입력값: `180분`" in context
    assert "자동 로그인, 쿠키 저장, CAPTCHA 우회, 게시 버튼 자동 클릭" in readme
    assert "기존 DuckDB 데이터, 설정, 자료팩, 초안, 리비전, 사실 확인과 발행 기록을 보존한다." in context
    assert "복구 전 현재 DB를 `pre_restore` 백업으로 자동 보존" in readme
    assert "활성 수집 잠금, DB 점유 또는 비어 있지 않은 WAL" in context
    assert "backups/" in gitignore
    assert '"backups"' in safe_zip


def test_current_docs_describe_read_only_gemini_stability_recommendation() -> None:
    readme = (PROJECT_ROOT / "README.md").read_text(encoding="utf-8")
    context = (PROJECT_ROOT / "AI_CONTEXT.md").read_text(encoding="utf-8")
    next_work = (PROJECT_ROOT / "docs" / "NEXT_WORK.md").read_text(
        encoding="utf-8"
    )

    assert "실제 런타임의 요청당 처리량과 사고 수준" in readme
    assert "현재 처리량보다 큰 값을 추천하지 않는다" in context
    assert "완료 실행 3회·요청 글감 60개" in readme
    assert "모델·처리량·사고 수준을 자동 변경하지 않음" in context
    assert "자료팩 화면의 직접 Gemini 초안 생성 경로는 사용하지 않으며" in context
    assert "요청당 15개·`high`·600초" in next_work
    assert "실제 요청 글감 수, 설정 상한, 사고 수준, 제한 시간과 finish reason" in next_work
    assert "개별 생성이나 2단계 생성은 사용하지 않고" in next_work


def test_current_docs_describe_084_gemini_call_metadata() -> None:
    readme = (PROJECT_ROOT / "README.md").read_text(encoding="utf-8")
    context = (PROJECT_ROOT / "AI_CONTEXT.md").read_text(encoding="utf-8")
    next_work = (PROJECT_ROOT / "docs" / "NEXT_WORK.md").read_text(encoding="utf-8")
    release = (PROJECT_ROOT / "docs" / "releases" / "0.10.84.md").read_text(
        encoding="utf-8"
    )

    assert "실제 요청 글감 수·설정 상한·사고 수준·제한 시간" in readme
    assert "`MAX_TOKENS` 종료를 일반 응답 검증 실패와 별도로" in readme
    assert "finishReason" in context
    assert "기존 호출에는 새 실행 조건과 종료 사유가 없을 수 있음" in context
    assert "로컬 `tpm_wait_seconds`" in next_work
    assert "Gemini `rate_limited`·`daily_quota_exhausted`" in next_work
    assert "특정 작업의 직접 원인으로 단정하지 않는다" in next_work
    assert "군집 개선은 Gemini 안정성 판단 이후" in next_work
    assert "외부 Gemini API 호출 수와 요청 내용은 변경하지 않습니다." in release
    assert "기존 DB·자료팩·초안·리비전·사실 확인·발행 기록을 보존" in release


def test_current_docs_describe_ten_minute_topic_analysis_timeout() -> None:
    readme = (PROJECT_ROOT / "README.md").read_text(encoding="utf-8")
    context = (PROJECT_ROOT / "AI_CONTEXT.md").read_text(encoding="utf-8")
    example = (PROJECT_ROOT / ".env.example").read_text(encoding="utf-8")
    config = (PROJECT_ROOT / "src" / "config.py").read_text(encoding="utf-8")

    assert "글감 분석 요청 제한 시간 기본값: **600초(10분)**" in readme
    assert "과거 기본값 `360초`는 `600초`로 자동 보정" in readme
    assert "Gemini 글감 분석 요청 제한 시간: `600초`" in context
    assert "GEMINI_TOPIC_ANGLE_TIMEOUT_SECONDS=600" in example
    assert "BACKGROUND_TOPIC_ANGLE_TIMEOUT_SECONDS = 600" in config
    assert "LEGACY_TOPIC_ANGLE_TIMEOUT_SECONDS = 360" in config


def test_current_docs_describe_read_only_source_diversity_diagnostic() -> None:
    readme = (PROJECT_ROOT / "README.md").read_text(encoding="utf-8")
    context = (PROJECT_ROOT / "AI_CONTEXT.md").read_text(encoding="utf-8")

    assert "`수집 출처 다양성 진단`은 기본 접힘 상태" in readme
    assert "최근 24시간·72시간·7일" in readme
    assert "다중 출처 군집 비율 5% 미만을 매우 낮음" in readme
    assert "수집 설정, 분석 입력 상한, 제목 정규화 또는 군집 유사도 기준을 자동으로 변경하지 않습니다." in readme
    assert "`source_items`, `trend_clusters`, `trend_cluster_items`를 읽기 전용" in context
    assert "실제 군집 기준 변경은 진단 결과와 로컬 DB·수집 이력을 확인한 별도 작업" in context
    assert "진단 수치를 근거로 실제 DB·수집 이력 확인 없이" in context


def test_current_docs_describe_read_only_cluster_case_diagnostic() -> None:
    readme = (PROJECT_ROOT / "README.md").read_text(encoding="utf-8")
    context = (PROJECT_ROOT / "AI_CONTEXT.md").read_text(encoding="utf-8")

    assert "`군집 실패·단일 출처 사례 상세 보기`는 기본 접힘 상태" in readme
    assert "유사 후보, 현재 제목 유사도, 공통 핵심어와 시간 차이" in readme
    assert "사례 화면은 군집을 자동 병합하지 않습니다." in readme
    assert "표시 원인은 확정 판정이 아니라" in readme
    assert "군집 사례 상세 진단도 동일한 세 테이블을 읽기만 하며" in context
    assert "Agent 분석 전 군집 유사도나 정규화 규칙을 자동 변경하지 않는다." in context


def test_current_docs_describe_081_gemini_and_cluster_followup() -> None:
    context = (PROJECT_ROOT / "AI_CONTEXT.md").read_text(encoding="utf-8")
    release = (PROJECT_ROOT / "docs" / "releases" / "0.10.81.md").read_text(
        encoding="utf-8"
    )

    assert "`response_partial`" in context
    assert "유효한 글감은 그대로 저장하고 누락·검증 탈락 항목은 다음 실행 대상으로" in context
    assert "출처 수집 성공 판정과 Gemini 후처리 결과는 분리" in context
    assert "출처 그룹별 분석 입력 상한 초과 추정" in context
    assert "사례 핵심어가 포함된 같은 기간 군집을 추가 조회" in context
    assert "600초 변경 효과는 적용 후 완료 실행 최소 3회" in context
    assert "군집 유사도 기준 0.72" in release
    assert "NAVER·Daum 분석 입력 상한" in release
    assert "자동 군집 병합과 순위 재계산 규칙" in release


def test_current_docs_describe_082_workflow_navigation_safety() -> None:
    context = (PROJECT_ROOT / "AI_CONTEXT.md").read_text(encoding="utf-8")
    release = (PROJECT_ROOT / "docs" / "releases" / "0.10.82.md").read_text(
        encoding="utf-8"
    )

    assert "### 제작 화면 이동 상태 안전화" in context
    assert "`prefill_topic_id`와 `prefill_angle`만 유지" in context
    assert "`prefill_content_pack_id`만 유지" in context
    assert "`prefill_draft_id`만 유지" in context
    assert "`ai_import_raw_<content_pack_id>`의 사용자 원문" in context
    assert "자료팩 재사용 payload는 같은 주제" in context
    assert "충돌하는 Streamlit 포인터와 파생 검사값만 제거" in context
    assert "DB 스키마 변경이나 마이그레이션은 없습니다" in release
    assert "사용자의 AI 원문 입력" in release


def test_current_docs_describe_collapsed_queue_evidence_and_publish_history() -> None:
    readme = (PROJECT_ROOT / "README.md").read_text(encoding="utf-8")
    context = (PROJECT_ROOT / "AI_CONTEXT.md").read_text(encoding="utf-8")

    assert "`AI 요청서 준비 N개 보기`를 기본 접힘 상태" in readme
    assert "`수집 근거 보기`도 안쪽에서 기본 접힘" in readme
    assert "최근 연결 신호 최대 8개" in readme
    assert "발행 기록은 물리 삭제하지 않습니다." in readme
    assert "정정·보관·복원 전후 값과 사유" in readme
    assert "AI 요청서 준비 작업 전체 묶음과 각 항목의 수집 근거는 기본값이 접힘" in context
    assert "`publish_record_history`" in context
    assert "기존 `publish_records` 행을 물리 삭제하지 않는다." in context


def test_current_docs_describe_content_pack_compare_and_safe_reuse() -> None:
    readme = (PROJECT_ROOT / "README.md").read_text(encoding="utf-8")
    context = (PROJECT_ROOT / "AI_CONTEXT.md").read_text(encoding="utf-8")

    assert "`자료팩 버전 기록·비교·입력값 재사용`은 기본 접힘 상태" in readme
    assert "불러오는 것만으로 새 자료팩이 생성되지 않으며" in readme
    assert "현재 주제 연결에서 빠진 트렌드 신호와 보관된 사실 참고 자료" in readme
    assert "기존 AI 생성 결과와 초안은 생성 당시 사용한 자료팩 버전에 계속 연결" in readme
    assert "입력값 재사용만으로 새 `content_packs` 행을 만들지 않는다." in context
    assert "생성 세션과 초안은 생성 당시 `content_pack_id` 연결을 유지" in context


def test_current_docs_describe_generation_history_revalidation() -> None:
    readme = (PROJECT_ROOT / "README.md").read_text(encoding="utf-8")
    context = (PROJECT_ROOT / "AI_CONTEXT.md").read_text(encoding="utf-8")

    assert "`AI 생성 결과 기록·현재 규칙 재검사`는 기본 접힘 상태" in readme
    assert "저장 당시와 현재의 통과 여부·오류·경고·스키마 차이" in readme
    assert "원문 다시 열기도 Streamlit 입력란에 값을 채울 뿐" in readme
    assert "기존 `generation_sessions`, 자료팩, 초안과 사실 확인 항목을 수정하지 않습니다." in readme
    assert "현재 `parse_ai_result()`와 `validate_ai_result_against_references()`" in context
    assert "저장 원문 다시 열기는 Streamlit 입력값만 준비" in context


def test_current_docs_describe_safe_draft_revision_restore() -> None:
    readme = (PROJECT_ROOT / "README.md").read_text(encoding="utf-8")
    context = (PROJECT_ROOT / "AI_CONTEXT.md").read_text(encoding="utf-8")

    assert "`초안 버전 기록·비교·복원`은 기본 접힘 상태" in readme
    assert "`복원 전 현재 편집본 자동 보존` 리비전" in readme
    assert "선택한 과거 내용을 다시 새 리비전으로 저장" in readme
    assert "기존 리비전, 출처, 이미지 지시와 사실 확인 기록은 삭제하지 않습니다." in readme
    assert "복원 후 주제 상태는 `editing`" in context
    assert "과거 리비전은 덮어쓰거나 물리 삭제하지 않고" in context


def test_current_docs_describe_fact_check_history_and_safe_revert() -> None:
    readme = (PROJECT_ROOT / "README.md").read_text(encoding="utf-8")
    context = (PROJECT_ROOT / "AI_CONTEXT.md").read_text(encoding="utf-8")

    assert "`사실 확인 변경 이력·안전 되돌리기`는 기본 접힘 상태" in readme
    assert "기능을 처음 연 시점의 현재 사실 확인 상태를 기준점" in readme
    assert "기능 도입 이전의 변경 내용은 역산하지 않으며" in readme
    assert "되돌리기 작업도 새 변경 이력으로 저장" in readme
    assert "기존 사실 확인 항목과 과거 이력은 물리 삭제하지 않으며" in readme
    assert "`fact_check_history`" in context
    assert "이미 발행된 주제는 `published`를 유지" in context


def test_current_docs_describe_083_apply_update_safety() -> None:
    context = (PROJECT_ROOT / "AI_CONTEXT.md").read_text(encoding="utf-8")
    release = (PROJECT_ROOT / "docs" / "releases" / "0.10.83.md").read_text(
        encoding="utf-8"
    )
    next_work = (PROJECT_ROOT / "docs" / "NEXT_WORK.md").read_text(
        encoding="utf-8"
    )

    assert "### 적용 도구 단일 커밋 안전 강화" in context
    assert "`behind 0, ahead 1`" in context
    assert "`pwsh.exe`를 우선" in context
    assert "Windows PowerShell 5.1" in context
    assert "`chcp 65001`과 상시 `pause`" in context
    assert "PowerShell 파서 테스트는 설치된 PowerShell 7과 Windows PowerShell 5.1을 각각 검사" in release
    assert "강제 push, `reset --hard`, `git clean -fd`" in release
    assert "P1. Streamlit 제작 흐름 전체 브라우저 회귀검증" in next_work


def test_current_docs_describe_087_topic_angle_quality_diagnostic() -> None:
    readme = (PROJECT_ROOT / "README.md").read_text(encoding="utf-8")
    context = (PROJECT_ROOT / "AI_CONTEXT.md").read_text(encoding="utf-8")
    release = (PROJECT_ROOT / "docs" / "releases" / "0.10.87.md").read_text(encoding="utf-8")
    database = (PROJECT_ROOT / "src" / "database.py").read_text(encoding="utf-8")

    assert "현재 문서 기준 버전: `0.10.110`" in context
    assert "주제 방향 v6 품질·운영 진단" in readme
    assert "성공 요청 4회·요청 글감 60개" in readme
    assert "feature_version VARCHAR NOT NULL DEFAULT ''" in database
    assert "외부 API를 호출하거나 설정·방향·원문을 자동 변경하지 않습니다." in readme
    assert "기존 호출 이력은 빈 버전으로 유지합니다." in release


def test_current_docs_describe_settings_diagnostic_connection_reuse() -> None:
    readme = (PROJECT_ROOT / "README.md").read_text(encoding="utf-8")
    context = (PROJECT_ROOT / "AI_CONTEXT.md").read_text(encoding="utf-8")
    release = (PROJECT_ROOT / "docs" / "releases" / "0.10.98.md").read_text(
        encoding="utf-8"
    )

    assert "## 설정 진단 DuckDB 연결" in readme
    assert "서로 다른 구성으로 다시 열지" in readme
    assert "설정 진단 DuckDB 연결 재사용 계약" in context
    assert "연결의 소유권과 종료는 호출한 앱" in context
    assert "`read_only=True`" in release
    assert "SELECT 쿼리만 실행" in release
