# SQLite 스키마

[English (primary)](schema.md) | 한국어

이 문서는 SQLite 기반 Baton에서 사용하는 테이블 구조와 record의 용도를 설명합니다.

이 Baton workflow에서는 SQLite DB가 handoff runtime state의 기준입니다. Agent는 record를 직접 수정하지 말고 `baton` 명령을 사용해야 합니다.

## 개요

테이블:

- `schema_migrations`: 순서가 보장되는 DB migration 이력
- `database_metadata`: 생성 및 migration에 사용한 Baton package version 진단 정보
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
- `handoff_failure_reviews`: 실패 handoff와 결정 CR 및 처리 결과의 연결
- `handoff_controls`: wait loop 중지/재개 제어
- `waiter_leases`: polling interval 자동 조절을 위한 handoff 및 CR waiter heartbeat
- `agent_sessions`: stable agent profile의 opt-in runtime host thread 및 model metadata
- `agent_workstreams`: role 내부의 세부 작업 라우팅 자격
- `handoff_notifications`: peer thread message 전달 결과 감사 기록
- `workspace_events`: handoff 전환의 선택적 Git commit provenance와 정책 결과
- `change_requests`: CR workflow 상태와 Markdown 파일 참조
- `cr_events`: CR 상태 변경 감사 로그
- `cr_handoffs`: CR과 revision/implementation handoff 연결
- `cr_handoff_supersessions`: 취소된 CR implementation의 감사 가능한 대체 관계

상태를 변경하는 CLI 명령은 `BEGIN IMMEDIATE` transaction을 사용해 write 작업을 직렬화합니다.

새 timestamp는 `YYYY-MM-DD HH:MM:SS.ffffff UTC` 형식을 사용합니다. Reader는 기존 초 단위 값도 읽으며, 과거 event의 timestamp가 같으면 audit report가 source별 event ID로 순서를 결정합니다.

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

`baton migrate`는 검증된 SQLite backup을 먼저 만든 뒤 pending migration, 해당되는 seed 보강, `PRAGMA quick_check`, `PRAGMA foreign_key_check`를 하나의 transaction에서 실행합니다. 실패하면 schema 변경, seed 변경, migration record가 함께 rollback됩니다. 일반 workflow 명령은 pending migration을 자동 적용하지 않고 실패하며, waiter, active handoff, cancellation acknowledgement 또는 claimed submitted CR review가 남아 있으면 migration을 거부합니다. 전체 기본 권한은 신규 또는 무버전 DB에만 seed하며, 이후 migration은 그 migration에서 새로 도입한 권한만 추가하므로 프로젝트별 권한 철회가 보존됩니다.

현재 binary가 아는 migration:

```text
1 initial_schema
2 handoff_cancel_permission
3 named_gates
4 waiter_leases
5 database_metadata
6 workspace_provenance
7 handoff_failures
8 cr_body_integrity
9 plan_revision_controls
10 opt_in_thread_notifications
11 workstream_routing
12 retry_and_replacement_tracking
```

`baton upgrade preflight`는 실행 파일 교체 전에 인식 가능한 구버전 schema도 검사할 수 있는 읽기 전용 운영 점검입니다. 명시적인 global stop을 요구하고 blocker object ID를 출력합니다. Migration 후에는 `baton migrate --check`로 DB가 현재 binary가 아는 최신 schema version인지 확인합니다.

## `database_metadata`

용도:

- 신규 versioned DB를 생성한 Baton package version을 알 수 있을 때 기록합니다.
- 최근 schema migration에 사용한 package version과 UTC 시각을 기록합니다.
- `baton project info` 진단에 사용하며 호환성 판단 기준은 아닙니다.

컬럼:

| 컬럼 | 타입 | 필수 | 용도 |
| --- | --- | --- | --- |
| `key` | `text primary key` | 예 | 고정된 metadata key입니다. |
| `value` | `text` | 예 | 진단 값입니다. |
| `updated_at` | `text` | 예 | metadata 갱신 UTC 시각입니다. |

