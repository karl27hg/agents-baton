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

SQLite가 workflow 상태의 기준이며 agent는 DB를 직접 수정하지 않고 `bin/baton`을 사용해야 합니다. CR의 구체적인 본문은 Markdown이 기준이고, Baton은 해당 파일의 frontmatter 상태를 DB와 동기화하며 제출·승인 본문 hash를 검증합니다.

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
baton project info
baton status
```

`baton init`은 선택한 디렉터리에 `.baton/project.json`, `.baton/baton.sqlite3`, `.baton/.gitignore`를 만듭니다. 새 CR 본문은 기본적으로 `.baton/change-requests/`에 생성됩니다. 이후 하위 디렉터리에서는 가장 가까운 상위 marker를 찾아 동일한 DB를 사용하므로 Git 존재 여부와 무관하며, 프로젝트를 이동하거나 전체 복사해도 경로를 다시 등록할 필요가 없습니다. 다른 위치에서 초기화하려면 `baton init --project-root PATH`를 사용합니다.

Marker는 있지만 DB가 없으면 workflow 이력 복구가 필요한 상태로 판단합니다. `init`은 이 경우 빈 DB를 만들어 기존 이력을 대체하지 않습니다.

명령 문법과 옵션은 일반적인 `-h`, `--help` 또는 `help`로 확인합니다. `help`는 중첩된 하위 명령도 지정할 수 있습니다.

```bash
baton -h
baton help
baton help wait
baton help cr wait-review
baton help project migrate
```

`guide`는 단순 명령 목록이 아니라 agent가 따라야 할 운영 정책을 출력합니다. 두 기능 모두 pipx 설치에 포함됩니다.

```bash
baton guide list
baton guide show bootstrap
baton guide show worker
baton guide show planner
baton guide show git
baton guide show upgrade
baton guide show changelog
```

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

두 프로젝트는 동일한 stateless 설치 실행파일을 사용하지만 각각 독립된 marker, DB, waiter, control 및 실행 프로세스를 사용합니다. pipx 패키지는 실행파일과 현재 버전에 맞는 agent guide를 함께 설치하지만 프로젝트의 `AGENTS.md`는 자동으로 변경하지 않습니다. `AGENTS.md`에서 `baton guide show bootstrap`, `baton guide show worker`, `baton guide show planner`를 역할에 맞게 읽도록 요구해야 합니다. 실행파일을 업데이트한 뒤에는 agent를 재개하기 전에 `baton guide show upgrade`로 현재 릴리스 조치와 `AGENTS.md` 점검 항목을 확인하고, 이전 버전을 건너뛴 경우 `baton guide show changelog`에서 그 사이 변경점도 확인합니다.

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

Git tag 설치의 버전을 변경하기 전에 현재 호환되는 Baton으로 각 활성 프로젝트를 사전 점검합니다. `upgrade preflight`는 global maintenance stop이 설정되고 active waiter, handoff, cancellation acknowledgement, claimed CR review가 모두 없어야 `READY`와 exit `0`을 반환합니다.

```bash
cd /path/to/your-project
baton upgrade preflight
baton stop --all --reason "Baton upgrade"
baton upgrade preflight

pipx install --force "git+https://github.com/karl27hg/agents-baton.git@vNEW.VERSION"
baton --version

