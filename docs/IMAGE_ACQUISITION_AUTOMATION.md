# 이미지 자동 획득 라우팅 계약

## 목적

`content-trend-tracker`는 블로그에 필요한 모든 이미지를 AI로 생성하지 않는다.
글의 목적과 근거 성격에 따라 **공식 화면 캡처 / 검증된 무료 자산 / 0원 생성 이미지** 중 적합한 획득 방식을 자동으로 계획하고, 이미지가 불필요한 글에는 image 블록을 만들지 않는다.

정상 목표:

```text
검증된 AI 결과
→ image 블록별 획득 전략 확인
→ 공식 화면 캡처 / 검증된 무료 자산 / 생성 이미지
→ 자동 획득
→ 공통 crop·resize·overlay·출처 메타 처리
→ draft/publish 준비
```

한 글에 여러 전략을 함께 사용할 수 있다. 예를 들어 대표 이미지는 생성하고 본문에는 정부 공식 안내 화면과 통계 표를 캡처할 수 있다.

## 전략

### `official_capture`

가격·정책·지원 조건·신청 절차·통계·공식 기능·사양·일정처럼 **공식 화면 자체가 독자의 확인에 도움이 되는 경우** 우선한다.

새 요청서의 `source_capture`에는 다음을 둔다.

- `needed`
- `source_id`
- `source_url`
- `capture_target`
- `capture_anchor`
- `capture_note`
- `checked_at`

`capture_anchor`는 자동 브라우저가 근거 영역을 찾을 때 사용할 짧고 구체적인 페이지 표시 문구다. 과거 schema 2.1 결과에는 이 필드가 없을 수 있으므로 파싱 호환성은 유지한다. 다만 anchor가 없으면 자동 캡처 준비 완료로 보지 않고 `needs_review`로 둔다.

자동 캡처 준비 완료 조건은 최소한 다음과 같다.

- 공개 `http/https` 페이지
- 검증된 `sources`의 `source_id`와 URL 일치
- `capture_target` 존재
- `capture_anchor` 존재
- localhost, 사설/로컬 IP, 로그인·계정·관리자 성격 URL이 아님

자동 캡처 실행기는 다음 안전 경계를 지켜야 한다.

- 새 임시·격리 브라우저 문맥
- 기존 Chrome 프로필/쿠키 가져오기 금지
- 자동 로그인 금지
- CAPTCHA 우회 금지
- 개인 대시보드/결제/계정 화면 캡처 금지
- 공개 페이지에서 필요한 근거 범위만 캡처

실제 브라우저 executor는 별도 단계에서 연결한다. 현재 계획 계층은 executor가 없어도 어떤 캡처가 안전하게 자동 실행 가능한지 먼저 판정해 draft에 보존한다.

### `generated`

공식 화면이 사실 근거로 필요하지 않고 설명용 일러스트가 더 적절할 때 사용한다.

현재 1차 생성 provider 기반은 Cloudflare Workers AI의 검토된 FLUX.1-schnell 경로다. 생성 경로는 비용이 발생하는 provider/model로 자동 전환하지 않는다.

### `verified_free`

기존 무료 이미지 2중 확인 계약을 유지한다.

- 개별 자산 페이지
- 별도 공식 라이선스 페이지
- 상업 이용 허용
- 결제/Premium/구독/크레딧 요구 없음

을 확인할 수 있는 경우에만 자동 준비 후보가 된다. 권리 조건을 확인할 수 없으면 자동 사용하지 않는다.

### 이미지 없음

이미지가 글 이해에 의미 있게 기여하지 않으면 image 블록 자체를 만들지 않는다. 이미지 개수를 채우기 위해 생성·캡처하지 않는다.

## 현재 구현

`src/services/content_pack_image_acquisition_service.py`가 image 블록을 실행 준비 계획으로 정규화한다.

주요 결과:

- `strategy`
- `status`: `ready` / `needs_review`
- `action`: `capture_public_source` / `generate_zero_cost_image` / `download_verified_free` / `manual_review`
- 위치·목적·caption·alt
- 공식 출처와 캡처 target/anchor/note
- 생성 prompt 또는 무료 자산 페이지
- `zero_cost_only`
- 공개 캡처의 격리·비로그인 안전 플래그

`content_pack_image_acquisition_runtime.py`는 기존 capture runtime 뒤에서 새 schema 2.1 결과에 이 계획을 붙인다. 계획은 각 `image_prompts`에도 복사되므로 기존 `drafts.image_prompts_json` 저장 경로를 그대로 사용해 DB schema 변경 없이 draft까지 보존된다.

정상 자동 계획(`status=ready`)은 최종 본문에 `내가 할 일` 같은 운영 메모를 삽입하지 않는다. 과거 결과나 자동 실행 조건이 부족한 캡처는 기존 호환 흐름을 유지하면서 사용자 확인 메모를 남길 수 있다.

## 현재 단계와 다음 단계

이번 단계가 완료하는 것은 **자동 획득 전략·실행 준비 데이터·안전 게이트·draft 보존 계약**이다.

아직 자동 실행하지 않는 부분:

1. 실제 공개 페이지 브라우저 로드
2. `capture_anchor` 기반 DOM/근거 영역 탐색
3. target 영역 screenshot
4. 캡처 파일 provenance 저장
5. 생성/무료 자산과 공통 crop·resize·overlay
6. 최종 본문 이미지 삽입 및 플랫폼 전달

실제 브라우저 executor를 채택하기 전 현재 Windows 환경에서 사용할 브라우저/자동화 방식과 격리 프로필 동작을 확인한다. 브라우저 실행 검증은 로컬 환경 근거가 필요한 단계로 분리한다.

## 권리·출처 원칙

공공·공식 사이트라는 이유만으로 페이지의 모든 사진·인물 이미지·저작물을 자유 재사용 가능하다고 간주하지 않는다.

자동 캡처 우선 대상은 글의 사실 설명에 직접 필요한 공개된 표·통계·요금·조건·신청 절차·공식 UI 등의 근거 영역이다. 사진·인물·홍보 이미지처럼 별도 권리 판단이 필요한 자산은 이용 조건이 명확하지 않으면 `needs_review`로 남긴다.

캡처 결과를 영구 저장할 때는 최소한 다음 provenance를 보존한다.

- source URL / source ID
- publisher / page title
- capture target / anchor
- captured_at
- 글 내 사용 목적
- 필요한 경우 이용 조건·출처 표시 정보

Secret, 사용자 로그인 상태, 쿠키, 개인 계정 정보는 캡처 메타데이터에 저장하지 않는다.