알려진 key는 `created_with_baton_version`, `last_migrated_with_baton_version`, `last_migrated_at`입니다. 과거 unversioned DB를 처음 migration하면 생성 version은 `unknown`으로 기록합니다. Schema 변경이 없는 package update는 이 table을 다시 쓰지 않습니다.

앞으로 schema를 변경할 때는 새 migration을 추가하고 `LATEST_SCHEMA_VERSION`을 증가시켜야 합니다. 이미 release된 migration을 직접 수정하면 안 됩니다.

## `workspace_events`

용도:

- Handoff register, claim, finish의 선택적 Git provenance를 기록합니다.
- Git history, diff 또는 source 본문을 복제하지 않고 workflow event와 commit ID를 연결합니다.
- `warn` 결과와 승인된 `strict` override를 감사 및 report에 제공합니다.

컬럼:

| 컬럼 | 타입 | 필수 | 용도 |
| --- | --- | --- | --- |
| `id` | `integer primary key` | 예 | 증가하는 workspace event ID입니다. |
| `entity_type` | `text` | 예 | `project`, `handoff` 또는 향후 `cr` provenance scope입니다. |
| `entity_id` | `text` | 아니오 | 해당되는 handoff 또는 CR ID입니다. |
| `operation` | `text` | 예 | `registered`, `claimed`, `finished` 같은 상태 전환입니다. |
| `policy` | `text` | 예 | 적용된 `warn` 또는 `strict` 정책입니다. `off`는 event를 만들지 않습니다. |
| `outcome` | `text` | 예 | `accepted`, `warning` 또는 승인된 `override`입니다. |
| `head_commit` | `text` | 아니오 | 전환 시점의 HEAD commit입니다. |
| `baseline_commit` | `text` | 아니오 | ancestry 비교에 사용한 이전 Baton commit입니다. |
| `branch` | `text` | 아니오 | 참고용 branch 이름 또는 `DETACHED`입니다. |
| `dirty` | `integer` | 예 | Git이 working tree 변경을 보고하면 `1`입니다. |
| `actor_role` | `text` | 아니오 | 전환 수행 또는 override 승인 role입니다. |
| `message` | `text` | 아니오 | 경고 상세 또는 override 사유입니다. |
| `created_at` | `text` | 예 | UTC event 시각입니다. |

Migration 6은 기본 `sm` role에 `workspace.override` 권한도 추가합니다. 기존 프로젝트 권한은 새로 도입된 이 권한 외에는 변경하지 않습니다.

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

- `init` 또는 각 권한을 도입한 migration 시 `sm`은 모든 CR 권한, `handoff.cancel`, `handoff.register`, `gate.manage`, `workspace.override`를 받습니다.
- 신규 프로젝트의 `planning`은 실패 결정에 필요한 CR 심사 권한과 `handoff.cancel`, `handoff.register`를 받습니다.
- Migration 7은 기존 프로젝트의 등록 동작을 보존하기 위해 이미 존재하는 모든 active role에 `handoff.register`를 부여합니다. SM은 프로젝트 정책 검토 후 이 호환 권한을 철회할 수 있습니다.

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
handoff.register
gate.manage
workspace.override
```

권한 부여와 철회는 `role permission-add`, `role permission-remove`를 사용합니다. 권한 철회는 프로젝트 정책 결정으로 취급하며, 반복 migration은 전체 기본 권한 집합을 다시 복원하지 않습니다.

## `handoff_jobs`

용도:

- handoff queue의 핵심 작업 record를 저장합니다.
- 파일 기반 구조의 `jobs/`, `blocked/`, `finished/` 같은 위치 상태를 대체합니다.
- `register`, `next`, `claim`, `finish`, `status` 명령이 사용하는 기본 데이터입니다.
- `fail`을 통해 실패 결과를 명시적으로 기록하면서 하위 작업이 풀리지 않게 합니다.

컬럼:

| 컬럼 | 타입 | 필수 | 용도 |
| --- | --- | --- | --- |
| `job_id` | `text primary key` | 예 | 안정적인 handoff ID입니다. 예: `HO-2026-06-02-001`. |
| `title` | `text` | 예 | 사람이 읽기 쉬운 짧은 제목입니다. |
| `status` | `text` | 예 | 현재 queue 상태입니다. |
| `target_role` | `text` | 예 | 이 job을 claim/finish할 수 있는 표준 role입니다. |
| `workstream` | `text` | 아니오 | claimant가 `target_role` 안에서 등록해야 하는 선택적 세부 작업 영역입니다. |
| `attempt` | `integer` | 예 | 1부터 시작하며 심사된 retry마다 증가하는 현재 실행 세대입니다. |
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
cancel_requested
failed
finished
cancelled
```

