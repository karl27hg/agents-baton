# 선택적 Git Workspace 연동

[English (primary)](git-integration.md) | 한국어

Baton은 계속 Git에 의존하지 않습니다. SQLite DB는 현재 workflow 상태의 원본이고 Git은 source와 문서 이력의 원본입니다. 선택적 workspace 연동은 commit provenance만 기록하고 checkout 불일치를 감지합니다. commit, diff 또는 파일 본문을 Baton DB에 복제하지 않습니다.

## 연동 설정

Baton control root의 `.baton/` 옆에 `baton.toml`을 만듭니다.

```toml
[baton]
required_version = ">=0.6.0.dev0,<0.7"

[vcs]
provider = "git"
policy = "warn"
```

`required_version`은 `==`, `!=`, `<`, `<=`, `>`, `>=` 비교를 쉼표로 연결할 수 있습니다. `dev`, `a`, `b`, `rc` prerelease를 지원합니다. 단일 checkout에서는 `baton.toml`을 Git으로 추적할 수 있습니다. 분리 worktree에서는 모든 agent가 같은 정책을 사용하도록 branch 밖의 공통 control root에 한 벌만 둡니다.

`baton.toml`이 없으면 기본 정책은 `off`이며 Git 명령을 실행하지 않습니다. `provider = "git"`만 있고 policy가 없으면 `warn`이 기본입니다.

Git provenance를 한 번 기록한 프로젝트에서는 `baton.toml`이 사라져도 보호를 자동으로 `off`로 낮추지 않습니다. DB에 마지막으로 기록된 `warn` 또는 `strict` 동작을 검사에 사용하고 공통 config 소실을 불일치로 보고합니다. 연동을 의도적으로 끄려면 공통 파일을 유지하고 `policy = "off"`로 설정합니다.

## 정책 모드

| 정책 | 동작 |
| --- | --- |
| `off` | Git을 검사하지 않고 자동 provenance도 기록하지 않습니다. 기존 프로젝트의 하위 호환 기본값입니다. |
| `warn` | workspace를 검사하고 기록합니다. 불일치가 있어도 작업을 허용하고 경고와 `warning` event를 한 번 남깁니다. |
| `strict` | 불일치가 있으면 상태 변경을 거부합니다. `workspace.override` 권한과 사유가 있는 명시적 override만 허용합니다. |

실패 보고 자체는 차단하지 않습니다. `strict` 불일치 상태에서 `fail`을 실행하면 `warning` workspace event를 기록한 뒤 실패 CR을 제출합니다. 이 규칙은 provenance 정책 때문에 실패 작업이 `in_progress`에 갇히는 것을 방지합니다.

Git 연동 후 기본값은 `warn`입니다. rebase, cherry-pick, 의도적인 branch 이관과 baseline이 없는 과거 handoff는 운영자 판단이 필요할 수 있기 때문입니다. 실제 branch workflow를 검증한 프로젝트만 `strict`로 변경합니다.

## 기록과 판정

`warn` 또는 `strict` 프로젝트에서 handoff를 register, claim, finish, fail할 때 다음 값을 기록합니다.

- HEAD commit
- 참고용 branch 이름 또는 `DETACHED`
- dirty working tree 여부
- 비교한 baseline commit
- policy와 결과
- actor role, 경고 또는 override 사유

Dirty 여부는 evidence이며 자동 불일치 사유는 아닙니다. 현재 판정에는 Baton version 호환성, Git 사용 가능 여부와 commit ancestry를 사용합니다. Git diff, source 본문과 Git log는 저장하지 않습니다.

Register commit은 claim의 baseline입니다. Claim commit은 finish의 baseline이며 과거 provenance가 없는 handoff는 가능한 기준만 사용합니다. 현재 HEAD가 baseline의 descendant이면 정상 진행으로 허용합니다. 과거 또는 분기된 commit으로 이동하면 `warn` 경고 또는 `strict` 차단이 발생합니다.

현재 보호 대상은 handoff `register`, `claim`, `finish`, `fail`입니다. CR 승인은 공통 Markdown 본문의 제출·승인 hash를 검사하며 Git ancestry를 CR 승인 조건으로 사용하지 않습니다.

`finished`는 agent가 handoff를 완료했다는 기록일 뿐입니다. `related_commit`이 후행 worktree에 merge 또는 cherry-pick됐음을 보장하지 않으므로, planner 또는 integrator가 통합을 확인한 뒤 통합 의존 작업을 release해야 합니다.

