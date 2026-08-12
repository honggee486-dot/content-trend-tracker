# 구조 요약

## 전체 흐름

```text
[youtube-trend-tracker]
YouTube API 수집·분석
        ↓
content_trend_signals.parquet
        ┐
        ├─→ source_items → trend_clusters → 오늘의 트렌드 순위
        │
[NAVER API HUB 검색 API]
뉴스·블로그 최신 결과
        │
[Daum 검색 API]
웹문서·카페 최신 결과
        │
[Google Trends 공식 RSS]
한국 급상승 검색어
        │
[Wikimedia Analytics API]
한국어 위키백과 조회 관심
        ┘

순위 주제 선택
→ 작업 주제 topics로 승격
→ 연결 신호 topic_source_links에 보존
→ 원문 제목·설명·출처 구성 기반 글쓰기 방향 선택
→ AI 자료팩·프롬프트
→ AI JSON 검사
→ 초안·사실 확인·발행 보조
```

## 주요 모듈

- `src/adapters/youtube_parquet_adapter.py`: YouTube 교환 파일 검증·읽기
- `src/adapters/youtube_duckdb_adapter.py`: 수동 fallback 읽기 전용 연결
- `src/adapters/naver_search_adapter.py`: NAVER API HUB 뉴스·블로그 검색 결과 정규화
- `src/adapters/google_trends_rss_adapter.py`: Google Trends 한국 공식 RSS 다운로드·검색량 정규화
- `src/adapters/wikimedia_pageviews_adapter.py`: 한국어 위키백과 인기 문서·조회수 정규화
- `src/services/trend_normalization.py`: HTML·범위명·일반 토큰·URL 추적 파라미터 정규화
- `src/services/trend_discovery_service.py`: 최근 신호 통합, 규칙 기반 군집, 점수 계산, 글쓰기 방향 서비스 연결
- `src/services/writing_angle_service.py`: 주제 의도 분류, 근거 특징 추출, 의도별 글쓰기 방향 제한과 추천 이유 생성
- `src/services/topic_service.py`: 외부 신호 저장, 작업 주제, 연결 근거 관리
- `src/services/content_pack_service.py`: 선택 근거를 Gemini·ChatGPT 요청서로 변환
- `src/services/ai_result_parser.py`: JSON과 자료팩 출처 ID·URL 검사
- `src/services/draft_service.py`: 초안·수정 버전·사실 확인 관리
- `src/services/publish_service.py`: 사용자가 직접 발행한 결과 기록
- `src/services/scheduler_service.py`: Windows 자동 수집 작업의 등록·갱신·삭제와 설정 최대치 기준 최소 간격 계산
- `src/services/scheduler_quota_analysis_service.py`: 실행 이력의 실제 NAVER·Daum 요청량, 재시도율과 현재 주기의 일일 한도 여유 계산
- `src/services/collection_history_service.py`: 전체 실행 시작·종료, 출처 결과 집계, 최근 이력 조회와 보관 정리
- `src/collection_history_ui.py`: 설정 화면의 실행 요약 지표, 제한된 최근 목록과 출처별 상세 표시

## DB 역할

### 전체 수집 실행 이력

- `collection_runs`: 자동·백그라운드 수집, 화면 수동 수집, 저장 데이터 순위 재계산의 전체 실행 상태와 합계
- `collection_run_sources`: 같은 `run_id`에 속한 출처별 상태·소요 시간·요청·재시도·신규·갱신·생략·오류 요약
- 기존 `sync_runs`는 출처 어댑터 단위 가져오기 기록으로 유지하며, 과거 행을 전체 실행과 추정 연결하지 않음
- 잠금 획득 뒤 `running` 한 행을 만들고 종료 때 같은 행을 갱신하며 출처 상세는 한 번에 일괄 저장
- 잠금 겹침은 `skipped_overlap`을 최선 노력으로 기록하고 기록 실패가 기존 정상 생략 종료 코드를 바꾸지 않음
- 6시간 이상 남은 `running`만 다음 실행 시작 때 실패로 정리하고 최근 실행은 건드리지 않음
- 기존 `sync_run_retention_days` 설정을 공유해 기본 90일 뒤 새 이력 두 테이블의 오래된 행만 정리

### 수집 원본

- `source_items`: YouTube·뉴스·블로그·Google Trends·위키백과 개별 항목과 추적 파라미터를 제거한 `normalized_url`
- `sync_runs`: 출처별 가져오기 실행 결과