baton guide show upgrade
baton guide show changelog
baton migrate
baton migrate --check
baton project info
# bundled upgrade guide와 프로젝트 AGENTS.md를 대조합니다.
baton resume --all
```

preflight가 출력한 ID를 모두 정리한 뒤 실행파일을 교체합니다. 새 실행파일을 먼저 설치한 경우에도 알려진 구버전 schema에 대해 preflight 진단은 가능하지만, 실제 finish/fail/review transition은 이전 호환 Baton으로 처리해야 합니다. 이전 Baton이 새 schema를 지원하지 않을 수 있으므로 schema migration 후 임의로 downgrade하지 않습니다. 설치 버전을 고정하려면 `pipx pin agents-baton`, 다시 업그레이드를 허용하려면 `pipx unpin agents-baton`을 사용합니다.

정식 배포된 schema migration은 append-only로 유지하므로 오래 사용하지 않은 프로젝트도 다음 사용 시 여러 tag를 건너뛰어 최신 schema로 올릴 수 있습니다. 지원 범위는 정식 Baton schema와 인식 가능한 과거 unversioned DB이며 임의의 개발 snapshot, 수동 변경 schema 및 downgrade는 포함하지 않습니다. Schema v14는 기존 알림 row를 보존하면서 handoff attempt 내부의 `delivery_attempt`, recovery 원본 알림 ID와 사유를 추가합니다.

```bash
pipx uninstall agents-baton
```

Uninstall은 `baton`, `baton-report` 명령과 pipx 가상환경만 제거합니다. 각 프로젝트의 `.baton/` DB와 감사 이력은 삭제하지 않습니다.

기본 DB 경로는 marker와 같은 `.baton/`에 있는 `.baton/baton.sqlite3`입니다. 한 논리 프로젝트에서 Git worktree를 분리한 경우에는 worktree마다 초기화하지 않고 branch 밖의 공통 control DB를 지정합니다.

```bash
export BATON_DB=/absolute/path/to/project-control/.baton/baton.sqlite3
export BATON_WORKSPACE_ROOT="$PWD"
export BATON_AGENT_ID=backend-main
baton project info
```

모든 agent의 `project info`에 같은 DB가 나와야 합니다. `BATON_WORKSPACE_ROOT`는 선택적 Git 검사가 현재 source checkout을 보도록 하며 기본값은 현재 디렉터리입니다. 임의 DB 경로는 진단과 격리 테스트에도 사용할 수 있습니다.

```bash
bin/baton --db /tmp/baton.sqlite3 init
```

`<control-root>/.baton/baton.sqlite3` 형태의 외부 DB는 해당 control root의 config와 CR 경로를 사용합니다. 임의 이름의 외부 DB에는 암묵적인 project root가 없으므로 CR 파일은 절대 경로를 사용해야 합니다.

## 문서 구성

영문 문서를 기준으로 다음 순서로 읽는 것을 권장합니다.

1. [Agent bootstrap](docs/agent-bootstrap.md): 설치 명령, 프로젝트 확인, 기존 DB migration 안전 절차
2. [Upgrade guide](docs/upgrade-guide.md): 현재 릴리스 변경점, migration 및 `AGENTS.md` 재검토 항목
3. [README.md](README.md): 전체 기능, 기본값, 명령 및 운영 규칙
4. [다른 프로젝트에서 사용하기](docs/using-baton-in-projects.md): 설치, 버전 고정, 프로젝트 설정
5. [Named Gate 운영](docs/gates.md): Gate 소유권, 해제, 취소, 긴급 이관
6. [Planner prompt](docs/planner-prompt.md): 병렬 작업의 독립성 판정과 의존성 등록 정책
7. [Agent prompt](docs/agent-prompt.md): Baton worker agent에 추가할 영문 prompt
8. [Agent 사용법](docs/agent-usage.md): role, CR, wait, shift 명령 예시
9. [선택적 Git workspace 연동](docs/git-integration.ko.md) 또는 [영문 원본](docs/git-integration.md): `off`, `warn`, `strict`, checkout 및 override 정책
10. [SQLite schema](docs/schema.md) 또는 [한국어 번역](docs/schema.ko.md): 테이블과 migration 명세
11. [CHANGELOG.md](CHANGELOG.md): 버전별 변경 사항
12. [Release process](docs/release-process.md): 배포 승인 전 구현·문서·가이드 정합성 점검

`docs/agent-prompt.md`는 Codex role agent가 직접 따를 명령 규칙이므로 영문 원본을 agent 지시 사항에 연결하는 것을 권장합니다.

Baton으로 작업하는 agent는 subagent, child task, 병렬 agent session 또는 위임용 background agent를 직접 생성하지 않아야 합니다. 모든 위임은 설정된 role을 대상으로 하는 Baton handoff로 등록합니다. 설치된 guide에도 이 정책이 포함되지만 Baton CLI가 host의 agent 도구를 비활성화할 수는 없으므로, 실제 강제 규칙은 사용하는 프로젝트의 `AGENTS.md` 또는 동등한 host 정책에도 명시해야 합니다.

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
bin/baton role permission-add architecture handoff.register
bin/baton role permission-add architecture notification.observe
```

`sm`은 기본적으로 CR 권한, `handoff.cancel`, `handoff.register`, `handoff.evidence_correct`, `notification.observe`, 긴급 `gate.manage`, `workspace.override` 권한을 갖습니다. 신규 프로젝트의 `planning`도 실패 handoff 또는 blocking 완료 결과를 결정하는 데 필요한 등록·취소·증거 정정·알림 관찰·CR 심사 권한을 받습니다. Schema v15 migration은 새 `notification.observe` 권한을 `sm`과 `planning`에만 부여합니다. Schema v7로 올리는 기존 프로젝트는 이전 등록 동작을 깨지 않도록 기존 active role 모두에게 `handoff.register`를 승계하며, SM이 정책 검토 후 불필요한 권한을 철회할 수 있습니다. 사용자 수준 인증은 Baton의 범위가 아니므로 OS 계정, 저장소 권한 및 agent 운영 정책으로 별도 통제해야 합니다.

## 기본 Handoff 흐름

작업을 등록하고 대상 role이 대기, claim, 완료합니다.

