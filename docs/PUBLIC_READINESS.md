# Public 저장소 전환·유지 안전 기준

이 문서는 저장소 visibility를 Public으로 바꾸기 전의 전체 감사와, Public 전환 뒤 현재 추적 트리에 민감 자료가 다시 들어오는 실수를 조기에 탐지하는 지속 안전 게이트를 정의한다. visibility 변경 자체는 이 문서나 검사 스크립트가 자동 수행하지 않는다.

## 공개 범위 원칙

공개 대상은 프로그램 소스, 테스트, 일반 문서와 값이 비어 있는 예제 설정이다. 다음 자료는 Git에 포함하지 않는다.

- 실제 `.env`, API 키, OAuth token, cookie/session, 인증서와 private key
- 실제 DuckDB/SQLite DB, Parquet/Arrow/Feather 교환 데이터
- 로그, 보고서, export, backup, archive와 브라우저 프로필
- 실제 사용자 원문·글감·자료팩·초안·발행 기록
- 로컬 캐시, 가상환경, 빌드·테스트 산출물, IDE/OS 임시파일

`.gitignore`는 실수 방지 장치일 뿐 과거 Git 이력을 정리하지 않는다. Public 전환 판단에서는 현재 추적 파일과 reachable history를 별도로 검사한다.

## 전체 refs 준비

Public 전환 직전에는 기본 브랜치만 검사하지 말고 원격 heads/tags와 GitHub PR head refs를 로컬 감사 refs로 가져온다. 아래 명령은 원격 branch를 수정하지 않는다.

```powershell
git fetch --prune origin
git fetch origin "+refs/heads/*:refs/remotes/origin/*" "+refs/tags/*:refs/tags/*" "+refs/pull/*/head:refs/public-audit/pull/*"
```

이후 `git rev-list --objects --all`에서 접근 가능한 이력을 검사할 수 있다.

## Secret 검사

검증된 Secret scanner인 Gitleaks를 PATH에서 실행할 수 있게 준비한 뒤 프로젝트 루트에서 다음을 실행한다.

```powershell
python scripts/check_public_readiness.py
```

검사기는 다음을 수행한다.

- 현재 Git 추적 파일의 민감 파일명·확장자 차단
- reachable history의 민감 파일명·확장자 탐색
- 과거 archive/base64/분할 payload처럼 수동 검토가 필요한 경로 표시
- PR audit refs를 가져왔는지 확인
- commit 이메일은 실제 값을 출력하지 않고 마스킹해 개인정보 노출 여부 표시
- Gitleaks를 `--redact`, 전체 이력, archive/decode depth 제한과 함께 실행

Gitleaks가 설치되지 않았거나 finding을 반환하면 공개 준비 통과로 처리하지 않는다.

## Public 전환 후 지속 검사

Public 전환 뒤에는 전체 Git history와 Gitleaks를 매 push마다 다시 검사하지 않는다. 대신 일반 CI에서 다음의 빠른 현재 트리 검사만 수행한다.

```powershell
python scripts/check_public_readiness.py --tracked-only
```

`--tracked-only`은 `git ls-files`로 현재 커밋에서 추적되는 경로만 확인하고 `.env`, 실제 DB·Parquet, 로그, credential·cookie·key·archive 등 차단 패턴이 하나라도 있으면 실패한다. `.env.example`과 `data/.gitkeep`처럼 명시적으로 허용된 예제·빈 자리 파일은 통과한다.

이 검사는 Gitleaks 설치, 전체 history, PR audit refs, commit 이메일 또는 과거 archive 수동 검토 상태에 의존하지 않으므로 `main`과 `work/**`의 일반 CI에서 항상 실행할 수 있다. 반대로 **push 뒤 실행되는 CI이므로 Secret이 원격에 올라가기 전에 막아 주는 사전 차단 도구는 아니다.** `.gitignore`와 커밋 전 확인이 1차 방어이며, CI 검사는 현재 공개 트리에 보호 대상 파일이 남아 있는 실수를 빠르게 발견하는 2차 안전망이다.

새 history 정리, 과거 Secret 의심, 공개 범위 변경이 생기면 `--tracked-only` 통과만으로 충분하다고 보지 않고 아래 전체 Public readiness 감사를 다시 수행한다.

## 과거 archive·인코딩 payload

과거 reachable history에 archive, `.b64`, `.part*` 또는 임시 payload가 있으면 파일명만으로 안전하다고 판단하지 않는다. 표시된 경로를 실제로 복원·검토하고 Secret·실데이터·제3자 재배포 문제가 없음을 확인한 뒤에만 다음 명령으로 수동 검토 완료를 명시할 수 있다.

```powershell
python scripts/check_public_readiness.py --acknowledge-history-review
```

이 옵션은 Gitleaks finding, 실제 민감 파일 이력, 현재 추적 민감 파일 또는 누락된 PR refs를 무시하지 않는다.

## Secret 발견 시

실제 credential이 발견되면 값은 출력하거나 문서에 복사하지 않는다.

1. 해당 credential의 revoke/rotate 필요성을 먼저 판단한다.
2. 현재 파일 삭제만으로 해결됐다고 보지 않는다.
3. 영향받는 branch/tag/PR ref와 기존 clone 재유입 위험을 확인한다.
4. 필요한 history rewrite는 별도 고위험 작업으로 수행한다.

공개 준비 검사기는 revoke, history rewrite, force push, branch 삭제를 자동 실행하지 않는다.

## 개인정보와 문서

commit author 이메일이 개인 주소인 경우 공개 여부를 별도로 판단한다. 과거 이메일을 제거하려면 Git history 변경이 필요하므로 공개 안전 검사와 분리한다.

README, `AI_CONTEXT.md`, `docs/NEXT_WORK.md` 등에는 실제 사용자 데이터·계정 식별자·비공개 프로젝트의 내부 정보가 들어가지 않게 한다. 운영에 필요한 구조 설명과 기술적 roadmap은 공개 가능한 수준으로만 유지한다.

## 라이선스

Public visibility와 오픈소스 라이선스 부여는 별개다. 저장소에 LICENSE가 없는 상태에서는 프로젝트 저작권 정책을 별도로 결정한 뒤 필요할 때만 LICENSE/NOTICE/THIRD_PARTY_LICENSES를 추가한다.

외부 코드·이미지·폰트·데이터·바이너리를 새로 포함하는 경우에는 원 라이선스와 attribution/NOTICE/재배포 조건을 다시 확인한다.

## 최종 게이트

Public 전환 직전 최소 조건은 다음과 같다.

- `scripts/check_public_readiness.py` 최종 실행 결과 `ready: True`
- 현재 추적 민감 파일 0건
- 과거 민감 파일 경로 0건
- Gitleaks 전체 이력 통과
- archive/인코딩 이력 수동 검토 완료 또는 해당 refs의 안전한 정리 완료
- 실제 DB·API·로그·사용자 데이터가 GitHub에 포함되지 않음
- 공개할 문서·PR 기록·commit 이메일 범위를 사용자가 수용함
- 제3자 자료의 재배포 조건에 미확인 차단 요소가 없음

이 조건을 통과한 뒤에만 visibility 변경을 별도 작업으로 진행한다. Public 전환 뒤에는 `--tracked-only` CI를 유지하고, history 또는 공개 범위에 변화가 있을 때 전체 감사를 다시 수행한다.