### 자동 트렌드 분석

- `trend_clusters`: 현재 점수순 통합 주제, 콘텐츠 품질과 추천 판정
- `trend_cluster_items`: 통합 주제와 원본 항목 연결
- `trend_cluster_ai_profiles`: Gemini 표시 제목·요약·작성 설정과 스키마 버전 5의 게시 시급성·근거 계획·1순위 방향 추천 이유 저장
- `trend_cluster_ai_angles`: 글감별 추천 순서의 방향 3개와 공식 자료 검색어 저장

이 두 테이블은 최근 분석 결과를 다시 계산하는 용도입니다. 글 제작 이력과 분리되어 있습니다.

### 보관 정책과 정리

- `src/services/data_maintenance_service.py`가 DB 통계, 하루 1회 자동 정리, 수동 정리를 담당
- 원본 기준 시각은 `published_at → observed_at → imported_at` 순으로 사용
- 기본 30일을 넘긴 `source_items` 중 `topic_source_links`에 연결되지 않은 행만 삭제
- 삭제 대상과 연결된 `trend_cluster_items` 및 빈 `trend_clusters`도 함께 정리
- `sync_runs`는 기본 90일, `api_usage_counters`는 현재 달 포함 기본 13개월 유지
- 원본 삭제 시 `trend_ranking_signature`를 무효화해 다음 계산에서 캐시를 재사용하지 않음
- 수동 정리 후 `CHECKPOINT`를 시도하며, 자동 정리는 수집 지연을 줄이기 위해 CHECKPOINT 없이 하루 한 번 실행
- DB 삭제나 재생성 없이 기존 스키마와 데이터를 유지하는 방식으로 동작

### 사용자가 선택한 작업

- `topics`: 관심 주제와 제작 상태
- `topic_source_links`: 선택한 주제에 연결된 원본 신호
- `topic_references`: 사용자가 등록한 공식·공공·뉴스 자료
- `content_packs`: AI 요청서 버전
- `generation_sessions`: AI 원본 응답과 검사 결과
- `drafts`, `draft_revisions`: 현재 초안과 수정 이력
- `fact_check_items`: 사용자 사실 확인 상태
- `blog_profiles`: 여러 블로그 계정·사이트의 플랫폼, 로그인·글쓰기 URL, 기본 출력 형식과 발행 기본값
- `publish_records`: 수동 발행 기록과 선택한 `blog_profile_id`

## 자동 주제 통합

현재 MVP는 AI API 없이 로컬 규칙을 사용합니다.

1. HTML 엔티티·URL·해시태그·공백·반복 기호와 URL 추적 파라미터 정리
2. 수집 범위명·지역명·피드명과 `horror`, `vtuber`, `moments`, `먹방` 같은 일반 범주어 및 날짜·요일·일일 요약 표현을 식별 토큰에서 제외
3. 검색어 자체가 아니라 실제 원문 제목의 고유 토큰·압축 문자열·문장 유사도·정규 URL을 비교하며, 고유 토큰이 없는 동일 제목만으로는 군집을 합치지 않음
4. 전체 데이터에서 반복 빈도가 낮은 토큰만 blocking 키로 사용해 비교 후보를 제한하고, 정규 URL은 별도 후보 키로 사용하며 후보가 폭증하면 최근 군집 중심으로 상한 적용
5. 같은 URL과 복제 수준의 제목은 점수 계산에서 한 근거 묶음으로 정규화
6. 여러 근거에서 반복되는 고유명·제품명·버전·사건 표현으로 대표 제목 생성
7. 24시간 반감기 최근성, 출처 역할 교차 확인, 출처별 포화 근거량, 뉴스·커뮤니티·영상 신호를 분리 계산
8. `source_items`의 최초·이전·최근 가져오기 시각과 누적 포착 횟수를 갱신하고, 동일 URL·복제 제목을 한 묶음으로 제한한 뒤 짧은 간격의 반복 포착에 최대 10점의 보너스를 계산
9. Google Trends와 위키백과는 다른 원문이 있을 때만 작은 보조 점수로 반영
10. 동일 도메인·복제 원문을 감점하고 구체 제목과 독립 근거가 있는 경우에만 `추천` 판정
11. 입력 건수·최근 가져오기 시각·분석 범위·출처별 분석 상한과 평가 날짜가 같으면 기존 순위를 재사용한다. 날짜나 규칙이 바뀌어도 앱 진입 시에는 재계산하지 않고 마지막 결과를 표시하며, 다음 자동·수동 수집 또는 사용자의 순위 재계산 실행에서 최근성 점수를 갱신한다.
12. 최근 데이터 조회는 출처 그룹별 상한을 적용한다. 기본값은 YouTube 2,000, NAVER 4,000, Daum 4,000, Google Trends 500, 위키백과 500이며 각 그룹은 최신 자료부터 선택한다. 기본 72시간 범위를 벗어난 주제는 한 달 동안 고점으로 남지 않고 순위 입력에서 제외된다.
13. 계산 결과는 군집·연결 행을 DataFrame으로 만들어 DuckDB에 일괄 삽입하고 분석·DB 쓰기·전체 시간을 분리 기록한다.

