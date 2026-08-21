# SQLite 스키마

이 문서는 SQLite 기반 Baton에서 사용하는 테이블 구조와 record의 용도를 설명합니다.

이 Baton workflow에서는 SQLite DB가 handoff runtime state의 기준입니다. Agent는 record를 직접 수정하지 말고 `baton` 명령을 사용해야 합니다.

## 개요

테이블:

- `schema_migrations`: 순서가 보장되는 DB migration 이력
- `roles`: 표준 role 정의
- `role_aliases`: 표준 role로 변환되는 별칭
- `role_permissions`: role에 부여된 workflow 권한
- `handoff_jobs`: handoff의 기본 작업 record
- `handoff_dependencies`: handoff job 사이의 의존성
- `workflow_gates`: 미래 또는 수동 해제 workflow stage를 위한 named barrier
- `gate_owners`: 각 Gate를 해제하거나 이관할 수 있는 role
- `handoff_gate_dependencies`: handoff job에 연결된 Gate 요구사항
- `gate_events`: Gate 소유권 및 lifecycle 감사 로그
- `handoff_events`: 상태 변경과 운영 이벤트 감사 로그
- `handoff_controls`: wait loop 중지/재개 제어
- `change_requests`: CR workflow 상태와 Markdown 파일 참조
- `cr_events`: CR 상태 변경 감사 로그
- `cr_handoffs`: CR과 revision/implementation handoff 연결

상태를 변경하는 CLI 명령은 `BEGIN IMMEDIATE` transaction을 사용해 write 작업을 직렬화합니다.

## `schema_migrations`

용도:

- 적용된 DB migration을 정확히 한 번씩 기록합니다.
- 버전 정보가 없는 기존 Baton DB의 workflow record를 삭제하지 않고 현재 schema로 전환합니다.
- 구버전 Baton binary가 더 새로운 schema의 DB를 변경하지 못하게 합니다.

컬럼:

| 컬럼 | 타입 | 필수 | 용도 |
| --- | --- | --- | --- |
| `version` | `integer primary key` | 예 | 순서대로 증가하는 migration version입니다. |
| `name` | `text` | 예 | 변경되지 않는 migration 이름입니다. |
| `applied_at` | `text` | 예 | migration이 commit된 UTC 시각입니다. |

`baton migrate`는 pending migration, 해당되는 seed 보강, `PRAGMA quick_check`, `PRAGMA foreign_key_check`를 하나의 transaction에서 실행합니다. 실패하면 schema 변경, seed 변경, migration record가 함께 rollback됩니다. 전체 기본 권한은 신규 또는 무버전 DB에만 seed하며, 이후 migration은 그 migration에서 새로 도입한 권한만 추가하므로 프로젝트별 권한 철회가 보존됩니다.

Release된 migration:

```text
1 initial_schema
2 handoff_cancel_permission
3 named_gates
```

`baton migrate --check`는 DB가 현재 binary가 아는 최신 schema version인지 읽기 전용으로 확인합니다.

앞으로 schema를 변경할 때는 새 migration을 추가하고 `LATEST_SCHEMA_VERSION`을 증가시켜야 합니다. 이미 release된 migration을 직접 수정하면 안 됩니다.

## `roles`

용도:

- handoff job을 소유할 수 있는 표준 role을 정의합니다.
- 존재하지 않는 role로 handoff가 등록되는 것을 방지합니다.
- CLI 코드를 바꾸지 않고 role 구성을 변경할 수 있게 합니다.

컬럼:

| 컬럼 | 타입 | 필수 | 용도 |
| --- | --- | --- | --- |
| `role_id` | `text primary key` | 예 | 표준 role key입니다. 예: `frontend`, `qa`. |
| `display_name` | `text` | 예 | 사람이 읽기 쉬운 role 이름입니다. |
| `description` | `text` | 아니오 | 선택적 role 설명입니다. |
| `active` | `integer` | 예 | `1`이면 새 handoff가 이 role을 대상으로 할 수 있습니다. |
| `created_at` | `text` | 예 | UTC 생성 시각입니다. |
| `updated_at` | `text` | 예 | UTC 수정 시각입니다. |