상태 의미:

- `blocked`: 필수 upstream job이 완료되기를 기다리는 상태입니다.
- `open`: `target_role`이 claim할 수 있는 ready 상태입니다.
- `in_progress`: agent profile이 claim한 상태입니다.
- `cancel_requested`: 권한 있는 role이 취소를 요청했으며 원래 claimant가 작업을 멈춘 상태에서 검토 후 철회 또는 최종 취소 확인을 기다리는 상태입니다.
- `failed`: claim한 작업이 exit criteria를 충족하지 못해 실패 CR의 결정을 기다리거나 기록한 상태입니다.
- `finished`: closure evidence와 함께 완료된 상태입니다.
- `cancelled`: 단순 pause가 아니라 job 자체가 의도적으로 취소된 상태입니다.

권한이 있는 `cancel` 명령은 선택한 `blocked`, `open` 또는 심사가 끝난 `failed` job을 즉시 `cancelled`로 바꾸고 blocked 하위 job을 재귀적으로 취소합니다. `in_progress` job은 `cancel_requested`로 바뀝니다. claimant가 확인하기 전에 `handoff.cancel` 권한이 있는 role이 `cancel-withdraw`를 실행하면 검토 사유를 기록하면서 기존 `claimed_by`와 `started_at`을 유지한 채 `in_progress`로 돌아갑니다. 연결된 구현 CR이 이미 `cancelled` 또는 `superseded`이면 원 설계가 폐기되었으므로 철회를 거부합니다. 원래 claimant가 `cancel-ack`를 실행해야 최종 취소와 하위 전파가 확정됩니다. claimant가 확인할 수 없을 때만 감사되는 복구 경로인 `cancel --force`를 사용합니다. 실패 job은 먼저 연결된 실패 CR이 `rejected` 또는 `cancelled` 상태여야 합니다.

`fail`은 `in_progress` job만 `failed`로 바꾸고 연결된 실패 CR을 생성·제출하며, 하위 dependency는 `blocked`로 유지합니다. 실패 CR이 승인되면 reviewer가 `retry`로 `attempt`를 증가시키고 원래 job을 `open`으로 되돌릴 수 있습니다. 실패 CR이 거절되면 권한 있는 role이 해당 job과 하위 branch를 취소할 수 있습니다. 하위 작업은 재시도된 원래 job이 `finished`가 된 이후에만 ready 상태가 됩니다.

최소 ready job 예:

```text
job_id=HO-2026-06-02-001
title=Frontend upload follow-up
status=open
target_role=frontend
source_ref=cr:CR-2026-06-02-example
objective=Implement the approved upload follow-up.
exit_criteria=The approved behavior is implemented and verified.
created_at=2026-06-02 09:00:00 UTC
```

## `handoff_dependencies`

용도:

- job 사이의 의존성 edge를 저장합니다.
- `finish`, Gate release 및 reconciliation 명령이 `blocked` job을 언제 `open`으로 바꿀 수 있는지 판단하게 합니다.
- `handoff successors`가 하위 작업을 배정하지 않고 역방향으로 조회할 수 있게 합니다.

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
- `finish`는 upstream 완료와 같은 transaction에서 조건을 충족한 직접 하위 job을 승격합니다.
- upstream이 `failed`이면 성공 완료가 아니므로 실패 CR 심사와 재시도 동안 하위 job은 `blocked`로 유지됩니다.
- 필수 upstream job 중 하나라도 `cancelled`가 되면 Baton은 이를 기다리는 blocked job을 재귀적으로 `cancelled`로 변경합니다.
- 전파된 각 상태 변경에는 직접적인 upstream job을 원인으로 기록한 `dependency_cancelled` handoff event가 한 번 남습니다.
- 이미 취소된 dependency를 지정해 새 handoff를 등록하면 `blocked`가 아니라 즉시 `cancelled` 상태로 생성됩니다.
- 선언한 dependency가 모두 이미 `finished`라면 새 handoff는 별도 승격 명령 없이 `open`으로 시작합니다.
- 중복 dependency ID는 job을 삽입하기 전에 거부합니다.
- `failed` dependency를 참조한 새 handoff는 `blocked`로 남고 경고를 출력합니다. 독립 remediation은 failed job을 실행 dependency로 두지 말고 `source_ref`로 인과관계를 기록해야 합니다.
- `promote-ready`는 취소된 dependency 뒤에 blocked job이 남아 있는 이전 DB record도 함께 정리합니다.
- 독립 job과 관련 없는 dependency branch는 이 전파로 취소되지 않습니다.

## `handoff_failure_reviews`

용도:

- 각 handoff 실패 시도를 자동 제출된 결정 CR과 연결합니다.
- 실패를 성공 완료로 취급하지 않으면서 재시도와 취소 결정을 감사 가능하게 기록합니다.
- 같은 handoff에서 여러 번 실패하고 재시도하는 흐름을 지원합니다.

각 row는 `job_id`, 고유 `cr_id`, 실패 role, 사유, 선택 evidence, 실패 시각과 선택적인 `retry` 또는 `cancelled` 처리 결과 및 결정 role·시각·메시지를 기록합니다. 처리 결과가 없는 row가 해당 job의 활성 실패 심사입니다. CR reviewer는 `handoff.register`와 필요한 CR 심사 권한을 보유해야 합니다. 기본 reviewer는 `planning`이며, planning 자체가 실패한 경우 자기 심사를 막기 위해 `sm`이 기본값입니다.

| 컬럼 | 타입 | 필수 | 용도 |
| --- | --- | --- | --- |
| `id` | `integer primary key` | 예 | 증가하는 실패 시도 식별자입니다. |
| `job_id` | `text` | 예 | 실패한 handoff입니다. |
| `cr_id` | `text unique` | 예 | 자동 제출된 실패 CR입니다. |
| `failed_by_role` | `text` | 예 | 실패를 보고한 target role입니다. |
| `reason` | `text` | 예 | 구체적인 실패 사유입니다. |
| `evidence` | `text` | 아니오 | Test output 등 보조 evidence입니다. |
| `failed_at` | `text` | 예 | 실패 UTC 시각입니다. |
| `resolution` | `text` | 아니오 | `retry`, `cancelled` 또는 심사 중일 때 null입니다. |
| `resolved_by_role` | `text` | 아니오 | 심사 결정을 적용한 role입니다. |
| `resolved_at` | `text` | 아니오 | 처리 UTC 시각입니다. |
| `resolution_message` | `text` | 아니오 | 필수 재시도 또는 취소 사유입니다. |

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
agent_session_active
agent_session_replaced
agent_session_inactive
registered
claimed
finished
promoted
cancelled
cancellation_requested
cancellation_withdrawn
cancellation_acknowledged
cancellation_forced
dependency_cancelled
gate_cancelled
control_stopped
control_resumed
shift_started
shift_extended
shift_ended
notification_sent
notification_failed
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
- `finish`는 shift control을 확인하지 않으므로 이미 claim한 작업은 shift 만료 후에도 완료 보고할 수 있습니다. 단, `cancel_requested` 작업은 거부하며 검토 결과에 따라 `cancel-withdraw`로 `in_progress`를 복구하거나 취소가 확정된 후 claimant가 `cancel-ack`를 사용해야 합니다.

## `waiter_leases`

용도:

- 같은 Baton DB를 공유하는 `wait`, `cr wait-review`, 통합 `watch` process를 추적합니다.
- 기본 자동 polling interval 계산에 사용하는 활성 waiter 수를 제공합니다.
- workflow 이력이나 감사 증거가 아닌 일시적인 조율 상태를 저장합니다.

컬럼:

| 컬럼 | 타입 | 필수 | 용도 |
| --- | --- | --- | --- |
| `waiter_id` | `text primary key` | 예 | wait 명령 시작 시 생성되는 process-local UUID입니다. |
| `wait_kind` | `text` | 예 | `handoff`, `cr_review` 또는 `watch`입니다. |
| `role_id` | `text` | 예 | waiter에 연결된 표준 role입니다. |
| `started_at` | `text` | 예 | wait 명령이 등록된 UTC 시각입니다. |
| `heartbeat_at` | `text` | 예 | 최근 polling heartbeat UTC 시각입니다. |
| `lease_expires_at` | `text` | 예 | heartbeat 만료 시각입니다. 자동 wait는 30초이고 긴 고정 interval에는 5초 여유가 추가됩니다. |

동작:

- 정상 timeout, stop, 작업 발견, 오류 종료 시 `finally` cleanup에서 lease를 제거합니다.
- process 또는 pipe가 cleanup 전에 끊기면 이후 waiter heartbeat가 만료된 lease를 제거합니다.
- 자동 모드는 `min(30초, 3초 × 활성 waiter 수)`를 목표로 하며 동시 polling 방지를 위한 작은 고정 jitter를 추가합니다.
- 숫자 `--interval`은 해당 process에 고정 적용되지만, 그 lease도 자동 waiter가 사용하는 활성 수에 포함됩니다.
- 25초를 초과하는 고정 interval은 정상 sleep 중인 process가 stale로 제거되지 않도록 `interval + 5초` lease를 사용합니다.
- Lease record는 `baton-report` 감사 또는 workflow summary에 포함되지 않습니다.

## `agent_sessions`

용도:

- peer 알림을 명시적으로 사용할 때 stable Baton agent profile을 하나의 active runtime endpoint와 연결합니다.
- 기존 Codex task를 선택하는 데 필요한 host, thread ID, role, model을 기록합니다.
- runtime ID를 권한 또는 영속 identity로 취급하지 않으면서 inactive endpoint 이력을 보존합니다.

컬럼:

| 컬럼 | 타입 | 필수 | 용도 |
| --- | --- | --- | --- |
| `session_id` | `text primary key` | 예 | Baton이 생성한 runtime session UUID입니다. |
| `agent_id` | `text` | 예 | `claimed_by`에도 사용하는 stable profile identity입니다. |
| `role_id` | `text` | 예 | session이 표시하는 canonical role입니다. |
| `host` | `text` | 예 | message host이며 초기 사용 값은 `codex`입니다. |
| `thread_id` | `text` | 예 | host별 기존 task 식별자입니다. |
| `model` | `text` | 예 | 등록 시 제공한 정확한 model metadata입니다. |
| `status` | `text` | 예 | `active` 또는 `inactive`입니다. |
| `created_at` | `text` | 예 | 최초 등록 시각입니다. |
| `updated_at` | `text` | 예 | 최근 등록 또는 lifecycle 변경 시각입니다. |
| `ended_at` | `text` | 아니오 | endpoint 비활성화 시각입니다. |
| `end_reason` | `text` | 아니오 | 비활성화 또는 교체 사유입니다. |

`unique(host, thread_id)`는 한 host thread가 두 profile을 나타내는 것을 방지하고 partial unique index는 `agent_id`마다 하나의 active endpoint만 허용합니다. session 교체에는 명시적 `--replace`가 필요합니다. `active`는 현재 실행 중이라는 뜻이 아니라 이후 follow-up을 받을 수 있다는 뜻입니다. Planner가 waiter lease 대신 이 addressability에 의존하려면 문서화된 push-first idle 조건을 모두 충족해야 하며, session row 자체는 완전한 알림 경로를 증명하지 않습니다.