글쓰기 방향은 고정 개수를 채우지 않는다. 먼저 대표 주제명을 기준으로 정답 조회, 일정 확인, 문제 해결, 비교·선택, 가격·혜택, 출시·업데이트, 사용 방법, 팩트체크, 후기·반응, 인물·작품, 사건 요약, 일반 설명형 중 하나를 보수적으로 분류한다. 제목이 모호할 때만 서로 다른 발행처의 원문 두 건 이상이 같은 목적을 반복 지지하면 낮은 확신도로 보완한다.
대표 주제명보다 더 구체적인 원문 제목은 독립 근거가 반복 지지할 때만 글쓰기 주제로 승격한다. 이후 각 방향마다 지원 원문과 발행처를 다시 계산해 주 추천·보조·단일 근거 참고 방향으로 등급화한다. 문제 해결 경험은 3건과 발행처 2곳 이상일 때만 강한 추천 자격을 얻고, 비교는 실제 비교 원문과 비교 대상 2개가 있어야 한다. 추적 파라미터 URL, 동일 제목과 매우 유사한 복제 제목은 한 번만 계산한다. 공통 제품명만 일치하고 세부 사건이나 작성 목적이 여러 갈래이면 혼합 근거로 판정해 하위 목적 분포와 이슈 구분·공통 사실 방향을 우선한다.

각 의도는 허용된 방향만 생성한다. 비교는 명시적인 비교 목적, 장단점은 서로 다른 커뮤니티 발행처의 반복 반응, 일정·업데이트·사용법·팩트체크는 해당 표현과 출처 근거가 있어야 한다. 근거가 부족한 방향은 제외 사유와 함께 화면에 표시하며, 의도가 불명확하면 안전한 핵심 정리 1~2개만 반환한다. 캐시워크·오퀴즈 등 퀴즈 정답형은 이 공통 엔진의 한 의도로 처리하고 정답 정리 외 일반 후기·영향 템플릿을 차단한다.

자동 병합이 완벽하지 않으므로 실제 데이터를 보며 임계값과 일반 토큰 목록을 조정합니다. 현재 DB 규모에서는 외부 형태소 분석기나 임베딩 없이 수 초 안에 재계산하며, 입력이 바뀌지 않으면 저장된 결과를 재사용합니다.

## Gemini 모델 설정과 목록 캐시

- API 키는 기존처럼 `.env`의 `GEMINI_API_KEY`에서만 읽으며 DuckDB에 저장하지 않는다.
- 사용자가 설정 화면에서 모델 목록 새로고침을 실행하면 DB 연결을 열기 전에 Gemini `v1beta/models`를 조회한다.
- `supportedGenerationMethods`에 `generateContent`가 있는 Gemini 텍스트 모델만 정규화해 `app_settings.gemini_model_catalog_json`에 저장하고, 정상 조회 시각은 `gemini_model_catalog_refreshed_at`에 기록한다.
- 자동·예약 분석 모델은 `gemini_auto_analysis_model`, Gemini 직접 초안 생성 모델은 `gemini_manual_draft_model`에 각각 저장한다.
- 저장 모델이 비어 있을 때만 `.env`의 `GEMINI_MODEL`을 호환 기본값으로 사용한다. API 목록에 없는 기존 저장값도 자동 삭제하지 않는다.
- 목록 조회가 실패하면 기존 캐시와 선택값을 유지한다. 모델 목록 HTTP 요청 중에는 DuckDB 연결을 잡지 않는다.
- 오늘의 트렌드 화면은 수집 버튼 전에 자동 모델을 바로 표시·변경하며, 예약 스크립트도 같은 DB 설정을 읽는다.
- 예약·BAT 분석은 요청당 상위 65개를 한 묶음만 준비한다. 화면 수동 분석도 기본값은 65개·1개 요청이며 미처리 대상은 다음 실행에서 이어서 처리한다.


