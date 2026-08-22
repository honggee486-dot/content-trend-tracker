# 블로그 대표 이미지 생성 계약

## 목적

`content-trend-tracker`의 대표 이미지는 초고품질 아트 생성보다 **일관성·저비용·자동화·재현성**을 우선한다.

1차 경로는 Cloudflare Workers AI의 `@cf/black-forest-labs/flux-1-schnell`을 사용한다.
이미지 모델은 배경/일러스트만 생성하고, 제목·카테고리·브랜드 라벨은 프로그램의 별도 오버레이 단계가 담당한다.

이 기능은 본문 `image` 블록과 별개의 **글 대표 이미지(cover/hero)** 계약이다.

## 2026-08-22 공식 확인 기준

Cloudflare 공식 문서에서 다음을 확인했다.

- 모델 ID: `@cf/black-forest-labs/flux-1-schnell`
- 작업: text-to-image
- REST endpoint: `POST /accounts/{account_id}/ai/run/{model_name}`
- 인증: Cloudflare API Token
- 모델 문서의 입력: `prompt`, `steps`; 공식 사용 예제에서 `seed` 사용
- `prompt` 최대 2048자
- `steps` 기본 4, 최대 8
- 생성 결과는 Base64 이미지로 반환되는 모델 계약
- Workers AI 무료 할당량: 하루 10,000 Neurons
- 무료 할당량 소진은 HTTP 429 / 내부 코드 `3036`
- 일부 모델은 Paid plan이 필요할 수 있으므로 다른 유료 이미지 모델로 자동 fallback하지 않음

모델별 문서에는 이 모델의 `width`/`height`가 명시 입력으로 노출되지 않으므로
1차 구현은 임의로 해당 필드를 보내지 않는다. 따라서 `16:9`는 **모델 원본 출력 크기 보장**이 아니라
후처리 최종 캔버스 계약이다.

참고:
- https://developers.cloudflare.com/workers-ai/models/flux-1-schnell/
- https://developers.cloudflare.com/workers-ai/get-started/rest-api/
- https://developers.cloudflare.com/workers-ai/platform/pricing/
- https://developers.cloudflare.com/workers-ai/platform/errors/

## 1차 구현 범위

`src/services/content_pack_representative_image_service.py`

현재 구현은 다음 책임만 갖는다.

1. 글 제목·카테고리·요약에서 대표 이미지 배경 프롬프트 생성
2. 카테고리 성격에 맞는 최소 스타일 프리셋 적용
3. 제목·숫자·로고·워터마크 등을 이미지 모델이 직접 그리지 않도록 명시
4. Cloudflare REST 요청 생성
5. `prompt + seed + steps`만 모델 요청에 전달
6. JPEG/PNG Base64 결과 검증
7. 일시 오류에 제한적 재시도
8. 무료 할당량 소진·Paid plan 요구는 재시도/유료 전환 없이 종료
9. 재현성 메타데이터 반환
10. 최종 1280×720 오버레이 레이아웃 계약 반환

실제 API 호출은 API 키가 없으면 네트워크 요청 전에 차단된다.

## 프롬프트 원칙

기본 방향:

- clean editorial illustration
- modern
- trustworthy
- professional
- wide cinematic composition
- 16:9 최종 crop을 고려한 여백
- 하나의 명확한 시각적 주제

금지:

- readable text
- letters / numbers
- captions
- logos / trademarks
- signatures
- watermark
- UI
- cluttered composition

실제 제목은 프롬프트의 **주제 의미 전달**에는 사용하지만, 제목 문자열을 이미지 안에 렌더링하라고 요청하지 않는다.

## 스타일 프리셋

1차는 최소 프리셋만 유지한다.

- `general`
- `technology`
- `economy_policy`
- `lifestyle`
- `health`

알 수 없는 카테고리/프리셋은 `general`로 안전하게 정규화한다.
실사용 결과 없이 프리셋을 계속 늘리지 않는다.