## `agent_workstreams`

용도:

- role 권한과 세부 작업 라우팅 자격을 분리합니다.
- 하나의 넓은 role 안에서 `api-contract`, `ui-regression` 같은 안정적인 영역을 agent가 표시할 수 있게 합니다.

컬럼:

| 컬럼 | 타입 | 필수 | 용도 |
| --- | --- | --- | --- |
| `agent_id` | `text` | 예 | 안정적인 구체 agent profile입니다. |
| `role_id` | `text` | 예 | 세부 작업 영역이 적용되는 role입니다. |
| `workstream` | `text` | 예 | 정규화된 domain route입니다. |
| `created_at` | `text` | 예 | UTC 등록 시각입니다. |

복합 primary key는 `(agent_id, role_id, workstream)`입니다. Handoff 또는 CR의 workstream이 null이면 이전 role-only 라우팅을 유지합니다. Workstream 등록은 role 권한을 부여하지 않습니다.

## `handoff_notifications`

용도:

- agent가 기존 peer thread에 알림을 시도한 이후 실제 결과를 기록합니다.
- 감사용 sender/recipient profile 및 model metadata snapshot을 남깁니다.
- handoff ownership을 바꾸지 않으면서 성공 wake-up message의 반복을 막습니다.

컬럼:

| 컬럼 | 타입 | 필수 | 용도 |
| --- | --- | --- | --- |
| `id` | `integer primary key autoincrement` | 예 | 전달 시도 순서입니다. |
| `job_id` | `text` | 예 | message에 포함된 ready handoff입니다. |
| `attempt` | `integer` | 예 | 이 전달 기록과 연결된 handoff 실행 세대입니다. |
| `sender_session_id` | `text` | 예 | 발신 runtime session입니다. |
| `recipient_session_id` | `text` | 예 | 선택한 기존 peer runtime session입니다. |
| `sender_agent_id` | `text` | 예 | stable sender profile snapshot입니다. |
| `sender_model` | `text` | 예 | sender model snapshot입니다. |
| `recipient_agent_id` | `text` | 예 | stable recipient profile snapshot입니다. |
| `recipient_thread_id` | `text` | 예 | 전달을 시도한 host task입니다. |
| `recipient_model` | `text` | 예 | recipient model snapshot입니다. |
| `transport` | `text` | 예 | 전달에 사용한 runtime host입니다. |
| `delivery_status` | `text` | 예 | 호환성 저장 값이며 `sent`(host가 수락함) 또는 `failed`입니다. |
| `message_ref` | `text` | 아니오 | 선택적 host delivery/message reference입니다. |
| `detail` | `text` | 아니오 | 결과 상세이며 CLI는 실패 시 필수로 요구합니다. |
| `created_at` | `text` | 예 | 전달 시도 시각입니다. |

