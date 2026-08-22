# 공개 공식 페이지 캡처 Executor

## 목적

`content-trend-tracker`의 `official_capture` 계획을 실제 공개 페이지의 근거 영역 screenshot으로 연결하기 위한 실행 계층이다.

이 계층은 이미지 전략을 새로 판단하지 않는다. 기존 `content_pack_image_acquisition_service.py`에서 다음 조건을 통과해 `status=ready`, `action=capture_public_source`가 된 공식 캡처만 실행 대상으로 삼는다.

## 구현

- `src/services/content_pack_public_capture_service.py`
  - 공개 URL 및 DNS/IP 안전 검사
  - Chrome/Edge 자동 탐색
  - 임시 `--user-data-dir` 기반 격리 Headless Chromium
  - CDP `Fetch` request interception으로 HTTP(S) 요청을 실행 직전에 다시 검사
  - redirect/Document 요청이 로그인·로컬·사설 IP로 향하면 차단
  - `capture_anchor` visible text 탐색
  - 0개면 `needs_review`
  - 서로 다른 semantic container가 여러 개면 `needs_review`
  - 1개면 가까운 table/section/article/figure/main/card/table 영역을 선택
  - Bounding Box clip screenshot
  - PNG SHA-256과 provenance 저장
  - 브라우저 종료와 임시 프로필 정리
- `src/services/content_pack_public_capture_runtime.py`
  - 성공 결과를 `captured_image`에 연결
  - 실패는 `manual_review`로 안전 전환
  - 로그인·게시·사용자 브라우저 상태는 변경하지 않음

## 저장

기본 캡처 파일은 Git에서 제외된 `exports/captures/` 아래에 저장한다.

provenance 최소 필드:

- source_id
- source_url
- final_url
- page_title
- capture_target
- capture_anchor
- capture_note
- captured_at
- browser_engine
- region_locator
- clip_rect
- sha256
- safety_checked

Secret, 쿠키, 세션, 사용자 프로필 경로는 저장하지 않는다.

## 안전 경계

- `http`/`https` 공개 URL만 허용
- URL credential 금지
- localhost, `.local`, 사설·loopback·link-local·reserved IP 금지
- DNS 해석 결과에 비공개 주소가 하나라도 포함되면 금지
- login/signin/auth/oauth/account/billing/dashboard/admin/console 성격 경로 금지
- 브라우저의 각 HTTP(S) request도 CDP interception에서 다시 검사
- 최종 URL도 다시 검사
- 기존 Chrome/Edge 사용자 프로필 및 쿠키를 import하지 않음
- 자동 로그인·CAPTCHA 우회·개인 대시보드 접근 금지
- Anchor가 없거나 모호하면 추측 클릭 없이 `needs_review`
- 공식 사이트의 사진·인물·홍보 자산은 자동 재사용 대상으로 간주하지 않음

DNS 검사와 브라우저의 실제 연결 사이에는 일반적인 DNS rebinding의 이론적 시간차가 남을 수 있으므로, 캡처 실행기는 공개 근거 화면에만 제한하고 내부망 접근 권한이 있는 환경에서는 보수적으로 운영한다.

## 검증

`tests/test_content_workflow_public_capture_executor.py`는 기본 pytest/하네스에서 외부 네트워크를 사용하지 않는다.

실제 Chrome/Edge 스모크는 다음 환경변수를 명시한 로컬/Agent 검증에서만 실행한다.

```powershell
$env:CONTENT_TREND_BROWSER_SMOKE = "1"
python -m pytest -q tests/test_content_workflow_public_capture_executor.py
```

스모크 종료 후 환경변수는 필요에 따라 제거한다.

## 현재 연결 상태

Executor 기반은 구현하지만 **AI 결과를 파싱하자마자 자동 캡처하지 않는다.**

정상 제작 순서는 계속 다음과 같다.

```text
1차 글쓰기
→ Luna High 감사
→ 필요한 최소 수정
→ 최종 사실·출처 검사
→ 최종 텍스트 확정
→ 이미지 전략 재계획
→ ready official_capture만 이 executor로 실행
```

즉 현재 단계의 후속 작업은 최종 텍스트 이후 이미지 orchestration에서 이 실행기를 호출하도록 연결하고, 생성 이미지·무료 자산과 공통 후처리/본문 삽입을 완성하는 것이다.
