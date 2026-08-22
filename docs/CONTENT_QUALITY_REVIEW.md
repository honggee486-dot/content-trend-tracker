# Luna High 글 품질 감사 계약

## 목적

`content-trend-tracker`는 1차 글쓰기 결과를 그대로 이미지·발행 준비로 넘기지 않는다.

텍스트가 기본 JSON·출처 검사를 통과하면 **Luna High를 1회 편집·품질 감사자**로 사용하고,
Luna가 직접 글을 다시 쓰는 대신 기존 작성 모델에 전달할 수정 요청만 구조화한다.

정상 흐름:

```text
기존 작성 모델 1차 글
→ 기본 JSON·출처 검사
→ Luna High 텍스트 품질 감사 1회
→ pass면 그대로 다음 단계
→ revision_needed면 수정 요청서 생성
→ 같은 기존 작성 모델이 필요한 부분만 1회 수정
→ 기존 parser/출처/최종 사실 확인
→ 최종 텍스트 확정
→ 이미지 전략을 새로 계획
```

이미지·캡처·썸네일은 Luna 감사 대상에서 제외한다.

## 역할 분리

### 기존 작성 모델

- 실제 글 작성
- Luna 수정 요청 반영
- 기존 JSON schema 유지
- 최종 본문 기준 fact_checks 갱신

### Luna High

- 글 전체를 다시 쓰지 않음
- 문제 탐지
- 수정 필요 위치와 이유 설명
- 기존 작성 모델에 전달할 구체적인 수정 요청 작성
- 재작성 때 보존할 좋은 부분 표시

### 기존 검사기

- AI JSON 형식 검사
- source_id·출처 계약 검사
- 최종 결과 유효성 판정

Luna가 `pass`라고 해도 기존 최종 사실 확인을 생략하는 의미가 아니다.

## 검토 범위

Luna는 다음 텍스트 품질을 본다.

- 근거보다 강한 단정
- fact_checks/sources와 본문 주장 불일치
- 모순
- 중요한 정보 누락
- 검색 의도 불충족
- 반복·장황함
- 문단·소제목 구조
- 제목·요약·SEO와 본문 불일치
- 부자연스러운 키워드 반복
- AI 상투문구
- 가독성·명료성

Luna는 새 사실·수치·날짜·URL·source_id를 임의로 만들지 않는다.
근거가 부족하면 `삭제`, `표현 완화`, `공식 근거 재확인`을 수정 요청으로 남긴다.

## 출력 schema

```json
{
  "schema_version": "1.0",
  "review_status": "pass",
  "overall_reason": "현재 근거와 구조에서 실질 수정이 필요하지 않음",
  "revision_requests": [],
  "keep_points": [
    "공식 근거 중심 설명"
  ]
}
```

수정이 필요하면:

```json
{
  "schema_version": "1.0",
  "review_status": "revision_needed",
  "overall_reason": "일부 표현이 출처가 직접 뒷받침하는 범위보다 강함",
  "revision_requests": [
    {
      "severity": "high",
      "type": "fact_support",
      "target": "지원 대상 설명",
      "problem": "출처 범위보다 강한 단정",
      "request": "출처가 직접 뒷받침하는 범위로 표현을 완화"
    }
  ],
  "keep_points": [
    "첫 문단의 핵심 답변"
  ]
}
```

지원 type:

- `fact_support`
- `consistency`
- `coverage`
- `structure`
- `redundancy`
- `clarity`
- `seo`
- `style`

## 반복 제한

정상 케이스는:

- Luna 감사 최대 1회
- Luna가 `revision_needed`일 때 기존 작성 모델 수정 최대 1회

으로 한다.

Luna → 작성 모델 → Luna → 작성 모델의 무한 품질 루프를 만들지 않는다.

수정 모델 응답이 기존 parser를 통과하지 못하면 같은 글을 계속 재생성하지 않고 오류 상태로 남긴다.
사실 근거 부족도 모델을 계속 바꾸기보다 기존 사실 확인 경로에서 재검색·삭제·완화·사용자 확인으로 처리한다.

## 이미지 경계

1차 작성 결과에 image block이 있더라도 Luna 감사 입력에서는 제외한다.

Luna 통과 또는 수정 완료 뒤 만들어지는 `final_text_data`는:

- image block 제거
- `image_prompts=[]`
- 과거 `image_acquisition_plans` 제거

상태로 텍스트를 확정한다.

그 다음 최종 텍스트 기준으로 이미지 필요성과 위치를 다시 판단한다.

```text
최종 텍스트
→ 이미지 필요 여부
→ official_capture / generated / verified_free / 없음
→ 실제 획득
→ crop·resize·overlay·provenance
```

이렇게 해서 Luna 수정으로 문단이 이동·삭제됐는데 과거 이미지 계획을 그대로 실행하는 문제를 막는다.

## 수동/자동 작성과의 관계

자동 작성:

- 성공한 기존 작성 target을 수정 단계에서도 재사용하는 방향을 기본으로 한다.
- 동일 target 재사용이 불가능하면 임의의 다른 유료 모델로 바꾸지 않는다.
- 자동 작성 0원 fallback 계약은 별도로 유지한다.

수동 작성:

- 기존 ChatGPT 전달 흐름을 보존한다.
- Luna 수정 요청이 생기면 `원고 + 수정 요청`을 기존 수동 작성 흐름으로 다시 전달할 수 있게 한다.

Luna High는 자동 작성 모델 1~4순위 목록에 섞지 않는다.
작성자와 감사자의 책임을 분리한다.

## 현재 구현

`src/services/content_quality_review_service.py`가 다음 순수 계약을 제공한다.

- Luna 검토용 텍스트 payload 생성
- Luna 검수 prompt 생성
- 검수 JSON 파싱·검증
- 기존 작성 모델용 최소 수정 prompt 생성
- image 계획 제거 후 최종 텍스트 데이터 생성
- injected `luna_runner` / `writer_runner`로 1회 감사·1회 수정 cycle 실행

실제 Luna 인증·호출 방식과 자동 작성 executor 연결은 별도 통합 단계다.
특정 비공개 API·쿠키·세션을 추측해 사용하지 않는다.