## 글감별 AI 작성 설정

`trend_cluster_ai_profiles.content_plan_json`은 초기 Gemini 분석이 만든 독자 대상, 목적, 카테고리, 권장 분량, 제목 원칙, 본문 구성, 금지 표현을 보존한다. `verification_points_json`은 AI 요청서의 반드시 확인할 사실 기본값으로 사용한다.

`topic_content_preferences`는 주제로 승격한 글감과 원본 군집을 연결하고 사용자가 AI 요청서 화면에서 수정한 값을 별도로 저장한다. 기본값 우선순위는 사용자 저장값, Gemini 추천값, 전역 설정 순서이며 Gemini 재분석은 사용자 저장값을 덮어쓰지 않는다. `content_packs`는 실제 요청서를 생성할 때의 최종 스냅샷을 버전별로 계속 보존한다.

```text
trend_cluster_ai_profiles.content_plan_json
        ↓ 최초 기본값
topic_content_preferences
        ↓ 사용자가 수정한 글감별 기본값
content_packs (버전별 최종 요청서 스냅샷)
```

## API 키와 자동 실행

- `.env`에서 `NAVER_CLIENT_ID`, `NAVER_CLIENT_SECRET`을 읽음
- DB에는 키를 저장하지 않음
- `scripts/refresh_trends.py`가 화면 버튼과 동일한 수집·분석 흐름을 실행
- `run_trend_refresh.bat`이 예약 실행 진입점이며 프로젝트 폴더 기준의 가상환경 Python과 스크립트 절대 경로를 따옴표로 실행
- 화면 수동 수집·순위 재계산·예약 BAT는 `data/trend_refresh.lock`을 원자적으로 생성하는 단일 실행 잠금을 공유
- 잠금에는 PID·시작 시각·진입점·소유권 토큰을 저장하고, 활성 PID의 잠금은 유지하며 종료된 PID·깨진 잠금만 다음 실행에서 복구
- 동시에 들어온 두 번째 실행은 오류가 아닌 정상 생략으로 종료하며 API 호출과 DB 쓰기를 시작하지 않음
- BAT 또는 `scripts/refresh_trends.py` 실행은 모두 `background_refresh`, 화면 버튼은 `manual_refresh`, 순위 재계산 버튼은 `ranking_rebuild`로 기록
- 작업 스케줄러와 사용자의 직접 BAT 실행은 현재 신뢰할 수 있는 구분 신호가 없어 둘 다 `background_refresh`로 처리
- 설정 화면은 `ContentTrendTracker_AutoRefresh` 고정 작업명을 `schtasks /Create /SC MINUTE /MO <분> /F` 방식으로 등록
- 같은 이름에 `/F`를 적용하므로 주기 변경은 기존 작업을 새 간격으로 교체하고, 별도 삭제 버튼은 `schtasks /Delete` 사용
- 등록 작업은 현재 Windows 사용자가 로그인된 상태에서 제한된 권한으로 실행하고 PowerShell 숨김 창에서 BAT를 호출
- 작업 XML의 반복 간격과 현재 프로젝트 BAT 경로를 읽어 실제 등록 상태를 표시
- 설정 최대치 계산은 `포털 탐색어 × 뉴스·블로그 또는 웹문서·카페 2종 × 페이지 수`를 출처별 1회 계획 호출량으로 사용
- 정상 호출 최소 간격과 최대 2회 재시도까지 가정한 권장 간격을 분리 표시하며, 기존 일·월 쿼터 보호 로직은 그대로 유지
- 실제 사용량 계산은 최근 7일 `collection_runs`와 `collection_run_sources`에서 자동·수동 수집의 NAVER·Daum 요청만 제한 조회하고, 예측 표본은 성공·부분 성공으로 한정하되 최근 24시간 실제 호출에는 실패 요청도 포함
- `request_count`가 재시도를 포함하므로 `retry_count`를 다시 더하지 않고, 실행당 평균·최대 요청과 현재 주기의 하루 예상 사용률을 계산
- 출처별 표본 6회 미만은 설정 최대치 우선, 6~23회는 참고, 24회 이상은 안정적 표본으로 운영 판단을 표시
- 스케줄러 간격은 `app_settings.trend_refresh_interval_minutes`에 마지막 정상 등록값만 저장하며, 새 DB의 기본 입력값은 240분이다. 기존 DB와 이미 등록된 Windows 작업은 자동 변경하지 않는다.
- 예약 실행은 저장된 자동 분석 모델을 읽고 요청당 상위 65개 후보 한 묶음만 Gemini로 보낸다.

