# Baton 한국어 안내

[English (primary)](README.md) | 한국어 안내

이 문서는 Baton을 한국어로 빠르게 이해하고 설정하기 위한 안내서입니다. 동작과 명령의 기준 문서는 영문 [README.md](README.md) 및 각 영문 상세 문서입니다. 번역과 영문 내용이 다르면 영문 문서와 설치된 CLI의 `--help` 출력을 우선합니다.

## 개요

Baton은 같은 저장소에서 작업하는 Codex app의 role agent들이 다음 작업을 SQLite로 조율할 수 있게 하는 CLI입니다.

- role 간 handoff 등록, 대기, claim, 완료 및 취소
- handoff 및 named Gate 의존성 관리
- Markdown 본문과 SQLite 상태를 결합한 CR(change request) 심사
- role별 권한과 안정적인 agent profile 관리
- 제한된 wait, shift, stop/resume 제어
- 감사 이력과 요약 보고서 조회

SQLite가 workflow 상태의 기준이며 agent는 DB를 직접 수정하지 않고 `bin/baton`을 사용해야 합니다. CR의 구체적인 본문은 Markdown이 기준이고, Baton은 해당 파일의 frontmatter 상태를 DB와 동기화합니다.

## 실행 조건

- Python 3.10 이상과 표준 라이브러리 `sqlite3`
- `.baton/` 상태 디렉터리를 만들 수 있는 로컬 파일 시스템
- macOS 또는 Linux 권장
- Windows는 현재 테스트하지 않음
- 런타임용 외부 Python 패키지는 필요하지 않음

`pipx`는 Baton을 사용자 명령으로 설치할 때만 사용하는 선택 도구이며 런타임 의존성이 아닙니다.

테스트 실행에는 `bash`, `mktemp`, `awk`, `grep`, `sed`, `sleep`이 추가로 필요합니다.

## 빠른 시작

소스 checkout에서는 `bin/baton`을 사용합니다.

```bash
bin/baton --version
bin/baton init
bin/baton migrate --check
bin/baton role list
bin/baton status
```

`pipx install .`로 설치한 뒤에는 Baton 저장소가 아니라 관리할 프로젝트에서 명령을 실행합니다.

```bash
cd /path/to/your-project
baton guide show bootstrap
baton init
baton migrate --check
baton status
```

기본 DB 경로 `.baton/baton.sqlite3`는 `baton`을 실행한 현재 프로젝트를 기준으로 결정됩니다. 사용자 단위의 빠른 설치에는 pipx가 적합하지만, 프로젝트 저장소가 Baton 버전을 직접 기록해야 하면 release tag에 고정한 submodule을 사용합니다. 자세한 내용은 [설치 가이드](docs/using-baton-in-projects.md)를 확인합니다.

`pipx` 명령이 없다면 Baton을 설치하기 전에 pipx를 먼저 설치합니다.

macOS와 Homebrew 환경:

```bash
brew install pipx
pipx ensurepath
```

Ubuntu 23.04 이상:

```bash
sudo apt update
sudo apt install pipx
pipx ensurepath
```

`pipx ensurepath` 실행 후 새 터미널을 열고 설치를 확인합니다.

```bash
pipx --version
```