명시적 완료 증거 검증은 workspace policy와 별개입니다. policy가 `off`여도 `finish --commit <ref>`와 `handoff evidence-correct --commit <ref>`는 `BATON_WORKSPACE_ROOT` 또는 현재 디렉터리에서 reference를 commit으로 해석하고 canonical full commit ID를 저장합니다. 없는 object나 commit으로 해석할 수 없는 object는 거부합니다. 외부 또는 아직 fetch하지 않은 reference를 의도적으로 기록하려면 `--allow-unresolved-commit`과 구체적인 `--unresolved-reason`을 함께 사용해야 하며, Baton은 unresolved 상태와 사유를 감사 기록으로 남깁니다.

완료 증거는 append-only입니다. 잘못된 reference는 SQLite를 변경하지 말고 `handoff evidence-correct`로 정정합니다. 원 claimant는 target role로 자기 job을 정정할 수 있고 `planning`과 `sm`은 심사된 복구를 위한 `handoff.evidence_correct` 권한을 받습니다. `handoff show`는 원본, 유효 값, resolution 상태, 전체 정정 이력을 표시합니다.

## 사용법

```bash
baton workspace check
baton workspace check --job HO-YYYY-MM-DD-001
baton workspace events
baton workspace events --job HO-YYYY-MM-DD-001
baton-report audit --job HO-YYYY-MM-DD-001
```

Git 검사는 기본적으로 현재 디렉터리를 대상으로 합니다. control root에서 명령을 실행한다면 source checkout을 명시합니다.

```bash
BATON_WORKSPACE_ROOT=/absolute/path/to/worktree baton workspace check
```

Workspace policy 검사는 명시적인 workspace 검사와 handoff register, claim, finish, fail 전환에서만 실행됩니다. Commit 증거 해석은 호출자가 `finish` 또는 `handoff evidence-correct`에 commit을 명시했을 때만 실행됩니다. 두 검사 모두 `wait` 또는 `cr wait-review` polling loop에서는 실행하지 않습니다.

`strict` 상태에서 의도적으로 branch를 이관해야 한다면 다음처럼 사유와 `workspace.override` 권한 role을 지정합니다.

```bash
baton claim HO-YYYY-MM-DD-001 \
  --role backend \
  --accept-workspace-change \
  --workspace-reason "Release branch로 의도적으로 이관" \
  --workspace-authorized-by-role sm
```

Schema migration 6은 기본 `sm` role에 `workspace.override`를 추가합니다. 성공한 override는 `workspace_events`에 기록됩니다.

## Checkout 절차와 제한

```bash
baton stop --all --reason "Git checkout maintenance"
baton handoff list --status in_progress
git status --short
git switch <branch>
baton workspace check
baton project info
baton shift status
```

Checkout 전에 진행 중 handoff를 완료하거나 명시적으로 취소합니다. 새 branch와 shift를 확인한 뒤에만 resume합니다.

- Baton은 checkout을 background에서 감시하거나 Git hook을 설치하지 않습니다.
- 하나의 Baton DB를 공유하는 모든 worktree는 DB project root 옆의 동일한 `baton.toml`을 사용합니다. 과거 사용 후 파일이 사라지면 새 `off` 프로젝트가 아니라 정책 문제로 처리합니다.
- `<control-root>/.baton/baton.sqlite3` 형태의 외부 DB는 해당 control root의 config와 CR 경로를 사용합니다. 임의 이름의 외부 DB에는 암묵적인 project root가 없습니다.
- 한 논리 프로젝트의 분리 Git worktree들은 `BATON_DB`로 하나의 로컬 canonical DB를 공유해야 하며, 서로 무관한 프로젝트는 DB를 공유하면 안 됩니다.
- 공통 control root는 같은 PC의 로컬 파일시스템에 둡니다. 네트워크 또는 동기화 저장소의 SQLite는 지원하지 않습니다.
- control root가 checkout 안에 있으면 `git clean -fdx`가 ignore된 `.baton/`을 삭제할 수 있습니다. disposable worktree 밖에 두거나 파괴적 정리 전에 백업합니다.
- DB migration은 checkout으로 되돌아가지 않습니다. 새 schema를 지원하지 않는 과거 Baton binary를 사용하면 안 됩니다.