기본 seed role:

```text
sm
planning
architecture
backend
frontend
qa
devops
ui-design
backend-design
```

## `role_aliases`

용도:

- 축약어 또는 과거 role 이름을 표준 role로 매핑합니다.
- Agent가 `fe` 같은 alias를 사용해도 record에는 `frontend`가 저장되도록 합니다.

컬럼:

| 컬럼 | 타입 | 필수 | 용도 |
| --- | --- | --- | --- |
| `alias` | `text primary key` | 예 | 사용자나 agent가 입력하는 별칭입니다. |
| `role_id` | `text` | 예 | `roles.role_id`에 있는 표준 role입니다. |

예:

```text
alias=fe, role_id=frontend
```

## `role_permissions`

용도:

- role identity와 workflow action 권한을 분리해서 저장합니다.
- handoff 소유권 규칙을 바꾸지 않고 reviewer role을 설정할 수 있게 합니다.
- 심사 및 관리 권한과 구현 handoff를 claim할 수 있는 능력을 분리합니다.

컬럼:

| 컬럼 | 타입 | 필수 | 용도 |
| --- | --- | --- | --- |
| `role_id` | `text` | 예 | `roles.role_id`에 있는 표준 role입니다. |
| `permission` | `text` | 예 | 권한 key입니다. 예: `cr.review`, `cr.approve`. |

Primary key:

```text
(role_id, permission)
```

Seed 권한:

- `init` 또는 각 권한을 도입한 migration 시 `sm`은 모든 CR 권한, `handoff.cancel`, `gate.manage`를 받습니다.

알려진 권한:

```text
cr.admin
cr.review
cr.request_revision
cr.approve
cr.reject
cr.assign_implementation
cr.mark_implemented
handoff.cancel
gate.manage
```

권한 부여와 철회는 `role permission-add`, `role permission-remove`를 사용합니다. 권한 철회는 프로젝트 정책 결정으로 취급하며, 반복 migration은 전체 기본 권한 집합을 다시 복원하지 않습니다.

## `handoff_jobs`

용도:

- handoff queue의 핵심 작업 record를 저장합니다.
- 파일 기반 구조의 `jobs/`, `blocked/`, `finished/` 같은 위치 상태를 대체합니다.
- `register`, `next`, `claim`, `finish`, `status` 명령이 사용하는 기본 데이터입니다.

컬럼:

| 컬럼 | 타입 | 필수 | 용도 |
| --- | --- | --- | --- |
| `job_id` | `text primary key` | 예 | 안정적인 handoff ID입니다. 예: `HO-2026-06-02-001`. |
| `title` | `text` | 예 | 사람이 읽기 쉬운 짧은 제목입니다. |
| `status` | `text` | 예 | 현재 queue 상태입니다. |
| `target_role` | `text` | 예 | 이 job을 claim/finish할 수 있는 표준 role입니다. |
| `source_ref` | `text` | 아니오 | 원천 CR, QA report, 사용자 요청, 문서 참조입니다. |
| `objective` | `text` | 예 | target role이 완료해야 할 작업 목적입니다. |
| `exit_criteria` | `text` | 예 | 완료 판단 기준입니다. |
| `created_at` | `text` | 예 | UTC 생성 시각입니다. |
| `claimed_by` | `text` | 아니오 | claim 시 사용된 stable profile name 또는 명시적 claimant입니다. |
| `started_at` | `text` | 아니오 | claim된 UTC 시각입니다. |
| `finished_at` | `text` | 아니오 | 완료된 UTC 시각입니다. |
| `closure_evidence` | `text` | 아니오 | job 완료 시 필요한 증거입니다. |
| `related_commit` | `text` | 아니오 | 완료 산출물과 연결되는 commit SHA 또는 reference입니다. |

허용되는 `status` 값:

```text
blocked
open
in_progress
finished
cancelled
```

상태 의미:

- `blocked`: 필수 upstream job이 완료되기를 기다리는 상태입니다.
- `open`: `target_role`이 claim할 수 있는 ready 상태입니다.
- `in_progress`: agent profile이 claim한 상태입니다.
- `finished`: closure evidence와 함께 완료된 상태입니다.
- `cancelled`: 단순 pause가 아니라 job 자체가 의도적으로 취소된 상태입니다.

권한이 있는 `cancel` 명령은 선택한 `blocked`, `open`, `in_progress` job을 `cancelled`로 바꾸고, 그 job에 의존하는 `blocked` 하위 job만 재귀적으로 취소합니다. 관련 없는 queue branch는 변경하지 않습니다. `finished`와 이미 `cancelled`인 job에는 적용할 수 없습니다.

최소 ready job 예:

```text
job_id=HO-2026-06-02-001
title=Frontend upload follow-up
status=open
target_role=frontend
source_ref=docs/change-requests/CR-2026-06-02-example.md
objective=Implement the approved upload follow-up.
exit_criteria=The approved behavior is implemented and verified.
created_at=2026-06-02 09:00:00 UTC
```

## `handoff_dependencies`

용도:

- job 사이의 의존성 edge를 저장합니다.
- `promote-ready`가 `blocked` job을 언제 `open`으로 바꿀 수 있는지 판단하게 합니다.

컬럼:

| 컬럼 | 타입 | 필수 | 용도 |
| --- | --- | --- | --- |
| `job_id` | `text` | 예 | 다른 job을 기다리는 dependent job입니다. |
| `depends_on_job_id` | `text` | 예 | 먼저 완료되어야 하는 upstream job입니다. |

Primary key:

```text
(job_id, depends_on_job_id)
```

예:

```text
job_id=HO-2026-06-02-003
depends_on_job_id=HO-2026-06-02-001
```

승격 규칙:

- `blocked` job은 모든 `depends_on_job_id`가 `finished` 상태일 때만 `open`으로 승격됩니다.
- 필수 upstream job 중 하나라도 `cancelled`가 되면 Baton은 이를 기다리는 blocked job을 재귀적으로 `cancelled`로 변경합니다.
- 전파된 각 상태 변경에는 직접적인 upstream job을 원인으로 기록한 `dependency_cancelled` handoff event가 한 번 남습니다.
- 이미 취소된 dependency를 지정해 새 handoff를 등록하면 `blocked`가 아니라 즉시 `cancelled` 상태로 생성됩니다.
- `promote-ready`는 취소된 dependency 뒤에 blocked job이 남아 있는 이전 DB record도 함께 정리합니다.
- 독립 job과 관련 없는 dependency branch는 이 전파로 취소되지 않습니다.

## Named Gate 테이블

`workflow_gates`는 실제 predecessor handoff가 생성되기 전에도 사용할 수 있는 안정적인 이름을 저장합니다.

| 컬럼 | 타입 | 필수 | 용도 |
| --- | --- | --- | --- |
| `gate_name` | `text primary key` | 예 | 정규화된 안정적인 Gate 이름입니다. |
| `status` | `text` | 예 | `pending`, `released`, `cancelled` 중 하나입니다. |
| `created_by_role` | `text` | 예 | Gate를 생성한 role입니다. |
| `created_at` | `text` | 예 | UTC 생성 시각입니다. |
| `resolved_at` | `text` | 아니오 | 해제 또는 취소된 UTC 시각입니다. |
| `resolution_evidence` | `text` | 아니오 | 필수 해제 evidence 또는 취소 사유입니다. |

`gate_owners`의 primary key는 `(gate_name, role_id)`입니다. `gate create`에 `--owner-role`이 없으면 생성 role이 기본 소유자가 되며, `--owner-role`을 반복하면 공동 소유가 됩니다.

`handoff_gate_dependencies`의 primary key는 `(job_id, gate_name)`입니다. 모든 handoff dependency가 `finished`이고 모든 Gate dependency가 `released`일 때만 handoff가 `open`으로 승격됩니다. 이미 취소된 Gate에 연결해 등록한 handoff는 즉시 `cancelled`가 됩니다.