## 출처별 수집 실패 격리

`최신 데이터 수집·분석`과 `scripts/refresh_trends.py`는 YouTube, NAVER API HUB, Google Trends, Wikimedia 수집을 독립 단계로 처리한다.

```text
YouTube Parquet 가져오기 ─────┐
Google Trends RSS 수집 ────────┤
Wikimedia Pageviews 수집 ──────┼→ 사용 가능한 source_items로 순위 재계산
NAVER API HUB 수집 ────────────┤
Daum 검색 API 수집 ────────────┘
```

한 출처의 DNS·네트워크·인증 오류는 다른 출처의 정상 반영을 취소하지 않는다. 외부 응답 중 `external_id`가 없거나 정규화 가능한 제목이 없는 개별 항목은 `items_skipped`로 집계해 제외하고 정상 항목은 계속 저장한다. 각 오류는 사용자 메시지와 콘솔 경고로 분리해 남기며, 순위 계산 자체가 실패한 경우에만 전체 작업 실패로 처리한다.




YouTube Parquet는 파일 경로·크기·수정 시각과 가져오기 상한으로 서명을 만들고 `app_settings.youtube_parquet_last_import_signature`에 마지막 성공 값을 저장한다. 서명이 같고 기존 YouTube 신호가 존재하면 파일 읽기와 갱신을 생략한다.

## 외부 데이터 호출량과 NAVER 안전장치

- `api_usage_counters`: NAVER·Google Trends·Wikimedia 실제 요청 시도를 제공자별 일·월 단위로 로컬 집계
- 사이드바 사용량 영역은 같은 범용 카운터를 읽으므로 이후 출처도 동일 구조로 추가 가능
- `api_quota_service.py`: 사용자 일간·월간 한도와 공식 제공 한도를 비교하고 초과 전 요청 차단
- 기본 로컬 한도: 일 25,000회·월 775,000회(현재 무료 최대치)
- 공식 검색 API 한도: API Key 기준 일 25,000회·월 775,000회·최대 50 RPS
- 한도 초과 시 네이버 수집만 건너뛰고 YouTube와 저장 데이터 순위 계산은 계속
- 로컬 DB 카운터는 다른 앱이나 NAVER Cloud 콘솔의 사용량을 알 수 없으므로 콘솔 값을 최종 기준으로 사용
- 기존 월 20,000회 기본 설정은 정책 버전 마이그레이션으로 월 775,000회에 한 번 자동 전환
- 현재는 검색 API만 사용하며 Data Lab은 호출하지 않음
- Data Lab은 현재 월 최대 50,000회까지 0원이지만, 실제 유료 정책 적용 시 공지를 확인한 뒤 별도 제한 추가


## Daum 검색 통합

