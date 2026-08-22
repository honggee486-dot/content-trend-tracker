# 콘텐츠 기회 레이더 데이터 계약

## 목적

콘텐츠 기회 레이더는 이미 많이 쓰인 주제를 단순 집계하는 대신 **검색·관심 수요가 커지는 속도와 최근 콘텐츠 공급의 차이**를 시간축으로 관찰한다.

현재 단계는 개발 순서 2번인 데이터 계약과 3번 Watchlist의 백엔드 기반이다. 예상 검색 의도 생성, 기존 글감 승격, 운영 콘솔 UI는 다음 단계에서 이 계약을 사용한다.

## 기존 데이터 재사용

새 기능을 위해 원본 수집 행을 복제하지 않는다.

현재 다음 데이터를 재사용한다.

- `trend_clusters`: 현재 순위, 기존 글감기회, 최초·최근 신호 시각
- `trend_cluster_items`: 현재 군집과 원본 연결
- `source_items`: 출처, 신호값, 최초·최근 재수집 시각
- `collection_query_discoveries`: NAVER·Daum 검색에서 실제 발견한 최근 공급 신호

같은 URL·외부 ID 재수집 때 기존 `source_items` 행을 갱신하는 멱등성 계약은 그대로 유지한다. 원문 게시 시각을 레이더 관측 시각으로 덮어쓰지 않는다.

## 추가 데이터

레이더 서비스는 필요할 때 다음 추가형 테이블을 `CREATE TABLE IF NOT EXISTS`로 만든다.

### `trend_opportunity_snapshots`

한 번의 순위 계산에서 관찰한 군집 상태를 시간순으로 보존한다.

주요 값:

- `cluster_id`
- `observed_at`
- `first_seen_at` / `last_seen_at`
- 현재 `trend_score` / 기존 `opportunity_score`
- `demand_score`
- `supply_score`
- `saturation_score`
- `velocity`
- `acceleration`
- `expected_lifetime`
- `radar_status`
- `recommended_action`
- 출처별 최초 포착 순서와 현재 출처 건수

### `trend_opportunity_watchlist`

군집별 최신 상태를 한 행으로 보관한다. 과거 관측은 snapshot 원장에 남기고 Watchlist는 현재 판단만 제공한다.

상태 변경 시 `status_changed_at`을 갱신하며, 상태가 유지되면 최초 변경 시각을 보존한다.

## 현재 점수 의미

현재 값은 외부 유료 데이터나 새 검색 호출 없이 기존 수집 자료만 사용하는 **초기 결정론적 기준값**이다.

### 수요 `demand_score`

다음을 조합한다.

- 기존 트렌드 점수
- 기존 글감기회 점수
- YouTube 신호
- Google Trends 신호
- Wikimedia 조회 신호
- 서로 다른 출처 유형의 교차 등장
- 저장된 신호 강도

### 공급 `supply_score`

현재 군집 안의 NAVER·Daum 결과와 최근 24시간 `collection_query_discoveries`를 이용한다.

따라서 현재 공급 점수는 인터넷 전체 문서 수가 아니라 **프로그램이 실제로 관찰한 최근 콘텐츠 공급 압력**이다. 이 값을 전체 검색 결과 수라고 표시해서는 안 된다.

### 속도와 가속도

`velocity`는 직전 snapshot 대비 수요 점수 변화량을 경과 시간으로 나눈 값이다.

`acceleration`은 직전 velocity 대비 현재 velocity의 변화율이다.

첫 관측에서는 비교 기준이 없으므로 둘 다 `0`이다. 충분한 관측 이력이 쌓인 뒤 의미가 커진다.

### 포화도 `saturation_score`

현재 공급 점수와 수요 대비 공급 격차를 조합한다. 수요가 감소하면서 공급이 높은 경우 추가로 포화 쪽에 가중한다.

### 예상 수명 `expected_lifetime`

현재는 제목의 정보성·시점 의존 표현과 상태를 이용해 다음 범주로만 분류한다.

- `hours`
- `1_2_days`
- `3_7_days`
- `weeks`
- `ending`

이는 확정 예측이 아니라 우선순위 결정을 위한 운영 추정값이다. 실제 관측 데이터가 쌓이면 임계값과 분류 규칙을 조정한다.

## Watchlist 상태와 행동

상태와 행동은 별도 계약이다.

| 상태 | 표시 | 기본 의미 |
| --- | --- | --- |
| `hot` | 🔥 급상승 | 수요가 빠르게 증가하고 아직 포화되지 않음 |
| `early` | 🟠 초기 신호 | 아직 작성 확신이 부족해 추가 관찰 필요 |
| `opportunity` | 🟢 정보성 기회 | 수요 대비 공급이 낮아 정보성 글 가치가 있음 |
| `saturated` | ⚪ 포화/종료 | 공급이 과하거나 관심이 꺾여 우선순위가 낮음 |

행동은 `write_now`, `watch`, `close`로 분리한다.

예를 들어 `hot`과 `opportunity`는 모두 `write_now`가 될 수 있지만 두 상태의 의미는 다르다.

## 출처 확산 순서

`source_spread_json`에는 각 출처 유형이 **우리 프로그램에서 처음 포착된 시각**을 기준으로 순서를 저장한다.

이는 "Google에서 시작해 YouTube로 전파됐다" 같은 인과관계를 증명하지 않는다. 실제 수집 주기와 출처별 지연이 다르므로 화면에서도 `최초 관측 순서`로 표현한다.

## 실행 위치와 실패 경계

현재 공통 트렌드 런타임 설치 경로가 `finalize_prepared_trend_rankings()`를 감싸며, 순위 저장이 끝난 다음 레이더 snapshot/Watchlist를 갱신한다.

중요한 부분은 순위 저장 transaction이 먼저 끝난다는 점이다.

```text
수집·군집·순위 계산
→ 순위 DB COMMIT
→ 콘텐츠 기회 레이더 관측
```

레이더 계산이나 추가 테이블 기록이 실패해도 이미 성공한 수집·군집·순위 결과를 실패로 되돌리지 않는다. 반환 결과의 `opportunity_radar.status=failed`로 분리한다.

기존 순위가 재사용된 경우에는 같은 snapshot을 중복 생성하지 않고 현재 Watchlist 요약만 반환한다.

## 비용·보안

현재 레이더 계산은:

- 새 AI 호출 없음
- 새 외부 API 호출 없음
- 유료 서비스 호출 없음
- 브라우저 자동화 없음
- 실제 사용자 인증정보 사용 없음

이다.

## 다음 단계

다음 작업은 이 데이터 계약 위에서 다음을 연결한다.

1. 현재 신호에서 예상 검색 질문 후보 생성
2. 질문 후보를 실제 근거와 검색 신호로 검증
3. `write_now` 후보를 기존 글감·주제 방향 흐름에 안전하게 승격
4. 좌측 메뉴 + 상단 `🔥/🟠/🟢/⚪` 미니 대시보드 + 상세 목록 UI
5. 실제 누적 관측 표본으로 velocity·saturation 임계값 재검증

초기 임계값을 영구 정책으로 간주하지 않는다. 실사용 데이터가 쌓이기 전까지는 보수적인 운영 기준값으로 취급한다.