## 실패 처리

기본 최대 시도 횟수는 2회이며 최대 설정 가능값은 3회다.

재시도 후보:
- HTTP 408
- HTTP 429 중 일시 rate-limit/capacity
- HTTP 5xx 일부
- 네트워크/timeout

즉시 중단:
- 인증/권한/잘못된 요청
- 잘못된 모델
- Base64 또는 이미지 형식 검증 실패
- 일일 무료 할당량 소진 (`3036`)
- Paid plan 필요 (`5035`)

Google 이미지 API나 다른 유료 모델로 자동 전환하지 않는다.

## 최종 16:9 및 오버레이 계약

최종 대표 이미지 표준:

- 1280×720
- 16:9
- 템플릿: `hero_v1`
- 제목 최대 3줄
- 카테고리 라벨 선택 가능
- 모델 생성 텍스트 사용 금지
- 배경은 `center_cover` 방식으로 최종 캔버스에 맞추는 방향

현재 서비스는 `build_overlay_spec()`으로 이 계약을 반환한다.
실제 raster crop/overlay와 폰트 선택은 별도 단계로 연결한다.

폰트 파일을 저장소에 포함하지 않는다. Windows 실사용에서는 시스템에 존재하는 한글 폰트를 사용하고,
향후 크로스플랫폼 렌더링이 필요할 때 라이선스가 확인된 별도 폰트 전략을 검토한다.

## 메타데이터 계약

서비스 결과는 다음을 보존할 수 있게 한다.

- provider
- model
- prompt
- seed
- steps
- created_at
- status
- error_type / error_message
- final_width / final_height
- aspect_ratio
- style_preset
- overlay_template
- draft_id
- image_stage

이미지 원본 bytes 자체는 메타데이터 직렬화에 포함하지 않는다.

DB schema는 이번 단계에서 변경하지 않는다.
대표 이미지 파일/메타데이터의 실제 영구 저장 위치와 draft/publish 연결은
후속 통합 단계에서 기존 데이터 보존 규칙을 확인한 뒤 추가형 방식으로 결정한다.

## 저장 위치 방향

실제 파일 저장 연결 시 원본과 최종본은 분리한다.

```text
data/generated_images/representative/<draft_id>/
  original.<ext>
  final.png
  metadata.json   # DB 메타 저장을 채택하지 않는 경우에만 검토
```

`data/*`는 기존 `.gitignore` 보호 범위이므로 생성 결과를 Git에 포함하지 않는다.

## 라이선스·사용 조건

Cloudflare 모델 페이지는 Black Forest Labs의 현재 Terms and License로 연결한다.
현재 BFL 약관은 생성 Input/Output을 `Your Content`로 다루며 회사가 소유권을 주장하지 않는다고 설명하지만,
실제 사용은 당시의 Cloudflare/BFL 약관·Usage Policy와 적용 법률을 따른다.

프로그램은 AI 생성물을 실제 사건 사진이나 사람이 촬영한 사실 자료처럼 오인시키는 용도로 사용하지 않는다.
약관 변경 가능성이 있으므로 provider/model 또는 사용 목적이 바뀌면 다시 확인한다.

## 후속 연결 순서

1. 자동/수동 작성 결과가 기존 검사기를 통과해 `draft`가 만들어지는 지점에 대표 이미지 생성 트리거 연결
2. 생성된 background를 1280×720로 crop
3. 제목·카테고리 오버레이 렌더러 연결
4. 원본/최종 이미지 저장
5. 대표 이미지 메타데이터를 draft/publish 흐름에 추가형으로 연결
6. 발행 보조에서 대상 블로그별 대표 이미지 준비
7. 실사용 품질을 보고 프리셋/템플릿 추가 여부 판단

Google 이미지 API는 품질 부족이 실제 반복 확인될 때만 수동 선택형 고품질 재생성 후보로 검토한다.