Planning agent는 병렬 handoff를 등록하기 전에 [Planner prompt](docs/planner-prompt.md)를 따라야 합니다. 입력, 수정 대상, contract, 공유 상태와 완료 순서가 모두 독립적인 작업만 병렬로 열고, 하나라도 불확실하면 `--depends-on` 또는 named Gate로 순서를 명시합니다. Baton은 선언된 의존성과 claim 원자성을 보장하지만 소스 파일 충돌이나 누락된 의존성을 추론하지는 않습니다.

worker 결과를 planner가 다시 통합·검토해야 하면 모든 필수 worker job을 `--depends-on`으로 연결한 planning handoff를 마지막에 등록합니다. 이 작업은 최초 planner 개인이 아니라 planning role queue로 돌아가므로 다른 planner도 수행할 수 있도록 계약과 판단 근거를 완결되게 기록해야 합니다. planner/SM이 CR 심사와 handoff를 겸하면 `baton watch --role planning --timeout 900`으로 CR을 우선 확인한 뒤 handoff를 확인하고, shift가 활성인 동안 처리 후 다시 `watch`로 진입합니다.

Baton은 agent가 최종 응답을 보낸 뒤 새로운 Codex 턴을 스스로 만들 수 없습니다. 지속 순환하려면 agent가 shift가 활성인 동안 최종 응답으로 작업을 끝내지 않고 같은 턴에서 bounded `watch`를 반복해야 합니다.

```bash
bin/baton register \
  --title "Frontend follow-up" \
  --role frontend \
  --source-ref "cr:CR-example" \
  --objective "Implement the approved change." \
  --exit-criteria "The behavior is implemented and verified."

bin/baton wait --role frontend --timeout 900
bin/baton next --role frontend
bin/baton handoff show HO-YYYY-MM-DD-001
bin/baton claim HO-YYYY-MM-DD-001 --role frontend
bin/baton finish HO-YYYY-MM-DD-001 \
  --role frontend \
  --evidence "Verification passed." \
  --outcome pass \
  --commit HEAD
```

`finish`는 lifecycle 완료를 기록합니다. 완료된 검수나 분석 결과는 `--outcome pass|fail|conditional|inconclusive`로 구분하고, 성공을 전제로 한 후행 작업을 막아야 하는 non-pass 결과에는 `--blocking`을 사용합니다. 판단 CR이 여러 개면 `--outcome-cr CR-...`를 반복합니다. 첫 CR은 호환용 `outcome_cr_id`로 유지되지만 blocking 해소 여부는 연결된 모든 CR을 기준으로 판단합니다. 완료 후 독립 blocker가 더 발견되면 `handoff.evidence_correct` 권한 role이 `handoff outcome-cr-link HO-... --cr CR-... --role planning --reason "..."`로 append-only 연결을 추가합니다. 반면 handoff 자체가 exit criteria를 충족하지 못해 재시도 또는 취소 심사가 필요하면 `finish --outcome fail`이 아니라 `baton fail`을 사용합니다.

명시한 `--commit`은 로컬 Git commit으로 해석되어 canonical full ID로 저장됩니다. 외부 또는 아직 fetch하지 않은 reference를 의도적으로 기록하려면 `--allow-unresolved-commit`과 `--unresolved-reason`을 함께 사용해야 합니다. 잘못 기록한 완료 commit은 원 행을 수정하지 말고 원 claimant 또는 `handoff.evidence_correct` 권한 role이 다음처럼 정정 이력을 추가합니다.

```bash
bin/baton handoff evidence-correct HO-YYYY-MM-DD-001 \
  --role planning \
  --commit <correct-commit> \
  --reason "완료 보고의 commit reference를 정정합니다."
```

일반 dependency는 lifecycle의 `finished` 이후를 의미하며 검수 성공을 뜻하지는 않습니다. 따라서 `completion_outcome=fail`인 finished 작업도 일반 후행 작업을 열 수 있습니다. 성공 결과가 필수인 구현은 named Gate 뒤에 두고 planner 또는 reviewer가 결과를 확인한 뒤 Gate를 release 또는 cancel해야 합니다.

Claim한 작업이 exit criteria를 충족할 수 없다면 `finish` 대신 실패를 보고합니다.

```bash
bin/baton fail HO-YYYY-MM-DD-001 \
  --role frontend \
  --reason "승인된 API contract로 필요한 상태를 표현할 수 없습니다." \
  --evidence "Contract test failure: tests/api-contract.sh"
```

`fail`은 job을 `failed`로 바꾸고 연결된 실패 CR을 자동 제출하며 모든 하위 handoff를 `blocked`로 유지합니다. 기본 reviewer는 `planning`이고 planning 자체의 실패는 자기 심사를 피하기 위해 `sm`으로 배정됩니다. 다른 reviewer는 `handoff.register`와 필요한 CR 심사 권한을 보유해야 합니다.