- 인증: 카카오디벨로퍼스 REST API 키 (`KAKAO_REST_API_KEY`)
- 엔드포인트: `/v2/search/web`, `/v2/search/cafe`
- 소스 타입: `daum_web`, `daum_cafe`
- 로컬 쿼터: 일 50,000회, 월 3,000,000회 상한
- 월간 3,000,000건은 카카오 앱 전체 API 공통 무료 쿼터이므로 카카오 콘솔이 최종 기준
- Daum 실패는 NAVER·YouTube·Google Trends·Wikimedia 처리와 격리
- 기본 수집 강도: 최대 50개 탐색어 × 웹문서·카페 2종 × 2페이지 = 실행당 최대 약 200회
- NAVER는 기본 6개, Daum은 기본 4개의 제한된 작업으로 HTTP 요청을 병렬 처리
- NAVER와 Daum의 네트워크 수집은 제공자 단위로 동시에 실행하지만 결과 저장은 메인 스레드에서 NAVER→Daum 순으로 수행
- 각 요청은 독립 결과로 수집해 일부 실패가 전체 출처 결과를 폐기하지 않으며, 성공 결과만 결정론적 작업 순서로 병합
- 429·5xx·타임아웃·일시 연결 오류는 최대 2회 재시도하고 인증·DNS 오류는 같은 배치의 대기 작업을 빠르게 생략
- 재시도 예산은 현재 일·월 안전 한도에서 계획 요청 수를 제외한 잔여량으로 제한
- `api_usage_counters`에는 계획 호출이 아니라 실제 HTTP 시도 횟수만 기록
- 네트워크 수집 후 DB 쓰기는 메인 스레드에서 staging DataFrame과 단일 UPSERT로 수행
- Streamlit 수집·재계산 작업이 대기 또는 실행 중일 때는 사이드바 탐색을 잠그고 `오늘의 트렌드` 화면을 유지한다. 장시간 콜백 중 페이지 이동으로 스크립트가 재실행되어 동일 작업이 중복 시작되는 것을 막기 위한 UI 실행 잠금이다. 작업 완료 또는 오류 처리 시 세션의 작업·진행 상태를 제거해 탐색을 다시 연다.
- 설정 키: `trend_portal_query_limit`, `trend_portal_pages_per_query`, `trend_results_per_query`, `naver_search_workers`, `daum_search_workers`, `trend_refresh_interval_minutes`
## 수동 콘텐츠 제작 흐름의 연속성과 중복 방지

콘텐츠 제작은 `AI 요청서 → AI 결과 가져오기 → 글 편집 → 발행 보조` 네 단계로 진행한다. 각 화면은 공통 단계 이동 UI를 사용하며 다음 단계로 이동할 때 선택한 `content_pack_id`와 `draft_id`를 Streamlit 세션 상태로 전달한다. 저장 데이터가 기준이므로 앱을 재시작한 뒤에도 최근 자료팩과 초안 목록에서 작업을 이어갈 수 있다.

AI 결과는 저장 전에 `content_pack_id`, AI 제공자, 원문 응답을 SHA-256 지문으로 묶는다. 검사 후 제공자나 JSON이 변경되면 기존 검사 결과는 저장 권한을 잃고 다시 검사를 요구한다. 이 지문은 UI의 오래된 검사 상태를 막기 위한 값이며 DB 스키마에는 저장하지 않는다.

`save_generation_and_draft`는 같은 자료팩·AI 제공자·원문 응답으로 이미 생성된 generation과 draft가 있으면 기존 ID를 반환한다. `mark_published`도 같은 초안·블로그 프로필·플랫폼·발행 URL의 완료 기록이 있으면 기존 기록을 반환한다. 이 멱등 처리는 더블 클릭이나 재시도로 인한 중복 행을 막되, 다른 AI 제공자·다른 응답·다른 블로그 프로필·다른 발행 URL은 별도 작업으로 허용한다.

블로그 연결은 자동 로그인이나 쿠키 자동화를 사용하지 않는다. `blog_profiles`에는 사용자에게 보이는 프로필 이름, 플랫폼 코드, 로그인·글쓰기 URL, 일반 텍스트 또는 Markdown 기본 형식, 기본 카테고리와 태그만 저장한다. 같은 플랫폼의 프로필 수는 프로그램에서 제한하지 않는다. 기존 `naver_write_url`, `tistory_write_url` 설정은 최초 추가형 마이그레이션에서 기본 프로필 두 개로 옮기며 이전 버전 호환을 위해 설정 행 자체는 유지한다. 프로필 숨기기는 소프트 삭제로 처리해 과거 `publish_records`의 참조를 보존한다.
## 글감 적합성 게이트

랭킹은 대표 제목 한 줄만 보지 않고 클러스터에 연결된 전체 원문 묶음을 함께 검사한다.

