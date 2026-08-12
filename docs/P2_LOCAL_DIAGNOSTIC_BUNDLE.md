# P2 로컬 읽기 전용 진단 묶음

P1 자동 브라우저 회귀검증 이후 남은 P2 운영 판단은 실제 로컬 DuckDB 표본을 읽어야 합니다. 여러 진단을 따로 실행하지 않아도 되도록 `run_p2_diagnostics.bat`이 기존 P2 운영 진단과 NAVER·Daum 분석 입력 상한 진단을 한 번의 읽기 전용 실행으로 묶습니다.

## 실행

프로젝트 루트에서 다음 명령을 사용합니다.

```powershell
.\run_p2_diagnostics.bat
```

최근 30일 포털 요청과 최근 20회 수집을 함께 확인하려면 인자를 그대로 전달합니다.

```powershell
.\run_p2_diagnostics.bat --days 30 --refresh-runs 20
```

ChatGPT 등에서 구조화된 결과를 검토할 때는 하나의 JSON 객체로 출력할 수 있습니다.

```powershell
.\run_p2_diagnostics.bat --json
```

JSON에는 `operation`, `source_analysis_limits`, `read_only_verification`이 함께 포함됩니다.

## 군집 품질 표본

최신 2단계 군집 작업에 완료 배치가 있으면 `operation.trend_clustering.quality_sample`에 저장된 결과를 읽기 전용으로 재구성한 품질 표본이 함께 포함됩니다.

- `trend_clustering_jobs`의 최신 작업 시각과 모델을 기준으로 그 작업에서 갱신된 `trend_cluster_processing`을 찾습니다.
- `first_stage_key`와 현재 `trend_cluster_items`·`trend_clusters`·`source_items`을 연결해 다중 신규 군집, 기존 군집 연결, 단독 신규 후보, `retry`·`needs_review` 후보를 구분합니다.
- 각 유형은 최대 8개 대표 표본과 원문 제목·출처를 출력하며 Gemini나 군집 계산을 다시 실행하지 않습니다.
- `snapshot_matches_job=true`는 현재 `trend_clusters` 스냅샷의 계산 시각이 해당 작업 범위와 일치한다는 뜻입니다.
- `reconstruction_reliable=true`는 스냅샷 일치에 더해 처리 단위·기존 연결·신규 군집·불확실·검토 수치가 작업 원장의 집계와 모두 일치하고 후보 식별도 완전할 때만 설정됩니다.
- 이후 다른 순위·군집 계산이 현재 군집 스냅샷을 교체했다면 표본을 억지로 당시 결과로 해석하지 않고 `reconstruction_reliable=false`로 표시합니다.
- 단독 후보 비율은 과분리 결론이 아니라 실제 제목·사건을 비교하기 위한 신호입니다. 비율만으로 병합 임계값이나 프롬프트를 자동 변경하지 않습니다.

사람용 출력도 현재 토큰 분할 계약을 기준으로 `50,000` 스냅샷 안전 상한과 외부 1회 계약을 표시하며, 과거 `300개` 절대 상한을 현재 계약처럼 표시하지 않습니다.

## 군집 운영 표본이 없을 때

`trend_clustering.sample_available`이 `false`이고 다음 행동이 군집 표본 추가라면, 진단만을 위해 전체 운영 군집 작업을 반복 실행하지 않습니다. 먼저 비용과 변경 범위를 제한한 P2 전용 표본 명령을 사용할 수 있습니다.

```powershell
.\run_p2_clustering_sample.bat --confirm
```

이 명령은 `run_p2_diagnostics.bat`과 달리 **읽기 전용이 아닙니다**.

- 실제 DuckDB에 정상 군집 결과를 저장합니다.
- 실제 Gemini를 호출할 수 있습니다.
- 기존 운영 군집 알고리즘과 모델은 그대로 사용합니다.
- 외부 작업 스냅샷은 최대 1회만 준비합니다.
- 현재 토큰 분할 계약에서 `50,000`은 Gemini 요청당 후보 수가 아니라 한 작업의 내부 스냅샷 스캔 안전 상한입니다.
- 스냅샷 안의 제목·사건·식별·기존 군집 관점은 입력 토큰 기준으로 자동 분할되므로 Gemini API 요청 자체는 여러 번 발생할 수 있습니다.
- 각 Gemini 요청은 한 번에 하나씩 순차 실행하며 입력 TPM 계약을 그대로 사용합니다.
- 후속 주제 방향 자동 생성은 실행하지 않아 군집 표본 외 추가 Gemini 사용을 만들지 않습니다.
- 이미 수집 또는 군집 작업이 실행 중이면 새 표본을 강행하지 않고 종료합니다.
- `--confirm`을 생략하면 실제 DB 저장과 Gemini 호출을 시작하지 않습니다.

표본 실행이 정상 종료되면 다시 아래 읽기 전용 진단을 실행합니다.

```powershell
.\run_p2_diagnostics.bat --json
```

그 결과의 `trend_clustering` 배치·토큰·처리량과 `quality_sample`의 실제 제목 표본을 함께 보고 다음 P3 단일 축 개선 여부를 판단합니다. 현재 토큰 분할 계약은 `contract_mode=token_partitioned_snapshot`, 외부 `configured_max_batches=1`, 스냅샷 스캔 상한 `50,000`을 기준으로 판정하며 과거 고정 후보 수 계약 기록도 읽기 호환성을 유지합니다.

## 안전 계약

- `run_p2_diagnostics.bat`은 실제 DB를 `read_only=True`로 한 번 열어 진단을 연속 계산합니다.
- 읽기 전용 진단은 외부 API, Gemini 생성, 수집, 군집 저장, 설정 변경을 호출하지 않습니다.
- 군집 품질 표본도 현재 저장된 작업·처리·군집·원문 테이블을 SELECT로만 읽습니다.
- 읽기 전용 진단 실행 전후 DB와 WAL의 존재 여부·크기·수정 시각을 비교합니다.
- 읽기 전용 진단 중 다른 프로세스가 DB나 WAL을 변경하면 종료 코드 `3`으로 `무변경 검증`을 통과시키지 않습니다.
- `run_p2_clustering_sample.bat --confirm`은 위 읽기 전용 계약의 예외이며, 사용자가 명시적으로 요청한 1회 스냅샷 실제 군집 표본만 저장합니다.
- 이 결과만으로 처리량, 상한, 사고 수준, 제한 시간, 군집 기준을 자동 변경하지 않습니다.

기존 `run_operation_diagnostics.bat`과 `run_source_analysis_limit_diagnostics.bat`은 각각의 세부 진단만 확인할 때 그대로 유지합니다. 이 묶음 명령은 두 진단을 대체하거나 정책을 변경하지 않고, P2 실제 로컬 표본 수집을 한 번에 수행하기 위한 실행 경로입니다.