실패 CR을 승인한 reviewer는 원래 job을 재시도할 수 있습니다.

```bash
bin/baton cr approve CR-YYYY-MM-DD-001 --role planning --evidence "수정안으로 재시도합니다."
bin/baton retry HO-YYYY-MM-DD-001 \
  --role planning \
  --cr-id CR-YYYY-MM-DD-001 \
  --reason "심사된 수정안을 적용합니다."
```

대상 role은 다시 열린 job을 새로 claim해야 합니다. `retry`는 원래 job의 attempt를 증가시키며 수정 baseline에 대한 새 알림도 이 attempt에 기록됩니다. 재시도하지 않기로 결정하면 실패 CR을 거절한 뒤 `cancel`을 실행하며, 이때 해당 blocked dependency branch만 연쇄 취소됩니다.

`in_progress` 작업을 취소하면 즉시 최종 취소되지 않고 `cancel_requested`가 됩니다. 원래 claimant는 먼저 작업을 멈추고 `events`에서 취소 요청 사유를 확인합니다. 검토 결과 기존 작업에 문제가 없다면 `handoff.cancel` 권한이 있는 planner/SM이 `cancel-withdraw HO-... --role sm --reason "..."`로 요청을 철회할 수 있습니다. 기존 claimant와 시작 시각은 유지되며, claimant는 `handoff show`에서 다시 `in_progress`가 된 것을 확인한 후 재claim 없이 이어서 작업합니다. 취소 또는 대체된 CR에 연결된 구현 작업은 원 설계가 폐기되었으므로 철회할 수 없습니다. 취소가 확정된 경우에만 claimant가 중단 내용과 남은 변경을 evidence로 기록해 `cancel-ack`를 실행합니다. claimant가 유실된 경우에만 SM이 사유와 함께 `cancel --force`를 사용합니다.

### Codex peer 알림 선택 사용

Baton이 Codex 메시지를 직접 보내지는 않습니다. 기존 Codex task가 `agent session-set --role <role> --agent-id <profile> --host codex --thread-id <id> --model <model>`로 자신의 런타임 주소와 모델을 등록하면 해당 프로젝트가 알림 방식을 선택한 것으로 봅니다. 안정적인 profile이 claim identity이고 thread ID와 모델명은 로컬 진단·라우팅 메타데이터일 뿐 권한을 부여하지 않습니다.

선행 작업을 `finish`한 agent는 `notify targets <finished-job> --role <role> --from-agent <profile>`로 모든 의존성과 Gate가 충족된 직접 후속 작업 및 현재 다른 active handoff를 소유하지 않은 peer task 후보를 확인합니다. 선행 scheduling edge가 없는 ready CR 구현 handoff 등은 가짜 dependency를 만들지 않고 `notify candidates <ready-handoff> --role <role> --from-agent <profile>`로 직접 후보를 확인합니다. 후보 하나에 handoff ID와 `handoff show`·`claim` 지시를 메시지로 보낸 뒤 실제 결과를 다음처럼 기록합니다.

```bash
baton notify record HO-READY \
  --role frontend \
  --from-agent frontend-main \
  --to-agent backend-main \
  --status sent \
  --detail "Codex가 follow-up을 수락함"
```

실패하면 `--status failed --detail <사유>`를 기록하고 다음 후보를 시도하거나 기존 `wait`/`watch`로 복구합니다. 성공 기록은 target role이 stop 또는 shift 밖이면 거부됩니다. 호환성을 위해 DB에는 `sent`로 저장하지만 사람용 출력은 `host_accepted`로 표시하며, 이는 수신 task가 메시지를 읽거나 claim했다는 뜻이 아닙니다. `notify status HO-... --stale-after 15m`은 기존 `notification_state`와 함께 사실 기반 context, 최신 delivery attempt, recovery 횟수를 표시합니다. 최신 성공 전달이 stale이고 작업이 open/unclaimed이며 같은 recipient session과 shift가 유효할 때만 같은 task에 recovery follow-up을 한 번 보내고 `notify retry ... --notification <id> --reason stale_unclaimed --status sent|failed`로 결과를 기록할 수 있습니다. 이 명령도 메시지를 직접 보내지 않으며 실패 recovery도 1회 한도를 소비합니다. 이후에는 반복 broadcast하지 않고 `wait`/`watch`로 복구합니다. 메시지는 claim이 아니며 새 task나 subagent를 만들거나 Baton에 없는 작업을 지시해서는 안 됩니다.