- 날짜·요일은 서로 다른 날짜를 구분하는 보조값으로 유지하지만 군집 후보를 만드는 주제 식별자로 사용하지 않는다.
- `업데이트`, `안내`, `정리`, `후기`, `비교` 같은 작성 형식 단어는 제품·서비스·인물·사건을 대신하는 글감 대상이 될 수 없다.
- 공백 없이 붙은 `2026년7월16일` 같은 날짜도 달력 식별자로 처리하며, `오늘의 운세`처럼 날짜와 반복 형식만 남은 주제는 글감 대상이 없는 것으로 판정한다.
- 운세·날씨·일일 뉴스 표현 자체는 구체 대상이 아니지만, 서비스명·지역명·사건명이 별도로 반복 확인되면 정상 후보로 유지한다.
- 랭킹 알고리즘 서명·최근 입력 상태·평가 날짜가 달라지면 오늘의 트렌드 진입 시 경량 상태 조회로 갱신 필요만 안내한다. 실제 재계산은 다음 자동·수동 수집 완료 시 또는 사용자가 순위 재계산 버튼을 눌렀을 때 수행한다.
- 대표 제목의 구체 식별자가 독립 중복 제거 근거 묶음 2곳 이상에서 확인되지 않으면 다중 원문 군집을 자동 보류한다.
- 구체 식별자가 없는 항목은 삭제하지 않고 전체 목록과 원문 감사에 남긴다.
- 동일 URL은 일반 식별자가 없어도 중복으로 묶을 수 있지만, 날짜만 같은 서로 다른 URL은 묶지 않는다.

## 테스트 임시 파일

`pytest.ini`에서 pytest 캐시 제공자를 끄고, 기본 테스트 임시 파일은 운영체제 임시 폴더를 사용한다. `run_tests.bat`는 별도 임시 폴더를 생성한 뒤 테스트 종료 시 제거한다. 프로젝트 루트에 남은 과거 임시 폴더는 `clean_test_artifacts.bat`로 정리한다.
## 글감 후보 근거 진단과 사용자 평가

- 후보 상세 진단은 `trend_cluster_items`와 `source_items`를 읽어 정규 URL 기준으로 중복을 제외한 실제 근거 수를 계산한다.
- 발행처·출처 종류·반복 식별 토큰·근거 시간 범위를 함께 표시해 대표 제목만 보고 글감을 선택하지 않도록 한다.
- 사용자 평가는 `trend_feedback`에 현재 군집 ID와 제목, 평가 유형, 메모, 당시 근거 통계를 스냅샷으로 저장한다.
- 평가는 랭킹 점수나 추천 판정을 자동 변경하지 않는다. `쓸모없는 글감`과 `잘못 묶인 주제`는 사용자가 선택한 화면 필터에서만 숨긴다.
- 군집 ID는 연결된 원문 집합으로 결정되므로 원문 구성이 바뀐 새 군집에는 과거 평가를 무리하게 자동 전이하지 않는다. 과거 평가는 후속 QA 분석 자료로 보존한다.


## 반복 포착과 시간 감쇠

- `source_items.first_imported_at`, `previous_imported_at`, `last_imported_at`, `observation_count`는 동일 원문이 서로 다른 수집 실행에서 다시 포착됐는지 기록한다. 기존 DB는 `imported_at`을 최초·최근 포착 시각으로 안전하게 채우고 횟수 1부터 시작한다.
- 재포착 점수는 신규 자료에 불이익을 주지 않는다. 같은 URL이나 복제 제목은 한 번만 세며, 최근 두 포착 간격이 짧고 반복 횟수가 많은 독립 근거가 여러 개일수록 높아진다.
- 재수집 시각은 반복성 판단에만 사용한다. 원문의 `published_at` 또는 `observed_at`을 덮어써 오래된 기사를 새 기사로 취급하지 않는다.
- 최종 트렌드 점수는 기존 최근성 감쇠에 재포착 보너스를 더하며, 상세 화면의 트렌드 지표와 점수 산정 이유에서 별도로 확인할 수 있다.
## DuckDB 연결 수명주기

수집 오케스트레이션은 다른 프로세스의 화면 사용 시간을 확보하기 위해 다음 경계를 지킵니다.

- 설정·쿼터·기존 신호·Gemini 대상 조회: 짧은 읽기/쓰기 연결
- 외부 HTTP 요청, YouTube 교환 파일 읽기, 군집 점수 계산, Gemini 응답 대기: DB 연결 없음
- 출처 결과, 순위 테이블, Gemini 결과와 실행 이력 저장: 묶음 단위 짧은 연결과 트랜잭션

`trend_refresh.lock`은 수집 파이프라인끼리의 중복 실행을 막고, DuckDB 파일 잠금은 실제 저장 구간의 프로세스 충돌을 막는다. 둘은 목적이 다르며 임의로 삭제하지 않는다.

