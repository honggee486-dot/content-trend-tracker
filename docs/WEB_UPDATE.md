# 로컬 웹 업데이트

## 목적

로컬 Streamlit 상단 네비게이션에서 원격 `origin/work/*` 누적 작업 브랜치를 확인하고,
기존 `apply_update.bat`의 작업 브랜치 모드로 검증·적용한 뒤 앱을 다시 실행한다.
로컬·원격 `main`을 변경하거나 push하지 않는다.

## 최초 적용

웹 업데이트 기능 자체를 로컬에 받기 전에는 프로젝트 터미널에서 한 번 직접 적용한다.

```powershell
.\apply_update.bat work/0.10.105
```

검증이 끝나면 다음 명령으로 앱을 실행한다.

```powershell
.\run_app.bat
```

## 이후 사용

1. 앱을 `localhost`, `127.0.0.1` 또는 `::1`로 연다.
2. 상단 네비게이션의 `앱 업데이트`를 눌러 메뉴를 펼친다.
3. `원격 브랜치 새로고침`으로 `origin/work/*`를 조회한다.
4. 기본 선택된 가장 높은 버전 또는 다른 적용 가능 브랜치를 확인한다.
5. 브랜치명·커밋·`ahead/behind`·변경 파일 수·원격 커밋 시각을 확인한다.
6. `선택한 작업 브랜치 적용 후 앱 재시작`을 누른다.

별도 확인 체크박스는 표시하지 않는다. 로컬 접속, 허용 브랜치, SHA 재확인, 작업 트리 청결, 실행 중 작업 차단이 모두 통과한 경우에만 적용 버튼이 활성화된다.

상단 메뉴를 닫은 상태에서는 원격 조회를 자동 실행하지 않는다. 사용자가 새로고침을 눌렀을 때만
원격 브랜치를 가져오며, 적용 직전에는 원격 SHA와 실행 상태를 다시 확인한다.

## 적용 조건

다음 조건을 모두 만족할 때만 버튼이 활성화된다.

- `origin`이 `honggee486-dot/content-trend-tracker`와 일치한다.
- 브랜치명이 `work/<버전>` 형식이다.
- 작업 브랜치가 최신 `origin/main`보다 `behind 0`, `ahead 1 이상`이다.
- 화면에서 확인한 원격 커밋 SHA가 적용 직전에도 동일하다.
- 로컬 미커밋·미추적 변경이 없다.
- 수집·출처 동기화·2단계 군집 작업이 실행 중이지 않다.
- 수집·군집 잠금 파일이 없다.
- 다른 웹 업데이트가 실행 중이지 않다.

## 실행과 복구

웹 요청은 임의 명령 문자열을 실행하지 않는다. 검증된 원격 브랜치명과 커밋만
별도 PowerShell 프로세스에 인수 배열로 전달한다. 별도 프로세스는 현재 Streamlit을
종료하고 기존 `apply_update.bat`을 실행한다.

앱은 PowerShell 프로세스 생성 요청만 성공했다고 업데이트 시작으로 간주하지 않는다.
최대 8초 동안 작업자가 `waiting_for_app` 이상의 상태를 실제로 기록했는지 확인한 뒤에만
시작 성공을 표시한다. 시작 전에 종료되거나 상태를 기록하지 못하면 현재 앱을 종료하지 않고
즉시 실패를 표시한다. Antigravity/ConPTY 터미널에서는 `DETACHED_PROCESS`를 사용하지 않고
새 프로세스 그룹으로 실행한다.

- 성공: 선택한 작업 브랜치에서 앱을 다시 실행한다.
- 시작 실패: 현재 Streamlit을 유지하고 부트스트랩 오류를 화면과 상태 파일에 기록한다.
- 검증·적용 실패: 가능한 경우 시작 전 로컬 브랜치와 커밋으로 돌아간 뒤 앱을 다시 실행한다.
- 자동 재시작 실패: 프로젝트 폴더에서 `.\run_app.bat`을 직접 실행한다.

상태와 로그는 Git 작업 트리를 더럽히지 않도록 저장소 밖에 기록한다.

```text
%LOCALAPPDATA%\content-trend-tracker\update_restart_status.json
%LOCALAPPDATA%\content-trend-tracker\update_restart_bootstrap.log
%LOCALAPPDATA%\content-trend-tracker\update_restart.log
```

- `update_restart_bootstrap.log`: PowerShell 시작·구문·인수 오류
- `update_restart.log`: `apply_update.bat` 검증과 적용 출력

이 기능은 로컬 전용이다. LAN 주소나 프록시를 통해 접속한 화면에서는 적용 버튼을 사용할 수 없다.