host-accepted 이후 수신 측 terminal 실패가 외부에서 확인되면 `notification.observe` 권한이 있는 planning/SM role이 `notify observe HO-... --notification <id> --result execution_failed --reason-class policy_blocked`로 1회 기록할 수 있습니다. Result와 reason class는 CLI의 고정 목록만 허용되고 원문 host 오류나 prompt는 저장하지 않습니다. 이 관찰은 `notify list`와 `notify status`에 표시되는 감사 정보일 뿐 delivery, recovery, claim, handoff 상태를 변경하지 않습니다.

`notify list`는 최신 운영 기록이 host 출력 제한보다 먼저 보이도록 무제한 newest-first를 기본으로 사용합니다. 출력량은 `--limit 20`처럼 제한하고, 시간순 전체 감사에는 `--order oldest`를 명시합니다. `--after-id`와 `--before-id`는 배타적 ID 경계이며 `--recovery-only`, `--job`, `--status`, `--format json`과 조합할 수 있습니다. 이전 페이지는 `--before-id <직전 페이지의 가장 작은 ID> --limit <개수>`로 조회합니다.

메시지로 다시 깨울 수 있는 Codex planner는 active handoff와 claimed review가 없고, 모든 복귀 지점이 명시적 planning handoff이며, 각 producer가 active planner session에 알릴 수 있을 때 CLI waiter 없이 `addressable idle` 상태로 현재 turn을 끝낼 수 있습니다. 이는 Baton 상태가 아닙니다. CR 알림 경로가 없거나 미지정 planning 작업, stale endpoint, 전송 실패, 비 Codex host가 있으면 `watch`를 유지합니다.

`next`는 한 번만 확인하는 비대기 명령입니다. 작업이 없다는 이유로 agent가 종료되면 안 되며, shift가 활성 상태인 동안 제한된 `wait`를 반복해야 합니다.
`next --explain`은 resolved agent, 요청 role, active session role, workstream 제외와 현재 ownership을 표시합니다. role 불일치는 명시적 겸임 운용을 위해 경고만 하며 자동 차단하지 않습니다. `next` 출력만으로 작업을 시작하지 말고 claim 전에 `handoff show`로 objective, source reference, dependency, Gate, exit criteria를 모두 확인해야 합니다. `handoff list`는 role과 status별 queue를 읽기 전용으로 조회합니다.

### 선택적 Git workspace 연동

Baton은 기본적으로 Git에 의존하지 않습니다. Git commit provenance와 checkout 불일치 경고가 필요한 프로젝트만 control root에 공통 `baton.toml`을 둡니다. 단일 checkout에서는 Git으로 추적할 수 있지만, 분리 worktree에서는 branch마다 다른 설정을 두지 않습니다.

```toml
[baton]
required_version = ">=0.6.0.dev0,<0.7"

[vcs]
provider = "git"
policy = "warn"
```

설정이 없으면 workspace policy는 `off`, Git provider만 설정하면 `warn`이 기본입니다. `strict`는 불일치한 register, claim, finish를 차단하며 `workspace.override` 권한을 가진 role의 사유 있는 override만 허용합니다. 단, 실패 보고는 차단하지 않고 warning event를 기록해 작업이 `in_progress`에 갇히지 않게 합니다. 명시적 완료 증거는 예외로, policy가 `off`여도 `finish --commit`과 `handoff evidence-correct --commit`은 선택한 로컬 workspace에서 reference를 검증합니다.

```bash
bin/baton workspace check
bin/baton workspace check --job HO-YYYY-MM-DD-001
bin/baton workspace events --job HO-YYYY-MM-DD-001
```

자세한 의미와 checkout 절차는 [선택적 Git workspace 연동 가이드](docs/git-integration.ko.md)를 따릅니다. Git 검사는 register, claim, finish, fail과 명시적인 check에서만 실행되며 wait polling에는 영향을 주지 않습니다. `finished`는 선행 commit이 후행 worktree에 통합됐음을 보장하지 않으므로 planner 또는 integrator가 merge/cherry-pick을 확인해야 합니다.

## CR 흐름

CR 본문은 공통 control root의 Markdown에 작성하고 상태 전환은 Baton으로 수행합니다. 새 CR은 기본적으로 branch와 무관한 `.baton/change-requests/`에 생성됩니다.

