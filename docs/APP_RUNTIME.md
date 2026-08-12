# 앱 실행·종료·웹 업데이트 운영 계약

## 목적

`content-trend-tracker`는 Streamlit 프로세스를 터미널과 분리된 숨김 앱으로 임의 재실행하지 않는다. 한 개의 전경 supervisor가 Streamlit 자식 프로세스를 소유하고, 실행·웹 업데이트·재시작을 같은 생명주기 안에서 관리한다. 별도 종료 도구는 supervisor가 비정상 종료된 경우에도 등록된 프로세스만 정확히 정리한다.

## 전용 주소

- 주소: `http://127.0.0.1:8518`
- 바인딩: 로컬 루프백 전용
- 다른 Streamlit 프로젝트가 기본 포트 `8501`을 사용해도 이 앱과 충돌하지 않는다.
- 포트 `8518`이 다른 프로세스에 점유되면 임의의 다음 포트로 이동하지 않고 점유 PID를 표시한 뒤 실행을 중단한다.

## 실행

```powershell
.\run_app.bat
```

`run_app.bat`은 프로젝트 가상환경 Python을 우선 확인한 뒤 `scripts/app_supervisor.ps1 -Action Run`을 현재 터미널에서 실행한다.

supervisor는 다음을 수행한다.

1. 이름 있는 mutex로 동일 프로젝트 supervisor의 중복 실행을 차단한다.
2. 전용 포트 `8518`이 비어 있는지 확인한다.
3. Streamlit을 자식 프로세스로 실행하고 PID·시작 시각·포트·프로젝트 경로를 상태 파일에 기록한다.
4. 브라우저에서 전용 주소를 한 번 연다.
5. 터미널의 `Ctrl+C` 또는 종료 요청을 받으면 등록된 자식 프로세스 트리를 정리한다.

실행 상태는 저장소가 아니라 다음 사용자 로컬 디렉터리에 기록한다.

```text
%LOCALAPPDATA%\content-trend-tracker\app_runtime.json
```

실제 DB, `.env`, 저장소 `data`와 Git 작업 트리에는 런타임 PID 상태를 만들지 않는다.

## 종료

현재 실행 터미널에서 `Ctrl+C`를 누르거나 다음 명령을 사용한다.

```powershell
.\stop_app.bat
```

`stop_app.bat`은 `scripts/stop_registered_app.ps1`을 호출한다. 이 스크립트는 상태 파일의 프로젝트 경로·전용 포트·supervisor PID·Streamlit PID와 각 프로세스 시작 시각을 대조한 뒤 등록된 프로세스 트리만 종료한다. 모든 `python.exe` 또는 모든 Streamlit 프로세스를 일괄 종료하지 않는다.

supervisor가 이미 종료됐지만 등록된 Streamlit 자식이 남아 있으면 해당 자식의 PID와 시작 시각을 다시 확인한 뒤 정리한다. 반대로 상태 파일 없이 포트만 점유된 경우에는 다른 프로젝트일 수 있으므로 점유 PID를 안내하고 임의 종료하지 않는다. PID가 다른 프로세스에 재사용된 경우에도 해당 PID를 종료하지 않는다.

등록 프로세스 정리 후 전용 포트 `8518`이 실제로 해제됐을 때만 런타임 상태와 대기 중 업데이트 요청을 삭제한다.

## 웹 업데이트

웹 업데이트 버튼은 더 이상 Streamlit이 자신을 종료한 뒤 숨김 `run_app.bat`을 새로 실행하지 않는다.

1. 현재 Streamlit이 `run_app.bat` supervisor에 등록된 인스턴스인지 확인한다.
2. 현재 Streamlit PID와 supervisor PID의 시작 시각을 포함한 업데이트 요청을 사용자 로컬 상태 디렉터리에 기록한다.
3. 요청 전용 프로세스가 현재 Streamlit만 종료한다.
4. 기존 supervisor가 종료를 감지하고 `apply_update.bat work/<version>`을 실행한다.
5. 검증 성공 시 같은 supervisor가 같은 포트로 Streamlit을 다시 실행한다.
6. 검증 실패 시 현재 작업 상태로 앱을 다시 실행하고 실패 상태를 화면에 남긴다.

따라서 웹 업데이트 후에도 원래 Antigravity 터미널이 앱 생명주기를 계속 소유한다. 이후 같은 터미널에서 `Ctrl+C`를 누르면 재시작된 앱까지 함께 종료된다.

웹 업데이트 요청과 결과는 다음 파일을 사용한다.

```text
%LOCALAPPDATA%\content-trend-tracker\app_update_request.json
%LOCALAPPDATA%\content-trend-tracker\update_restart_status.json
%LOCALAPPDATA%\content-trend-tracker\update_restart.log
```

업데이트 요청은 supervisor PID, Streamlit PID, 각 프로세스 시작 시각, 프로젝트 경로, 포트, 브랜치와 예상 커밋이 모두 일치할 때만 처리한다.

## 기존 실행 방식에서 전환

이 구조가 처음 적용되는 시점에는 이전 웹 업데이트가 만든 숨김 Streamlit 인스턴스가 남아 있을 수 있다. 최초 적용은 실행 중 수집·군집 작업이 없는 상태에서 터미널의 `apply_update.bat work/<version>`으로 수행하고, 기존 앱을 모두 종료한 뒤 새 `run_app.bat`으로 시작한다.

이전 실행 방식은 새 런타임 상태 파일을 만들지 않았으므로 최초 전환 시 `stop_app.bat`이 알 수 없는 8501 포트 프로세스를 임의로 종료하지 않는다. 해당 포트의 점유 프로세스 명령줄을 확인해 기존 `content-trend-tracker` Streamlit임을 확인한 경우에만 수동으로 종료해야 한다.

이후에는 웹 업데이트가 별도 숨김 앱을 만들지 않는다.

## 검증 범위

자동 테스트는 다음 계약을 확인한다.

- 실행과 종료 BAT가 전용 포트 `8518`과 등록 상태를 사용함
- `.streamlit/config.toml`도 동일 포트를 고정함
- 이름 있는 mutex와 PID 시작 시각으로 단일 인스턴스를 확인함
- 종료 도구가 등록된 supervisor·Streamlit만 대상으로 하고 전체 Python 프로세스 종료 명령을 사용하지 않음
- 상태 파일 없이 포트만 점유된 경우 임의 종료하지 않음
- 웹 업데이트가 supervisor에 요청만 전달하고 실제 적용·재시작은 supervisor가 수행함

실제 Windows 터미널에서의 `Ctrl+C`, 포트 해제, 브라우저 재연결과 웹 업데이트 재시작은 로컬 적용 후 수동 확인이 필요하다.