partial unique index는 `(job_id, attempt)`마다 최대 하나의 `sent` row만 허용합니다. CLI text는 이 저장 값을 `host_accepted`로 표시하며 recipient acknowledgement를 뜻하지 않습니다. `notify status`는 별도의 권위 상태를 추가하지 않고 현재 handoff와 최근 전달 기록에서 accepted-unclaimed, stale-unclaimed, claimed 결과를 계산합니다. 실패 전달 기록은 fallback 진단을 위해 보존합니다. 심사된 retry는 handoff attempt를 증가시켜 과거 감사 row를 유지하면서 수정 baseline에 대한 새 성공 전달 1건을 허용합니다. 일반적으로 `finish`가 ready 직접 하위 작업을 승격하며, `notify targets`는 호환성 reconciliation을 위해 동일한 범위의 승격을 유지하고 선택적 workstream과 일치하며 현재 `in_progress` 또는 `cancel_requested` handoff나 claimed submitted CR review를 소유하지 않은 active Codex peer 후보를 반환합니다. 프로젝트 로컬 global 또는 대상 role stop이 적용되거나 shift가 만료된 경우에는 후보 없이 `outside_shift`를 반환하며 handoff는 `open`으로 유지됩니다. 이 명령은 message를 보내지 않으며 다른 model host가 호환되는 peer messaging을 제공한다고 가정하지 않습니다. `notify record`는 agent가 host messaging tool을 사용한 후 보고한 결과를 기록합니다. 어느 명령도 handoff를 claim하지 않습니다. 인증 token과 message 본문은 저장하지 않습니다.

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
| `reviewer_workstream` | `text` | 아니오 | 구체 reviewer에게 요구되는 선택적 세부 작업 영역입니다. |
| `review_claimed_by` | `text` | 아니오 | 현재 review claim을 소유하거나 심사를 완료한 구체 agent입니다. |
| `review_started_at` | `text` | 아니오 | 최근 review claim의 UTC 시각입니다. |
| `file_path` | `text` | 예 | Markdown 본문 파일 경로입니다. |
| `created_at` | `text` | 예 | UTC 생성 시각입니다. |
| `updated_at` | `text` | 예 | UTC 수정 시각입니다. |
| `submitted_at` | `text` | 아니오 | 마지막 제출 시각입니다. |
| `approved_at` | `text` | 아니오 | 승인 시각입니다. |
| `rejected_at` | `text` | 아니오 | 최종 반려 시각입니다. |
| `implemented_at` | `text` | 아니오 | 구현 완료 시각입니다. |
| `revision_count` | `integer` | 예 | 보강 요청 횟수입니다. |
| `active_revision_job_id` | `text` | 아니오 | 진행 중인 보강 handoff입니다. |
| `submitted_body_hash` | `text` | 아니오 | 마지막 submit 또는 resubmit에서 캡처한 Markdown 본문의 SHA-256입니다. |
| `approved_body_hash` | `text` | 아니오 | reviewer가 승인한 불변 본문의 SHA-256입니다. null이면 migration된 과거 미봉인 승인입니다. |
| `superseded_by_cr_id` | `text` | 아니오 | `cr supersede`로 이 승인을 대체한 approved CR입니다. |
| `superseded_by_ref` | `text` | 아니오 | replacement CR 대신 사용한 불변 authoritative design reference입니다. |

허용되는 `status` 값:

```text
draft
submitted
revision_requested
approved
rejected
implemented
superseded
cancelled
```

상태 규칙:

- `draft -> submitted`는 author role이 수행합니다.
- `submitted -> revision_requested`, `approved`, `rejected`는 reviewer role이 수행합니다.
- Workstream으로 라우팅된 submitted review는 자격이 있는 구체 agent가 먼저 claim해야 하며, 해당 claimant만 심사 결정을 할 수 있습니다.
- Resubmit과 reviewer 재지정은 이전 review claim을 해제합니다. `cr release-review`는 결정하지 않은 submitted review claim을 감사 사유와 함께 해제합니다.
- `revision_requested -> submitted`는 Markdown 본문 보강 후 author role이 수행합니다.
- 승인은 현재 본문이 `submitted_body_hash`와 일치해야 하며 `approved_body_hash`를 기록합니다.
- implementation handoff 생성, claim, finish와 최종 구현 완료 처리는 승인 본문 hash가 유지돼야 합니다.
- hash 없이 migration된 과거 approved CR은 새 구현 전에 reviewer가 명시적으로 `cr seal`해야 합니다.
- `approved -> implemented`는 연결된 implementation handoff가 최소 1개 있어야 합니다. 모든 implementation은 `finished`이거나, 명시적인 replacement chain이 finished implementation에 도달하는 `cancelled` 상태여야 합니다.
- `approved -> superseded`는 `cr.admin`과 approved replacement CR 또는 `handoff.register` 권한을 가진 role의 불변 authoritative design reference가 필요합니다. 연결된 queued 구현 작업은 취소되고 active 작업은 `cancel_requested`가 되며 finished 작업은 보존됩니다.
- `cancelled`는 `cr.admin` 권한을 가진 role이 수행하고 연결된 미완료 구현 작업을 같은 취소 규칙으로 정리하며 audit event를 남깁니다.
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
body_sealed
rejected
reviewer_reassigned
cancelled
superseded
supersedes
implementation_handoff_created
implementation_handoff_superseded
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