```bash
bin/baton cr create \
  --title "Upload policy" \
  --author-role planning \
  --reviewer-role sm

bin/baton cr submit CR-YYYY-MM-DD-001 --role planning
bin/baton cr wait-review --role sm --timeout 900
bin/baton cr list --status submitted --reviewer-role sm
bin/baton cr show CR-YYYY-MM-DD-001
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

Revision handoff는 항상 CR 작성 role로 돌아갑니다. `--assign-back`으로 다른 role을 지정할 수 없으며, 심사 사유는 handoff objective에 포함됩니다. Baton이 frontmatter를 동기화하는 동안 Markdown이 변경되면 사람의 편집을 덮어쓰지 않고 명령을 실패시킵니다.
SQLite와 파일시스템은 하나의 transaction이 아니므로 비정상 종료 후 frontmatter가 의심되면 `bin/baton cr sync CR-ID`로 DB 상태를 기준으로 managed header만 복구합니다. CR 본문은 보존됩니다. 단, 승인 본문이 변경된 경우 `cr sync`는 이를 숨기지 않고 실패합니다.

`submit`과 `resubmit`은 본문 hash를 기록하고 `approve`는 같은 본문인지 확인한 뒤 승인 hash를 고정합니다. 승인 후 본문이 바뀌면 implementation handoff 생성·claim·finish·최종 구현 완료 처리가 차단됩니다. 승인 후 요구 변경은 기존 본문을 고치지 않고 새 CR로 진행합니다.

`cr status`, `cr show`와 managed frontmatter는 현재 심사 소유자인 `active_review_claimed_by`와 역사적 마지막 claimant인 `last_review_claimed_by`를 구분합니다. active 값은 CR이 `submitted`일 때만 표시됩니다. Frontmatter의 기존 `review_claimed_by` alias는 active 값을 따르고, SQLite 및 JSON의 같은 필드는 호환성을 위해 역사 값을 유지합니다. `cr list --claimed-by`는 현재 submitted review만 검색합니다.

호환되지 않는 새 CR이 승인되면 `cr supersede OLD_CR --by NEW_CR --role sm --reason "..."`로 이전 승인을 대체합니다. planner/SM에게 직접 설계 권한이 있고 독립 심사가 필요하지 않다면 자기 심사용 CR을 만들지 않고 `--by-source-ref <불변-설계-참조>`를 사용합니다. 이전 CR은 `superseded` 상태와 승인 본문을 보존하고, 연결된 queued 구현 작업은 취소되며 active 구현 작업은 `cancel_requested`가 됩니다. 이미 finished인 결과는 보존하고 새 설계에 필요한 보강 handoff를 별도로 등록합니다. 단순 `cr cancel`도 연결된 미완료 구현 작업을 같은 규칙으로 정리합니다.

같은 approved CR 안에서 취소된 구현 경로를 다른 implementation handoff로 대체했다면 `cr supersede-handoff CR-ID OLD --replacement NEW --role <reviewer> --reason <사유>`로 관계를 먼저 기록합니다. `mark-implemented`는 이 replacement chain이 finished이면서 non-blocking인 handoff에 도달해야만 취소된 old job을 완료된 것으로 인정합니다.

Schema migration 후 과거 approved CR이 `legacy-unsealed`로 표시되면 지정 reviewer가 본문을 확인하고 새 구현 전에 명시적으로 봉인합니다.

```bash
bin/baton cr seal CR-YYYY-MM-DD-001 \
  --role sm \
  --evidence "과거 승인 본문을 확인했습니다."
```

승인과 구현 handoff 생성은 별도 결정입니다. 자세한 명령과 심사 권한은 [영문 README의 Change Request Flow](README.md#change-request-flow)를 따릅니다.

정상 경로에서는 reviewer가 `cr create-handoff`로 구현 작업을 생성합니다. 일반 `register`로 이미 완료된 구현이 있다면 합성 작업을 새로 만들지 말고, 지정 reviewer가 다음과 같이 명시적으로 연결합니다.

```bash
bin/baton cr show CR-YYYY-MM-DD-001
bin/baton cr link-handoff CR-YYYY-MM-DD-001 HO-YYYY-MM-DD-001 \
  --role sm \
  --reason "이미 완료된 구현을 승인된 CR에 연결합니다."
