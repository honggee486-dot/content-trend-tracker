# Blogger OAuth·API 사전점검

Blogger 공식 API 비공개 초안 전송 전에 로컬 OAuth 준비 상태를 비밀값 노출 없이 확인합니다.

## 원클릭 실행

프로젝트 루트에서 다음 BAT를 실행합니다.

```powershell
.\run_blogger_preflight.bat
```

개인정보 제한 JSON 결과가 필요하면 다음과 같이 실행합니다.

```powershell
.\run_blogger_preflight.bat --json
```

사용자 지정 경로를 검사할 수도 있습니다.

```powershell
.\run_blogger_preflight.bat `
  --client C:\path\to\blogger_oauth_client.json `
  --token C:\path\to\blogger_oauth_token.json
```

## 검사 항목

- Google API Python 의존성 설치 여부
- OAuth 클라이언트가 `데스크톱 앱` 구조인지
- 클라이언트 필수 인증 항목 존재 여부
- 로컬 브라우저 승인용 loopback 리디렉션
- 로컬 OAuth 토큰 JSON 구조
- Blogger API 권한 범위
- 액세스 토큰 만료와 갱신 토큰 존재 여부

검사기는 클라이언트 ID, 클라이언트 비밀값, 액세스 토큰과 갱신 토큰의 실제 값을 출력하지 않습니다.

## 종료 코드

- `0`: Blogger API 호출 준비 완료
- `1`: 의존성 또는 OAuth 설정 보완 필요
- `3`: 데스크톱 OAuth 클라이언트는 준비됐지만 Google 계정 연결 필요

## 안전 범위

사전점검은 다음 작업을 수행하지 않습니다.

- Google 또는 Blogger 네트워크 요청
- DuckDB 연결이나 쓰기
- OAuth 승인 브라우저 열기
- 토큰 갱신
- 블로그 목록 조회
- 초안 생성 또는 게시

실제 계정 연결과 Blogger API 호출은 사용자가 Streamlit 화면에서 해당 버튼을 눌렀을 때만 수행합니다.