그 밖의 Linux 배포판과 설치 방식은 [pipx 공식 설치 안내](https://pipx.pypa.io/latest/how-to/install-pipx.html)를 따릅니다. 소스 checkout에서 `bin/baton`을 직접 실행하는 경우에는 pipx가 필요하지 않습니다.

현재 내부 검증 브랜치는 다음 명령으로 설치할 수 있습니다.

```bash
pipx install "git+https://github.com/karl27hg/agents-baton.git@codex/pipx-packaging"
baton --version
```

pipx 설치는 OS 사용자당 한 번만 하면 됩니다. 프로젝트마다 다시 설치하지 말고 각 프로젝트 루트에서 `baton init`을 실행합니다.

```bash
cd /path/to/project-a
baton init

cd /path/to/project-b
baton init
```

두 프로젝트는 동일한 설치 실행파일을 사용하지만 각각 `project-a/.baton/baton.sqlite3`와 `project-b/.baton/baton.sqlite3`를 사용합니다. pipx 패키지는 실행파일과 현재 버전에 맞는 agent guide를 함께 설치하지만 프로젝트의 `AGENTS.md`는 자동으로 변경하지 않습니다. `AGENTS.md`에서 `baton guide show bootstrap`, `baton guide show worker`, `baton guide show planner`를 역할에 맞게 읽도록 요구해야 합니다.

기존에 `tools/baton` 아래에서 Baton을 사용한 프로젝트라면 기본 DB가 보이지 않는다는 이유로 새 DB를 초기화하지 않습니다. 먼저 쓰기 없이 자동 탐색과 migration 가능 여부를 검사합니다.

```bash
baton project migrate --check
```

자동으로 찾지 못하면 기존 DB 경로를 지정합니다. 경로를 지정해도 즉시 migration하지 않고 동일한 검사를 수행합니다.

```bash
baton project migrate --check --source-db /path/to/existing/baton.sqlite3
```

출력된 원본, 대상, schema, 대기 agent 수를 확인하고 agent를 중지한 뒤 `plan_token`으로 적용합니다.

```bash
baton project migrate --apply --plan-token <token>
```

검사 때 `--source-db` 또는 `--project-root`를 사용했다면 적용할 때도 같은 값을 사용합니다. Baton은 변경된 원본, 활성 waiter, 호환되지 않는 DB, 복수 후보, 서로 다른 기존 대상 DB를 거부하며, 적용 전 `.baton/backups/`에 검증된 백업을 만듭니다.

Git tag 설치의 버전을 변경할 때는 새 tag를 명시하여 교체하고 각 프로젝트 DB를 migration합니다.

```bash
pipx install --force "git+https://github.com/karl27hg/agents-baton.git@vNEW.VERSION"
baton --version

cd /path/to/your-project
baton migrate
baton migrate --check
```

이전 Baton이 새 schema를 지원하지 않을 수 있으므로 schema migration 후 임의로 downgrade하지 않습니다. 설치 버전을 고정하려면 `pipx pin agents-baton`, 다시 업그레이드를 허용하려면 `pipx unpin agents-baton`을 사용합니다.

```bash
pipx uninstall agents-baton
```

Uninstall은 `baton`, `baton-report` 명령과 pipx 가상환경만 제거합니다. 각 프로젝트의 `.baton/` DB와 감사 이력은 삭제하지 않습니다.

기본 DB 경로는 `.baton/baton.sqlite3`입니다. 다른 DB를 사용하려면 모든 명령에 `--db`를 지정합니다.

```bash
bin/baton --db /tmp/baton.sqlite3 init
```

## 문서 구성

영문 문서를 기준으로 다음 순서로 읽는 것을 권장합니다.

1. [Agent bootstrap](docs/agent-bootstrap.md): 설치 명령, 프로젝트 확인, 기존 DB migration 안전 절차
2. [README.md](README.md): 전체 기능, 기본값, 명령 및 운영 규칙
3. [다른 프로젝트에서 사용하기](docs/using-baton-in-projects.md): 설치, 버전 고정, 프로젝트 설정
4. [Named Gate 운영](docs/gates.md): Gate 소유권, 해제, 취소, 긴급 이관
5. [Planner prompt](docs/planner-prompt.md): 병렬 작업의 독립성 판정과 의존성 등록 정책
6. [Agent prompt](docs/agent-prompt.md): Baton worker agent에 추가할 영문 prompt
7. [Agent 사용법](docs/agent-usage.md): role, CR, wait, shift 명령 예시
8. [SQLite schema](docs/schema.md) 또는 [한국어 번역](docs/schema.ko.md): 테이블과 migration 명세
9. [CHANGELOG.md](CHANGELOG.md): 버전별 변경 사항

`docs/agent-prompt.md`는 Codex role agent가 직접 따를 명령 규칙이므로 영문 원본을 agent 지시 사항에 연결하는 것을 권장합니다.

## SM Agent 설정 순서

새 DB를 초기화하거나 기존 DB를 migration합니다.

```bash
bin/baton init
bin/baton migrate
bin/baton migrate --check
```

role과 권한을 확인합니다.

```bash
bin/baton role list
bin/baton role permission-list sm
bin/baton-report summary
```

프로젝트의 실제 role을 Baton role에 대응시키고 CR 심사 role에는 필요한 권한만 부여합니다.

```bash
bin/baton role add content-design --display-name "Content Design"
bin/baton role alias-add fe frontend
bin/baton role permission-add architecture cr.review
bin/baton role permission-add architecture cr.approve
```

`sm`은 기본적으로 CR 권한, `handoff.cancel`, 긴급 `gate.manage` 권한을 갖습니다. 사용자 수준 인증은 Baton의 범위가 아니므로 OS 계정, 저장소 권한 및 agent 운영 정책으로 별도 통제해야 합니다.

## 기본 Handoff 흐름

작업을 등록하고 대상 role이 대기, claim, 완료합니다.

Planning agent는 병렬 handoff를 등록하기 전에 [Planner prompt](docs/planner-prompt.md)를 따라야 합니다. 입력, 수정 대상, contract, 공유 상태와 완료 순서가 모두 독립적인 작업만 병렬로 열고, 하나라도 불확실하면 `--depends-on` 또는 named Gate로 순서를 명시합니다. Baton은 선언된 의존성과 claim 원자성을 보장하지만 소스 파일 충돌이나 누락된 의존성을 추론하지는 않습니다.

```bash
bin/baton register \
  --title "Frontend follow-up" \
  --role frontend \
  --source-ref "docs/change-requests/CR-example.md" \
  --objective "Implement the approved change." \
  --exit-criteria "The behavior is implemented and verified."

bin/baton wait --role frontend --timeout 900
bin/baton next --role frontend
bin/baton claim HO-YYYY-MM-DD-001 --role frontend
bin/baton finish HO-YYYY-MM-DD-001 \
  --role frontend \
  --evidence "Verification passed."
```

`next`는 한 번만 확인하는 비대기 명령입니다. 작업이 없다는 이유로 agent가 종료되면 안 되며, shift가 활성 상태인 동안 제한된 `wait`를 반복해야 합니다.

## CR 흐름

CR 본문은 Markdown에 작성하고 상태 전환은 Baton으로 수행합니다.

```bash
bin/baton cr create \
  --title "Upload policy" \
  --author-role planning \
  --reviewer-role sm

bin/baton cr submit CR-YYYY-MM-DD-001 --role planning
bin/baton cr wait-review --role sm --timeout 900
```

보강이 필요하면 reviewer가 revision handoff를 생성합니다. 작성 role은 Markdown 본문을 수정하고 재심사를 요청한 뒤 revision handoff를 완료합니다.

```bash
bin/baton cr request-revision CR-YYYY-MM-DD-001 \
  --role sm \
  --reason "Clarify the acceptance criteria."

bin/baton cr resubmit CR-YYYY-MM-DD-001 \
  --role planning \
  --evidence "Acceptance criteria clarified."
```

승인과 구현 handoff 생성은 별도 결정입니다. 자세한 명령과 심사 권한은 [영문 README의 Change Request Flow](README.md#change-request-flow)를 따릅니다.

## Wait와 자원 사용

`wait`와 `cr wait-review`는 작업이 없을 때 `time.sleep()`으로 대기하므로 busy loop로 CPU를 계속 점유하지 않습니다.

- 기본 timeout: 900초
- 기본 polling interval: `auto`
- 자동 목표 interval: `min(30초, 3초 × 같은 DB의 활성 waiter 수)`
- 고정 interval override: `--interval N`, 최소 1초
- 정상적인 최대 반응 지연: 현재 interval 정도
- 종료 코드 `0`: 작업 발견
- 종료 코드 `2`: timeout, shift가 활성 상태이면 다시 대기
- 종료 코드 `3`: stop 상태, resume 전까지 재시도하지 않음

`--interval`을 생략하는 것은 `--interval auto`를 명시하는 것과 동일합니다.

종료 코드 `2`는 shift가 활성 상태인 동안 내부 bounded-loop 경계일 뿐 사용자에게 보고할 진행 상태가 아닙니다. Agent는 상태 변화가 없으면 timeout이나 대기 메시지를 반복하지 않고 즉시 다시 대기합니다. 작업 발견, claim/finish, stop 또는 shift 만료, 개입이 필요한 오류, 사용자의 상태 요청이 있을 때만 한 번 보고합니다.

```bash
bin/baton wait --role frontend --timeout 900
bin/baton wait --role frontend --timeout 900 --interval auto
```

일반 `wait`는 주기마다 stop/shift 확인, 의존성 및 Gate 조정, role queue 조회를 수행합니다. Baton은 handoff와 CR waiter를 같은 `waiter_leases` table에 heartbeat로 등록하고 활성 수에 비례해 interval을 자동으로 늘립니다. 한 명은 3초, 두 명은 각각 6초, 열 명 이상은 각각 최대 30초를 목표로 하며 동시 polling을 줄이는 작은 jitter가 적용됩니다.

정상 종료 시 lease는 즉시 제거됩니다. 자동 waiter의 process 연결이 끊기면 30초 lease가 만료되고 이후 heartbeat가 stale record를 정리합니다. 25초를 초과하는 고정 interval은 정상 sleep을 보호하기 위해 `interval + 5초` lease를 사용합니다. 숫자 interval을 명시한 waiter도 활성 수에는 포함되지만 자신의 sleep은 지정된 값으로 고정됩니다.

단일 및 10개 동시 waiter의 CPU, memory, DB 증가와 한계는 영문 기준 문서인 [Baton v0.5.0 idle wait resource check](docs/benchmarks/v0.5.0-idle-wait-resource.md)에 기록되어 있습니다. 이 자료는 저장소 evidence로 유지하며 별도의 release 첨부 파일로 배포하지 않습니다.

## Shift와 중지

```bash
bin/baton shift start --role frontend
bin/baton shift extend --role frontend
bin/baton shift status --role frontend
bin/baton shift end --role frontend --reason "End of day"
```

`shift start`의 기본 duration은 4시간, `shift extend`의 기본 duration은 1시간입니다. shift가 만료되면 새로운 wait와 claim은 중지되지만 이미 claim한 작업의 `finish` 보고는 허용됩니다.

즉시 대기 제어를 변경하려면 다음 명령을 사용합니다.

```bash
bin/baton stop --role frontend --reason "Pause polling"
bin/baton resume --role frontend
bin/baton stop --all --reason "End of day"
bin/baton resume --all
```

`stop`은 handoff를 취소하지 않습니다. 실행 중인 wait는 다음 polling 시점에 stop을 확인하므로 자동 모드에서는 활성 waiter 수에 따라 최대 약 30초가 걸릴 수 있습니다.

## 다른 프로젝트에 설치

안정적인 운영에는 Baton 저장소를 Git submodule로 추가하고 검증된 release tag에 고정하는 방식을 권장합니다.

```bash
mkdir -p tools
git submodule add git@github.com:karl27hg/agents-baton.git tools/baton
cd tools/baton
git checkout vX.Y.Z
```

`vX.Y.Z`는 사용할 검증된 Baton release tag로 바꿉니다.

다른 프로젝트 루트에서 `tools/baton/bin/baton`을 실행하면 해당 프로젝트의 `.baton/`에 runtime DB가 생성됩니다. 자세한 `.gitignore`, `AGENTS.md`, prompt 설정은 [설치 가이드](docs/using-baton-in-projects.md)를 확인합니다.

## 버전 변경과 DB 보존

Baton 버전을 변경한 뒤 role agent를 시작하기 전에 migration을 명시적으로 수행합니다.

```bash
bin/baton migrate
bin/baton migrate --check
```

Migration은 version이 지정되어 있고 transaction 단위로 실행되며 반복 실행할 수 있습니다. 기존 handoff, CR, event, control, role, permission 데이터는 보존되고 실패한 migration은 rollback됩니다. DB보다 오래된 Baton binary는 더 새로운 schema를 수정할 수 없습니다.

## 감사와 요약

`bin/baton-report`는 DB를 읽기 전용으로 엽니다.

```bash
bin/baton-report summary
bin/baton-report summary --format json
bin/baton-report audit
bin/baton-report audit --role frontend
bin/baton-report audit --format csv
```

## 제한 사항

- Markdown handoff 파일 자체의 import/export는 제공하지 않습니다.
- Handoff claim/finish 권한은 대상 role 기준이며 사용자 인증은 외부 정책에 맡깁니다.
- CR 심사는 role 권한을 사용하지만 Baton만으로 실제 사용자를 인증하지 않습니다.
- 이 저장소의 Baton DB는 다른 프로젝트의 활성 workflow 상태가 아닙니다.

## 라이선스

Baton은 Apache License 2.0으로 배포됩니다. [LICENSE](LICENSE)와 [NOTICE](NOTICE)를 확인하십시오.