```

연결은 `cr.review`와 `cr.assign_implementation` 권한을 모두 가진 지정 reviewer만 수행할 수 있습니다. 대상은 승인 본문이 변하지 않은 approved CR, 정확히 `cr:<CR-ID>`인 `source_ref`, finished 상태, non-blocking 완료 결과, 검증 가능한 effective commit evidence를 모두 만족해야 합니다. `unresolved` 또는 `legacy_unchecked` 증거는 먼저 `handoff evidence-correct`로 검증해야 합니다. 같은 연결 재시도는 `already-linked`로 처리하고 다른 CR의 구현으로 재사용하지 않으며, migration이나 조회 명령이 자동으로 연결을 추론하지 않습니다.

기본 `cr status`와 `cr show`는 approved CR에 공식 implementation 연결이 없고 기계적 연결 조건을 통과한 경우에만 `implementation_adoption_candidate`를 표시합니다. 이는 구현 목적을 확정하는 증거가 아니라 reviewer가 내용을 확인해야 하는 힌트입니다. implementation이 연결됐거나 CR이 terminal이면 후보를 표시하지 않습니다. 모든 exact-source 미연결 기록을 감사하려면 `--include-related-handoffs`를 사용하며, 이 경우 validation/acceptance를 구현으로 오인하지 않도록 `related_handoff_unlinked`라는 중립 이름을 사용합니다.

`cr mark-implemented`는 모든 구현 및 명시적 replacement chain이 finished이면서 `completion_blocking=0`일 때만 허용됩니다. blocking 완료 결과는 작업 자체가 끝났더라도 CR 종료를 막습니다. non-blocking인 non-pass 결과를 수용할지는 증거를 확인한 reviewer의 명시적 결정입니다.

`baton status`와 `baton-report summary`의 기존 `outcome.blocking` 또는 `blocking`은 현재 blocker 수가 아니라 `completion_blocking=1`인 역사적 누적 총계입니다. RC7은 이를 `blocking_total`, `blocking_with_open_cr`, `blocking_with_implemented_cr`, `blocking_with_terminal_unimplemented_cr`, `blocking_without_cr`로 추가 분류합니다. rejected/cancelled/superseded CR은 해결로 추론하지 않고 terminal-unimplemented로 남깁니다. `handoff show`의 `blocking_context`와 `outcome_cr_status`로 개별 결과를 확인할 수 있습니다.

## Wait와 자원 사용

`wait`, `cr wait-review`, 통합 `watch`는 작업이 없을 때 `time.sleep()`으로 대기하므로 busy loop로 CPU를 계속 점유하지 않습니다. `watch`는 해당 role에 배정된 submitted CR을 먼저 확인하고 그 다음 ready handoff를 확인합니다.

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
bin/baton watch --role planning --timeout 900
```

일반 `wait`는 주기마다 stop/shift 확인, 의존성 및 Gate 조정, role queue 조회를 수행합니다. Baton은 handoff, CR, 통합 watcher를 같은 `waiter_leases` table에 heartbeat로 등록하고 활성 수에 비례해 interval을 자동으로 늘립니다. 한 명은 3초, 두 명은 각각 6초, 열 명 이상은 각각 최대 30초를 목표로 하며 동시 polling을 줄이는 작은 jitter가 적용됩니다.

정상 종료 시 lease는 즉시 제거됩니다. 자동 waiter의 process 연결이 끊기면 30초 lease가 만료되고 이후 heartbeat가 stale record를 정리합니다. 25초를 초과하는 고정 interval은 정상 sleep을 보호하기 위해 `interval + 5초` lease를 사용합니다. 숫자 interval을 명시한 waiter도 활성 수에는 포함되지만 자신의 sleep은 지정된 값으로 고정됩니다.

단일 및 10개 동시 waiter의 CPU, memory, DB 증가와 한계는 영문 기준 문서인 [Baton v0.5.0 idle wait resource check](docs/benchmarks/v0.5.0-idle-wait-resource.md)에 기록되어 있습니다. 이 자료는 저장소 evidence로 유지하며 별도의 release 첨부 파일로 배포하지 않습니다.

## Shift와 중지

```bash
bin/baton shift status --role frontend
bin/baton shift start --role frontend
bin/baton shift extend --role frontend
bin/baton shift end --role frontend --reason "End of day"
```

`shift start`의 기본 duration은 4시간, `shift extend`의 기본 duration은 1시간입니다. shift가 만료되면 새로운 wait, watch, claim은 중지되지만 이미 claim한 작업의 완료 보고는 허용됩니다. 단, 작업이 `cancel_requested`이면 `finish`나 `fail`을 실행하지 말고 검토 결과에 따라 `cancel-withdraw` 이후 작업을 재개하거나 `cancel-ack`로 중단을 확인해야 합니다.

worker는 첫 wait 전에 적용되는 전역 및 role shift 상태를 확인합니다. 미래 deadline이 없고 중지되거나 만료된 scope도 없을 때만 기본 4시간 role shift를 시작합니다. 이미 활성 deadline이 있으면 유지하고, 만료 또는 중지된 scope는 사용자나 SM의 명시적인 승인 없이 다시 시작, 연장 또는 resume하지 않습니다.

프로젝트의 모든 role에 같은 운영 시간을 적용하려면 전역 shift를 사용합니다.

```bash
bin/baton shift status
bin/baton shift start --all
bin/baton shift extend --all
bin/baton shift end --all --reason "End of day"
```

전역과 role scope는 함께 적용됩니다. `all`이 중지 또는 만료되면 role shift가 활성이어도 모든 role이 멈추며, 특정 role scope가 중지 또는 만료되면 전역 shift가 활성이어도 해당 role은 멈춥니다. 한 scope의 시작, 연장 또는 resume은 다른 scope의 상태를 해제하지 않습니다.

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

