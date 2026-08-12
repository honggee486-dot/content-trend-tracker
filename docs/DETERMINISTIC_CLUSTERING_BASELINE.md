# Deterministic 군집 baseline 읽기 전용 비교

## 목적

현재 1차 규칙과 Gemini 2차 군집 사이에 저비용 deterministic 판단을 넣을 가치가 있는지 실제 동일 작업 데이터로 비교한다. 이 기능은 **진단 전용**이며 현재 군집 결과, Gemini 호출 방식, 설정, DB 스키마를 변경하지 않는다.

## 실행 위치

기존 P2 읽기 전용 운영 진단에 `trend_clustering.deterministic_baseline`으로 포함된다. 기본 사람용 출력에서도 비교 완전성, 후보·비교 쌍·병합 후보 수, 현재 군집 일치·불일치·안전 차단 수와 대표 제목 표본을 바로 확인할 수 있다.

```powershell
.\run_operation_diagnostics.bat
```

구조화된 전체 필드를 확인해야 할 때만 JSON 출력을 사용한다.

```powershell
.\run_operation_diagnostics.bat --json
```

사람용 출력은 일치·불일치·안전 차단 제목 표본을 종류별 최대 3개 표시한다. `comparison_complete=false`이면 불완전 사유를 함께 표시하고 precision/recall 참고값은 출력하지 않는다. 더 많은 구조화 표본과 원시 진단 필드가 필요하면 JSON을 사용한다.

진단 CLI는 실제 DB를 `read_only=True`로 열고 실행 전후 DB·WAL 상태를 비교하는 기존 계약을 유지한다. deterministic baseline 자체도 SELECT만 수행하며 외부 API를 호출하지 않는다.

## 신뢰 조건

baseline 비교는 같은 작업의 `quality_sample`이 다음 조건을 모두 만족할 때만 실행한다.

- 품질 표본을 재구성할 수 있음
- 최신 작업 시각과 현재 군집 스냅샷이 일치함
- 작업 원장의 처리·신규·기존 연결·불확실·검토 집계가 재구성 결과와 일치함
- `first_stage_key` 누락이나 한 후보가 여러 군집으로 해석되는 모호성이 없음

위 조건을 만족하지 않으면 `quality_sample_unavailable` 또는 `quality_sample_unreliable`로 비교를 중단한다. 오래된 스냅샷을 현재 결과로 추정하지 않는다. 이 외에도 baseline 재구성 단계에서 `first_stage_key` 누락이나 미확정 후보를 다시 발견하면 방어적으로 `comparison_complete=false`로 처리해 부분 비교의 일치율을 채택 근거로 사용하지 않는다.

## 비교 규칙

1. 같은 군집 작업 시간 범위의 `trend_cluster_processing`과 `source_items`를 읽는다.
2. 같은 `first_stage_key`의 원문 제목을 하나의 비교 후보로 재구성한다.
3. 상태가 모두 `processed`이고 최종 `cluster_id`가 하나로 확정된 후보만 평가한다.
4. 현재 안전 프로필 생성과 `must_split_profiles`를 그대로 재사용한다.
5. 비교 쌍 후보는 제품·주체 식별어로 blocking하여 전 후보의 무제한 O(n²) 비교를 하지 않는다.
6. `특징`, `선택`, `포인트`, `총정리` 같은 일반 편집 표현은 제목 유사도 토큰에서 제외한다.
7. 완전 동일 정규화 제목이거나, 공통 식별 근거가 충분하면서 제목 token-set 유사도가 `92.0` 이상인 쌍만 baseline 병합 후보로 본다.
8. 날짜·회차·제품·행동·방향 등 현재 `must_split` 충돌이 있으면 유사도가 높아도 병합 후보에서 제외하고 안전 차단 표본으로 기록한다.

초기 안전 경계는 block당 최대 `200`개, 전체 비교 쌍 최대 `20,000`개다. 비교 쌍 상한은 **상한 개수 자체를 채운 것만으로 중단으로 보지 않고**, 그 이후 평가해야 할 새 고유 후보 쌍이 실제로 더 발견될 때 `pair_limit_reached=true`로 표시한다. 비교 쌍 상한을 초과해야 하거나 block 최대 크기를 넘어 해당 block을 건너뛴 경우, 또는 재구성 입력에 누락·미확정 후보가 있으면 `comparison_complete=false`로 표시해 불완전 표본을 완전 비교처럼 해석하지 않는다.

불완전 사유는 `comparison_incomplete_reasons`에 `missing_first_stage_key`, `unresolved_candidates`, `oversized_blocks_skipped`, `pair_limit_reached` 중 해당 값을 기록한다.

## 출력 해석

주요 필드는 다음과 같다.

- `evaluable_candidate_count`: 비교 가능한 1차 후보 수
- `evaluated_candidate_pair_count`: blocking 후 실제 검토한 후보 쌍 수
- `baseline_merge_pair_count`: deterministic 규칙이 병합 후보로 제안한 쌍 수
- `same_cluster_agreement_pair_count`: baseline 제안 중 현재 저장 군집도 같은 쌍
- `different_cluster_disagreement_pair_count`: baseline은 병합 후보지만 현재 저장 군집은 다른 쌍
- `blocked_candidate_pair_count`: 높은 유사도 조건을 만족했지만 안전 충돌로 차단된 쌍
- `comparison_incomplete_reasons`: 비교를 완전하다고 볼 수 없는 구체적인 원인 목록
- `precision_vs_current_percent`, `recall_vs_current_percent`: 현재 저장 군집을 비교 기준으로 계산한 참고값
- `samples.agreements`, `samples.disagreements`, `samples.safety_blocks`: 실제 제목 표본

`precision_vs_current_percent`와 `recall_vs_current_percent`는 **정답률이 아니다**. 현재 저장 군집에도 오병합·과분리가 있을 수 있으므로, 일치·불일치 제목 표본을 사람이 함께 확인하기 위한 비교 지표로만 사용한다. 또한 `comparison_complete=false`이면 두 값은 `null`로 반환한다. 비교에서 빠진 후보나 쌍이 있는 상태의 부분 일치율은 deterministic baseline 채택 판단에 사용하지 않는다.

## 채택 기준

이 진단만으로 deterministic baseline을 생산 군집 경로에 적용하지 않는다. 동일 데이터 표본에서 다음이 함께 확인될 때만 `ambiguous-only Gemini` 전환을 별도 단일 축 작업으로 검토한다.

- 명백한 병합·분리 품질이 현재와 같거나 개선됨
- 안전 차단 규칙이 기존 오병합 방어를 유지함
- Gemini로 넘길 후보나 요청 수가 실제로 감소할 근거가 있음
- 입력·출력 토큰과 총 처리 시간이 감소할 근거가 있음

제목 정규화, 전역 임계값, 사고 수준, 프롬프트, 파서, Google Trends 연결 규칙을 이 진단과 동시에 변경하지 않는다.