## `cr_handoff_supersessions`

용도:

- 같은 approved CR에서 취소된 implementation handoff가 명시적으로 대체됐음을 기록합니다.
- 폐기된 job과 event를 보존하면서 replacement chain이 끝난 뒤에만 CR 종료를 허용합니다.
- migration 또는 `mark-implemented`가 무관한 취소를 완료로 추측하지 못하게 합니다.

컬럼:

| 컬럼 | 타입 | 필수 | 용도 |
| --- | --- | --- | --- |
| `cr_id` | `text` | 예 | 폐기 및 대체 implementation handoff가 공유하는 approved CR입니다. |
| `retired_job_id` | `text` | 예 | 취소된 implementation handoff입니다. |
| `replacement_job_id` | `text` | 예 | 폐기된 경로를 대체하는 유효한 implementation handoff입니다. |
| `actor_role` | `text` | 예 | 대체 관계를 기록한 지정 reviewer role입니다. |
| `reason` | `text` | 예 | 감사되는 대체 사유입니다. |
| `created_at` | `text` | 예 | 관계 생성 UTC 시각입니다. |

Primary key는 `(cr_id, retired_job_id)`입니다. `cr supersede-handoff`는 두 job이 같은 approved CR의 implementation으로 연결돼 있어야 하고, old job은 `cancelled`여야 하며, failed 또는 cancelled replacement는 거부합니다.

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
idx_agent_sessions_active_agent on agent_sessions(agent_id) where status = 'active'
idx_agent_sessions_role_status on agent_sessions(role_id, status, updated_at)
idx_handoff_notifications_job on handoff_notifications(job_id, id)
idx_handoff_notifications_sent_attempt on handoff_notifications(job_id, attempt) where delivery_status = 'sent'
idx_handoff_notifications_recipient on handoff_notifications(recipient_agent_id, created_at)
idx_cr_status_reviewer on change_requests(status, reviewer_role)
idx_cr_handoffs_cr on cr_handoffs(cr_id)
idx_cr_handoff_supersessions_replacement on cr_handoff_supersessions(cr_id, replacement_job_id)
```

용도:

- `status, target_role`: `next --role`과 status filtering을 빠르게 처리합니다.
- `dependencies.job_id`: 특정 job의 dependency 조회를 빠르게 처리합니다.
- `dependencies.depends_on_job_id`: reverse dependency 분석을 빠르게 처리합니다.
- `events.job_id`: 특정 job의 event history 조회를 빠르게 처리합니다.
- `handoff_gate_dependencies`: job별 Gate 확인과 Gate별 dependent job 조회를 빠르게 처리합니다.
- `gate_events.gate_name`: Gate 감사 이력 조회를 빠르게 처리합니다.
- `agent_sessions`: profile별 active endpoint uniqueness와 role별 후보 조회를 처리합니다.
- `handoff_notifications`: job/recipient별 감사 조회와 job attempt별 단일 성공 전달을 처리합니다.
- `cr.status, reviewer_role`: `cr wait-review` 조회를 빠르게 처리합니다.
- `cr_handoffs.cr_id`: implementation 완료 검사를 빠르게 처리합니다.
- `cr_handoff_supersessions`: CR 종료 시 replacement chain 검증을 빠르게 처리합니다.

## Identity Model

DB는 `handoff_jobs.claimed_by`, `handoff_events.actor_id`, 그리고 opt-in `agent_sessions`와 `handoff_notifications`에 runtime/model metadata를 기록합니다.

정책:

- long-lived identity로 stable profile name을 사용합니다.
- 예: `frontend-main`, `qa-regression`, `sm`.
- Codex thread ID, turn ID, 임시 파일만을 long-lived identity로 의존하지 않습니다.
- thread ID와 model name은 알림 metadata이며 routing 권한 또는 permission 판단 입력이 아닙니다.

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
finish updates eligible direct successors to open after dependencies are finished
promote-ready reconciles older or externally restored state
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