다른 프로젝트 루트에서 `tools/baton/bin/baton init`을 실행하면 해당 위치의 `.baton/`에 marker와 runtime DB가 생성됩니다. 자세한 `.gitignore`, `AGENTS.md`, prompt 설정은 [설치 가이드](docs/using-baton-in-projects.md)를 확인합니다.

## 버전 변경과 DB 보존

Baton 버전을 변경하기 전에 `upgrade preflight`를 실행하고 모든 blocker를 정리합니다. 실행파일 교체 뒤 role agent를 시작하기 전에 migration을 명시적으로 수행합니다.

```bash
bin/baton upgrade preflight
bin/baton migrate
bin/baton migrate --check
```

Migration은 version이 지정되어 있고 transaction 단위로 실행되며 반복 실행할 수 있습니다. 기존 handoff, CR, event, control, role, permission 데이터는 보존되고 실패한 migration은 rollback됩니다. Schema v8은 CR hash 필드를 추가하지만 과거 승인 본문의 hash를 추측하지 않습니다. Schema v12는 기존 job과 notification을 attempt 1로 보존하고 retry 세대 및 명시적인 implementation 대체 관계를 추가합니다. Schema v13은 완료 결과와 append-only 증거 정정을 추가하며 기존 완료 job은 `completion_outcome=unspecified`, 기존 commit은 `legacy_unchecked`로 보존합니다. Schema v14는 기존 notification을 보존하고 delivery attempt 순서 및 명시적인 recovery 관계를 추가합니다. Schema v15는 기존 `outcome_cr_id`를 첫 ordered link로 백필하고 다중 outcome CR과 제한된 notification observation을 추가하며 추가 관계나 결과를 추측하지 않습니다. DB보다 오래된 Baton binary는 더 새로운 schema를 수정할 수 없습니다.

일반 workflow 명령은 pending migration을 자동 적용하지 않습니다. `baton migrate`가 변경 전에 검증된 backup을 만들며, migration이 필요한 DB에 일반 명령을 실행하면 명시적으로 실패합니다. Schema migration 5는 생성 및 최근 migration에 사용된 Baton 버전을 진단 정보로 기록하지만 호환성은 계속 `schema_migrations`로 판단합니다. `project info`와 `upgrade preflight`의 `cli_schema_compatible`, `database_schema_current`, `migration_required`, `workflow_commands_ready`로 현재 실행 가능 여부를 판단합니다.

## 감사와 요약

`bin/baton-report`는 현재 프로젝트 marker가 가리키는 DB를 읽기 전용으로 엽니다. 여러 프로젝트를 합산하는 전역 보고서는 아닙니다.

```bash
bin/baton-report summary
bin/baton-report summary --format json
bin/baton-report audit
bin/baton-report audit --role frontend
bin/baton-report audit --format csv
```

Summary에는 handoff lifecycle, 구조화된 완료 결과, blocking 결과, CR, Gate와 실패 심사 건수가 포함되며 아직 결정되지 않은 실패 심사는 `pending`으로 표시됩니다.

## 제한 사항

- Markdown handoff 파일 자체의 import/export는 제공하지 않습니다.
- Handoff claim/finish/fail 권한은 대상 role 기준이고, register와 심사된 retry에는 `handoff.register`, 관리 취소에는 `handoff.cancel`, 심사된 append-only commit 정정에는 `handoff.evidence_correct`가 필요합니다. 사용자 인증은 외부 정책에 맡깁니다.
- CR 심사는 role 권한을 사용하지만 Baton만으로 실제 사용자를 인증하지 않습니다.
- pipx 실행 파일은 OS 사용자 범위에서 공유되지만 DB와 stop/wait 상태는 Baton marker별로 분리됩니다. 여러 프로젝트가 같은 명시적 `--db`를 공유하도록 구성하면 ID, CR 경로, control까지 하나의 workflow로 합쳐지므로 피해야 합니다.
- 같은 논리 프로젝트의 분리 Git worktree는 하나의 canonical 로컬 DB를 공유해야 합니다. Baton은 source branch의 commit을 자동 병합하거나 겹치는 파일 수정을 감지하지 않습니다.
- Baton은 파일시스템 전체 검색, 전역 프로젝트 registry 또는 일괄 migration을 제공하지 않습니다. 각 프로젝트는 다음 사용 시 독립적으로 검사하고 migration합니다.
- 활성 DB는 local filesystem에 두어야 합니다. Network mount, cloud 동기화 폴더 또는 여러 PC가 공유하는 DB는 SQLite lock 전제를 보장하지 않으므로 agent 조정 용도로 사용하지 않습니다.
- 이 저장소의 Baton DB는 다른 프로젝트의 활성 workflow 상태가 아닙니다.

## 라이선스

Baton은 Apache License 2.0으로 배포됩니다. [LICENSE](LICENSE)와 [NOTICE](NOTICE)를 확인하십시오.