`gate_events`는 actor role, status transition, 사유 또는 evidence, UTC 시각과 함께 `created`, `released`, `cancelled`, `ownership_transferred` 이벤트를 기록합니다.

Gate 권한 규칙:

- 소유자는 pending Gate를 해제, 취소 또는 이관할 수 있습니다.
- `gate.manage` 권한이 있는 role은 긴급 복구를 위해 소유권을 이관할 수 있지만, 소유하지 않은 Gate를 직접 해제하거나 취소할 수는 없습니다.
- `gate transfer`는 전체 소유자 집합을 교체하며 감사 사유가 필수입니다.
- Gate 해제와 조건을 충족한 handoff 승격은 같은 transaction에서 처리됩니다.
- Gate 취소는 직접 연결된 blocked handoff와 그 blocked 하위 handoff만 취소하며 관련 없는 queue branch는 유지합니다.
- Baton은 role 권한을 기록하지만 개별 사용자를 인증하지 않습니다.

## `handoff_events`

용도:

- workflow operation에 대한 감사 로그를 제공합니다.
- 누가, 언제, 어떤 상태를, 왜 변경했는지 기록합니다.
- Agent와 사람이 claim identity와 lifecycle history를 검증할 수 있게 합니다.

컬럼:

| 컬럼 | 타입 | 필수 | 용도 |
| --- | --- | --- | --- |
| `id` | `integer primary key autoincrement` | 예 | 이벤트 순번입니다. |
| `job_id` | `text` | 아니오 | 관련 job ID입니다. 없을 수도 있습니다. |
| `event_type` | `text` | 예 | 이벤트 이름입니다. 예: `registered`, `claimed`, `finished`. |
| `actor_role` | `text` | 아니오 | operation을 수행한 role입니다. |
| `actor_id` | `text` | 아니오 | stable profile name 또는 명시적 agent identity입니다. |
| `from_status` | `text` | 아니오 | 이전 상태입니다. |
| `to_status` | `text` | 아니오 | 변경된 상태입니다. |
| `message` | `text` | 아니오 | evidence, reason, event detail입니다. |
| `created_at` | `text` | 예 | UTC 이벤트 시각입니다. |

현재 이벤트 타입:

```text
role_added
role_alias_added
role_permission_added
role_permission_removed
registered
claimed
finished
promoted
cancelled
dependency_cancelled
gate_cancelled
control_stopped
control_resumed
shift_started
shift_extended
shift_ended
```

Claim event 예:

```text
event_type=claimed
job_id=HO-2026-06-02-001
actor_role=frontend
actor_id=frontend-main
from_status=open
to_status=in_progress
created_at=2026-06-02 09:10:00 UTC
```

## `handoff_controls`

용도:

- wait loop의 중지/재개 제어를 저장합니다.
- role agent의 최대 작동 시간을 나타내는 optional shift deadline을 저장합니다.
- Baton SQLite workflow에서는 파일 flag check를 대체합니다.
- job status 자체는 변경하지 않습니다.

컬럼:

| 컬럼 | 타입 | 필수 | 용도 |
| --- | --- | --- | --- |
| `scope` | `text primary key` | 예 | 제어 범위입니다. 예: `all`, `role:frontend`. |
| `stopped` | `integer` | 예 | `1`이면 matching wait loop를 중지시키고, `0`이면 허용합니다. |
| `reason` | `text` | 아니오 | 사람이 읽을 수 있는 중지 사유입니다. |
| `work_until` | `text` | 아니오 | UTC shift deadline입니다. 만료되면 Baton이 해당 scope를 stopped로 표시합니다. |
| `updated_at` | `text` | 예 | 마지막 제어 변경 UTC 시각입니다. |

Scope 예:

```text
all
role:frontend
role:qa
role:sm
```

Wait 동작:

1. `handoff_controls`에서 `all` 또는 `role:<role>`을 확인합니다.
2. `work_until`이 만료되었으면 해당 scope를 stopped로 표시합니다.
3. stopped 상태이면 exit code `3`으로 종료합니다.
4. stopped 상태가 아니면 promotion과 queue check를 실행합니다.

`cr wait-review`도 동일한 control scope와 exit code를 사용합니다.

Claim 동작:

- `claim`은 새 작업 착수 전에 같은 control을 확인합니다.
- `finish`는 shift control을 확인하지 않으므로, 이미 claim한 작업은 shift 만료 후에도 완료 보고할 수 있습니다.

## `change_requests`

용도:

- CR workflow 상태와 metadata를 저장합니다.
- CR 본문을 담는 Markdown 파일을 가리킵니다.
- SQLite를 상태 기준으로 두고 Markdown frontmatter는 Baton이 관리하는 projection으로 취급합니다.

컬럼:

| 컬럼 | 타입 | 필수 | 용도 |
| --- | --- | --- | --- |
| `cr_id` | `text primary key` | 예 | 안정적인 CR ID입니다. 예: `CR-2026-06-02-001`. |
| `title` | `text` | 예 | 사람이 읽기 쉬운 짧은 제목입니다. |
| `status` | `text` | 예 | 현재 CR workflow 상태입니다. |
| `author_role` | `text` | 예 | CR 본문을 작성/보강하는 role입니다. |
| `reviewer_role` | `text` | 예 | 이 CR을 심사할 수 있는 role입니다. |
| `file_path` | `text` | 예 | Markdown 본문 파일 경로입니다. |
| `created_at` | `text` | 예 | UTC 생성 시각입니다. |
| `updated_at` | `text` | 예 | UTC 수정 시각입니다. |
| `submitted_at` | `text` | 아니오 | 마지막 제출 시각입니다. |
| `approved_at` | `text` | 아니오 | 승인 시각입니다. |
| `rejected_at` | `text` | 아니오 | 최종 반려 시각입니다. |
| `implemented_at` | `text` | 아니오 | 구현 완료 시각입니다. |
| `revision_count` | `integer` | 예 | 보강 요청 횟수입니다. |
| `active_revision_job_id` | `text` | 아니오 | 진행 중인 보강 handoff입니다. |

허용되는 `status` 값:

```text
draft
submitted
revision_requested
approved
rejected
implemented
cancelled
```

상태 규칙:

- `draft -> submitted`는 author role이 수행합니다.
- `submitted -> revision_requested`, `approved`, `rejected`는 reviewer role이 수행합니다.
- `revision_requested -> submitted`는 Markdown 본문 보강 후 author role이 수행합니다.
- `approved -> implemented`는 연결된 implementation handoff가 최소 1개 있어야 하고, 모든 implementation handoff가 `finished`여야 합니다.
- `cancelled`는 `cr.admin` 권한을 가진 role이 수행하며 audit event를 남깁니다.
- `reviewer_role`은 terminal review 전까지 `cr.admin` 권한을 가진 role이 재지정할 수 있습니다.

## `cr_events`

용도:

- CR workflow operation에 대한 감사 로그를 제공합니다.
- 심사 결정, 작성자의 재제출, 구현 handoff 연결을 기록합니다.

컬럼:

| 컬럼 | 타입 | 필수 | 용도 |
| --- | --- | --- | --- |
| `id` | `integer primary key autoincrement` | 예 | 이벤트 순번입니다. |
| `cr_id` | `text` | 예 | 관련 CR ID입니다. |
| `event_type` | `text` | 예 | 이벤트 이름입니다. 예: `submitted`, `approved`. |
| `actor_role` | `text` | 아니오 | operation을 수행한 role입니다. |
| `from_status` | `text` | 아니오 | 이전 CR 상태입니다. |
| `to_status` | `text` | 아니오 | 변경된 CR 상태입니다. |
| `message` | `text` | 아니오 | evidence, reason, 연결된 job ID입니다. |
| `created_at` | `text` | 예 | UTC 이벤트 시각입니다. |

현재 CR 이벤트 타입:

```text
created
submitted
resubmitted
revision_requested
approved
rejected
reviewer_reassigned
cancelled
implementation_handoff_created
implemented
```

## `cr_handoffs`

용도:

- CR과 생성된 handoff job을 연결합니다.
- revision handoff와 implementation handoff를 구분합니다.
- `cr mark-implemented`가 구현 완료 여부를 검증할 수 있게 합니다.

컬럼:

| 컬럼 | 타입 | 필수 | 용도 |
| --- | --- | --- | --- |
| `cr_id` | `text` | 예 | 관련 CR ID입니다. |
| `job_id` | `text` | 예 | 관련 handoff job ID입니다. |
| `kind` | `text` | 예 | `revision` 또는 `implementation`입니다. |
| `created_at` | `text` | 예 | 연결 생성 UTC 시각입니다. |

Primary key:

```text
(cr_id, job_id)
```

## Index

Index:

```sql
idx_handoff_jobs_status_role on handoff_jobs(status, target_role)
idx_handoff_dependencies_job on handoff_dependencies(job_id)
idx_handoff_dependencies_dep on handoff_dependencies(depends_on_job_id)
idx_handoff_events_job on handoff_events(job_id)
idx_handoff_gate_dependencies_job on handoff_gate_dependencies(job_id)
idx_handoff_gate_dependencies_gate on handoff_gate_dependencies(gate_name)
idx_gate_events_gate on gate_events(gate_name)
idx_cr_status_reviewer on change_requests(status, reviewer_role)
idx_cr_handoffs_cr on cr_handoffs(cr_id)
```

용도:

- `status, target_role`: `next --role`과 status filtering을 빠르게 처리합니다.
- `dependencies.job_id`: 특정 job의 dependency 조회를 빠르게 처리합니다.
- `dependencies.depends_on_job_id`: reverse dependency 분석을 빠르게 처리합니다.
- `events.job_id`: 특정 job의 event history 조회를 빠르게 처리합니다.
- `handoff_gate_dependencies`: job별 Gate 확인과 Gate별 dependent job 조회를 빠르게 처리합니다.
- `gate_events.gate_name`: Gate 감사 이력 조회를 빠르게 처리합니다.
- `cr.status, reviewer_role`: `cr wait-review` 조회를 빠르게 처리합니다.
- `cr_handoffs.cr_id`: implementation 완료 검사를 빠르게 처리합니다.

## Identity Model

DB는 `handoff_jobs.claimed_by`와 `handoff_events.actor_id`에 identity를 기록합니다.

정책:

- long-lived identity로 stable profile name을 사용합니다.
- 예: `frontend-main`, `qa-regression`, `sm`.
- Codex thread ID, turn ID, 임시 파일만을 long-lived identity로 의존하지 않습니다.

CLI identity 결정 순서:

1. `--claimed-by`
2. `BATON_AGENT_ID`
3. `--agent-id-file` 또는 `BATON_AGENT_ID_FILE`
4. role name

## Lifecycle 예

Register:

```text
handoff_jobs.status=open
handoff_events.event_type=registered
```

Claim:

```text
handoff_jobs.status=in_progress
handoff_jobs.claimed_by=frontend-main
handoff_jobs.started_at=<utc>
handoff_events.event_type=claimed
```

Finish:

```text
handoff_jobs.status=finished
handoff_jobs.finished_at=<utc>
handoff_jobs.closure_evidence=<evidence>
handoff_events.event_type=finished
```

Blocked dependency flow:

```text
handoff_jobs.status=blocked
handoff_dependencies records dependency edges
promote-ready updates status to open after dependencies are finished
handoff_events.event_type=promoted
```

Named Gate 흐름:

```text
workflow_gates.status=pending
gate_owners에 하나 이상의 해제 role 기록
handoff_gate_dependencies가 blocked job과 Gate 연결
gate release가 released 전환과 eligible job 승격을 transaction으로 처리
gate cancel이 cancelled 전환과 영향받는 blocked branch만 취소
gate_events가 모든 소유권 및 lifecycle 결정을 기록
```
