#!/usr/bin/env python3
"""SQLite-backed Baton CLI."""

from __future__ import annotations

import argparse
import configparser
import hashlib
import json
import os
import re
import sqlite3
import subprocess
import sys
import time
import uuid
from contextlib import closing
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from importlib.resources import files
from pathlib import Path

from agents_baton import __version__


DEFAULT_ROLES = (
    ("sm", "System Manager"),
    ("planning", "Planning"),
    ("architecture", "Architecture"),
    ("backend", "Backend"),
    ("frontend", "Frontend"),
    ("qa", "QA"),
    ("devops", "DevOps"),
    ("ui-design", "UI Design"),
    ("backend-design", "Backend Design"),
)

STATUSES = {
    "blocked",
    "open",
    "in_progress",
    "cancel_requested",
    "failed",
    "finished",
    "cancelled",
}
CR_STATUSES = {
    "draft",
    "submitted",
    "revision_requested",
    "approved",
    "rejected",
    "implemented",
    "superseded",
    "cancelled",
}
BATON_VERSION = __version__
AUTO_INTERVAL_BASE_SECONDS = 3
AUTO_INTERVAL_MAX_SECONDS = 30
WAITER_LEASE_SECONDS = 30
REVIEW_PERMISSIONS = {
    "cr.admin",
    "cr.review",
    "cr.request_revision",
    "cr.approve",
    "cr.reject",
    "cr.assign_implementation",
    "cr.mark_implemented",
}
HANDOFF_PERMISSIONS = {
    "handoff.cancel",
    "handoff.evidence_correct",
    "handoff.register",
}
GATE_PERMISSIONS = {
    "gate.manage",
}
WORKSPACE_PERMISSIONS = {
    "workspace.override",
}
FAILURE_REVIEW_PERMISSIONS = {
    "handoff.register",
    "cr.review",
    "cr.request_revision",
    "cr.approve",
    "cr.reject",
    "cr.mark_implemented",
}
KNOWN_PERMISSIONS = (
    REVIEW_PERMISSIONS | HANDOFF_PERMISSIONS | GATE_PERMISSIONS | WORKSPACE_PERMISSIONS
)
COMPLETION_OUTCOMES = {
    "unspecified",
    "pass",
    "fail",
    "conditional",
    "inconclusive",
}
LATEST_SCHEMA_VERSION = 14
PROJECT_FORMAT_VERSION = 1
PROJECT_MARKER_NAME = "project.json"
PROJECT_CONFIG_NAME = "baton.toml"
DEFAULT_CR_DIRECTORY = ".baton/change-requests"
GUIDE_FILES = {
    "bootstrap": "agent-bootstrap.md",
    "worker": "agent-prompt.md",
    "planner": "planner-prompt.md",
    "git": "git-integration.md",
    "upgrade": "upgrade-guide.md",
    "changelog": "changelog.md",
}


class MigrationError(RuntimeError):
    pass


@dataclass(frozen=True)
class WorkspaceConfig:
    root: Path
    path: Path
    provider: str
    policy: str
    required_version: str


@dataclass(frozen=True)
class WorkspaceSnapshot:
    repository_root: Path
    head_commit: str
    branch: str
    dirty: bool


@dataclass(frozen=True)
class WorkspaceAssessment:
    config: WorkspaceConfig
    snapshot: WorkspaceSnapshot | None
    baseline_commit: str
    issues: tuple[str, ...]


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S.%f UTC")


def parse_utc(value: str) -> datetime:
    for timestamp_format in ("%Y-%m-%d %H:%M:%S.%f UTC", "%Y-%m-%d %H:%M:%S UTC"):
        try:
            return datetime.strptime(value, timestamp_format).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    raise ValueError(f"invalid UTC timestamp: {value}")


def format_utc(value: datetime) -> str:
    return value.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S.%f UTC")


def parse_duration(value: str) -> timedelta:
    text = value.strip().lower()
    match = re.fullmatch(r"(\d+)([smhd]?)", text)
    if not match:
        raise SystemExit("ERROR: duration must be an integer with optional s, m, h, or d suffix")
    amount = int(match.group(1))
    unit = match.group(2) or "s"
    if amount <= 0:
        raise SystemExit("ERROR: duration must be greater than zero")
    if unit == "s":
        return timedelta(seconds=amount)
    if unit == "m":
        return timedelta(minutes=amount)
    if unit == "h":
        return timedelta(hours=amount)
    if unit == "d":
        return timedelta(days=amount)
    raise SystemExit(f"ERROR: unsupported duration unit: {unit}")


def parse_poll_interval(value: str) -> int | None:
    if value.strip().lower() == "auto":
        return None
    try:
        interval = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("interval must be an integer number of seconds") from exc
    if interval < 1:
        raise argparse.ArgumentTypeError("interval must be at least 1 second")
    return interval


def automatic_poll_interval(active_waiters: int) -> int:
    return min(AUTO_INTERVAL_MAX_SECONDS, AUTO_INTERVAL_BASE_SECONDS * max(1, active_waiters))


def poll_sleep_seconds(configured_interval: int | None, active_waiters: int, waiter_id: str) -> float:
    if configured_interval is not None:
        return float(configured_interval)
    target = automatic_poll_interval(active_waiters)
    jitter_units = int(waiter_id.replace("-", "")[-8:], 16) % 501
    jitter_fraction = 0.05 + (jitter_units / 10000)
    return max(1.0, target * (1.0 - jitter_fraction))


def bounded_sleep_seconds(interval: float, deadline: float | None) -> float:
    if deadline is None:
        return interval
    return min(interval, max(0.0, deadline - time.monotonic()))


def waiter_lease_seconds(configured_interval: int | None) -> int:
    if configured_interval is None:
        return WAITER_LEASE_SECONDS
    return max(WAITER_LEASE_SECONDS, configured_interval + 5)


def today_text() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def normalize_role(role: str) -> str:
    return role.strip().lower().replace("_", "-").replace(" ", "-")


def normalize_gate_name(name: str) -> str:
    normalized = name.strip().lower().replace("_", "-").replace(" ", "-")
    if not re.fullmatch(r"[a-z0-9][a-z0-9.-]*", normalized):
        raise SystemExit("ERROR: gate name must contain only lowercase letters, digits, dots, and hyphens")
    return normalized


def normalize_workstream(name: str) -> str:
    normalized = name.strip().lower().replace("_", "-").replace(" ", "-")
    if not re.fullmatch(r"[a-z0-9][a-z0-9.-]*", normalized):
        raise SystemExit(
            "ERROR: workstream must contain only lowercase letters, digits, dots, and hyphens"
        )
    return normalized


def slugify(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.strip().lower())
    return slug.strip("-") or "change-request"


def connect(db_path: str, *, create: bool = False) -> sqlite3.Connection:
    path = Path(db_path)
    if not path.exists() and not create:
        raise MigrationError(
            f"database does not exist: {db_path}; run 'baton init' only for a new project; "
            "in an isolated worktree, set BATON_DB to the existing shared control database"
        )
    if create and path.parent != Path("."):
        path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path, timeout=30)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    con.execute("PRAGMA busy_timeout = 30000")
    return con


def connect_readonly(db_path: str) -> sqlite3.Connection:
    path = Path(db_path)
    if not path.exists():
        raise MigrationError(f"database does not exist: {db_path}")
    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=30)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    con.execute("PRAGMA busy_timeout = 30000")
    return con


def enable_wal(con: sqlite3.Connection, timeout_seconds: float = 30.0) -> None:
    deadline = time.monotonic() + timeout_seconds
    while True:
        try:
            con.execute("PRAGMA journal_mode = WAL").fetchone()
            return
        except sqlite3.OperationalError as exc:
            if "locked" not in str(exc).lower() and "busy" not in str(exc).lower():
                raise
            if time.monotonic() >= deadline:
                raise MigrationError("timed out waiting to enable SQLite WAL mode") from exc
            time.sleep(0.05)


def default_agent_id_file() -> Path:
    override = os.environ.get("BATON_AGENT_ID_FILE") or os.environ.get("HANDOFF_AGENT_ID_FILE")
    if override:
        return Path(override)
    root = find_project_root() or find_legacy_project_root()
    if not root:
        raise MigrationError("Baton project marker not found; run 'baton init' in the intended project root")
    return root / ".baton" / "agent-id"


def agent_id_file(args: argparse.Namespace) -> Path:
    value = getattr(args, "agent_id_file", "") or ""
    if value:
        return Path(value)
    database_value = getattr(args, "db", "") or ""
    if database_value:
        database = Path(database_value).expanduser().resolve()
        if database.is_file() and database.name == "baton.sqlite3" and database.parent.name == ".baton":
            return database.parent / "agent-id"
    return default_agent_id_file()


def read_agent_id(args: argparse.Namespace) -> str:
    env_value = (os.environ.get("BATON_AGENT_ID") or os.environ.get("HANDOFF_AGENT_ID") or "").strip()
    if env_value:
        return env_value
    try:
        path = agent_id_file(args)
    except MigrationError:
        return ""
    if path.exists():
        return path.read_text(encoding="utf-8").strip()
    return ""


def write_agent_id(path: Path, agent_id: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(agent_id.rstrip() + "\n", encoding="utf-8")
    try:
        path.chmod(0o600)
    except OSError:
        pass


def claimed_by_value(args: argparse.Namespace, role: str) -> str:
    explicit = getattr(args, "claimed_by", "") or ""
    if explicit.strip():
        return explicit.strip()
    stored = read_agent_id(args)
    return stored or role


def agent_id_value(args: argparse.Namespace, option_name: str = "agent_id") -> str:
    explicit = getattr(args, option_name, "") or ""
    agent_id = explicit.strip() or read_agent_id(args)
    if not agent_id:
        raise SystemExit(
            "ERROR: agent identity is required; use --agent-id, BATON_AGENT_ID, "
            "or 'baton agent init'"
        )
    return agent_id


def optional_agent_id(args: argparse.Namespace, option_name: str = "agent_id") -> str:
    explicit = getattr(args, option_name, "") or ""
    return explicit.strip() or read_agent_id(args)


def agent_has_workstream(
    con: sqlite3.Connection,
    agent_id: str,
    role: str,
    workstream: str,
) -> bool:
    return bool(
        con.execute(
            """
            select 1
            from agent_workstreams
            where agent_id = ? and role_id = ? and workstream = ?
            """,
            (agent_id, role, workstream),
        ).fetchone()
    )


def require_workstream_assignment(
    con: sqlite3.Connection,
    agent_id: str,
    role: str,
    workstream: str | None,
) -> None:
    if not workstream:
        return
    if not agent_has_workstream(con, agent_id, role, workstream):
        raise SystemExit(
            f"ERROR: agent {agent_id} is not registered for "
            f"role={role} workstream={workstream}"
        )


def require_agent_capacity(
    con: sqlite3.Connection,
    agent_id: str,
    *,
    handoff_id: str = "",
    cr_id: str = "",
) -> None:
    handoff = con.execute(
        """
        select job_id
        from handoff_jobs
        where claimed_by = ? and status in ('in_progress', 'cancel_requested')
          and job_id != ?
        order by created_at, job_id
        limit 1
        """,
        (agent_id, handoff_id),
    ).fetchone()
    if handoff:
        raise SystemExit(
            f"ERROR: agent {agent_id} already owns active handoff {handoff['job_id']}; "
            "finish, fail, or release it before claiming more work"
        )
    review = con.execute(
        """
        select cr_id
        from change_requests
        where review_claimed_by = ? and status = 'submitted' and cr_id != ?
        order by submitted_at, created_at, cr_id
        limit 1
        """,
        (agent_id, cr_id),
    ).fetchone()
    if review:
        raise SystemExit(
            f"ERROR: agent {agent_id} already owns active CR review {review['cr_id']}; "
            "complete or release it before claiming more work"
        )


SCHEMA_V1_SQL = """
        create table if not exists roles (
          role_id text primary key,
          display_name text not null,
          description text,
          active integer not null default 1,
          created_at text not null,
          updated_at text not null
        );

        create table if not exists role_aliases (
          alias text primary key,
          role_id text not null references roles(role_id)
        );

        create table if not exists role_permissions (
          role_id text not null references roles(role_id) on delete cascade,
          permission text not null,
          primary key (role_id, permission)
        );

        create table if not exists handoff_jobs (
          job_id text primary key,
          title text not null,
          status text not null check (status in ('blocked', 'open', 'in_progress', 'finished', 'cancelled')),
          target_role text not null references roles(role_id),
          source_ref text,
          objective text not null,
          exit_criteria text not null,
          created_at text not null,
          claimed_by text,
          started_at text,
          finished_at text,
          closure_evidence text,
          related_commit text
        );

        create table if not exists handoff_dependencies (
          job_id text not null references handoff_jobs(job_id) on delete cascade,
          depends_on_job_id text not null references handoff_jobs(job_id),
          primary key (job_id, depends_on_job_id)
        );

        create table if not exists handoff_events (
          id integer primary key autoincrement,
          job_id text,
          event_type text not null,
          actor_role text,
          actor_id text,
          from_status text,
          to_status text,
          message text,
          created_at text not null
        );

        create table if not exists handoff_controls (
          scope text primary key,
          stopped integer not null default 0,
          reason text,
          work_until text,
          updated_at text not null
        );

        create table if not exists change_requests (
          cr_id text primary key,
          title text not null,
          status text not null check (status in ('draft', 'submitted', 'revision_requested', 'approved', 'rejected', 'implemented', 'cancelled')),
          author_role text not null references roles(role_id),
          reviewer_role text not null references roles(role_id),
          file_path text not null unique,
          created_at text not null,
          updated_at text not null,
          submitted_at text,
          approved_at text,
          rejected_at text,
          implemented_at text,
          revision_count integer not null default 0,
          active_revision_job_id text references handoff_jobs(job_id)
        );

        create table if not exists cr_events (
          id integer primary key autoincrement,
          cr_id text not null references change_requests(cr_id) on delete cascade,
          event_type text not null,
          actor_role text,
          from_status text,
          to_status text,
          message text,
          created_at text not null
        );

        create table if not exists cr_handoffs (
          cr_id text not null references change_requests(cr_id) on delete cascade,
          job_id text not null references handoff_jobs(job_id) on delete cascade,
          kind text not null check (kind in ('revision', 'implementation')),
          created_at text not null,
          primary key (cr_id, job_id)
        );

        create index if not exists idx_handoff_jobs_status_role on handoff_jobs(status, target_role);
        create index if not exists idx_handoff_dependencies_job on handoff_dependencies(job_id);
        create index if not exists idx_handoff_dependencies_dep on handoff_dependencies(depends_on_job_id);
        create index if not exists idx_handoff_events_job on handoff_events(job_id);
        create index if not exists idx_cr_status_reviewer on change_requests(status, reviewer_role);
        create index if not exists idx_cr_handoffs_cr on cr_handoffs(cr_id);
        """


def execute_sql_script(con: sqlite3.Connection, script: str) -> None:
    statement_lines: list[str] = []
    for line in script.splitlines():
        statement_lines.append(line)
        statement = "\n".join(statement_lines).strip()
        if statement and sqlite3.complete_statement(statement):
            con.execute(statement)
            statement_lines = []
    if "\n".join(statement_lines).strip():
        raise MigrationError("incomplete schema migration SQL")


def migration_v1_initial_schema(con: sqlite3.Connection) -> None:
    execute_sql_script(con, SCHEMA_V1_SQL)
    columns = {row["name"] for row in con.execute("PRAGMA table_info(handoff_controls)").fetchall()}
    if "work_until" not in columns:
        con.execute("alter table handoff_controls add column work_until text")


def migration_v2_handoff_cancel_permission(con: sqlite3.Connection) -> None:
    con.execute(
        """
        insert into roles(role_id, display_name, created_at, updated_at)
        values ('sm', 'System Manager', ?, ?)
        on conflict(role_id) do nothing
        """,
        (utc_now(), utc_now()),
    )
    con.execute(
        """
        insert into role_permissions(role_id, permission)
        values ('sm', 'handoff.cancel')
        on conflict(role_id, permission) do nothing
        """
    )


def migration_v3_named_gates(con: sqlite3.Connection) -> None:
    execute_sql_script(
        con,
        """
        create table if not exists workflow_gates (
          gate_name text primary key,
          status text not null check (status in ('pending', 'released', 'cancelled')),
          created_by_role text not null references roles(role_id),
          created_at text not null,
          resolved_at text,
          resolution_evidence text
        );

        create table if not exists gate_owners (
          gate_name text not null references workflow_gates(gate_name) on delete cascade,
          role_id text not null references roles(role_id),
          primary key (gate_name, role_id)
        );

        create table if not exists handoff_gate_dependencies (
          job_id text not null references handoff_jobs(job_id) on delete cascade,
          gate_name text not null references workflow_gates(gate_name),
          primary key (job_id, gate_name)
        );

        create table if not exists gate_events (
          id integer primary key autoincrement,
          gate_name text not null references workflow_gates(gate_name) on delete cascade,
          event_type text not null,
          actor_role text,
          from_status text,
          to_status text,
          message text,
          created_at text not null
        );

        create index if not exists idx_handoff_gate_dependencies_job
          on handoff_gate_dependencies(job_id);
        create index if not exists idx_handoff_gate_dependencies_gate
          on handoff_gate_dependencies(gate_name);
        create index if not exists idx_gate_events_gate
          on gate_events(gate_name);
        """,
    )
    con.execute(
        """
        insert into roles(role_id, display_name, created_at, updated_at)
        values ('sm', 'System Manager', ?, ?)
        on conflict(role_id) do nothing
        """,
        (utc_now(), utc_now()),
    )
    con.execute(
        """
        insert into role_permissions(role_id, permission)
        values ('sm', 'gate.manage')
        on conflict(role_id, permission) do nothing
        """
    )


def migration_v4_waiter_leases(con: sqlite3.Connection) -> None:
    execute_sql_script(
        con,
        """
        create table if not exists waiter_leases (
          waiter_id text primary key,
          wait_kind text not null check (wait_kind in ('handoff', 'cr_review')),
          role_id text not null references roles(role_id),
          started_at text not null,
          heartbeat_at text not null,
          lease_expires_at text not null
        );

        create index if not exists idx_waiter_leases_expiry
          on waiter_leases(lease_expires_at);
        """,
    )


def migration_v5_database_metadata(con: sqlite3.Connection) -> None:
    execute_sql_script(
        con,
        """
        create table if not exists database_metadata (
          key text primary key,
          value text not null,
          updated_at text not null
        );
        """,
    )


def migration_v6_workspace_provenance(con: sqlite3.Connection) -> None:
    execute_sql_script(
        con,
        """
        create table if not exists workspace_events (
          id integer primary key autoincrement,
          entity_type text not null check (entity_type in ('project', 'handoff', 'cr')),
          entity_id text,
          operation text not null,
          policy text not null check (policy in ('warn', 'strict')),
          outcome text not null check (outcome in ('accepted', 'warning', 'override')),
          head_commit text,
          baseline_commit text,
          branch text,
          dirty integer not null default 0,
          actor_role text,
          message text,
          created_at text not null
        );

        create index if not exists idx_workspace_events_entity
          on workspace_events(entity_type, entity_id, operation, id);
        """,
    )
    con.execute(
        """
        insert into roles(role_id, display_name, created_at, updated_at)
        values ('sm', 'System Manager', ?, ?)
        on conflict(role_id) do nothing
        """,
        (utc_now(), utc_now()),
    )
    con.execute(
        """
        insert into role_permissions(role_id, permission)
        values ('sm', 'workspace.override')
        on conflict(role_id, permission) do nothing
        """
    )


def migration_v7_handoff_failures(con: sqlite3.Connection) -> None:
    con.execute("PRAGMA legacy_alter_table = ON")
    try:
        con.execute("alter table handoff_jobs rename to handoff_jobs_v6")
        execute_sql_script(
            con,
            """
            create table handoff_jobs (
              job_id text primary key,
              title text not null,
              status text not null check (status in ('blocked', 'open', 'in_progress', 'failed', 'finished', 'cancelled')),
              target_role text not null references roles(role_id),
              source_ref text,
              objective text not null,
              exit_criteria text not null,
              created_at text not null,
              claimed_by text,
              started_at text,
              finished_at text,
              closure_evidence text,
              related_commit text
            );

            insert into handoff_jobs(
              job_id, title, status, target_role, source_ref, objective, exit_criteria,
              created_at, claimed_by, started_at, finished_at, closure_evidence, related_commit
            )
            select
              job_id, title, status, target_role, source_ref, objective, exit_criteria,
              created_at, claimed_by, started_at, finished_at, closure_evidence, related_commit
            from handoff_jobs_v6;

            drop table handoff_jobs_v6;

            create index if not exists idx_handoff_jobs_status_role on handoff_jobs(status, target_role);

            create table if not exists handoff_failure_reviews (
              id integer primary key autoincrement,
              job_id text not null references handoff_jobs(job_id) on delete cascade,
              cr_id text not null unique references change_requests(cr_id) on delete cascade,
              failed_by_role text not null references roles(role_id),
              reason text not null,
              evidence text,
              failed_at text not null,
              resolution text check (resolution in ('retry', 'cancelled')),
              resolved_by_role text references roles(role_id),
              resolved_at text,
              resolution_message text
            );

            create index if not exists idx_handoff_failure_reviews_job
              on handoff_failure_reviews(job_id, id);
            """,
        )
    finally:
        con.execute("PRAGMA legacy_alter_table = OFF")

    # Before this permission existed, every active role could register work.
    # Preserve that effective access for upgraded projects.
    con.execute(
        """
        insert into role_permissions(role_id, permission)
        select role_id, 'handoff.register'
        from roles
        where active = 1
        on conflict(role_id, permission) do nothing
        """
    )


def migration_v8_cr_body_integrity(con: sqlite3.Connection) -> None:
    columns = {
        row["name"] for row in con.execute("PRAGMA table_info(change_requests)").fetchall()
    }
    if "submitted_body_hash" not in columns:
        con.execute("alter table change_requests add column submitted_body_hash text")
    if "approved_body_hash" not in columns:
        con.execute("alter table change_requests add column approved_body_hash text")


def migration_v9_plan_revision_controls(con: sqlite3.Connection) -> None:
    con.execute("PRAGMA legacy_alter_table = ON")
    try:
        con.execute("alter table handoff_jobs rename to handoff_jobs_v8")
        execute_sql_script(
            con,
            """
            create table handoff_jobs (
              job_id text primary key,
              title text not null,
              status text not null check (status in ('blocked', 'open', 'in_progress', 'cancel_requested', 'failed', 'finished', 'cancelled')),
              target_role text not null references roles(role_id),
              source_ref text,
              objective text not null,
              exit_criteria text not null,
              created_at text not null,
              claimed_by text,
              started_at text,
              finished_at text,
              closure_evidence text,
              related_commit text
            );

            insert into handoff_jobs(
              job_id, title, status, target_role, source_ref, objective, exit_criteria,
              created_at, claimed_by, started_at, finished_at, closure_evidence, related_commit
            )
            select
              job_id, title, status, target_role, source_ref, objective, exit_criteria,
              created_at, claimed_by, started_at, finished_at, closure_evidence, related_commit
            from handoff_jobs_v8;

            drop table handoff_jobs_v8;
            create index if not exists idx_handoff_jobs_status_role on handoff_jobs(status, target_role);
            """,
        )

        con.execute("alter table change_requests rename to change_requests_v8")
        execute_sql_script(
            con,
            """
            create table change_requests (
              cr_id text primary key,
              title text not null,
              status text not null check (status in ('draft', 'submitted', 'revision_requested', 'approved', 'rejected', 'implemented', 'superseded', 'cancelled')),
              author_role text not null references roles(role_id),
              reviewer_role text not null references roles(role_id),
              file_path text not null unique,
              created_at text not null,
              updated_at text not null,
              submitted_at text,
              approved_at text,
              rejected_at text,
              implemented_at text,
              revision_count integer not null default 0,
              active_revision_job_id text references handoff_jobs(job_id),
              submitted_body_hash text,
              approved_body_hash text,
              superseded_by_cr_id text references change_requests(cr_id),
              superseded_by_ref text
            );

            insert into change_requests(
              cr_id, title, status, author_role, reviewer_role, file_path,
              created_at, updated_at, submitted_at, approved_at, rejected_at,
              implemented_at, revision_count, active_revision_job_id,
              submitted_body_hash, approved_body_hash
            )
            select
              cr_id, title, status, author_role, reviewer_role, file_path,
              created_at, updated_at, submitted_at, approved_at, rejected_at,
              implemented_at, revision_count, active_revision_job_id,
              submitted_body_hash, approved_body_hash
            from change_requests_v8;

            drop table change_requests_v8;
            create index if not exists idx_cr_status_reviewer on change_requests(status, reviewer_role);
            """,
        )

        con.execute("alter table waiter_leases rename to waiter_leases_v8")
        execute_sql_script(
            con,
            """
            create table waiter_leases (
              waiter_id text primary key,
              wait_kind text not null check (wait_kind in ('handoff', 'cr_review', 'watch')),
              role_id text not null references roles(role_id),
              started_at text not null,
              heartbeat_at text not null,
              lease_expires_at text not null
            );

            insert into waiter_leases(
              waiter_id, wait_kind, role_id, started_at, heartbeat_at, lease_expires_at
            )
            select waiter_id, wait_kind, role_id, started_at, heartbeat_at, lease_expires_at
            from waiter_leases_v8;

            drop table waiter_leases_v8;
            create index if not exists idx_waiter_leases_expiry on waiter_leases(lease_expires_at);
            """,
        )
    finally:
        con.execute("PRAGMA legacy_alter_table = OFF")


def migration_v10_opt_in_thread_notifications(con: sqlite3.Connection) -> None:
    execute_sql_script(
        con,
        """
        create table if not exists agent_sessions (
          session_id text primary key,
          agent_id text not null,
          role_id text not null references roles(role_id),
          host text not null,
          thread_id text not null,
          model text not null,
          status text not null check (status in ('active', 'inactive')),
          created_at text not null,
          updated_at text not null,
          ended_at text,
          end_reason text,
          unique(host, thread_id)
        );

        create unique index if not exists idx_agent_sessions_active_agent
          on agent_sessions(agent_id) where status = 'active';
        create index if not exists idx_agent_sessions_role_status
          on agent_sessions(role_id, status, updated_at);

        create table if not exists handoff_notifications (
          id integer primary key autoincrement,
          job_id text not null references handoff_jobs(job_id) on delete cascade,
          sender_session_id text not null references agent_sessions(session_id),
          recipient_session_id text not null references agent_sessions(session_id),
          sender_agent_id text not null,
          sender_model text not null,
          recipient_agent_id text not null,
          recipient_thread_id text not null,
          recipient_model text not null,
          transport text not null,
          delivery_status text not null check (delivery_status in ('sent', 'failed')),
          message_ref text,
          detail text,
          created_at text not null
        );

        create index if not exists idx_handoff_notifications_job
          on handoff_notifications(job_id, id);
        create unique index if not exists idx_handoff_notifications_sent_job
          on handoff_notifications(job_id) where delivery_status = 'sent';
        create index if not exists idx_handoff_notifications_recipient
          on handoff_notifications(recipient_agent_id, created_at);
        """,
    )


def migration_v11_workstream_routing(con: sqlite3.Connection) -> None:
    handoff_columns = {
        row["name"] for row in con.execute("PRAGMA table_info(handoff_jobs)").fetchall()
    }
    if "workstream" not in handoff_columns:
        con.execute("alter table handoff_jobs add column workstream text")
    cr_columns = {
        row["name"] for row in con.execute("PRAGMA table_info(change_requests)").fetchall()
    }
    for column in ("reviewer_workstream", "review_claimed_by", "review_started_at"):
        if column not in cr_columns:
            con.execute(f"alter table change_requests add column {column} text")
    execute_sql_script(
        con,
        """
        create table if not exists agent_workstreams (
          agent_id text not null,
          role_id text not null references roles(role_id) on delete cascade,
          workstream text not null,
          created_at text not null,
          primary key (agent_id, role_id, workstream)
        );

        create index if not exists idx_agent_workstreams_route
          on agent_workstreams(role_id, workstream, agent_id);
        create index if not exists idx_handoff_jobs_workstream
          on handoff_jobs(status, target_role, workstream);
        create index if not exists idx_cr_reviewer_workstream
          on change_requests(status, reviewer_role, reviewer_workstream);
        create index if not exists idx_cr_review_claim
          on change_requests(review_claimed_by, status);
        """,
    )


def migration_v12_retry_and_replacement_tracking(con: sqlite3.Connection) -> None:
    handoff_columns = {
        row["name"] for row in con.execute("PRAGMA table_info(handoff_jobs)").fetchall()
    }
    if "attempt" not in handoff_columns:
        con.execute("alter table handoff_jobs add column attempt integer not null default 1")
    notification_columns = {
        row["name"] for row in con.execute("PRAGMA table_info(handoff_notifications)").fetchall()
    }
    if "attempt" not in notification_columns:
        con.execute(
            "alter table handoff_notifications add column attempt integer not null default 1"
        )
    execute_sql_script(
        con,
        """
        drop index if exists idx_handoff_notifications_sent_job;
        create unique index if not exists idx_handoff_notifications_sent_attempt
          on handoff_notifications(job_id, attempt) where delivery_status = 'sent';

        create table if not exists cr_handoff_supersessions (
          cr_id text not null references change_requests(cr_id) on delete cascade,
          retired_job_id text not null references handoff_jobs(job_id),
          replacement_job_id text not null references handoff_jobs(job_id),
          actor_role text not null references roles(role_id),
          reason text not null,
          created_at text not null,
          primary key (cr_id, retired_job_id),
          check (retired_job_id != replacement_job_id)
        );

        create index if not exists idx_cr_handoff_supersessions_replacement
          on cr_handoff_supersessions(cr_id, replacement_job_id);
        """,
    )


def migration_v13_completion_evidence(con: sqlite3.Connection) -> None:
    handoff_columns = {
        row["name"] for row in con.execute("PRAGMA table_info(handoff_jobs)").fetchall()
    }
    additions = {
        "completion_outcome": (
            "text not null default 'unspecified' "
            "check (completion_outcome in "
            "('unspecified', 'pass', 'fail', 'conditional', 'inconclusive'))"
        ),
        "completion_blocking": (
            "integer not null default 0 check (completion_blocking in (0, 1))"
        ),
        "outcome_cr_id": "text references change_requests(cr_id)",
        "related_commit_resolution": (
            "text not null default 'not_provided' "
            "check (related_commit_resolution in "
            "('not_provided', 'resolved', 'unresolved', 'legacy_unchecked'))"
        ),
        "related_commit_resolution_reason": "text",
    }
    for column, definition in additions.items():
        if column not in handoff_columns:
            con.execute(f"alter table handoff_jobs add column {column} {definition}")
    con.execute(
        """
        update handoff_jobs
        set related_commit_resolution = 'legacy_unchecked'
        where related_commit is not null and trim(related_commit) != ''
          and related_commit_resolution = 'not_provided'
        """
    )
    execute_sql_script(
        con,
        """
        create table if not exists handoff_evidence_corrections (
          id integer primary key autoincrement,
          job_id text not null references handoff_jobs(job_id) on delete cascade,
          previous_commit text,
          corrected_commit text not null,
          commit_resolution text not null
            check (commit_resolution in ('resolved', 'unresolved')),
          reason text not null,
          actor_role text not null references roles(role_id),
          actor_id text not null,
          created_at text not null
        );

        create index if not exists idx_handoff_evidence_corrections_job
          on handoff_evidence_corrections(job_id, id);
        """,
    )
    for role_id in ("sm", "planning"):
        con.execute(
            """
            insert into role_permissions(role_id, permission)
            values (?, 'handoff.evidence_correct')
            on conflict(role_id, permission) do nothing
            """,
            (role_id,),
        )


def migration_v14_notification_recovery(con: sqlite3.Connection) -> None:
    notification_columns = {
        row["name"] for row in con.execute("PRAGMA table_info(handoff_notifications)").fetchall()
    }
    if "delivery_attempt" not in notification_columns:
        con.execute(
            "alter table handoff_notifications "
            "add column delivery_attempt integer not null default 1"
        )
    if "retry_of_notification_id" not in notification_columns:
        con.execute(
            "alter table handoff_notifications add column retry_of_notification_id integer"
        )
    if "recovery_reason" not in notification_columns:
        con.execute("alter table handoff_notifications add column recovery_reason text")

    rows = con.execute(
        "select id, job_id, attempt from handoff_notifications "
        "order by job_id, attempt, id"
    ).fetchall()
    counters: dict[tuple[str, int], int] = {}
    for row in rows:
        key = (str(row["job_id"]), int(row["attempt"]))
        counters[key] = counters.get(key, 0) + 1
        con.execute(
            "update handoff_notifications set delivery_attempt = ? where id = ?",
            (counters[key], row["id"]),
        )

    execute_sql_script(
        con,
        """
        drop index if exists idx_handoff_notifications_sent_attempt;
        create unique index if not exists idx_handoff_notifications_delivery_attempt
          on handoff_notifications(job_id, attempt, delivery_attempt);
        create unique index if not exists idx_handoff_notifications_initial_sent
          on handoff_notifications(job_id, attempt)
          where delivery_status = 'sent' and retry_of_notification_id is null;
        create unique index if not exists idx_handoff_notifications_recovery
          on handoff_notifications(job_id, attempt)
          where retry_of_notification_id is not null;
        """,
    )


MIGRATIONS = (
    (1, "initial_schema", migration_v1_initial_schema),
    (2, "handoff_cancel_permission", migration_v2_handoff_cancel_permission),
    (3, "named_gates", migration_v3_named_gates),
    (4, "waiter_leases", migration_v4_waiter_leases),
    (5, "database_metadata", migration_v5_database_metadata),
    (6, "workspace_provenance", migration_v6_workspace_provenance),
    (7, "handoff_failures", migration_v7_handoff_failures),
    (8, "cr_body_integrity", migration_v8_cr_body_integrity),
    (9, "plan_revision_controls", migration_v9_plan_revision_controls),
    (10, "opt_in_thread_notifications", migration_v10_opt_in_thread_notifications),
    (11, "workstream_routing", migration_v11_workstream_routing),
    (12, "retry_and_replacement_tracking", migration_v12_retry_and_replacement_tracking),
    (13, "completion_evidence", migration_v13_completion_evidence),
    (14, "notification_recovery", migration_v14_notification_recovery),
)


def migration_table_exists(con: sqlite3.Connection) -> bool:
    return bool(
        con.execute(
            "select 1 from sqlite_master where type = 'table' and name = 'schema_migrations'"
        ).fetchone()
    )


def current_schema_version(con: sqlite3.Connection) -> int:
    if not migration_table_exists(con):
        return 0
    row = con.execute("select coalesce(max(version), 0) as version from schema_migrations").fetchone()
    return int(row["version"])


def validate_database(con: sqlite3.Connection) -> None:
    quick_check = con.execute("PRAGMA quick_check").fetchone()
    if not quick_check or quick_check[0] != "ok":
        detail = quick_check[0] if quick_check else "no result"
        raise MigrationError(f"database integrity check failed: {detail}")
    foreign_key_error = con.execute("PRAGMA foreign_key_check").fetchone()
    if foreign_key_error:
        raise MigrationError(
            "database foreign key check failed: "
            f"table={foreign_key_error[0]} rowid={foreign_key_error[1]}"
        )


def validate_migration_definitions() -> None:
    defined_versions = [version for version, _, _ in MIGRATIONS]
    if defined_versions != list(range(1, LATEST_SCHEMA_VERSION + 1)):
        raise MigrationError(
            "invalid Baton migration definitions: "
            f"expected 1..{LATEST_SCHEMA_VERSION}, got {defined_versions}"
        )


def applied_migration_versions(con: sqlite3.Connection) -> set[int]:
    applied_rows = con.execute(
        "select version, name from schema_migrations order by version"
    ).fetchall()
    applied_versions = {int(row["version"]) for row in applied_rows}
    known_migrations = {version: name for version, name, _ in MIGRATIONS}
    unknown_versions = sorted(applied_versions - set(known_migrations))
    if unknown_versions:
        raise MigrationError(
            "database schema is newer than this Baton version: "
            f"unknown migration versions {unknown_versions}"
        )
    mismatched_names = [
        f"{row['version']}:{row['name']}"
        for row in applied_rows
        if row["name"] != known_migrations[int(row["version"])]
    ]
    if mismatched_names:
        raise MigrationError(
            "database migration history does not match this Baton version: "
            f"{mismatched_names}"
        )
    return applied_versions


def check_schema(con: sqlite3.Connection) -> int:
    validate_migration_definitions()
    current = current_schema_version(con)
    if current != LATEST_SCHEMA_VERSION:
        raise MigrationError(
            f"database migration required: schema={current} latest={LATEST_SCHEMA_VERSION}"
        )
    applied_migration_versions(con)
    validate_database(con)
    return current


def database_schema_compatibility(con: sqlite3.Connection) -> dict[str, object]:
    schema = current_schema_version(con)
    compatible = True
    error = ""
    try:
        validate_migration_definitions()
        if schema > LATEST_SCHEMA_VERSION:
            raise MigrationError(
                f"database schema is newer than this Baton version: "
                f"schema={schema} latest={LATEST_SCHEMA_VERSION}"
            )
        if migration_table_exists(con):
            applied_migration_versions(con)
        validate_database(con)
    except MigrationError as exc:
        compatible = False
        error = str(exc)
    return {
        "schema_version": schema,
        "supported_schema_version": LATEST_SCHEMA_VERSION,
        "database_schema_current": compatible and schema == LATEST_SCHEMA_VERSION,
        "migration_required": compatible and schema < LATEST_SCHEMA_VERSION,
        "cli_schema_compatible": compatible,
        "workflow_commands_ready": compatible and schema == LATEST_SCHEMA_VERSION,
        "compatibility_error": error,
    }


def migrate_schema(
    con: sqlite3.Connection,
    *,
    transaction_started: bool = False,
) -> tuple[int, int, list[str]]:
    validate_migration_definitions()
    restore_foreign_keys = False
    if not transaction_started:
        enable_wal(con)
        con.execute("PRAGMA foreign_keys = OFF")
        restore_foreign_keys = True
        con.execute("BEGIN IMMEDIATE")
    try:
        existing_tables = {
            str(row[0])
            for row in con.execute(
                "select name from sqlite_master where type = 'table' and name not like 'sqlite_%'"
            ).fetchall()
        }
        new_database = not existing_tables
        previous_version = current_schema_version(con)
        if previous_version > LATEST_SCHEMA_VERSION:
            raise MigrationError(
                "database schema is newer than this Baton version: "
                f"schema={previous_version} latest={LATEST_SCHEMA_VERSION}"
            )
        con.execute(
            """
            create table if not exists schema_migrations (
              version integer primary key,
              name text not null,
              applied_at text not null
            )
            """
        )
        applied_versions = applied_migration_versions(con)

        applied_names: list[str] = []
        for version, name, migration in MIGRATIONS:
            if version in applied_versions:
                continue
            migration(con)
            con.execute(
                "insert into schema_migrations(version, name, applied_at) values (?, ?, ?)",
                (version, name, utc_now()),
            )
            applied_names.append(f"{version}:{name}")

        seed_roles(con)
        if previous_version == 0:
            seed_role_permissions(con)
        if applied_names:
            now = utc_now()
            created_with = BATON_VERSION if new_database else "unknown"
            con.execute(
                """
                insert into database_metadata(key, value, updated_at)
                values ('created_with_baton_version', ?, ?)
                on conflict(key) do nothing
                """,
                (created_with, now),
            )
            for key, value in (
                ("last_migrated_with_baton_version", BATON_VERSION),
                ("last_migrated_at", now),
            ):
                con.execute(
                    """
                    insert into database_metadata(key, value, updated_at)
                    values (?, ?, ?)
                    on conflict(key) do update set
                      value = excluded.value,
                      updated_at = excluded.updated_at
                    """,
                    (key, value, now),
                )
        validate_database(con)
        con.commit()
    except BaseException as exc:
        con.rollback()
        if isinstance(exc, sqlite3.DatabaseError):
            raise MigrationError(f"database migration failed: {exc}") from exc
        raise
    finally:
        if restore_foreign_keys:
            con.execute("PRAGMA foreign_keys = ON")
    return previous_version, LATEST_SCHEMA_VERSION, applied_names


def project_marker_path(root: Path) -> Path:
    return root / ".baton" / PROJECT_MARKER_NAME


def read_project_marker(root: Path) -> dict[str, object]:
    marker = project_marker_path(root)
    try:
        payload = json.loads(marker.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise MigrationError(f"invalid Baton project marker: {marker}: {exc}") from exc
    if not isinstance(payload, dict):
        raise MigrationError(f"invalid Baton project marker object: {marker}")
    if payload.get("format_version") != PROJECT_FORMAT_VERSION:
        raise MigrationError(
            f"unsupported Baton project marker version: {payload.get('format_version')} "
            f"expected={PROJECT_FORMAT_VERSION} marker={marker}"
        )
    if payload.get("database") != "baton.sqlite3":
        raise MigrationError(f"unsupported Baton project database setting in marker: {marker}")
    return payload


def find_project_root(start: Path | None = None) -> Path | None:
    current = (start or Path.cwd()).expanduser().resolve()
    for candidate in (current, *current.parents):
        marker = project_marker_path(candidate)
        if marker.is_file():
            read_project_marker(candidate)
            return candidate
    return None


def find_legacy_project_root(start: Path | None = None) -> Path | None:
    current = (start or Path.cwd()).expanduser().resolve()
    for candidate in (current, *current.parents):
        if (candidate / ".baton" / "baton.sqlite3").is_file():
            return candidate
    return None


def ensure_control_ignore(root: Path) -> Path:
    control_dir = root / ".baton"
    control_dir.mkdir(parents=True, exist_ok=True)
    ignore_path = control_dir / ".gitignore"
    try:
        with ignore_path.open("x", encoding="utf-8") as output:
            output.write("*\n")
            output.flush()
            os.fsync(output.fileno())
    except FileExistsError:
        pass
    return ignore_path


def write_project_marker(root: Path) -> Path:
    ensure_control_ignore(root)
    marker = project_marker_path(root)
    if marker.exists():
        read_project_marker(root)
        return marker
    marker.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "format_version": PROJECT_FORMAT_VERSION,
        "database": "baton.sqlite3",
        "created_with_baton_version": BATON_VERSION,
    }
    temporary = marker.with_name(f".{marker.name}.baton-{uuid.uuid4().hex}.tmp")
    try:
        with temporary.open("x", encoding="utf-8") as output:
            json.dump(payload, output, indent=2, sort_keys=True)
            output.write("\n")
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, marker)
    finally:
        if temporary.exists():
            temporary.unlink()
    return marker


def ensure_marker_for_database(database_path: Path) -> Path | None:
    resolved = database_path.expanduser().resolve()
    if resolved.name != "baton.sqlite3" or resolved.parent.name != ".baton":
        return None
    return write_project_marker(resolved.parent.parent)


def validate_marker_for_database(database_path: Path) -> None:
    resolved = database_path.expanduser().resolve()
    if resolved.name != "baton.sqlite3" or resolved.parent.name != ".baton":
        return
    marker = project_marker_path(resolved.parent.parent)
    if marker.exists():
        read_project_marker(resolved.parent.parent)


def project_root_path(explicit_root: str) -> Path:
    if explicit_root:
        root = Path(explicit_root).expanduser().resolve()
        if not root.is_dir():
            raise MigrationError(f"project root is not a directory: {root}")
        return root
    return find_project_root() or find_legacy_project_root() or Path.cwd().resolve()


def project_database_paths(root: Path) -> tuple[Path, tuple[Path, ...]]:
    target = (root / ".baton" / "baton.sqlite3").resolve()
    candidates = (
        target,
        (root / "tools" / "baton" / ".baton" / "baton.sqlite3").resolve(),
        (root / "tools" / "agents-baton" / ".baton" / "baton.sqlite3").resolve(),
    )
    return target, candidates


def default_database_path() -> str:
    return str(project_root_path("") / ".baton" / "baton.sqlite3")


def connection_project_root(con: sqlite3.Connection) -> Path:
    row = con.execute("PRAGMA database_list").fetchone()
    database_path = Path(row[2]).resolve() if row and row[2] else None
    if database_path and database_path.parent.name == ".baton":
        return database_path.parent.parent
    discovered_root = find_project_root()
    if discovered_root:
        expected_database = (discovered_root / ".baton" / "baton.sqlite3").resolve()
        if database_path == expected_database:
            return discovered_root
    raise MigrationError(
        "relative project file path cannot be resolved for an external --db; "
        "use an absolute CR path or the project-local .baton/baton.sqlite3"
    )


def database_project_root(database_value: str) -> Path | None:
    database = Path(database_value).expanduser().resolve()
    if database.name == "baton.sqlite3" and database.parent.name == ".baton":
        return database.parent.parent
    discovered = find_project_root()
    if discovered and database == (discovered / ".baton" / "baton.sqlite3").resolve():
        return discovered
    return None


def decode_config_string(value: str, *, key: str, path: Path) -> str:
    text = value.strip()
    if len(text) >= 2 and text[0] == text[-1] == '"':
        try:
            decoded = json.loads(text)
        except json.JSONDecodeError as exc:
            raise MigrationError(f"invalid {key} string in {path}: {exc}") from exc
        if not isinstance(decoded, str):
            raise MigrationError(f"invalid {key} value in {path}: expected a string")
        return decoded.strip()
    if len(text) >= 2 and text[0] == text[-1] == "'":
        return text[1:-1].strip()
    return text


def read_workspace_config(database_value: str) -> WorkspaceConfig:
    root = database_project_root(database_value)
    if root is None:
        fallback = Path.cwd().resolve()
        return WorkspaceConfig(fallback, fallback / PROJECT_CONFIG_NAME, "", "off", "")
    path = root / PROJECT_CONFIG_NAME
    if not path.is_file():
        return WorkspaceConfig(root, path, "", "off", "")
    parser = configparser.ConfigParser(
        interpolation=None,
        inline_comment_prefixes=("#",),
    )
    try:
        with path.open(encoding="utf-8") as source:
            parser.read_file(source)
    except (OSError, configparser.Error) as exc:
        raise MigrationError(f"invalid Baton project config: {path}: {exc}") from exc

    provider = ""
    configured_policy = ""
    required_version = ""
    if parser.has_option("vcs", "provider"):
        provider = decode_config_string(
            parser.get("vcs", "provider"), key="vcs.provider", path=path
        ).lower()
    if parser.has_option("vcs", "policy"):
        configured_policy = decode_config_string(
            parser.get("vcs", "policy"), key="vcs.policy", path=path
        ).lower()
    if parser.has_option("baton", "required_version"):
        required_version = decode_config_string(
            parser.get("baton", "required_version"),
            key="baton.required_version",
            path=path,
        )

    if provider not in {"", "git"}:
        raise MigrationError(f"unsupported vcs.provider in {path}: {provider}")
    policy = configured_policy or ("warn" if provider == "git" else "off")
    if policy not in {"off", "warn", "strict"}:
        raise MigrationError(f"unsupported vcs.policy in {path}: {policy}")
    if policy != "off" and provider != "git":
        raise MigrationError(f"vcs.policy={policy} requires vcs.provider=git in {path}")
    if required_version:
        version_requirement_matches(BATON_VERSION, required_version)
    return WorkspaceConfig(root, path, provider, policy, required_version)


def parsed_baton_version(value: str) -> tuple[int, int, int, int, int]:
    text = value.strip().lower().removeprefix("v").split("+", 1)[0]
    match = re.fullmatch(
        r"(\d+)(?:\.(\d+))?(?:\.(\d+))?(?:(?:\.)?(dev|a|b|rc)(\d+))?",
        text,
    )
    if not match:
        raise MigrationError(f"unsupported Baton version value: {value}")
    major, minor, patch = (int(match.group(index) or 0) for index in (1, 2, 3))
    phase = match.group(4) or "final"
    phase_rank = {"dev": 0, "a": 1, "b": 2, "rc": 3, "final": 4}[phase]
    phase_number = int(match.group(5) or 0)
    return major, minor, patch, phase_rank, phase_number


def version_requirement_matches(version: str, requirement: str) -> bool:
    actual = parsed_baton_version(version)
    for raw_specifier in requirement.split(","):
        specifier = raw_specifier.strip()
        if not specifier:
            raise MigrationError(f"invalid empty Baton version specifier: {requirement}")
        match = re.fullmatch(r"(<=|>=|==|!=|<|>)?\s*(.+)", specifier)
        if not match:
            raise MigrationError(f"invalid Baton version specifier: {specifier}")
        operator = match.group(1) or "=="
        expected = parsed_baton_version(match.group(2))
        matches = {
            "==": actual == expected,
            "!=": actual != expected,
            "<": actual < expected,
            "<=": actual <= expected,
            ">": actual > expected,
            ">=": actual >= expected,
        }[operator]
        if not matches:
            return False
    return True


def run_git(root: Path, *arguments: str) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            ["git", "-C", str(root), *arguments],
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
        )
    except FileNotFoundError as exc:
        raise MigrationError("Git workspace inspection failed: git command not found") from exc
    except subprocess.TimeoutExpired as exc:
        raise MigrationError("Git workspace inspection timed out") from exc


def resolve_completion_commit(
    value: str,
    workspace_root_value: str,
    *,
    allow_unresolved: bool,
    unresolved_reason: str,
) -> tuple[str, str, str]:
    commit = value.strip()
    reason = unresolved_reason.strip()
    if not commit:
        if allow_unresolved:
            raise SystemExit("ERROR: --allow-unresolved-commit requires --commit")
        return "", "not_provided", ""
    if len(commit) > 255 or commit.startswith("-") or any(char.isspace() for char in commit):
        raise SystemExit(f"ERROR: invalid commit reference syntax: {commit!r}")

    root = (
        Path(workspace_root_value).expanduser().resolve()
        if workspace_root_value
        else Path.cwd().resolve()
    )
    repository_result = run_git(root, "rev-parse", "--show-toplevel")
    if repository_result.returncode != 0:
        detail = "workspace is not a Git repository"
    else:
        repository_root = Path(repository_result.stdout.strip()).resolve()
        resolved = run_git(
            repository_root,
            "rev-parse",
            "--verify",
            "--end-of-options",
            f"{commit}^{{commit}}",
        )
        if resolved.returncode == 0:
            canonical = resolved.stdout.strip().splitlines()[0]
            return canonical, "resolved", ""
        object_type = run_git(repository_root, "cat-file", "-t", commit)
        if object_type.returncode == 0:
            detail = f"object exists but is not commit-resolvable: type={object_type.stdout.strip()}"
        else:
            detail = "commit object does not exist in the local repository"

    if not allow_unresolved:
        raise SystemExit(
            f"ERROR: cannot resolve --commit {commit!r}: {detail}; "
            "use --allow-unresolved-commit with --unresolved-reason only for "
            "cross-repository or remote-only evidence"
        )
    if not reason:
        raise SystemExit("ERROR: --unresolved-reason is required with --allow-unresolved-commit")
    return commit, "unresolved", reason


def git_result_value(result: subprocess.CompletedProcess[str], operation: str) -> str:
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip() or f"exit={result.returncode}"
        raise MigrationError(f"Git workspace inspection failed during {operation}: {detail}")
    return result.stdout.strip()


def capture_git_snapshot(root: Path) -> WorkspaceSnapshot:
    repository_root = Path(
        git_result_value(run_git(root, "rev-parse", "--show-toplevel"), "repository discovery")
    ).resolve()
    head_commit = git_result_value(run_git(root, "rev-parse", "HEAD"), "HEAD resolution")
    branch_result = run_git(root, "symbolic-ref", "--quiet", "--short", "HEAD")
    if branch_result.returncode == 0:
        branch = branch_result.stdout.strip()
    elif branch_result.returncode == 1:
        branch = "DETACHED"
    else:
        git_result_value(branch_result, "branch resolution")
        branch = "DETACHED"
    status = git_result_value(
        run_git(root, "status", "--porcelain", "--untracked-files=normal"),
        "working tree status",
    )
    return WorkspaceSnapshot(repository_root, head_commit, branch, bool(status))


def git_commit_is_ancestor(root: Path, baseline: str, current: str) -> bool:
    result = run_git(root, "merge-base", "--is-ancestor", baseline, current)
    if result.returncode == 0:
        return True
    if result.returncode == 1:
        return False
    git_result_value(result, "commit ancestry check")
    return False


def latest_recorded_workspace_policy(database_value: str) -> str:
    path = Path(database_value)
    if not path.is_file():
        return ""
    try:
        with closing(connect_readonly(database_value)) as con:
            if "workspace_events" not in database_table_names(con):
                return ""
            row = con.execute(
                "select policy from workspace_events order by id desc limit 1"
            ).fetchone()
    except (MigrationError, sqlite3.Error):
        return ""
    return str(row["policy"]) if row else ""


def assess_workspace(
    database_value: str,
    baseline_commit: str = "",
    workspace_root_value: str = "",
) -> WorkspaceAssessment:
    config = read_workspace_config(database_value)
    issues: list[str] = []
    if (
        not config.path.is_file()
        and database_project_root(database_value) is not None
    ):
        previous_policy = latest_recorded_workspace_policy(database_value)
        if previous_policy in {"warn", "strict"}:
            config = WorkspaceConfig(
                config.root,
                config.path,
                "git",
                previous_policy,
                "",
            )
            issues.append(
                f"shared {PROJECT_CONFIG_NAME} is missing after Git workspace integration was enabled"
            )
    if config.required_version and not version_requirement_matches(BATON_VERSION, config.required_version):
        issues.append(
            f"Baton version {BATON_VERSION} does not satisfy {config.required_version}"
        )
    snapshot: WorkspaceSnapshot | None = None
    if config.provider == "git" and config.policy != "off":
        try:
            workspace_root = (
                Path(workspace_root_value).expanduser().resolve()
                if workspace_root_value
                else Path.cwd().resolve()
            )
            if not workspace_root.is_dir():
                raise MigrationError(f"workspace root is not a directory: {workspace_root}")
            snapshot = capture_git_snapshot(workspace_root)
            if baseline_commit and not git_commit_is_ancestor(
                snapshot.repository_root,
                baseline_commit,
                snapshot.head_commit,
            ):
                issues.append(
                    f"current HEAD {snapshot.head_commit} does not descend from baseline {baseline_commit}"
                )
        except MigrationError as exc:
            issues.append(str(exc))
    return WorkspaceAssessment(config, snapshot, baseline_commit, tuple(issues))


def latest_workspace_commit(database_value: str, job_id: str, operations: tuple[str, ...]) -> str:
    with closing(connect_readonly(database_value)) as con:
        check_schema(con)
        placeholders = ",".join("?" for _ in operations)
        row = con.execute(
            f"""
            select head_commit
            from workspace_events
            where entity_type = 'handoff'
              and entity_id = ?
              and operation in ({placeholders})
              and head_commit is not null
              and head_commit != ''
            order by id desc
            limit 1
            """,
            (job_id, *operations),
        ).fetchone()
    return str(row["head_commit"]) if row else ""


def record_workspace_event(
    con: sqlite3.Connection,
    assessment: WorkspaceAssessment,
    *,
    entity_type: str,
    entity_id: str,
    operation: str,
    actor_role: str,
    outcome: str,
    message: str,
) -> None:
    snapshot = assessment.snapshot
    con.execute(
        """
        insert into workspace_events(
          entity_type, entity_id, operation, policy, outcome, head_commit,
          baseline_commit, branch, dirty, actor_role, message, created_at
        )
        values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            entity_type,
            entity_id or None,
            operation,
            assessment.config.policy,
            outcome,
            snapshot.head_commit if snapshot else None,
            assessment.baseline_commit or None,
            snapshot.branch if snapshot else None,
            int(snapshot.dirty) if snapshot else 0,
            actor_role or None,
            message or None,
            utc_now(),
        ),
    )


def apply_workspace_policy(
    con: sqlite3.Connection,
    args: argparse.Namespace,
    assessment: WorkspaceAssessment,
    *,
    entity_type: str,
    entity_id: str,
    operation: str,
    actor_role: str,
) -> None:
    if assessment.config.policy == "off":
        return
    issues = "; ".join(assessment.issues)
    if assessment.issues and assessment.config.policy == "strict":
        if not getattr(args, "accept_workspace_change", False):
            if operation == "failed":
                print(
                    f"WARNING: workspace failed: {issues}; failure reporting was not blocked",
                    file=sys.stderr,
                )
                record_workspace_event(
                    con,
                    assessment,
                    entity_type=entity_type,
                    entity_id=entity_id,
                    operation=operation,
                    actor_role=actor_role,
                    outcome="warning",
                    message=f"{issues}; failure reporting was not blocked",
                )
                return
            raise MigrationError(
                f"workspace policy strict blocked {operation}: {issues}; "
                "use an authorized workspace override only for an intentional transition"
            )
        reason = (getattr(args, "workspace_reason", "") or "").strip()
        if not reason:
            raise MigrationError("--workspace-reason is required with --accept-workspace-change")
        authorized_by = (getattr(args, "workspace_authorized_by_role", "") or actor_role).strip()
        authorized_role = require_permission(con, authorized_by, "workspace.override")
        record_workspace_event(
            con,
            assessment,
            entity_type=entity_type,
            entity_id=entity_id,
            operation=operation,
            actor_role=authorized_role,
            outcome="override",
            message=f"{issues}; override_reason={reason}",
        )
        return
    outcome = "warning" if assessment.issues else "accepted"
    if assessment.issues:
        print(f"WARNING: workspace {operation}: {issues}", file=sys.stderr)
    record_workspace_event(
        con,
        assessment,
        entity_type=entity_type,
        entity_id=entity_id,
        operation=operation,
        actor_role=actor_role,
        outcome=outcome,
        message=issues,
    )


def project_file_path(con: sqlite3.Connection, value: str) -> Path:
    path = Path(value).expanduser()
    if path.is_absolute():
        return path
    return connection_project_root(con) / path


def resolve_migration_source(root: Path, source_value: str) -> tuple[Path, Path]:
    target, candidates = project_database_paths(root)
    if source_value:
        source = Path(source_value).expanduser()
        if not source.is_absolute():
            source = (Path.cwd() / source).resolve()
        else:
            source = source.resolve()
        if not source.is_file():
            raise MigrationError(f"source database does not exist: {source}")
        return source, target

    found = tuple(dict.fromkeys(path for path in candidates if path.is_file()))
    if not found:
        raise MigrationError(
            "no Baton database found in the project or legacy tools layout; "
            "rerun with --source-db PATH"
        )
    if len(found) > 1:
        paths = ", ".join(str(path) for path in found)
        raise MigrationError(
            f"multiple Baton databases found; choose the canonical database with --source-db: {paths}"
        )
    return found[0], target


def database_content_signature(con: sqlite3.Connection) -> str:
    digest = hashlib.sha256()
    for line in con.iterdump():
        encoded = line.encode("utf-8")
        digest.update(len(encoded).to_bytes(8, "big"))
        digest.update(encoded)
    return digest.hexdigest()


def database_path_content_signature(path: Path) -> str:
    with closing(connect_readonly(str(path))) as con:
        return database_content_signature(con)


def database_table_names(con: sqlite3.Connection) -> set[str]:
    return {
        str(row[0])
        for row in con.execute(
            "select name from sqlite_master where type = 'table' and name not like 'sqlite_%'"
        ).fetchall()
    }


def active_waiter_count(con: sqlite3.Connection) -> int:
    if "waiter_leases" not in database_table_names(con):
        return 0
    row = con.execute(
        "select count(*) from waiter_leases where lease_expires_at > ?",
        (utc_now(),),
    ).fetchone()
    return int(row[0])


def active_waiter_rows(con: sqlite3.Connection) -> list[sqlite3.Row]:
    if "waiter_leases" not in database_table_names(con):
        return []
    return con.execute(
        """
        select waiter_id, wait_kind, role_id, heartbeat_at, lease_expires_at
        from waiter_leases
        where lease_expires_at > ?
        order by role_id, wait_kind, waiter_id
        """,
        (utc_now(),),
    ).fetchall()


def in_progress_handoff_count(con: sqlite3.Connection) -> int:
    if "handoff_jobs" not in database_table_names(con):
        return 0
    return int(
        con.execute(
            "select count(*) from handoff_jobs where status in ('in_progress', 'cancel_requested')"
        ).fetchone()[0]
    )


def in_progress_handoff_rows(con: sqlite3.Connection) -> list[sqlite3.Row]:
    if "handoff_jobs" not in database_table_names(con):
        return []
    return con.execute(
        """
        select job_id, status, target_role, claimed_by
        from handoff_jobs
        where status in ('in_progress', 'cancel_requested')
        order by created_at, job_id
        """
    ).fetchall()


def active_cr_review_count(con: sqlite3.Connection) -> int:
    if "change_requests" not in database_table_names(con):
        return 0
    columns = {
        row["name"] for row in con.execute("PRAGMA table_info(change_requests)").fetchall()
    }
    if "review_claimed_by" not in columns:
        return 0
    return int(
        con.execute(
            """
            select count(*)
            from change_requests
            where status = 'submitted' and review_claimed_by is not null
            """
        ).fetchone()[0]
    )


def active_cr_review_rows(con: sqlite3.Connection) -> list[sqlite3.Row]:
    if "change_requests" not in database_table_names(con):
        return []
    columns = {
        row["name"] for row in con.execute("PRAGMA table_info(change_requests)").fetchall()
    }
    if "review_claimed_by" not in columns:
        return []
    reviewer_workstream = (
        "reviewer_workstream" if "reviewer_workstream" in columns else "null"
    )
    return con.execute(
        f"""
        select cr_id, reviewer_role, {reviewer_workstream} as reviewer_workstream,
               review_claimed_by
        from change_requests
        where status = 'submitted' and review_claimed_by is not null
        order by submitted_at, created_at, cr_id
        """
    ).fetchall()


def global_stop_is_active(con: sqlite3.Connection) -> bool:
    if "handoff_controls" not in database_table_names(con):
        return False
    row = con.execute(
        "select stopped from handoff_controls where scope = 'all'",
    ).fetchone()
    return bool(row and row[0])


def inspect_project_migration(root: Path, source: Path, target: Path) -> dict[str, object]:
    marker_exists = project_marker_path(root).is_file()
    if marker_exists:
        read_project_marker(root)
    if target.is_file() and source.resolve() != target.resolve():
        raise MigrationError(
            "target database already exists and differs from the selected source; "
            f"source={source} target={target}; Baton will not merge or overwrite databases"
        )
    try:
        with closing(connect_readonly(str(source))) as source_con:
            tables = database_table_names(source_con)
            baton_tables = {"roles", "handoff_jobs", "handoff_controls", "change_requests"}
            if not tables.intersection(baton_tables):
                raise MigrationError(f"source is not a recognized Baton database: {source}")
            validate_database(source_con)
            source_version = current_schema_version(source_con)
            if source_version > LATEST_SCHEMA_VERSION:
                raise MigrationError(
                    "database schema is newer than this Baton version: "
                    f"schema={source_version} latest={LATEST_SCHEMA_VERSION}"
                )
            if migration_table_exists(source_con):
                applied_migration_versions(source_con)
            waiters = active_waiter_count(source_con)
            in_progress = in_progress_handoff_count(source_con)
            active_reviews = active_cr_review_count(source_con)
            globally_stopped = global_stop_is_active(source_con)
            probe = sqlite3.connect(":memory:")
            probe.row_factory = sqlite3.Row
            probe.execute("PRAGMA foreign_keys = ON")
            try:
                source_con.backup(probe)
                signature = database_content_signature(probe)
                previous, current, pending = migrate_schema(probe)
                check_schema(probe)
            finally:
                probe.close()
    except sqlite3.DatabaseError as exc:
        raise MigrationError(f"database migration check failed: {exc}") from exc

    token_source = "\n".join(
        (
            str(root),
            str(source),
            str(target),
            signature,
            str(source_version),
            str(LATEST_SCHEMA_VERSION),
            ",".join(pending),
        )
    )
    token = hashlib.sha256(token_source.encode("utf-8")).hexdigest()[:24]
    return {
        "project_root": root,
        "source": source,
        "target": target,
        "source_schema": source_version,
        "target_schema": current,
        "pending": pending,
        "layout_move": source.resolve() != target.resolve(),
        "active_waiters": waiters,
        "in_progress_handoffs": in_progress,
        "active_cr_reviews": active_reviews,
        "global_stop": globally_stopped,
        "project_marker": marker_exists,
        "token": token,
        "signature": signature,
        "previous": previous,
    }


def print_project_migration_plan(plan: dict[str, object]) -> None:
    pending = ",".join(plan["pending"]) if plan["pending"] else "none"
    print("Migration check OK")
    print(f"project_root: {plan['project_root']}")
    print(f"source_db: {plan['source']}")
    print(f"target_db: {plan['target']}")
    print(f"source_schema: {plan['source_schema']}")
    print(f"target_schema: {plan['target_schema']}")
    print(f"pending_migrations: {pending}")
    print(f"layout_move: {'yes' if plan['layout_move'] else 'no'}")
    print(f"active_waiters: {plan['active_waiters']}")
    print(f"in_progress_handoffs: {plan['in_progress_handoffs']}")
    print(f"active_cr_reviews: {plan['active_cr_reviews']}")
    print(f"global_stop: {'yes' if plan['global_stop'] else 'no'}")
    print(f"project_marker: {'present' if plan['project_marker'] else 'missing'}")
    print(f"plan_token: {plan['token']}")


def backup_database(source: Path, backup_path: Path, expected_signature: str = "") -> None:
    backup_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with closing(connect_readonly(str(source))) as source_con:
            backup_con = sqlite3.connect(backup_path)
            try:
                source_con.backup(backup_con)
                backup_con.row_factory = sqlite3.Row
                validate_database(backup_con)
            finally:
                backup_con.close()
        with closing(connect_readonly(str(backup_path))) as backup_con:
            actual_signature = database_content_signature(backup_con)
        if expected_signature and actual_signature != expected_signature:
            backup_path.unlink()
            raise MigrationError("source database changed after migration check; rerun --check")
        backup_path.chmod(0o600)
    except sqlite3.DatabaseError as exc:
        if backup_path.exists():
            backup_path.unlink()
        raise MigrationError(f"database backup failed: {exc}") from exc


def command_project_migrate(args: argparse.Namespace) -> int:
    root = project_root_path(args.project_root)
    source, target = resolve_migration_source(root, args.source_db)
    plan = inspect_project_migration(root, source, target)
    if args.check:
        print_project_migration_plan(plan)
        return 0
    if args.plan_token != plan["token"]:
        raise MigrationError(
            "migration plan token is missing or stale; rerun --check with the same paths, "
            "then pass --plan-token TOKEN to --apply"
        )
    if plan["active_waiters"]:
        raise MigrationError(
            f"cannot migrate while {plan['active_waiters']} Baton waiter(s) are active; stop agents and check again"
        )
    if plan["in_progress_handoffs"]:
        raise MigrationError(
            f"cannot migrate while {plan['in_progress_handoffs']} handoff(s) are in progress; "
            "finish or cancel them and check again"
        )
    if plan["active_cr_reviews"]:
        raise MigrationError(
            f"cannot migrate while {plan['active_cr_reviews']} CR review(s) are claimed; "
            "decide or release them and check again"
        )
    if plan["layout_move"] and not plan["global_stop"]:
        raise MigrationError(
            "layout migration requires global maintenance stop; run 'baton stop --all', then rerun --check"
        )

    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup_path = root / ".baton" / "backups" / f"baton-before-{timestamp}-{uuid.uuid4().hex[:8]}.sqlite3"
    pending = list(plan["pending"])

    if source.resolve() == target.resolve() and not pending:
        marker = ensure_marker_for_database(target)
        print(
            f"Migration not required source={source} schema={plan['source_schema']} "
            f"marker={marker or 'not-applicable'}"
        )
        return 0

    backup_database(source, backup_path, str(plan["signature"]))
    applied: list[str]
    if source.resolve() == target.resolve():
        with closing(connect(str(target))) as con:
            previous, current, applied = migrate_schema(con)
    else:
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary_target = target.parent / f".{target.name}.migrating-{uuid.uuid4().hex}"
        redirect_path = source.parent / f".{source.name}.redirect-{uuid.uuid4().hex}"
        redirect_target = os.path.relpath(target, source.parent)
        try:
            os.symlink(redirect_target, redirect_path)
            backup_database(backup_path, temporary_target)
            with closing(connect(str(temporary_target))) as con:
                previous, current, applied = migrate_schema(con)
                check_schema(con)
            with closing(sqlite3.connect(temporary_target)) as con:
                con.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
                con.execute("PRAGMA journal_mode = DELETE").fetchone()
            if database_path_content_signature(source) != plan["signature"]:
                raise MigrationError("source database changed during project migration; target was not installed")
            os.replace(temporary_target, target)
            target.chmod(0o600)
            try:
                os.replace(redirect_path, source)
            except OSError:
                target.unlink()
                raise
            for sidecar in (Path(f"{source}-wal"), Path(f"{source}-shm")):
                if sidecar.exists():
                    sidecar.unlink()
        finally:
            for temporary_file in (
                temporary_target,
                Path(f"{temporary_target}-wal"),
                Path(f"{temporary_target}-shm"),
                redirect_path,
            ):
                if temporary_file.exists():
                    temporary_file.unlink()

    with closing(connect_readonly(str(target))) as con:
        check_schema(con)
    marker = ensure_marker_for_database(target)
    applied_text = ",".join(applied) if applied else "none"
    print(
        f"Project migration complete source={source} target={target} "
        f"schema={previous}->{current} applied={applied_text} backup={backup_path} "
        f"marker={marker or 'not-applicable'}"
    )
    if plan["layout_move"]:
        print(f"Legacy path redirected: {source} -> {redirect_target}")
    return 0


def command_project_info(args: argparse.Namespace) -> int:
    database = Path(args.db).expanduser().resolve()
    marker_root = None
    if database.name == "baton.sqlite3" and database.parent.name == ".baton":
        candidate_root = database.parent.parent
        if project_marker_path(candidate_root).is_file():
            read_project_marker(candidate_root)
            marker_root = candidate_root
    with closing(connect_readonly(str(database))) as con:
        compatibility = database_schema_compatibility(con)
        metadata = {}
        if "database_metadata" in database_table_names(con):
            metadata = {
                row["key"]: row["value"]
                for row in con.execute(
                    "select key, value from database_metadata order by key"
                ).fetchall()
            }
    workspace_config = read_workspace_config(str(database))
    payload = {
        "project_root": str(marker_root) if marker_root else "",
        "project_marker": str(project_marker_path(marker_root)) if marker_root else "",
        "database": str(database),
        "baton_cli_version": BATON_VERSION,
        **compatibility,
        "workspace_config": str(workspace_config.path) if workspace_config.path.is_file() else "",
        "vcs_provider": workspace_config.provider,
        "vcs_policy": workspace_config.policy,
        "required_baton_version": workspace_config.required_version,
        **metadata,
    }
    if args.format == "json":
        print(json.dumps(payload, indent=2, ensure_ascii=False))
        return 0
    for key, value in payload.items():
        rendered = str(value).lower() if isinstance(value, bool) else value
        print(f"{key}: {rendered}")
    return 0


def command_workspace_check(args: argparse.Namespace) -> int:
    baseline = ""
    if args.job_id:
        with closing(connect_readonly(args.db)) as con:
            check_schema(con)
            row = con.execute(
                "select status from handoff_jobs where job_id = ?",
                (args.job_id,),
            ).fetchone()
            if not row:
                raise MigrationError(f"unknown job: {args.job_id}")
        baseline = latest_workspace_commit(args.db, args.job_id, ("claimed", "registered"))
    assessment = assess_workspace(args.db, baseline, args.workspace_root)
    snapshot = assessment.snapshot
    result = (
        "off"
        if assessment.config.policy == "off"
        else ("mismatch" if assessment.issues else "compatible")
    )
    payload = {
        "project_root": str(assessment.config.root),
        "config": str(assessment.config.path) if assessment.config.path.is_file() else "",
        "provider": assessment.config.provider,
        "policy": assessment.config.policy,
        "baton_version": BATON_VERSION,
        "required_version": assessment.config.required_version,
        "repository_root": str(snapshot.repository_root) if snapshot else "",
        "head_commit": snapshot.head_commit if snapshot else "",
        "baseline_commit": assessment.baseline_commit,
        "branch": snapshot.branch if snapshot else "",
        "dirty": snapshot.dirty if snapshot else False,
        "result": result,
        "issues": list(assessment.issues),
    }
    if args.format == "json":
        print(json.dumps(payload, indent=2, ensure_ascii=False))
    else:
        for key, value in payload.items():
            if key == "issues":
                print(f"issues: {'; '.join(value)}")
            else:
                print(f"{key}: {str(value).lower() if isinstance(value, bool) else value}")
    return 1 if assessment.config.policy == "strict" and assessment.issues else 0


def command_workspace_events(args: argparse.Namespace) -> int:
    conditions: list[str] = []
    params: list[object] = []
    if args.job_id:
        conditions.extend(("entity_type = 'handoff'", "entity_id = ?"))
        params.append(args.job_id)
    where = f"where {' and '.join(conditions)}" if conditions else ""
    with closing(connect_readonly(args.db)) as con:
        check_schema(con)
        rows = con.execute(
            f"""
            select entity_type, entity_id, operation, policy, outcome, head_commit,
                   baseline_commit, branch, dirty, actor_role, message, created_at
            from workspace_events
            {where}
            order by id desc
            limit ?
            """,
            (*params, args.limit),
        ).fetchall()
    payload = [{key: row[key] for key in row.keys()} for row in rows]
    if args.format == "json":
        print(json.dumps(payload, indent=2, ensure_ascii=False))
        return 0
    if not rows:
        print("No workspace events.")
        return 0
    for row in rows:
        print(
            f"{row['created_at']}\t{row['entity_type']}\t{row['entity_id'] or ''}\t"
            f"{row['operation']}\t{row['policy']}\t{row['outcome']}\t"
            f"{row['head_commit'] or ''}\t{row['branch'] or ''}\t"
            f"dirty={row['dirty']}\t{row['message'] or ''}"
        )
    return 0


def command_guide_list(args: argparse.Namespace) -> int:
    del args
    for name in GUIDE_FILES:
        print(name)
    return 0


def command_guide_show(args: argparse.Namespace) -> int:
    guide = files("agents_baton").joinpath("guides", GUIDE_FILES[args.guide_name])
    print(guide.read_text(encoding="utf-8"), end="")
    return 0


def command_help(args: argparse.Namespace) -> int:
    args.root_parser.print_help()
    return 0


def init_schema(con: sqlite3.Connection) -> None:
    check_schema(con)


def seed_roles(con: sqlite3.Connection) -> None:
    now = utc_now()
    for role_id, display_name in DEFAULT_ROLES:
        con.execute(
            """
            insert into roles(role_id, display_name, created_at, updated_at)
            values (?, ?, ?, ?)
            on conflict(role_id) do nothing
            """,
            (role_id, display_name, now, now),
        )


def seed_role_permissions(con: sqlite3.Connection) -> None:
    for permission in KNOWN_PERMISSIONS:
        con.execute(
            """
            insert into role_permissions(role_id, permission)
            values ('sm', ?)
            on conflict(role_id, permission) do nothing
            """,
            (permission,),
        )
    for permission in (
        "cr.review",
        "cr.request_revision",
        "cr.approve",
        "cr.reject",
        "cr.mark_implemented",
        "handoff.cancel",
        "handoff.register",
    ):
        con.execute(
            """
            insert into role_permissions(role_id, permission)
            values ('planning', ?)
            on conflict(role_id, permission) do nothing
            """,
            (permission,),
        )


def begin_immediate(con: sqlite3.Connection) -> None:
    con.execute("BEGIN IMMEDIATE")


def event(
    con: sqlite3.Connection,
    event_type: str,
    job_id: str | None = None,
    actor_role: str | None = None,
    actor_id: str | None = None,
    from_status: str | None = None,
    to_status: str | None = None,
    message: str | None = None,
) -> None:
    con.execute(
        """
        insert into handoff_events(job_id, event_type, actor_role, actor_id, from_status, to_status, message, created_at)
        values (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (job_id, event_type, actor_role, actor_id, from_status, to_status, message, utc_now()),
    )


def gate_event(
    con: sqlite3.Connection,
    gate_name: str,
    event_type: str,
    actor_role: str | None = None,
    from_status: str | None = None,
    to_status: str | None = None,
    message: str | None = None,
) -> None:
    con.execute(
        """
        insert into gate_events(gate_name, event_type, actor_role, from_status, to_status, message, created_at)
        values (?, ?, ?, ?, ?, ?, ?)
        """,
        (gate_name, event_type, actor_role, from_status, to_status, message, utc_now()),
    )


def cancel_blocked_dependents(
    con: sqlite3.Connection,
    upstream_job_id: str,
    actor_role: str,
) -> list[str]:
    pending = [upstream_job_id]
    cancelled: list[str] = []
    while pending:
        current_upstream_id = pending.pop(0)
        dependents = con.execute(
            """
            select j.job_id
            from handoff_dependencies d
            join handoff_jobs j on j.job_id = d.job_id
            where d.depends_on_job_id = ? and j.status = 'blocked'
            order by j.created_at, j.job_id
            """,
            (current_upstream_id,),
        ).fetchall()
        for dependent in dependents:
            dependent_job_id = dependent["job_id"]
            changed = con.execute(
                """
                update handoff_jobs
                set status = 'cancelled'
                where job_id = ? and status = 'blocked'
                """,
                (dependent_job_id,),
            ).rowcount
            if changed != 1:
                continue
            event(
                con,
                "dependency_cancelled",
                job_id=dependent_job_id,
                actor_role=actor_role,
                from_status="blocked",
                to_status="cancelled",
                message=f"Dependency {current_upstream_id} was cancelled.",
            )
            cancelled.append(dependent_job_id)
            pending.append(dependent_job_id)
    return cancelled


def cancel_handoff_with_dependents(
    con: sqlite3.Connection,
    job_id: str,
    actor_role: str,
    message: str,
    event_type: str = "cancelled",
) -> list[str]:
    row = con.execute(
        "select status from handoff_jobs where job_id = ?",
        (job_id,),
    ).fetchone()
    if not row or row["status"] in {"finished", "cancelled"}:
        return []
    con.execute(
        "update handoff_jobs set status = 'cancelled' where job_id = ?",
        (job_id,),
    )
    event(
        con,
        event_type,
        job_id=job_id,
        actor_role=actor_role,
        from_status=row["status"],
        to_status="cancelled",
        message=message,
    )
    return [job_id, *cancel_blocked_dependents(con, job_id, actor_role)]


def request_handoff_cancellation(
    con: sqlite3.Connection,
    job_id: str,
    actor_role: str,
    message: str,
) -> tuple[str, list[str]]:
    row = con.execute(
        "select status from handoff_jobs where job_id = ?",
        (job_id,),
    ).fetchone()
    if not row:
        raise SystemExit(f"ERROR: unknown job: {job_id}")
    if row["status"] in {"finished", "cancelled"}:
        raise SystemExit(f"ERROR: {row['status']} job cannot be cancelled: {job_id}")
    if row["status"] == "cancel_requested":
        raise SystemExit(f"ERROR: cancellation is already requested: {job_id}")
    if row["status"] == "in_progress":
        con.execute(
            "update handoff_jobs set status = 'cancel_requested' where job_id = ?",
            (job_id,),
        )
        event(
            con,
            "cancellation_requested",
            job_id=job_id,
            actor_role=actor_role,
            from_status="in_progress",
            to_status="cancel_requested",
            message=message,
        )
        return "requested", [job_id]
    return "cancelled", cancel_handoff_with_dependents(con, job_id, actor_role, message)


def cancel_linked_implementation_handoffs(
    con: sqlite3.Connection,
    cr_id: str,
    actor_role: str,
    reason: str,
) -> tuple[list[str], list[str]]:
    rows = con.execute(
        """
        select h.job_id, h.status
        from cr_handoffs ch
        join handoff_jobs h on h.job_id = ch.job_id
        where ch.cr_id = ? and ch.kind = 'implementation'
        order by h.created_at, h.job_id
        """,
        (cr_id,),
    ).fetchall()
    failed = [row["job_id"] for row in rows if row["status"] == "failed"]
    if failed:
        raise SystemExit(
            "ERROR: resolve the failure CR before cancelling or superseding its parent CR: "
            + ", ".join(failed)
        )
    requested: list[str] = []
    cancelled: list[str] = []
    for row in rows:
        if row["status"] in {"finished", "cancelled"}:
            continue
        if row["status"] == "cancel_requested":
            requested.append(row["job_id"])
            continue
        outcome, affected = request_handoff_cancellation(
            con,
            row["job_id"],
            actor_role,
            reason,
        )
        if outcome == "requested":
            requested.extend(affected)
        else:
            cancelled.extend(affected)
    return requested, list(dict.fromkeys(cancelled))


def cancel_jobs_for_gate(
    con: sqlite3.Connection,
    gate_name: str,
    actor_role: str,
) -> list[str]:
    rows = con.execute(
        """
        select j.job_id
        from handoff_gate_dependencies d
        join handoff_jobs j on j.job_id = d.job_id
        where d.gate_name = ? and j.status = 'blocked'
        order by j.created_at, j.job_id
        """,
        (gate_name,),
    ).fetchall()
    cancelled: list[str] = []
    for row in rows:
        job_id = row["job_id"]
        changed = con.execute(
            "update handoff_jobs set status = 'cancelled' where job_id = ? and status = 'blocked'",
            (job_id,),
        ).rowcount
        if changed != 1:
            continue
        event(
            con,
            "gate_cancelled",
            job_id=job_id,
            actor_role=actor_role,
            from_status="blocked",
            to_status="cancelled",
            message=f"Gate {gate_name} was cancelled.",
        )
        cancelled.append(job_id)
        cancelled.extend(cancel_blocked_dependents(con, job_id, actor_role))
    return cancelled


def cr_event(
    con: sqlite3.Connection,
    cr_id: str,
    event_type: str,
    actor_role: str | None = None,
    from_status: str | None = None,
    to_status: str | None = None,
    message: str | None = None,
) -> None:
    con.execute(
        """
        insert into cr_events(cr_id, event_type, actor_role, from_status, to_status, message, created_at)
        values (?, ?, ?, ?, ?, ?, ?)
        """,
        (cr_id, event_type, actor_role, from_status, to_status, message, utc_now()),
    )


def resolve_role(con: sqlite3.Connection, role: str) -> str:
    normalized = normalize_role(role)
    row = con.execute("select role_id from roles where role_id = ? and active = 1", (normalized,)).fetchone()
    if row:
        return row["role_id"]
    row = con.execute(
        """
        select r.role_id
        from role_aliases a
        join roles r on r.role_id = a.role_id
        where a.alias = ? and r.active = 1
        """,
        (normalized,),
    ).fetchone()
    if row:
        return row["role_id"]
    raise SystemExit(f"ERROR: unknown or inactive role: {role}")


def require_permission(con: sqlite3.Connection, role: str, permission: str) -> str:
    role_id = resolve_role(con, role)
    row = con.execute(
        "select 1 from role_permissions where role_id = ? and permission = ?",
        (role_id, permission),
    ).fetchone()
    if not row:
        raise SystemExit(f"ERROR: role {role_id} lacks permission {permission}")
    return role_id


def require_failure_reviewer(con: sqlite3.Connection, role: str) -> str:
    role_id = resolve_role(con, role)
    granted = {
        row["permission"]
        for row in con.execute(
            "select permission from role_permissions where role_id = ?",
            (role_id,),
        ).fetchall()
    }
    missing = sorted(FAILURE_REVIEW_PERMISSIONS - granted)
    if missing:
        raise SystemExit(
            f"ERROR: failure reviewer role {role_id} lacks permissions: {', '.join(missing)}"
        )
    return role_id


def require_gate_owner(con: sqlite3.Connection, gate_name: str, role: str) -> str:
    role_id = resolve_role(con, role)
    owner = con.execute(
        "select 1 from gate_owners where gate_name = ? and role_id = ?",
        (gate_name, role_id),
    ).fetchone()
    if not owner:
        raise SystemExit(f"ERROR: role {role_id} does not own gate {gate_name}")
    return role_id


def require_gate_transfer_authority(con: sqlite3.Connection, gate_name: str, role: str) -> str:
    role_id = resolve_role(con, role)
    owner = con.execute(
        "select 1 from gate_owners where gate_name = ? and role_id = ?",
        (gate_name, role_id),
    ).fetchone()
    manager = con.execute(
        "select 1 from role_permissions where role_id = ? and permission = 'gate.manage'",
        (role_id,),
    ).fetchone()
    if not owner and not manager:
        raise SystemExit(f"ERROR: role {role_id} does not own gate {gate_name} and lacks permission gate.manage")
    return role_id


def require_distinct_cr_roles(author_role: str, reviewer_role: str) -> None:
    if author_role == reviewer_role:
        raise SystemExit("ERROR: CR author_role and reviewer_role cannot be the same")


def next_job_id(con: sqlite3.Connection) -> str:
    prefix = f"HO-{today_text()}-"
    rows = con.execute("select job_id from handoff_jobs where job_id like ?", (f"{prefix}%",)).fetchall()
    current = 0
    for row in rows:
        try:
            current = max(current, int(row["job_id"].rsplit("-", 1)[1]))
        except ValueError:
            continue
    return f"{prefix}{current + 1:03d}"


def next_cr_id(con: sqlite3.Connection) -> str:
    prefix = f"CR-{today_text()}-"
    rows = con.execute("select cr_id from change_requests where cr_id like ?", (f"{prefix}%",)).fetchall()
    current = 0
    for row in rows:
        try:
            current = max(current, int(row["cr_id"].rsplit("-", 1)[1]))
        except ValueError:
            continue
    return f"{prefix}{current + 1:03d}"


def active_review_claimant(row: sqlite3.Row) -> str:
    if row["status"] != "submitted":
        return ""
    return str(row["review_claimed_by"] or "")


def cr_frontmatter(row: sqlite3.Row) -> str:
    last_review_claimant = str(row["review_claimed_by"] or "")
    values = {
        "cr_id": row["cr_id"],
        "title": row["title"],
        "status": row["status"],
        "author_role": row["author_role"],
        "reviewer_role": row["reviewer_role"],
        "reviewer_workstream": row["reviewer_workstream"] or "",
        "active_review_claimed_by": active_review_claimant(row),
        "last_review_claimed_by": last_review_claimant,
        "review_claimed_by": active_review_claimant(row),
        "revision_count": str(row["revision_count"]),
        "active_revision_job_id": row["active_revision_job_id"] or "",
        "submitted_body_hash": row["submitted_body_hash"] or "",
        "approved_body_hash": row["approved_body_hash"] or "",
        "superseded_by_cr_id": row["superseded_by_cr_id"] or "",
        "superseded_by_ref": row["superseded_by_ref"] or "",
        "managed_by": "baton",
        "updated_at": row["updated_at"],
    }
    body = "\n".join(
        f"{key}: {value}" if value else f"{key}:" for key, value in values.items()
    )
    return f"---\n{body}\n---\n"


def read_stable_cr_bytes(path: Path) -> tuple[bytes, tuple[int, int, int, int]]:
    if not path.is_file():
        raise MigrationError(f"CR document is missing: {path}")
    try:
        stat = path.stat()
        content = path.read_bytes()
        current_stat = path.stat()
    except OSError as exc:
        raise MigrationError(f"cannot read CR document {path}: {exc}") from exc
    signature = (stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns)
    current_signature = (
        current_stat.st_dev,
        current_stat.st_ino,
        current_stat.st_size,
        current_stat.st_mtime_ns,
    )
    if signature != current_signature:
        raise MigrationError(f"CR document changed while it was being read: {path}")
    return content, signature


def cr_file_signature(path: Path) -> tuple[int, int, int, int, str]:
    content, signature = read_stable_cr_bytes(path)
    return (*signature, hashlib.sha256(content).hexdigest())


def cr_markdown_body(text: str) -> str:
    if text.startswith("---\n"):
        end = text.find("\n---\n", 4)
        if end != -1:
            return text[end + len("\n---\n") :]
    return text


def cr_body_snapshot(path: Path) -> tuple[str, str]:
    content, _ = read_stable_cr_bytes(path)
    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise MigrationError(f"CR document is not valid UTF-8: {path}") from exc
    body = cr_markdown_body(text)
    return body, hashlib.sha256(body.encode("utf-8")).hexdigest()


def cr_body_hash(path: Path) -> str:
    return cr_body_snapshot(path)[1]


def require_cr_body_hash(
    con: sqlite3.Connection,
    row: sqlite3.Row,
    hash_column: str,
    label: str,
) -> str:
    expected = str(row[hash_column] or "")
    if not expected:
        raise MigrationError(
            f"CR {row['cr_id']} has no recorded {label} body hash; "
            "the assigned reviewer must seal the current body before implementation"
        )
    path = project_file_path(con, row["file_path"])
    current = cr_body_hash(path)
    if current != expected:
        raise MigrationError(
            f"CR {row['cr_id']} body changed after {label}: {path}; "
            "restore the reviewed body or create a new CR"
        )
    return current


def cr_body_integrity(
    con: sqlite3.Connection,
    row: sqlite3.Row,
    current_hash: str | None = None,
) -> tuple[str, str]:
    if row["status"] in {"approved", "implemented", "superseded"}:
        expected = str(row["approved_body_hash"] or "")
    elif row["status"] == "submitted":
        expected = str(row["submitted_body_hash"] or "")
    else:
        return "editable", ""
    if not expected:
        return "legacy-unsealed", ""
    path = project_file_path(con, row["file_path"])
    if not path.is_file():
        return "missing", expected
    if current_hash is None:
        try:
            current_hash = cr_body_hash(path)
        except MigrationError:
            return "unreadable", expected
    return ("ok" if current_hash == expected else "mismatch"), expected


def write_cr_markdown(
    path: Path,
    frontmatter: str,
    body: str,
    *,
    expected_signature: tuple[int, int, int, int, str] | None = None,
    expect_missing: bool = False,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = path.with_name(f".{path.name}.baton-{uuid.uuid4().hex}.tmp")
    try:
        with temporary_path.open("x", encoding="utf-8") as temporary_file:
            temporary_file.write(frontmatter + "\n" + body.lstrip())
            temporary_file.flush()
            os.fsync(temporary_file.fileno())
        if expect_missing:
            if path.exists():
                raise MigrationError(f"CR document was created concurrently; retry the command: {path}")
        elif expected_signature is not None:
            if not path.exists() or cr_file_signature(path) != expected_signature:
                raise MigrationError(f"CR document changed concurrently; retry the command: {path}")
            temporary_path.chmod(path.stat().st_mode & 0o777)
        os.replace(temporary_path, path)
    finally:
        if temporary_path.exists():
            temporary_path.unlink()


def sync_cr_file(
    con: sqlite3.Connection,
    cr_id: str,
    *,
    new_body: str = "",
    allow_body_mismatch: bool = False,
) -> None:
    row = con.execute("select * from change_requests where cr_id = ?", (cr_id,)).fetchone()
    if not row:
        raise SystemExit(f"ERROR: unknown CR: {cr_id}")
    path = project_file_path(con, row["file_path"])
    frontmatter = cr_frontmatter(row)
    if not path.exists():
        body = new_body or (
            f"# {row['title']}\n\n## Background\n\n## Requirements\n\n## Acceptance Criteria\n"
        )
        write_cr_markdown(path, frontmatter, body, expect_missing=True)
        return
    if (
        not allow_body_mismatch
        and row["status"] in {"approved", "implemented", "superseded"}
        and row["approved_body_hash"]
    ):
        require_cr_body_hash(con, row, "approved_body_hash", "approval")
    if (
        not allow_body_mismatch
        and row["status"] == "submitted"
        and row["submitted_body_hash"]
    ):
        require_cr_body_hash(con, row, "submitted_body_hash", "submission")
    signature = cr_file_signature(path)
    text = path.read_text(encoding="utf-8")
    body = cr_markdown_body(text)
    if body != text:
        write_cr_markdown(path, frontmatter, body, expected_signature=signature)
        return
    write_cr_markdown(path, frontmatter, text, expected_signature=signature)


def control_scopes_for_role(role: str) -> tuple[str, str]:
    return ("all", f"role:{normalize_role(role)}")


def expire_shift_controls(con: sqlite3.Connection, scopes: tuple[str, ...] | None = None) -> None:
    now = utc_now()
    if scopes:
        placeholders = ", ".join("?" for _ in scopes)
        rows = con.execute(
            f"""
            select scope, work_until
            from handoff_controls
            where stopped = 0 and work_until is not null and work_until != '' and scope in ({placeholders})
            """,
            scopes,
        ).fetchall()
    else:
        rows = con.execute(
            """
            select scope, work_until
            from handoff_controls
            where stopped = 0 and work_until is not null and work_until != ''
            """
        ).fetchall()
    for row in rows:
        if row["work_until"] <= now:
            con.execute(
                """
                update handoff_controls
                set stopped = 1, reason = ?, updated_at = ?
                where scope = ?
                """,
                (f"shift expired at {row['work_until']}", now, row["scope"]),
            )


def get_stop_control(con: sqlite3.Connection, role: str) -> sqlite3.Row | None:
    scopes = control_scopes_for_role(role)
    expire_shift_controls(con, scopes)
    return con.execute(
        """
        select scope, reason, updated_at
        from handoff_controls
        where stopped = 1 and scope in (?, ?)
        order by case scope when 'all' then 0 else 1 end
        limit 1
        """,
        scopes,
    ).fetchone()


def start_waiter(args: argparse.Namespace, wait_kind: str, permission: str = "") -> tuple[str, str, int]:
    with connect(args.db) as con:
        init_schema(con)
        role = require_permission(con, args.role, permission) if permission else resolve_role(con, args.role)
    waiter_id = str(uuid.uuid4())
    active_waiters = heartbeat_waiter(
        args.db,
        waiter_id,
        wait_kind,
        role,
        waiter_lease_seconds(args.interval),
    )
    return waiter_id, role, active_waiters


def heartbeat_waiter(
    db_path: str,
    waiter_id: str,
    wait_kind: str,
    role: str,
    lease_seconds: int,
) -> int:
    now_value = datetime.now(timezone.utc)
    now = format_utc(now_value)
    expires_at = format_utc(now_value + timedelta(seconds=lease_seconds))
    with connect(db_path) as con:
        init_schema(con)
        begin_immediate(con)
        con.execute("delete from waiter_leases where lease_expires_at <= ?", (now,))
        con.execute(
            """
            insert into waiter_leases(
              waiter_id, wait_kind, role_id, started_at, heartbeat_at, lease_expires_at
            )
            values (?, ?, ?, ?, ?, ?)
            on conflict(waiter_id) do update set
              wait_kind = excluded.wait_kind,
              role_id = excluded.role_id,
              heartbeat_at = excluded.heartbeat_at,
              lease_expires_at = excluded.lease_expires_at
            """,
            (waiter_id, wait_kind, role, now, now, expires_at),
        )
        active_waiters = con.execute("select count(*) from waiter_leases").fetchone()[0]
        con.commit()
    return int(active_waiters)


def unregister_waiter(db_path: str, waiter_id: str) -> None:
    try:
        with connect(db_path) as con:
            init_schema(con)
            begin_immediate(con)
            con.execute("delete from waiter_leases where waiter_id = ?", (waiter_id,))
            con.commit()
    except (MigrationError, OSError, sqlite3.Error):
        # A failed cleanup is bounded by the lease expiry and must not hide the wait result.
        pass


def upsert_control(con: sqlite3.Connection, scope: str, stopped: bool, reason: str = "") -> None:
    con.execute(
        """
        insert into handoff_controls(scope, stopped, reason, updated_at)
        values (?, ?, ?, ?)
        on conflict(scope) do update set
          stopped = excluded.stopped,
          reason = excluded.reason,
          updated_at = excluded.updated_at
        """,
        (scope, 1 if stopped else 0, reason, utc_now()),
    )


def set_shift_until(con: sqlite3.Connection, scope: str, work_until: str, reason: str = "") -> None:
    con.execute(
        """
        insert into handoff_controls(scope, stopped, reason, work_until, updated_at)
        values (?, 0, ?, ?, ?)
        on conflict(scope) do update set
          stopped = 0,
          reason = excluded.reason,
          work_until = excluded.work_until,
          updated_at = excluded.updated_at
        """,
        (scope, reason, work_until, utc_now()),
    )


def command_init(args: argparse.Namespace) -> int:
    if args.project_root:
        root = Path(args.project_root).expanduser().resolve()
        if not root.is_dir():
            raise MigrationError(f"project root is not a directory: {root}")
        expected_database = (root / ".baton" / "baton.sqlite3").resolve()
        if args.db_explicit and Path(args.db).expanduser().resolve() != expected_database:
            raise MigrationError(
                "init --project-root cannot be combined with an external --db; "
                f"expected database: {expected_database}"
            )
        args.db = str(expected_database)
    path = Path(args.db)
    validate_marker_for_database(path)
    resolved_path = path.expanduser().resolve()
    if (
        not path.exists()
        and resolved_path.name == "baton.sqlite3"
        and resolved_path.parent.name == ".baton"
        and project_marker_path(resolved_path.parent.parent).exists()
    ):
        raise MigrationError(
            f"Baton project marker exists but its database is missing: {resolved_path}; "
            "restore the database instead of reinitializing workflow history"
        )
    if path.exists() and path.stat().st_size:
        with closing(connect_readonly(args.db)) as con:
            tables = database_table_names(con)
            if tables:
                baton_tables = {"roles", "handoff_jobs", "handoff_controls", "change_requests"}
                if not tables.intersection(baton_tables):
                    raise MigrationError(f"database is not empty and is not a recognized Baton database: {args.db}")
                check_schema(con)
                marker = ensure_marker_for_database(path)
                print(f"Already initialized {args.db}")
                if marker:
                    print(f"Project marker {marker}")
                print(
                    "Agent action: run 'baton guide show bootstrap' and review project AGENTS.md"
                )
                return 0
    with connect(args.db, create=True) as con:
        migrate_schema(con)
    marker = ensure_marker_for_database(path)
    print(f"Initialized {args.db}")
    if marker:
        print(f"Project marker {marker}")
    print("Agent action: run 'baton guide show bootstrap' and configure project AGENTS.md")
    return 0


def command_upgrade_preflight(args: argparse.Namespace) -> int:
    path = Path(args.db).expanduser().resolve()
    if not path.is_file():
        raise MigrationError(f"database does not exist: {path}")
    with closing(connect_readonly(str(path))) as con:
        tables = database_table_names(con)
        baton_tables = {"roles", "handoff_jobs", "handoff_controls", "change_requests"}
        if not tables.intersection(baton_tables):
            raise MigrationError(f"database is not a recognized Baton database: {path}")
        compatibility = database_schema_compatibility(con)
        if not compatibility["cli_schema_compatible"]:
            raise MigrationError(str(compatibility["compatibility_error"]))
        schema = int(compatibility["schema_version"])
        waiters = active_waiter_rows(con)
        handoffs = in_progress_handoff_rows(con)
        reviews = active_cr_review_rows(con)
        global_stop = global_stop_is_active(con)

    ready = global_stop and not waiters and not handoffs and not reviews
    payload = {
        "status": "ready" if ready else "not_ready",
        "database": str(path),
        "baton_version": BATON_VERSION,
        **compatibility,
        "global_stop": global_stop,
        "active_waiters": [{key: row[key] for key in row.keys()} for row in waiters],
        "active_handoffs": [{key: row[key] for key in row.keys()} for row in handoffs],
        "active_cr_reviews": [{key: row[key] for key in row.keys()} for row in reviews],
    }
    if args.format == "json":
        print(json.dumps(payload, indent=2, ensure_ascii=False))
        return 0 if ready else 2

    print(f"Upgrade preflight {'READY' if ready else 'NOT READY'}")
    print(f"database: {path}")
    print(f"baton_version: {BATON_VERSION}")
    print(f"schema_version: {schema}")
    print(f"supported_schema_version: {LATEST_SCHEMA_VERSION}")
    print(f"database_schema_current: {str(compatibility['database_schema_current']).lower()}")
    print(f"migration_required: {str(compatibility['migration_required']).lower()}")
    print(f"cli_schema_compatible: {str(compatibility['cli_schema_compatible']).lower()}")
    print(f"workflow_commands_ready: {str(compatibility['workflow_commands_ready']).lower()}")
    print(f"global_stop: {'yes' if global_stop else 'no'}")
    for row in waiters:
        print(
            f"waiter: {row['waiter_id']} kind={row['wait_kind']} role={row['role_id']} "
            f"lease_expires_at={row['lease_expires_at']}"
        )
    for row in handoffs:
        print(
            f"handoff: {row['job_id']} status={row['status']} role={row['target_role']} "
            f"claimed_by={row['claimed_by'] or ''}"
        )
    for row in reviews:
        print(
            f"cr_review: {row['cr_id']} role={row['reviewer_role']} "
            f"workstream={row['reviewer_workstream'] or ''} "
            f"claimed_by={row['review_claimed_by']}"
        )
    if not global_stop:
        print("action: run 'baton stop --all --reason \"Baton upgrade\"' before draining")
    if waiters:
        print("action: wait for listed waiter leases to exit or expire")
    if handoffs:
        print("action: finish, fail, or cooperatively cancel the listed handoffs")
    if reviews:
        print("action: decide or release the listed CR reviews")
    if ready:
        print(
            "action: upgrade Baton, read 'baton guide show upgrade' and "
            "'baton guide show changelog', migrate, verify, "
            "review project AGENTS.md, then explicitly resume"
        )
    else:
        print("action: rerun 'baton upgrade preflight' after every blocker is drained")
    return 0 if ready else 2


def command_migrate(args: argparse.Namespace) -> int:
    path = Path(args.db)
    validate_marker_for_database(path)
    output_verb = getattr(args, "output_verb", "Migrated")
    if args.check:
        with connect_readonly(args.db) as con:
            current = check_schema(con)
        print(f"Schema current {args.db} schema={current}")
        return 0

    if not path.is_file():
        raise MigrationError(f"database does not exist: {args.db}; run 'baton init' first")
    with closing(connect_readonly(args.db)) as con:
        tables = database_table_names(con)
        baton_tables = {"roles", "handoff_jobs", "handoff_controls", "change_requests"}
        if not tables.intersection(baton_tables):
            raise MigrationError(f"database is not a recognized Baton database: {args.db}")
        previous = current_schema_version(con)
        if previous == LATEST_SCHEMA_VERSION:
            current = check_schema(con)
            marker = ensure_marker_for_database(path)
            print(f"{output_verb} {args.db} schema={previous}->{current} applied=none")
            if marker:
                print(f"Project marker {marker}")
            print(
                "Agent action: read 'baton guide show upgrade' and "
                "'baton guide show changelog', then review project AGENTS.md"
            )
            return 0
        waiters = active_waiter_rows(con)
        active_handoffs = in_progress_handoff_rows(con)
        active_reviews = active_cr_review_rows(con)
        blockers: list[str] = []
        if waiters:
            blockers.append("waiters=" + ",".join(row["waiter_id"] for row in waiters))
        if active_handoffs:
            blockers.append(
                "handoffs="
                + ",".join(f"{row['job_id']}:{row['status']}" for row in active_handoffs)
            )
        if active_reviews:
            blockers.append("cr_reviews=" + ",".join(row["cr_id"] for row in active_reviews))
        if blockers:
            raise MigrationError(
                "cannot migrate while workflow activity is present: "
                + "; ".join(blockers)
                + "; drain with the previous compatible Baton and run 'baton upgrade preflight'"
            )
        signature = database_content_signature(con)

    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup_path = path.parent / "backups" / f"baton-before-{timestamp}-{uuid.uuid4().hex[:8]}.sqlite3"
    backup_database(path, backup_path, signature)
    with connect(args.db) as con:
        enable_wal(con)
        con.execute("PRAGMA foreign_keys = OFF")
        con.execute("BEGIN IMMEDIATE")
        if database_content_signature(con) != signature:
            con.rollback()
            backup_path.unlink()
            raise MigrationError("database changed while migration was starting; rerun 'baton migrate'")
        previous, current, applied = migrate_schema(con, transaction_started=True)
        con.execute("PRAGMA foreign_keys = ON")
    marker = ensure_marker_for_database(path)
    applied_text = ",".join(applied) if applied else "none"
    print(
        f"{output_verb} {args.db} schema={previous}->{current} "
        f"applied={applied_text} backup={backup_path}"
    )
    if marker:
        print(f"Project marker {marker}")
    print(
        "Agent action: read 'baton guide show upgrade' and "
        "'baton guide show changelog', then review project AGENTS.md"
    )
    return 0


def command_update(args: argparse.Namespace) -> int:
    print(
        "WARNING: 'baton update' is a deprecated database migration alias; use 'baton migrate'.",
        file=sys.stderr,
    )
    args.check = False
    args.output_verb = "Updated"
    return command_migrate(args)


def command_role_list(args: argparse.Namespace) -> int:
    with connect(args.db) as con:
        init_schema(con)
        rows = con.execute("select role_id, display_name, active from roles order by role_id").fetchall()
    for row in rows:
        state = "active" if row["active"] else "inactive"
        print(f"{row['role_id']}\t{row['display_name']}\t{state}")
    return 0


def command_role_add(args: argparse.Namespace) -> int:
    role_id = normalize_role(args.role_id)
    now = utc_now()
    with connect(args.db) as con:
        init_schema(con)
        begin_immediate(con)
        con.execute(
            """
            insert into roles(role_id, display_name, description, created_at, updated_at)
            values (?, ?, ?, ?, ?)
            """,
            (role_id, args.display_name or role_id, args.description, now, now),
        )
        event(con, "role_added", actor_role="sm", message=role_id)
        con.commit()
    print(f"Added role {role_id}")
    return 0


def command_role_alias_add(args: argparse.Namespace) -> int:
    alias = normalize_role(args.alias)
    with connect(args.db) as con:
        init_schema(con)
        begin_immediate(con)
        role_id = resolve_role(con, args.role_id)
        con.execute("insert into role_aliases(alias, role_id) values (?, ?)", (alias, role_id))
        event(con, "role_alias_added", actor_role="sm", message=f"{alias}->{role_id}")
        con.commit()
    print(f"Added alias {alias} -> {role_id}")
    return 0


def command_role_permission_list(args: argparse.Namespace) -> int:
    with connect(args.db) as con:
        init_schema(con)
        if args.role_id:
            role_id = resolve_role(con, args.role_id)
            rows = con.execute(
                """
                select role_id, permission
                from role_permissions
                where role_id = ?
                order by permission
                """,
                (role_id,),
            ).fetchall()
        else:
            rows = con.execute(
                """
                select role_id, permission
                from role_permissions
                order by role_id, permission
                """
            ).fetchall()
    for row in rows:
        print(f"{row['role_id']}\t{row['permission']}")
    return 0


def command_role_permission_add(args: argparse.Namespace) -> int:
    if args.permission not in KNOWN_PERMISSIONS:
        known = ", ".join(sorted(KNOWN_PERMISSIONS))
        raise SystemExit(f"ERROR: unknown permission: {args.permission} (known: {known})")
    with connect(args.db) as con:
        init_schema(con)
        begin_immediate(con)
        role_id = resolve_role(con, args.role_id)
        con.execute(
            """
            insert into role_permissions(role_id, permission)
            values (?, ?)
            on conflict(role_id, permission) do nothing
            """,
            (role_id, args.permission),
        )
        event(con, "role_permission_added", actor_role="sm", message=f"{role_id}:{args.permission}")
        con.commit()
    print(f"Added permission {args.permission} -> {role_id}")
    return 0


def command_role_permission_remove(args: argparse.Namespace) -> int:
    if args.permission not in KNOWN_PERMISSIONS:
        known = ", ".join(sorted(KNOWN_PERMISSIONS))
        raise SystemExit(f"ERROR: unknown permission: {args.permission} (known: {known})")
    with connect(args.db) as con:
        init_schema(con)
        begin_immediate(con)
        role_id = resolve_role(con, args.role_id)
        removed = con.execute(
            "delete from role_permissions where role_id = ? and permission = ?",
            (role_id, args.permission),
        ).rowcount
        if removed != 1:
            raise SystemExit(f"ERROR: permission is not granted: {role_id}:{args.permission}")
        event(con, "role_permission_removed", actor_role="sm", message=f"{role_id}:{args.permission}")
        con.commit()
    print(f"Removed permission {args.permission} -> {role_id}")
    return 0


def command_agent_init(args: argparse.Namespace) -> int:
    role = normalize_role(args.role)
    suffix = args.label.strip() if args.label else uuid.uuid4().hex[:8]
    agent_id = args.agent_id.strip() if args.agent_id else f"{role}-{suffix}"
    path = agent_id_file(args)
    if path.exists() and not args.force:
        raise SystemExit(f"ERROR: agent id file already exists: {path}")
    write_agent_id(path, agent_id)
    print(f"{agent_id}\t{path}")
    return 0


def command_agent_show(args: argparse.Namespace) -> int:
    agent_id = read_agent_id(args)
    if not agent_id:
        print("No agent id configured.")
        return 1
    print(agent_id)
    return 0


def command_agent_workstream_add(args: argparse.Namespace) -> int:
    agent_id = agent_id_value(args)
    workstream = normalize_workstream(args.workstream)
    with connect(args.db) as con:
        init_schema(con)
        begin_immediate(con)
        role = resolve_role(con, args.role)
        added = con.execute(
            """
            insert into agent_workstreams(agent_id, role_id, workstream, created_at)
            values (?, ?, ?, ?)
            on conflict(agent_id, role_id, workstream) do nothing
            """,
            (agent_id, role, workstream, utc_now()),
        ).rowcount
        if added:
            event(
                con,
                "agent_workstream_added",
                actor_role=role,
                actor_id=agent_id,
                message=workstream,
            )
        con.commit()
    print(f"{agent_id}\t{role}\t{workstream}\t{'added' if added else 'exists'}")
    return 0


def command_agent_workstream_remove(args: argparse.Namespace) -> int:
    agent_id = agent_id_value(args)
    workstream = normalize_workstream(args.workstream)
    with connect(args.db) as con:
        init_schema(con)
        begin_immediate(con)
        role = resolve_role(con, args.role)
        removed = con.execute(
            "delete from agent_workstreams where agent_id = ? and role_id = ? and workstream = ?",
            (agent_id, role, workstream),
        ).rowcount
        if removed != 1:
            raise SystemExit(
                f"ERROR: workstream registration does not exist: "
                f"agent={agent_id} role={role} workstream={workstream}"
            )
        event(
            con,
            "agent_workstream_removed",
            actor_role=role,
            actor_id=agent_id,
            message=workstream,
        )
        con.commit()
    print(f"{agent_id}\t{role}\t{workstream}\tremoved")
    return 0


def command_agent_workstream_list(args: argparse.Namespace) -> int:
    agent_id = args.agent_id.strip()
    conditions: list[str] = []
    params: list[str] = []
    with connect(args.db) as con:
        init_schema(con)
        if agent_id:
            conditions.append("agent_id = ?")
            params.append(agent_id)
        if args.role:
            conditions.append("role_id = ?")
            params.append(resolve_role(con, args.role))
        where = f"where {' and '.join(conditions)}" if conditions else ""
        rows = con.execute(
            f"""
            select agent_id, role_id, workstream, created_at
            from agent_workstreams
            {where}
            order by agent_id, role_id, workstream
            """,
            params,
        ).fetchall()
    if args.format == "json":
        print(
            json.dumps(
                [{key: row[key] for key in row.keys()} for row in rows],
                indent=2,
                ensure_ascii=False,
            )
        )
    elif not rows:
        print("No agent workstreams.")
    else:
        for row in rows:
            print(
                f"{row['agent_id']}\t{row['role_id']}\t{row['workstream']}\t{row['created_at']}"
            )
    return 0


def active_agent_session(con: sqlite3.Connection, agent_id: str) -> sqlite3.Row | None:
    return con.execute(
        """
        select *
        from agent_sessions
        where agent_id = ? and status = 'active'
        order by updated_at desc
        limit 1
        """,
        (agent_id,),
    ).fetchone()


def command_agent_session_set(args: argparse.Namespace) -> int:
    agent_id = agent_id_value(args)
    host = args.host.strip()
    thread_id = args.thread_id.strip()
    model = args.model.strip()
    if not host or not thread_id or not model:
        raise SystemExit("ERROR: --host, --thread-id, and --model cannot be blank")
    with connect(args.db) as con:
        init_schema(con)
        begin_immediate(con)
        role = resolve_role(con, args.role)
        endpoint = con.execute(
            "select * from agent_sessions where host = ? and thread_id = ?",
            (host, thread_id),
        ).fetchone()
        if endpoint and endpoint["agent_id"] != agent_id:
            raise SystemExit(
                f"ERROR: {host} thread {thread_id} is already bound to agent {endpoint['agent_id']}"
            )
        active = active_agent_session(con, agent_id)
        same_endpoint = bool(active and active["host"] == host and active["thread_id"] == thread_id)
        if active and not same_endpoint and not args.replace:
            raise SystemExit(
                f"ERROR: agent {agent_id} already has active session {active['session_id']} "
                f"at {active['host']}:{active['thread_id']}; use --replace after verifying the new thread"
            )
        now = utc_now()
        if active and not same_endpoint:
            con.execute(
                """
                update agent_sessions
                set status = 'inactive', updated_at = ?, ended_at = ?, end_reason = ?
                where session_id = ?
                """,
                (now, now, f"replaced by {host}:{thread_id}", active["session_id"]),
            )
            event(
                con,
                "agent_session_replaced",
                actor_role=role,
                actor_id=agent_id,
                message=f"{active['session_id']} -> {host}:{thread_id}",
            )
        if endpoint:
            session_id = endpoint["session_id"]
            con.execute(
                """
                update agent_sessions
                set role_id = ?, model = ?, status = 'active', updated_at = ?,
                    ended_at = null, end_reason = null
                where session_id = ?
                """,
                (role, model, now, session_id),
            )
        else:
            session_id = str(uuid.uuid4())
            con.execute(
                """
                insert into agent_sessions(
                  session_id, agent_id, role_id, host, thread_id, model,
                  status, created_at, updated_at
                ) values (?, ?, ?, ?, ?, ?, 'active', ?, ?)
                """,
                (session_id, agent_id, role, host, thread_id, model, now, now),
            )
        event(
            con,
            "agent_session_active",
            actor_role=role,
            actor_id=agent_id,
            message=f"session={session_id} host={host} thread={thread_id} model={model}",
        )
        con.commit()
    print(
        f"{session_id}\tactive\t{agent_id}\t{role}\t{host}\t{thread_id}\t{model}"
    )
    return 0


def command_agent_session_end(args: argparse.Namespace) -> int:
    agent_id = agent_id_value(args)
    reason = args.reason.strip()
    if not reason:
        raise SystemExit("ERROR: --reason cannot be blank")
    with connect(args.db) as con:
        init_schema(con)
        begin_immediate(con)
        session = active_agent_session(con, agent_id)
        if not session:
            raise SystemExit(f"ERROR: no active session for agent {agent_id}")
        now = utc_now()
        con.execute(
            """
            update agent_sessions
            set status = 'inactive', updated_at = ?, ended_at = ?, end_reason = ?
            where session_id = ?
            """,
            (now, now, reason, session["session_id"]),
        )
        event(
            con,
            "agent_session_inactive",
            actor_role=session["role_id"],
            actor_id=agent_id,
            message=f"session={session['session_id']} reason={reason}",
        )
        con.commit()
    print(f"{session['session_id']}\tinactive\t{agent_id}")
    return 0


def command_agent_session_list(args: argparse.Namespace) -> int:
    conditions: list[str] = []
    params: list[object] = []
    with connect(args.db) as con:
        init_schema(con)
        if args.role:
            conditions.append("role_id = ?")
            params.append(resolve_role(con, args.role))
        if args.status:
            conditions.append("status = ?")
            params.append(args.status)
        if args.agent_id:
            conditions.append("agent_id = ?")
            params.append(args.agent_id.strip())
        where = f"where {' and '.join(conditions)}" if conditions else ""
        rows = con.execute(
            f"""
            select session_id, status, agent_id, role_id, host, thread_id, model,
                   created_at, updated_at, ended_at, end_reason
            from agent_sessions
            {where}
            order by case status when 'active' then 0 else 1 end, updated_at desc, session_id
            """,
            params,
        ).fetchall()
    payload = [{key: row[key] for key in row.keys()} for row in rows]
    if args.format == "json":
        print(json.dumps(payload, indent=2, ensure_ascii=False))
        return 0
    if not rows:
        print("No agent sessions.")
        return 0
    for row in rows:
        print(
            f"{row['session_id']}\t{row['status']}\t{row['agent_id']}\t{row['role_id']}\t"
            f"{row['host']}\t{row['thread_id']}\t{row['model']}\t{row['updated_at']}"
        )
    return 0


def create_handoff_job(
    con: sqlite3.Connection,
    title: str,
    role: str,
    source_ref: str,
    objective: str,
    exit_criteria: str,
    depends_on: list[str],
    depends_on_gates: list[str],
    actor_role: str,
    workstream: str = "",
) -> tuple[str, str, str, tuple[str, ...]]:
    target_role = resolve_role(con, role)
    normalized_workstream = normalize_workstream(workstream) if workstream.strip() else None
    dependency_statuses: dict[str, str] = {}
    for dep in depends_on:
        if dep in dependency_statuses:
            raise SystemExit(f"ERROR: duplicate handoff dependency: {dep}")
        dependency = con.execute(
            "select status from handoff_jobs where job_id = ?",
            (dep,),
        ).fetchone()
        if not dependency:
            raise SystemExit(f"ERROR: dependency does not exist: {dep}")
        dependency_statuses[dep] = dependency["status"]
    gate_statuses: dict[str, str] = {}
    normalized_gates: list[str] = []
    for gate in depends_on_gates:
        gate_name = normalize_gate_name(gate)
        if gate_name in gate_statuses:
            raise SystemExit(f"ERROR: duplicate gate dependency: {gate_name}")
        gate_row = con.execute(
            "select status from workflow_gates where gate_name = ?",
            (gate_name,),
        ).fetchone()
        if not gate_row:
            raise SystemExit(f"ERROR: gate does not exist: {gate_name}")
        normalized_gates.append(gate_name)
        gate_statuses[gate_name] = gate_row["status"]
    job_id = next_job_id(con)
    cancelled_dependencies = [
        dep for dep, dependency_status in dependency_statuses.items() if dependency_status == "cancelled"
    ]
    failed_dependencies = [
        dep for dep, dependency_status in dependency_statuses.items() if dependency_status == "failed"
    ]
    waiting_dependencies = [
        dep for dep, dependency_status in dependency_statuses.items() if dependency_status != "finished"
    ]
    cancelled_gates = [gate for gate, gate_status in gate_statuses.items() if gate_status == "cancelled"]
    waiting_gates = [gate for gate, gate_status in gate_statuses.items() if gate_status == "pending"]
    status = (
        "cancelled"
        if cancelled_dependencies or cancelled_gates
        else ("blocked" if waiting_dependencies or waiting_gates else "open")
    )
    con.execute(
        """
        insert into handoff_jobs(
          job_id, title, status, target_role, workstream,
          source_ref, objective, exit_criteria, created_at
        )
        values (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            job_id,
            title,
            status,
            target_role,
            normalized_workstream,
            source_ref,
            objective,
            exit_criteria,
            utc_now(),
        ),
    )
    for dep in depends_on:
        con.execute(
            "insert into handoff_dependencies(job_id, depends_on_job_id) values (?, ?)",
            (job_id, dep),
        )
    for gate_name in normalized_gates:
        con.execute(
            "insert into handoff_gate_dependencies(job_id, gate_name) values (?, ?)",
            (job_id, gate_name),
        )
    messages: list[str] = []
    if cancelled_dependencies:
        messages.append(f"Cancelled dependencies: {', '.join(cancelled_dependencies)}")
    if failed_dependencies:
        messages.append(f"Failed dependencies: {', '.join(failed_dependencies)}")
    if cancelled_gates:
        messages.append(f"Cancelled gates: {', '.join(cancelled_gates)}")
    event(
        con,
        "registered",
        job_id=job_id,
        actor_role=actor_role,
        to_status=status,
        message="; ".join(messages),
    )
    warnings = tuple(
        f"dependency {dep} is failed; {job_id} remains blocked until it is retried and finished "
        "or the new handoff is cancelled"
        for dep in failed_dependencies
    )
    return job_id, status, target_role, warnings


def command_register(args: argparse.Namespace) -> int:
    depends_on = args.depends_on or []
    assessment = assess_workspace(args.db, workspace_root_value=args.workspace_root)
    with connect(args.db) as con:
        init_schema(con)
        begin_immediate(con)
        actor_role = require_permission(con, args.actor_role, "handoff.register")
        job_id, status, role, warnings = create_handoff_job(
            con,
            args.title,
            args.role,
            args.source_ref,
            args.objective,
            args.exit_criteria,
            depends_on,
            args.depends_on_gate or [],
            actor_role,
            args.workstream,
        )
        apply_workspace_policy(
            con,
            args,
            assessment,
            entity_type="handoff",
            entity_id=job_id,
            operation="registered",
            actor_role=actor_role,
        )
        con.commit()
    for warning in warnings:
        print(f"WARNING: {warning}", file=sys.stderr)
    route = f"\t{normalize_workstream(args.workstream)}" if args.workstream.strip() else ""
    print(f"{job_id}\t{status}\t{role}{route}")
    return 0


def command_gate_create(args: argparse.Namespace) -> int:
    gate_name = normalize_gate_name(args.gate_name)
    with connect(args.db) as con:
        init_schema(con)
        begin_immediate(con)
        actor_role = resolve_role(con, args.role)
        if con.execute("select 1 from workflow_gates where gate_name = ?", (gate_name,)).fetchone():
            raise SystemExit(f"ERROR: gate already exists: {gate_name}")
        requested_owners = args.owner_role or [actor_role]
        owners = list(dict.fromkeys(resolve_role(con, role) for role in requested_owners))
        con.execute(
            """
            insert into workflow_gates(gate_name, status, created_by_role, created_at)
            values (?, 'pending', ?, ?)
            """,
            (gate_name, actor_role, utc_now()),
        )
        for owner in owners:
            con.execute(
                "insert into gate_owners(gate_name, role_id) values (?, ?)",
                (gate_name, owner),
            )
        gate_event(
            con,
            gate_name,
            "created",
            actor_role=actor_role,
            to_status="pending",
            message=f"owners={','.join(owners)}",
        )
        con.commit()
    print(f"{gate_name}\tpending\towners={','.join(owners)}")
    return 0


def command_gate_status(args: argparse.Namespace) -> int:
    with connect(args.db) as con:
        init_schema(con)
        params: tuple[str, ...] = ()
        where = ""
        if args.gate_name:
            where = "where g.gate_name = ?"
            params = (normalize_gate_name(args.gate_name),)
        rows = con.execute(
            f"""
            select
              g.gate_name,
              g.status,
              g.created_by_role,
              g.created_at,
              g.resolved_at,
              g.resolution_evidence,
              group_concat(o.role_id, ',') as owners
            from workflow_gates g
            join gate_owners o on o.gate_name = g.gate_name
            {where}
            group by g.gate_name
            order by g.created_at, g.gate_name
            """,
            params,
        ).fetchall()
    if args.gate_name and not rows:
        raise SystemExit(f"ERROR: unknown gate: {normalize_gate_name(args.gate_name)}")
    for row in rows:
        resolved = f"\tresolved={row['resolved_at']}" if row["resolved_at"] else ""
        evidence = f"\t{row['resolution_evidence']}" if row["resolution_evidence"] else ""
        print(
            f"{row['gate_name']}\t{row['status']}\towners={row['owners']}\t"
            f"created_by={row['created_by_role']}\tcreated={row['created_at']}{resolved}{evidence}"
        )
    return 0


def command_gate_release(args: argparse.Namespace) -> int:
    gate_name = normalize_gate_name(args.gate_name)
    evidence = args.evidence.strip()
    if not evidence:
        raise SystemExit("ERROR: --evidence cannot be blank")
    with connect(args.db) as con:
        init_schema(con)
        begin_immediate(con)
        row = con.execute("select status from workflow_gates where gate_name = ?", (gate_name,)).fetchone()
        if not row:
            raise SystemExit(f"ERROR: unknown gate: {gate_name}")
        actor_role = require_gate_owner(con, gate_name, args.role)
        if row["status"] != "pending":
            raise SystemExit(f"ERROR: gate is not pending: {gate_name} status={row['status']}")
        con.execute(
            """
            update workflow_gates
            set status = 'released', resolved_at = ?, resolution_evidence = ?
            where gate_name = ?
            """,
            (utc_now(), evidence, gate_name),
        )
        gate_event(
            con,
            gate_name,
            "released",
            actor_role=actor_role,
            from_status="pending",
            to_status="released",
            message=evidence,
        )
        promoted = promote_ready_handoffs_for_gate(con, gate_name, actor_role)
        con.commit()
    print(f"Released {gate_name} promoted={len(promoted)}")
    return 0


def command_gate_cancel(args: argparse.Namespace) -> int:
    gate_name = normalize_gate_name(args.gate_name)
    reason = args.reason.strip()
    if not reason:
        raise SystemExit("ERROR: --reason cannot be blank")
    with connect(args.db) as con:
        init_schema(con)
        begin_immediate(con)
        row = con.execute("select status from workflow_gates where gate_name = ?", (gate_name,)).fetchone()
        if not row:
            raise SystemExit(f"ERROR: unknown gate: {gate_name}")
        actor_role = require_gate_owner(con, gate_name, args.role)
        if row["status"] != "pending":
            raise SystemExit(f"ERROR: gate is not pending: {gate_name} status={row['status']}")
        con.execute(
            """
            update workflow_gates
            set status = 'cancelled', resolved_at = ?, resolution_evidence = ?
            where gate_name = ?
            """,
            (utc_now(), reason, gate_name),
        )
        gate_event(
            con,
            gate_name,
            "cancelled",
            actor_role=actor_role,
            from_status="pending",
            to_status="cancelled",
            message=reason,
        )
        cancelled = cancel_jobs_for_gate(con, gate_name, actor_role)
        con.commit()
    print(f"Cancelled gate {gate_name} handoffs={len(cancelled)}")
    return 0


def command_gate_transfer(args: argparse.Namespace) -> int:
    gate_name = normalize_gate_name(args.gate_name)
    reason = args.reason.strip()
    if not reason:
        raise SystemExit("ERROR: --reason cannot be blank")
    with connect(args.db) as con:
        init_schema(con)
        begin_immediate(con)
        row = con.execute("select status from workflow_gates where gate_name = ?", (gate_name,)).fetchone()
        if not row:
            raise SystemExit(f"ERROR: unknown gate: {gate_name}")
        actor_role = require_gate_transfer_authority(con, gate_name, args.role)
        if row["status"] != "pending":
            raise SystemExit(f"ERROR: only pending gate ownership can be transferred: {gate_name} status={row['status']}")
        owners = list(dict.fromkeys(resolve_role(con, role) for role in args.owner_role))
        old_owners = [
            owner["role_id"]
            for owner in con.execute(
                "select role_id from gate_owners where gate_name = ? order by role_id",
                (gate_name,),
            ).fetchall()
        ]
        con.execute("delete from gate_owners where gate_name = ?", (gate_name,))
        for owner in owners:
            con.execute(
                "insert into gate_owners(gate_name, role_id) values (?, ?)",
                (gate_name, owner),
            )
        gate_event(
            con,
            gate_name,
            "ownership_transferred",
            actor_role=actor_role,
            from_status="pending",
            to_status="pending",
            message=f"{','.join(old_owners)}->{','.join(owners)}: {reason}",
        )
        con.commit()
    print(f"Transferred {gate_name} owners={','.join(owners)}")
    return 0


def command_gate_events(args: argparse.Namespace) -> int:
    gate_name = normalize_gate_name(args.gate_name)
    with connect(args.db) as con:
        init_schema(con)
        exists = con.execute("select 1 from workflow_gates where gate_name = ?", (gate_name,)).fetchone()
        if not exists:
            raise SystemExit(f"ERROR: unknown gate: {gate_name}")
        rows = con.execute(
            """
            select created_at, event_type, actor_role, from_status, to_status, message
            from gate_events
            where gate_name = ?
            order by id
            """,
            (gate_name,),
        ).fetchall()
    for row in rows:
        print(
            f"{row['created_at']}\t{row['event_type']}\t{row['actor_role'] or ''}\t"
            f"{row['from_status'] or ''}->{row['to_status'] or ''}\t{row['message'] or ''}"
        )
    return 0


def command_cr_create(args: argparse.Namespace) -> int:
    with connect(args.db) as con:
        init_schema(con)
        begin_immediate(con)
        author_role = resolve_role(con, args.author_role)
        reviewer_role = resolve_role(con, args.reviewer_role)
        reviewer_workstream = (
            normalize_workstream(args.reviewer_workstream)
            if args.reviewer_workstream.strip()
            else None
        )
        require_distinct_cr_roles(author_role, reviewer_role)
        cr_id = next_cr_id(con)
        file_path = args.file_path.strip()
        if not file_path:
            file_path = str(Path(args.dir) / f"{cr_id}-{slugify(args.title)}.md")
        resolved_file_path = project_file_path(con, file_path)
        if resolved_file_path.exists():
            raise SystemExit(f"ERROR: CR file already exists: {file_path}")
        now = utc_now()
        con.execute(
            """
            insert into change_requests(
              cr_id, title, status, author_role, reviewer_role, reviewer_workstream,
              file_path, created_at, updated_at
            )
            values (?, ?, 'draft', ?, ?, ?, ?, ?, ?)
            """,
            (
                cr_id,
                args.title,
                author_role,
                reviewer_role,
                reviewer_workstream,
                file_path,
                now,
                now,
            ),
        )
        cr_event(con, cr_id, "created", actor_role=author_role, to_status="draft")
        sync_cr_file(con, cr_id)
        con.commit()
    print(f"{cr_id}\tdraft\t{resolved_file_path}")
    return 0


def transition_cr_to_submitted(args: argparse.Namespace, resubmit: bool) -> int:
    evidence = getattr(args, "evidence", "") or ""
    with connect(args.db) as con:
        init_schema(con)
        begin_immediate(con)
        actor_role = resolve_role(con, args.role)
        row = con.execute("select * from change_requests where cr_id = ?", (args.cr_id,)).fetchone()
        if not row:
            raise SystemExit(f"ERROR: unknown CR: {args.cr_id}")
        expected = "revision_requested" if resubmit else "draft"
        if row["status"] != expected:
            raise SystemExit(f"ERROR: CR must be {expected}: {args.cr_id} status={row['status']}")
        if row["author_role"] != actor_role:
            raise SystemExit(f"ERROR: CR author role is {row['author_role']}, not {actor_role}")
        require_distinct_cr_roles(row["author_role"], row["reviewer_role"])
        body_hash = cr_body_hash(project_file_path(con, row["file_path"]))
        now = utc_now()
        con.execute(
            """
            update change_requests
            set status = 'submitted', submitted_at = ?, updated_at = ?,
                active_revision_job_id = null, submitted_body_hash = ?,
                approved_body_hash = null, review_claimed_by = null,
                review_started_at = null
            where cr_id = ?
            """,
            (now, now, body_hash, args.cr_id),
        )
        event_type = "resubmitted" if resubmit else "submitted"
        cr_event(con, args.cr_id, event_type, actor_role=actor_role, from_status=row["status"], to_status="submitted", message=evidence)
        sync_cr_file(con, args.cr_id)
        con.commit()
    print(f"{args.cr_id}\tsubmitted")
    return 0


def command_cr_submit(args: argparse.Namespace) -> int:
    return transition_cr_to_submitted(args, resubmit=False)


def command_cr_resubmit(args: argparse.Namespace) -> int:
    return transition_cr_to_submitted(args, resubmit=True)


def assert_reviewer_action(
    con: sqlite3.Connection,
    cr_id: str,
    role: str,
    permission: str,
    claimant: str = "",
) -> tuple[sqlite3.Row, str]:
    actor_role = require_permission(con, role, "cr.review")
    if permission != "cr.review":
        actor_role = require_permission(con, role, permission)
    row = con.execute("select * from change_requests where cr_id = ?", (cr_id,)).fetchone()
    if not row:
        raise SystemExit(f"ERROR: unknown CR: {cr_id}")
    if row["reviewer_role"] != actor_role:
        raise SystemExit(f"ERROR: CR reviewer role is {row['reviewer_role']}, not {actor_role}")
    if row["author_role"] == actor_role:
        raise SystemExit("ERROR: reviewer role cannot review its own CR")
    effective_claimant = claimant or actor_role
    if (
        row["status"] == "submitted"
        and row["review_claimed_by"]
        and row["review_claimed_by"] != effective_claimant
    ):
        raise SystemExit(
            f"ERROR: CR review is claimed by {row['review_claimed_by']}, not {effective_claimant}"
        )
    if row["status"] == "submitted" and row["reviewer_workstream"] and not row["review_claimed_by"]:
        raise SystemExit(
            f"ERROR: workstream-routed CR review must be claimed first: {cr_id}; "
            "run 'baton cr claim-review'"
        )
    return row, actor_role


def command_cr_claim_review(args: argparse.Namespace) -> int:
    with connect(args.db) as con:
        init_schema(con)
        begin_immediate(con)
        role = require_permission(con, args.role, "cr.review")
        claimant = claimed_by_value(args, role)
        row = con.execute(
            "select * from change_requests where cr_id = ?",
            (args.cr_id,),
        ).fetchone()
        if not row:
            raise SystemExit(f"ERROR: unknown CR: {args.cr_id}")
        if row["reviewer_role"] != role:
            raise SystemExit(f"ERROR: CR reviewer role is {row['reviewer_role']}, not {role}")
        if row["status"] != "submitted":
            raise SystemExit(f"ERROR: CR must be submitted: {args.cr_id} status={row['status']}")
        if row["review_claimed_by"]:
            raise SystemExit(
                f"ERROR: CR review is already claimed by {row['review_claimed_by']}: {args.cr_id}"
            )
        require_workstream_assignment(
            con,
            claimant,
            role,
            row["reviewer_workstream"],
        )
        require_agent_capacity(con, claimant, cr_id=args.cr_id)
        now = utc_now()
        changed = con.execute(
            """
            update change_requests
            set review_claimed_by = ?, review_started_at = ?, updated_at = ?
            where cr_id = ? and status = 'submitted' and review_claimed_by is null
            """,
            (claimant, now, now, args.cr_id),
        ).rowcount
        if changed != 1:
            raise SystemExit(
                f"ERROR: review claim failed due to concurrent state change: {args.cr_id}"
            )
        cr_event(
            con,
            args.cr_id,
            "review_claimed",
            actor_role=role,
            from_status="submitted",
            to_status="submitted",
            message=claimant,
        )
        sync_cr_file(con, args.cr_id)
        con.commit()
    print(f"{args.cr_id}\treview_claimed\t{claimant}")
    return 0


def command_cr_release_review(args: argparse.Namespace) -> int:
    reason = args.reason.strip()
    if not reason:
        raise SystemExit("ERROR: --reason cannot be blank")
    with connect(args.db) as con:
        init_schema(con)
        begin_immediate(con)
        role = require_permission(con, args.role, "cr.review")
        claimant = claimed_by_value(args, role)
        row = con.execute(
            "select * from change_requests where cr_id = ?",
            (args.cr_id,),
        ).fetchone()
        if not row:
            raise SystemExit(f"ERROR: unknown CR: {args.cr_id}")
        if row["reviewer_role"] != role:
            raise SystemExit(f"ERROR: CR reviewer role is {row['reviewer_role']}, not {role}")
        if row["status"] != "submitted":
            raise SystemExit(f"ERROR: CR must be submitted: {args.cr_id} status={row['status']}")
        if row["review_claimed_by"] != claimant:
            raise SystemExit(
                f"ERROR: CR review is claimed by {row['review_claimed_by'] or 'nobody'}, not {claimant}"
            )
        now = utc_now()
        con.execute(
            """
            update change_requests
            set review_claimed_by = null, review_started_at = null, updated_at = ?
            where cr_id = ?
            """,
            (now, args.cr_id),
        )
        cr_event(
            con,
            args.cr_id,
            "review_released",
            actor_role=role,
            from_status="submitted",
            to_status="submitted",
            message=f"{claimant}: {reason}",
        )
        sync_cr_file(con, args.cr_id)
        con.commit()
    print(f"{args.cr_id}\treview_released\t{claimant}")
    return 0


def command_cr_request_revision(args: argparse.Namespace) -> int:
    reason = args.reason.strip()
    if not reason:
        raise SystemExit("ERROR: --reason cannot be blank")
    with connect(args.db) as con:
        init_schema(con)
        begin_immediate(con)
        row, actor_role = assert_reviewer_action(
            con,
            args.cr_id,
            args.role,
            "cr.request_revision",
            optional_agent_id(args, "claimed_by"),
        )
        if row["status"] != "submitted":
            raise SystemExit(f"ERROR: CR must be submitted: {args.cr_id} status={row['status']}")
        if row["active_revision_job_id"]:
            raise SystemExit(f"ERROR: CR already has active revision handoff: {row['active_revision_job_id']}")
        assign_role = resolve_role(con, args.assign_back or row["author_role"])
        if assign_role != row["author_role"]:
            raise SystemExit(
                f"ERROR: revision handoff must return to CR author role {row['author_role']}; "
                f"delegated resubmission by {assign_role} is not supported"
            )
        job_id, job_status, _, _ = create_handoff_job(
            con,
            args.title or f"Revise rejected CR: {row['title']}",
            assign_role,
            f"cr:{args.cr_id}",
            f"Address CR revision feedback for {args.cr_id}: {reason}",
            "CR Markdown body is updated and baton cr resubmit is completed.",
            [],
            [],
            actor_role,
        )
        now = utc_now()
        con.execute(
            """
            update change_requests
            set status = 'revision_requested',
                revision_count = revision_count + 1,
                active_revision_job_id = ?,
                updated_at = ?
            where cr_id = ?
            """,
            (job_id, now, args.cr_id),
        )
        con.execute(
            "insert into cr_handoffs(cr_id, job_id, kind, created_at) values (?, ?, 'revision', ?)",
            (args.cr_id, job_id, now),
        )
        cr_event(con, args.cr_id, "revision_requested", actor_role=actor_role, from_status="submitted", to_status="revision_requested", message=reason)
        sync_cr_file(con, args.cr_id)
        con.commit()
    print(f"{args.cr_id}\trevision_requested\t{job_id}\t{job_status}\t{assign_role}")
    return 0


def command_cr_approve(args: argparse.Namespace) -> int:
    with connect(args.db) as con:
        init_schema(con)
        begin_immediate(con)
        row, actor_role = assert_reviewer_action(
            con,
            args.cr_id,
            args.role,
            "cr.approve",
            optional_agent_id(args, "claimed_by"),
        )
        if row["status"] != "submitted":
            raise SystemExit(f"ERROR: CR must be submitted: {args.cr_id} status={row['status']}")
        current_hash = cr_body_hash(project_file_path(con, row["file_path"]))
        submitted_hash = str(row["submitted_body_hash"] or "")
        if submitted_hash and current_hash != submitted_hash:
            raise MigrationError(
                f"CR {args.cr_id} body changed after submission; "
                "request revision so the author can resubmit the reviewed body"
            )
        now = utc_now()
        con.execute(
            """
            update change_requests
            set status = 'approved', approved_at = ?, updated_at = ?,
                submitted_body_hash = ?, approved_body_hash = ?
            where cr_id = ?
            """,
            (now, now, submitted_hash or current_hash, current_hash, args.cr_id),
        )
        cr_event(con, args.cr_id, "approved", actor_role=actor_role, from_status="submitted", to_status="approved", message=args.evidence)
        sync_cr_file(con, args.cr_id)
        con.commit()
    print(f"{args.cr_id}\tapproved")
    return 0


def command_cr_seal(args: argparse.Namespace) -> int:
    evidence = args.evidence.strip()
    if not evidence:
        raise SystemExit("ERROR: --evidence cannot be blank")
    with connect(args.db) as con:
        init_schema(con)
        begin_immediate(con)
        row, actor_role = assert_reviewer_action(
            con,
            args.cr_id,
            args.role,
            "cr.review",
        )
        if row["status"] not in {"approved", "implemented"}:
            raise SystemExit(
                f"ERROR: CR must be approved or implemented: {args.cr_id} status={row['status']}"
            )
        if row["approved_body_hash"]:
            require_cr_body_hash(con, row, "approved_body_hash", "approval")
            con.rollback()
            print(f"{args.cr_id}\talready-sealed\t{row['approved_body_hash']}")
            return 0
        body_hash = cr_body_hash(project_file_path(con, row["file_path"]))
        now = utc_now()
        con.execute(
            """
            update change_requests
            set submitted_body_hash = coalesce(submitted_body_hash, ?),
                approved_body_hash = ?, updated_at = ?
            where cr_id = ?
            """,
            (body_hash, body_hash, now, args.cr_id),
        )
        cr_event(
            con,
            args.cr_id,
            "body_sealed",
            actor_role=actor_role,
            from_status=row["status"],
            to_status=row["status"],
            message=evidence,
        )
        sync_cr_file(con, args.cr_id)
        con.commit()
    print(f"{args.cr_id}\tsealed\t{body_hash}")
    return 0


def command_cr_reject(args: argparse.Namespace) -> int:
    reason = args.reason.strip()
    if not reason:
        raise SystemExit("ERROR: --reason cannot be blank")
    with connect(args.db) as con:
        init_schema(con)
        begin_immediate(con)
        row, actor_role = assert_reviewer_action(
            con,
            args.cr_id,
            args.role,
            "cr.reject",
            optional_agent_id(args, "claimed_by"),
        )
        if row["status"] != "submitted":
            raise SystemExit(f"ERROR: CR must be submitted: {args.cr_id} status={row['status']}")
        now = utc_now()
        con.execute(
            "update change_requests set status = 'rejected', rejected_at = ?, updated_at = ? where cr_id = ?",
            (now, now, args.cr_id),
        )
        cr_event(con, args.cr_id, "rejected", actor_role=actor_role, from_status="submitted", to_status="rejected", message=reason)
        sync_cr_file(con, args.cr_id)
        con.commit()
    print(f"{args.cr_id}\trejected")
    return 0


def command_cr_reassign_reviewer(args: argparse.Namespace) -> int:
    reason = args.reason.strip()
    if not reason:
        raise SystemExit("ERROR: --reason cannot be blank")
    with connect(args.db) as con:
        init_schema(con)
        begin_immediate(con)
        actor_role = require_permission(con, args.role, "cr.admin")
        new_reviewer_role = resolve_role(con, args.reviewer_role)
        row = con.execute("select * from change_requests where cr_id = ?", (args.cr_id,)).fetchone()
        if not row:
            raise SystemExit(f"ERROR: unknown CR: {args.cr_id}")
        if row["status"] in {"approved", "rejected", "implemented", "superseded", "cancelled"}:
            raise SystemExit(f"ERROR: CR reviewer cannot be reassigned after terminal review: {args.cr_id} status={row['status']}")
        require_distinct_cr_roles(row["author_role"], new_reviewer_role)
        old_reviewer_role = row["reviewer_role"]
        new_workstream = (
            normalize_workstream(args.reviewer_workstream)
            if args.reviewer_workstream.strip()
            else None
        )
        old_workstream = row["reviewer_workstream"]
        if old_reviewer_role == new_reviewer_role and old_workstream == new_workstream:
            raise SystemExit(
                f"ERROR: CR reviewer route is already role={new_reviewer_role} "
                f"workstream={new_workstream or '*'}"
            )
        now = utc_now()
        con.execute(
            """
            update change_requests
            set reviewer_role = ?, reviewer_workstream = ?, review_claimed_by = null,
                review_started_at = null, updated_at = ?
            where cr_id = ?
            """,
            (new_reviewer_role, new_workstream, now, args.cr_id),
        )
        cr_event(
            con,
            args.cr_id,
            "reviewer_reassigned",
            actor_role=actor_role,
            from_status=row["status"],
            to_status=row["status"],
            message=(
                f"{old_reviewer_role}/{old_workstream or '*'}->"
                f"{new_reviewer_role}/{new_workstream or '*'}: {reason}"
            ),
        )
        sync_cr_file(con, args.cr_id)
        con.commit()
    print(
        f"{args.cr_id}\treviewer_reassigned\t"
        f"{old_reviewer_role}/{old_workstream or '*'}\t"
        f"{new_reviewer_role}/{new_workstream or '*'}"
    )
    return 0


def command_cr_cancel(args: argparse.Namespace) -> int:
    reason = args.reason.strip()
    if not reason:
        raise SystemExit("ERROR: --reason cannot be blank")
    with connect(args.db) as con:
        init_schema(con)
        begin_immediate(con)
        actor_role = require_permission(con, args.role, "cr.admin")
        row = con.execute("select * from change_requests where cr_id = ?", (args.cr_id,)).fetchone()
        if not row:
            raise SystemExit(f"ERROR: unknown CR: {args.cr_id}")
        if row["status"] == "cancelled":
            raise SystemExit(f"ERROR: CR is already cancelled: {args.cr_id}")
        if row["status"] == "implemented":
            raise SystemExit(f"ERROR: implemented CR cannot be cancelled: {args.cr_id}")
        if row["status"] == "superseded":
            raise SystemExit(f"ERROR: superseded CR cannot be cancelled: {args.cr_id}")
        failure = con.execute(
            """
            select id, job_id
            from handoff_failure_reviews
            where cr_id = ? and resolution is null
            """,
            (args.cr_id,),
        ).fetchone()
        requested_jobs, cancelled_jobs = cancel_linked_implementation_handoffs(
            con,
            args.cr_id,
            actor_role,
            f"CR {args.cr_id} cancelled: {reason}",
        )
        now = utc_now()
        con.execute(
            """
            update change_requests
            set status = 'cancelled', active_revision_job_id = null, updated_at = ?
            where cr_id = ?
            """,
            (now, args.cr_id),
        )
        active_revision_job_id = row["active_revision_job_id"]
        if active_revision_job_id:
            job = con.execute(
                "select status from handoff_jobs where job_id = ?",
                (active_revision_job_id,),
            ).fetchone()
            if job and job["status"] not in {"finished", "cancelled"}:
                cancel_handoff_with_dependents(
                    con,
                    active_revision_job_id,
                    actor_role,
                    f"CR {args.cr_id} cancelled: {reason}",
                )
        if failure:
            failed_job = con.execute(
                "select status from handoff_jobs where job_id = ?",
                (failure["job_id"],),
            ).fetchone()
            if failed_job and failed_job["status"] == "failed":
                cancel_handoff_with_dependents(
                    con,
                    failure["job_id"],
                    actor_role,
                    f"Failure CR {args.cr_id} cancelled: {reason}",
                )
            con.execute(
                """
                update handoff_failure_reviews
                set resolution = 'cancelled', resolved_by_role = ?, resolved_at = ?, resolution_message = ?
                where id = ?
                """,
                (actor_role, now, reason, failure["id"]),
            )
        cr_event(
            con,
            args.cr_id,
            "cancelled",
            actor_role=actor_role,
            from_status=row["status"],
            to_status="cancelled",
            message=reason,
        )
        sync_cr_file(con, args.cr_id, allow_body_mismatch=True)
        con.commit()
    print(
        f"{args.cr_id}\tcancelled\t"
        f"cancel_requested={len(requested_jobs)}\tcancelled_jobs={len(cancelled_jobs)}"
    )
    return 0


def command_cr_supersede(args: argparse.Namespace) -> int:
    reason = args.reason.strip()
    if not reason:
        raise SystemExit("ERROR: --reason cannot be blank")
    if args.by_cr and args.cr_id == args.by_cr:
        raise SystemExit("ERROR: a CR cannot supersede itself")
    with connect(args.db) as con:
        init_schema(con)
        begin_immediate(con)
        actor_role = require_permission(con, args.role, "cr.admin")
        old = con.execute(
            "select * from change_requests where cr_id = ?",
            (args.cr_id,),
        ).fetchone()
        replacement = None
        replacement_ref = args.by_source_ref.strip()
        if args.by_cr:
            replacement = con.execute(
                "select * from change_requests where cr_id = ?",
                (args.by_cr,),
            ).fetchone()
        if not old:
            raise SystemExit(f"ERROR: unknown CR: {args.cr_id}")
        if args.by_cr and not replacement:
            raise SystemExit(f"ERROR: unknown replacement CR: {args.by_cr}")
        if old["status"] != "approved":
            raise SystemExit(
                f"ERROR: superseded CR must be approved: {args.cr_id} status={old['status']}"
            )
        if replacement and replacement["status"] != "approved":
            raise SystemExit(
                f"ERROR: replacement CR must be approved: {args.by_cr} status={replacement['status']}"
            )
        require_cr_body_hash(con, old, "approved_body_hash", "approval")
        if replacement:
            require_cr_body_hash(con, replacement, "approved_body_hash", "approval")
            replacement_label = args.by_cr
        else:
            require_permission(con, actor_role, "handoff.register")
            if not replacement_ref:
                raise SystemExit("ERROR: --by-source-ref cannot be blank")
            replacement_label = replacement_ref
        requested_jobs, cancelled_jobs = cancel_linked_implementation_handoffs(
            con,
            args.cr_id,
            actor_role,
            f"CR {args.cr_id} superseded by {replacement_label}: {reason}",
        )
        now = utc_now()
        con.execute(
            """
            update change_requests
            set status = 'superseded', superseded_by_cr_id = ?, superseded_by_ref = ?, updated_at = ?
            where cr_id = ?
            """,
            (args.by_cr or None, replacement_ref or None, now, args.cr_id),
        )
        cr_event(
            con,
            args.cr_id,
            "superseded",
            actor_role=actor_role,
            from_status="approved",
            to_status="superseded",
            message=f"{replacement_label}: {reason}",
        )
        if replacement:
            cr_event(
                con,
                args.by_cr,
                "supersedes",
                actor_role=actor_role,
                from_status="approved",
                to_status="approved",
                message=f"{args.cr_id}: {reason}",
            )
        sync_cr_file(con, args.cr_id)
        con.commit()
    print(
        f"{args.cr_id}\tsuperseded_by\t{replacement_label}\t"
        f"cancel_requested={len(requested_jobs)}\tcancelled_jobs={len(cancelled_jobs)}"
    )
    return 0


def command_cr_create_handoff(args: argparse.Namespace) -> int:
    with connect(args.db) as con:
        init_schema(con)
        begin_immediate(con)
        row, actor_role = assert_reviewer_action(
            con,
            args.cr_id,
            args.by_role,
            "cr.assign_implementation",
        )
        if row["status"] != "approved":
            raise SystemExit(f"ERROR: CR must be approved: {args.cr_id} status={row['status']}")
        require_cr_body_hash(con, row, "approved_body_hash", "approval")
        job_id, status, role, warnings = create_handoff_job(
            con,
            args.title,
            args.role,
            f"cr:{args.cr_id}",
            args.objective,
            args.exit_criteria,
            args.depends_on or [],
            args.depends_on_gate or [],
            actor_role,
            args.workstream,
        )
        con.execute(
            "insert into cr_handoffs(cr_id, job_id, kind, created_at) values (?, ?, 'implementation', ?)",
            (args.cr_id, job_id, utc_now()),
        )
        cr_event(con, args.cr_id, "implementation_handoff_created", actor_role=actor_role, message=job_id)
        con.commit()
    for warning in warnings:
        print(f"WARNING: {warning}", file=sys.stderr)
    print(f"{args.cr_id}\t{job_id}\t{status}\t{role}")
    return 0


def effective_commit_resolution(con: sqlite3.Connection, job: sqlite3.Row) -> str:
    correction = con.execute(
        """
        select commit_resolution
        from handoff_evidence_corrections
        where job_id = ?
        order by id desc
        limit 1
        """,
        (job["job_id"],),
    ).fetchone()
    return str(
        correction["commit_resolution"]
        if correction
        else job["related_commit_resolution"]
    )


def cr_handoff_inspection(
    con: sqlite3.Connection, cr_id: str
) -> tuple[list[sqlite3.Row], list[sqlite3.Row]]:
    linked = con.execute(
        """
        select ch.kind, h.job_id, h.status, h.completion_outcome,
               h.completion_blocking,
               coalesce(
                 (select ec.commit_resolution
                  from handoff_evidence_corrections ec
                  where ec.job_id = h.job_id
                  order by ec.id desc limit 1),
                 h.related_commit_resolution
               ) as commit_resolution
        from cr_handoffs ch
        join handoff_jobs h on h.job_id = ch.job_id
        where ch.cr_id = ?
        order by ch.kind, h.created_at, h.job_id
        """,
        (cr_id,),
    ).fetchall()
    candidates = con.execute(
        """
        select h.job_id, h.status, h.completion_outcome,
               h.completion_blocking,
               exists (
                 select 1 from cr_handoffs other
                 where other.job_id = h.job_id
                   and other.kind = 'implementation'
               ) as implementation_linked_elsewhere,
               coalesce(
                 (select ec.commit_resolution
                  from handoff_evidence_corrections ec
                  where ec.job_id = h.job_id
                  order by ec.id desc limit 1),
                 h.related_commit_resolution
               ) as commit_resolution
        from handoff_jobs h
        where h.source_ref = ?
          and not exists (
            select 1 from cr_handoffs ch
            where ch.cr_id = ? and ch.job_id = h.job_id
          )
        order by h.created_at, h.job_id
        """,
        (f"cr:{cr_id}", cr_id),
    ).fetchall()
    return linked, candidates


def implementation_adoption_candidates(
    cr_status: str,
    linked: list[sqlite3.Row],
    related: list[sqlite3.Row],
) -> list[sqlite3.Row]:
    if cr_status != "approved" or any(
        item["kind"] == "implementation" for item in linked
    ):
        return []
    return [
        item
        for item in related
        if item["status"] == "finished"
        and not item["completion_blocking"]
        and item["commit_resolution"] not in {"legacy_unchecked", "unresolved"}
        and not item["implementation_linked_elsewhere"]
    ]


def print_cr_handoff_inspection(
    cr_status: str,
    linked: list[sqlite3.Row],
    related: list[sqlite3.Row],
    include_related: bool,
) -> None:
    for item in linked:
        print(
            f"{item['kind']}_handoff: {item['job_id']} status={item['status']} "
            f"outcome={item['completion_outcome']} "
            f"blocking={item['completion_blocking']} "
            f"commit_resolution={item['commit_resolution']}"
        )
    if include_related:
        label = "related_handoff_unlinked"
        output = related
    else:
        label = "implementation_adoption_candidate"
        output = implementation_adoption_candidates(cr_status, linked, related)
    for item in output:
        print(
            f"{label}: {item['job_id']} status={item['status']} "
            f"outcome={item['completion_outcome']} "
            f"blocking={item['completion_blocking']} "
            f"commit_resolution={item['commit_resolution']}"
        )


def command_cr_link_handoff(args: argparse.Namespace) -> int:
    reason = args.reason.strip()
    if not reason:
        raise SystemExit("ERROR: --reason cannot be blank")
    with connect(args.db) as con:
        init_schema(con)
        begin_immediate(con)
        cr, actor_role = assert_reviewer_action(
            con,
            args.cr_id,
            args.role,
            "cr.assign_implementation",
        )
        if cr["status"] != "approved":
            raise SystemExit(
                f"ERROR: CR must be approved: {args.cr_id} status={cr['status']}"
            )
        require_cr_body_hash(con, cr, "approved_body_hash", "approval")
        job = con.execute(
            "select * from handoff_jobs where job_id = ?",
            (args.job_id,),
        ).fetchone()
        if not job:
            raise SystemExit(f"ERROR: unknown job: {args.job_id}")
        existing = con.execute(
            "select kind from cr_handoffs where cr_id = ? and job_id = ?",
            (args.cr_id, args.job_id),
        ).fetchone()
        if existing:
            if existing["kind"] != "implementation":
                raise SystemExit(
                    f"ERROR: handoff is already linked to this CR as {existing['kind']}: "
                    f"{args.job_id}"
                )
            con.rollback()
            print(f"{args.cr_id}\t{args.job_id}\talready-linked\timplementation")
            return 0
        other_crs = [
            row["cr_id"]
            for row in con.execute(
                """
                select cr_id from cr_handoffs
                where job_id = ? and cr_id != ? and kind = 'implementation'
                order by cr_id
                """,
                (args.job_id, args.cr_id),
            ).fetchall()
        ]
        if other_crs:
            raise SystemExit(
                f"ERROR: handoff is already linked as implementation to another CR: "
                f"{', '.join(other_crs)}"
            )
        if job["status"] != "finished":
            raise SystemExit(
                f"ERROR: linked implementation handoff must be finished: "
                f"{args.job_id} status={job['status']}"
            )
        expected_source = f"cr:{args.cr_id}"
        if job["source_ref"] != expected_source:
            raise SystemExit(
                f"ERROR: handoff source_ref must be exactly {expected_source}: "
                f"{args.job_id} source_ref={job['source_ref'] or ''}"
            )
        if job["completion_blocking"]:
            raise SystemExit(
                f"ERROR: blocking completion cannot be linked as accepted implementation: "
                f"{args.job_id} outcome={job['completion_outcome']}"
            )
        resolution = effective_commit_resolution(con, job)
        if resolution in {"legacy_unchecked", "unresolved"}:
            raise SystemExit(
                f"ERROR: implementation commit evidence is {resolution}: {args.job_id}; "
                "verify it with 'baton handoff evidence-correct' before linking"
            )
        con.execute(
            """
            insert into cr_handoffs(cr_id, job_id, kind, created_at)
            values (?, ?, 'implementation', ?)
            """,
            (args.cr_id, args.job_id, utc_now()),
        )
        cr_event(
            con,
            args.cr_id,
            "implementation_handoff_linked",
            actor_role=actor_role,
            from_status="approved",
            to_status="approved",
            message=(
                f"job={args.job_id} mode=existing_finished_handoff "
                f"commit_resolution={resolution}; {reason}"
            ),
        )
        con.commit()
    print(f"{args.cr_id}\t{args.job_id}\tlinked\timplementation")
    return 0


def command_cr_supersede_handoff(args: argparse.Namespace) -> int:
    reason = args.reason.strip()
    if not reason:
        raise SystemExit("ERROR: --reason cannot be blank")
    if args.retired_job_id == args.replacement_job_id:
        raise SystemExit("ERROR: retired and replacement handoffs must be different")
    with connect(args.db) as con:
        init_schema(con)
        begin_immediate(con)
        row, actor_role = assert_reviewer_action(
            con,
            args.cr_id,
            args.role,
            "cr.assign_implementation",
        )
        if row["status"] != "approved":
            raise SystemExit(f"ERROR: CR must be approved: {args.cr_id} status={row['status']}")
        require_cr_body_hash(con, row, "approved_body_hash", "approval")
        linked = {
            item["job_id"]: item
            for item in con.execute(
                """
                select h.job_id, h.status
                from cr_handoffs ch
                join handoff_jobs h on h.job_id = ch.job_id
                where ch.cr_id = ? and ch.kind = 'implementation'
                  and h.job_id in (?, ?)
                """,
                (args.cr_id, args.retired_job_id, args.replacement_job_id),
            ).fetchall()
        }
        missing = [
            job_id
            for job_id in (args.retired_job_id, args.replacement_job_id)
            if job_id not in linked
        ]
        if missing:
            raise SystemExit(
                "ERROR: handoff is not linked to this CR as implementation: "
                + ", ".join(missing)
            )
        if linked[args.retired_job_id]["status"] != "cancelled":
            raise SystemExit(
                f"ERROR: retired implementation handoff must be cancelled: "
                f"{args.retired_job_id} status={linked[args.retired_job_id]['status']}"
            )
        replacement_status = linked[args.replacement_job_id]["status"]
        if replacement_status in {"cancelled", "failed"}:
            raise SystemExit(
                f"ERROR: replacement implementation handoff is not viable: "
                f"{args.replacement_job_id} status={replacement_status}"
            )
        existing = con.execute(
            """
            select replacement_job_id
            from cr_handoff_supersessions
            where cr_id = ? and retired_job_id = ?
            """,
            (args.cr_id, args.retired_job_id),
        ).fetchone()
        if existing:
            raise SystemExit(
                f"ERROR: implementation handoff {args.retired_job_id} is already superseded by "
                f"{existing['replacement_job_id']}"
            )
        now = utc_now()
        con.execute(
            """
            insert into cr_handoff_supersessions(
              cr_id, retired_job_id, replacement_job_id, actor_role, reason, created_at
            ) values (?, ?, ?, ?, ?, ?)
            """,
            (
                args.cr_id,
                args.retired_job_id,
                args.replacement_job_id,
                actor_role,
                reason,
                now,
            ),
        )
        cr_event(
            con,
            args.cr_id,
            "implementation_handoff_superseded",
            actor_role=actor_role,
            from_status="approved",
            to_status="approved",
            message=(
                f"{args.retired_job_id} -> {args.replacement_job_id}: {reason}"
            ),
        )
        con.commit()
    print(
        f"{args.cr_id}\t{args.retired_job_id}\tsuperseded_by\t"
        f"{args.replacement_job_id}"
    )
    return 0


def unresolved_cr_implementation_handoffs(
    con: sqlite3.Connection,
    cr_id: str,
) -> tuple[dict[str, str], list[tuple[str, str]]]:
    implementation_rows = {
        row["job_id"]: row
        for row in con.execute(
            """
            select h.job_id, h.status, h.completion_outcome, h.completion_blocking
            from cr_handoffs ch
            join handoff_jobs h on h.job_id = ch.job_id
            where ch.cr_id = ? and ch.kind = 'implementation'
            """,
            (cr_id,),
        ).fetchall()
    }
    statuses = {
        job_id: str(row["status"])
        for job_id, row in implementation_rows.items()
    }
    replacements = {
        row["retired_job_id"]: row["replacement_job_id"]
        for row in con.execute(
            """
            select retired_job_id, replacement_job_id
            from cr_handoff_supersessions
            where cr_id = ?
            """,
            (cr_id,),
        ).fetchall()
    }

    def is_resolved(job_id: str) -> bool:
        visited: set[str] = set()
        current = job_id
        while current not in visited:
            visited.add(current)
            row = implementation_rows.get(current)
            status = str(row["status"]) if row else None
            if status == "finished":
                return not bool(row["completion_blocking"])
            if status != "cancelled":
                return False
            current = replacements.get(current, "")
            if not current:
                return False
        return False

    unresolved = [
        (
            job_id,
            (
                f"finished:blocking:{implementation_rows[job_id]['completion_outcome']}"
                if status == "finished"
                and implementation_rows[job_id]["completion_blocking"]
                else status
            ),
        )
        for job_id, status in sorted(statuses.items())
        if not is_resolved(job_id)
    ]
    return statuses, unresolved


def command_cr_mark_implemented(args: argparse.Namespace) -> int:
    evidence = args.evidence.strip()
    if not evidence:
        raise SystemExit("ERROR: --evidence cannot be blank")
    with connect(args.db) as con:
        init_schema(con)
        begin_immediate(con)
        row, actor_role = assert_reviewer_action(
            con,
            args.cr_id,
            args.role,
            "cr.mark_implemented",
        )
        if row["status"] != "approved":
            raise SystemExit(f"ERROR: CR must be approved: {args.cr_id} status={row['status']}")
        require_cr_body_hash(con, row, "approved_body_hash", "approval")
        implementations, unresolved = unresolved_cr_implementation_handoffs(con, args.cr_id)
        if not implementations:
            linked, related = cr_handoff_inspection(con, args.cr_id)
            candidates = implementation_adoption_candidates(
                str(row["status"]), linked, related
            )
            candidate_text = ", ".join(
                f"{item['job_id']}:{item['status']}"
                for item in candidates
            )
            hint = (
                f"; eligible adoption candidates: {candidate_text}; "
                f"run 'baton cr link-handoff {args.cr_id} <job-id> "
                "--role <reviewer-role> --reason <reason>'"
                if candidate_text
                else ""
            )
            raise SystemExit(
                f"ERROR: CR has no implementation handoffs: {args.cr_id}{hint}"
            )
        if unresolved:
            details = ", ".join(f"{job_id}:{status}" for job_id, status in unresolved)
            raise SystemExit(
                f"ERROR: implementation handoffs are not closure-ready: {details}"
            )
        now = utc_now()
        con.execute(
            "update change_requests set status = 'implemented', implemented_at = ?, updated_at = ? where cr_id = ?",
            (now, now, args.cr_id),
        )
        cr_event(con, args.cr_id, "implemented", actor_role=actor_role, from_status="approved", to_status="implemented", message=evidence)
        sync_cr_file(con, args.cr_id)
        con.commit()
    print(f"{args.cr_id}\timplemented")
    return 0


def command_cr_list(args: argparse.Namespace) -> int:
    if args.limit <= 0:
        raise SystemExit("ERROR: --limit must be greater than zero")
    conditions: list[str] = []
    params: list[object] = []
    if args.status:
        conditions.append("status = ?")
        params.append(args.status)
    if args.reviewer_workstream:
        conditions.append("reviewer_workstream = ?")
        params.append(normalize_workstream(args.reviewer_workstream))
    if args.claimed_by:
        conditions.append("status = 'submitted'")
        if args.claimed_by == "unassigned":
            conditions.append("review_claimed_by is null")
        else:
            conditions.append("review_claimed_by = ?")
            params.append(args.claimed_by.strip())
    with connect(args.db) as con:
        init_schema(con)
        if args.reviewer_role:
            conditions.append("reviewer_role = ?")
            params.append(resolve_role(con, args.reviewer_role))
        where = f"where {' and '.join(conditions)}" if conditions else ""
        rows = con.execute(
            f"""
            select *
            from change_requests
            {where}
            order by created_at, cr_id
            """,
            params,
        ).fetchall()
        payload: list[dict[str, object]] = []
        for row in rows:
            integrity, _ = cr_body_integrity(con, row)
            if args.body_integrity and integrity != args.body_integrity:
                continue
            last_review_claimant = str(row["review_claimed_by"] or "")
            active_claimant = active_review_claimant(row)
            payload.append(
                {
                    "cr_id": row["cr_id"],
                    "status": row["status"],
                    "title": row["title"],
                    "author_role": row["author_role"],
                    "reviewer_role": row["reviewer_role"],
                    "reviewer_workstream": row["reviewer_workstream"] or "",
                    "review_claimed_by": last_review_claimant,
                    "active_review_claimed_by": active_claimant,
                    "last_review_claimed_by": last_review_claimant,
                    "body_integrity": integrity,
                    "file_path": str(project_file_path(con, row["file_path"])),
                }
            )
            if len(payload) >= args.limit:
                break
    if args.format == "json":
        print(json.dumps(payload, indent=2, ensure_ascii=False))
        return 0
    if not payload:
        print("No change requests.")
        return 0
    for item in payload:
        print(
            f"{item['cr_id']}\t{item['status']}\t{item['reviewer_role']}\t"
            f"{item['reviewer_workstream']}\t"
            f"{item['active_review_claimed_by'] or 'unassigned'}\t"
            f"{item['body_integrity']}\t{item['title']}"
        )
    return 0


def command_cr_status(args: argparse.Namespace) -> int:
    with connect(args.db) as con:
        init_schema(con)
        row = con.execute("select * from change_requests where cr_id = ?", (args.cr_id,)).fetchone()
        if not row:
            raise SystemExit(f"ERROR: unknown CR: {args.cr_id}")
        resolved_path = project_file_path(con, row["file_path"])
        integrity, expected_hash = cr_body_integrity(con, row)
        supersessions = con.execute(
            """
            select retired_job_id, replacement_job_id, actor_role, reason, created_at
            from cr_handoff_supersessions
            where cr_id = ?
            order by created_at, retired_job_id
            """,
            (args.cr_id,),
        ).fetchall()
        linked, candidates = cr_handoff_inspection(con, args.cr_id)
    print(f"{row['cr_id']}\t{row['status']}\t{row['title']}\t{resolved_path}")
    print(f"author_role: {row['author_role']}")
    print(f"reviewer_role: {row['reviewer_role']}")
    print(f"reviewer_workstream: {row['reviewer_workstream'] or ''}")
    print(f"active_review_claimed_by: {active_review_claimant(row)}")
    print(f"last_review_claimed_by: {row['review_claimed_by'] or ''}")
    print(f"revision_count: {row['revision_count']}")
    print(f"body_integrity: {integrity}")
    if expected_hash:
        print(f"body_hash: {expected_hash}")
    if row["active_revision_job_id"]:
        print(f"active_revision_job_id: {row['active_revision_job_id']}")
    if row["superseded_by_cr_id"]:
        print(f"superseded_by_cr_id: {row['superseded_by_cr_id']}")
    if row["superseded_by_ref"]:
        print(f"superseded_by_ref: {row['superseded_by_ref']}")
    for item in supersessions:
        print(
            f"implementation_supersession: {item['retired_job_id']} -> "
            f"{item['replacement_job_id']} role={item['actor_role']} "
            f"at={item['created_at']} reason={item['reason']}"
        )
    print_cr_handoff_inspection(
        str(row["status"]), linked, candidates, args.include_related_handoffs
    )
    return 0


def command_cr_show(args: argparse.Namespace) -> int:
    with connect(args.db) as con:
        init_schema(con)
        row = con.execute("select * from change_requests where cr_id = ?", (args.cr_id,)).fetchone()
        if not row:
            raise SystemExit(f"ERROR: unknown CR: {args.cr_id}")
        path = project_file_path(con, row["file_path"])
        body, current_hash = cr_body_snapshot(path)
        integrity, expected_hash = cr_body_integrity(con, row, current_hash)
        supersessions = con.execute(
            """
            select retired_job_id, replacement_job_id, actor_role, reason, created_at
            from cr_handoff_supersessions
            where cr_id = ?
            order by created_at, retired_job_id
            """,
            (args.cr_id,),
        ).fetchall()
        linked, candidates = cr_handoff_inspection(con, args.cr_id)
    print(f"cr_id: {row['cr_id']}")
    print(f"status: {row['status']}")
    print(f"title: {row['title']}")
    print(f"author_role: {row['author_role']}")
    print(f"reviewer_role: {row['reviewer_role']}")
    print(f"reviewer_workstream: {row['reviewer_workstream'] or ''}")
    print(f"active_review_claimed_by: {active_review_claimant(row)}")
    print(f"last_review_claimed_by: {row['review_claimed_by'] or ''}")
    if row["superseded_by_cr_id"]:
        print(f"superseded_by_cr_id: {row['superseded_by_cr_id']}")
    if row["superseded_by_ref"]:
        print(f"superseded_by_ref: {row['superseded_by_ref']}")
    for item in supersessions:
        print(
            f"implementation_supersession: {item['retired_job_id']} -> "
            f"{item['replacement_job_id']} role={item['actor_role']} "
            f"at={item['created_at']} reason={item['reason']}"
        )
    print_cr_handoff_inspection(
        str(row["status"]), linked, candidates, args.include_related_handoffs
    )
    print(f"file_path: {path}")
    print(f"body_integrity: {integrity}")
    if expected_hash:
        print(f"body_hash: {expected_hash}")
    print("--- body ---")
    print(body, end="" if body.endswith("\n") else "\n")
    return 0


def command_cr_sync(args: argparse.Namespace) -> int:
    with connect(args.db) as con:
        init_schema(con)
        sync_cr_file(con, args.cr_id)
    print(f"{args.cr_id}\tsynced")
    return 0


def command_cr_events(args: argparse.Namespace) -> int:
    with connect(args.db) as con:
        init_schema(con)
        rows = con.execute(
            """
            select created_at, event_type, actor_role, from_status, to_status, message
            from cr_events
            where cr_id = ?
            order by id
            """,
            (args.cr_id,),
        ).fetchall()
    for row in rows:
        print(
            f"{row['created_at']}\t{row['event_type']}\t{row['actor_role'] or ''}\t"
            f"{row['from_status'] or ''}->{row['to_status'] or ''}\t{row['message'] or ''}"
        )
    return 0


def active_agent_ownership(con: sqlite3.Connection, agent_id: str) -> tuple[str, str]:
    if not agent_id:
        return "", ""
    handoff = con.execute(
        """
        select job_id
        from handoff_jobs
        where claimed_by = ? and status in ('in_progress', 'cancel_requested')
        order by created_at, job_id
        limit 1
        """,
        (agent_id,),
    ).fetchone()
    review = con.execute(
        """
        select cr_id
        from change_requests
        where review_claimed_by = ? and status = 'submitted'
        order by submitted_at, created_at, cr_id
        limit 1
        """,
        (agent_id,),
    ).fetchone()
    return (
        str(handoff["job_id"]) if handoff else "",
        str(review["cr_id"]) if review else "",
    )


def warn_agent_session_role(con: sqlite3.Connection, role: str, agent_id: str) -> str:
    if not agent_id:
        return ""
    session = active_agent_session(con, agent_id)
    session_role = str(session["role_id"]) if session else ""
    if session_role and session_role != role:
        print(
            f"WARNING: resolved agent {agent_id} has active session role {session_role}, "
            f"but requested role is {role}; continuing because Baton permits explicit multi-role use",
            file=sys.stderr,
        )
    return session_role


def explain_agent_eligibility(
    con: sqlite3.Connection,
    role: str,
    agent_id: str,
    *,
    include_reviews: bool = False,
) -> None:
    session = active_agent_session(con, agent_id) if agent_id else None
    session_role = str(session["role_id"]) if session else ""
    handoffs = con.execute(
        """
        select job_id, workstream
        from handoff_jobs
        where status = 'open' and target_role = ?
        order by created_at, job_id
        """,
        (role,),
    ).fetchall()
    eligible_handoffs: list[str] = []
    excluded_handoffs: list[tuple[str, str]] = []
    for row in handoffs:
        workstream = str(row["workstream"] or "")
        if not workstream or agent_has_workstream(con, agent_id, role, workstream):
            eligible_handoffs.append(str(row["job_id"]))
        else:
            excluded_handoffs.append((str(row["job_id"]), workstream))
    active_handoff, active_review = active_agent_ownership(con, agent_id)
    print(
        f"eligibility: agent={agent_id or '<unset>'} requested_role={role} "
        f"session_role={session_role or '<none>'} ready_handoffs={len(handoffs)} "
        f"eligible_handoffs={len(eligible_handoffs)}",
        file=sys.stderr,
    )
    for job_id, workstream in excluded_handoffs:
        print(
            f"excluded_handoff: {job_id} reason=missing_workstream_registration "
            f"workstream={workstream}",
            file=sys.stderr,
        )
    if active_handoff:
        print(f"active_ownership: handoff={active_handoff}", file=sys.stderr)
    if active_review:
        print(f"active_ownership: cr_review={active_review}", file=sys.stderr)
    if not include_reviews:
        return
    reviews = con.execute(
        """
        select cr_id, reviewer_workstream, review_claimed_by
        from change_requests
        where status = 'submitted' and reviewer_role = ?
          and (review_claimed_by is null or review_claimed_by = ?)
        order by submitted_at, created_at, cr_id
        """,
        (role, agent_id),
    ).fetchall()
    eligible_reviews = 0
    for row in reviews:
        workstream = str(row["reviewer_workstream"] or "")
        if not workstream or agent_has_workstream(con, agent_id, role, workstream):
            eligible_reviews += 1
        else:
            print(
                f"excluded_cr_review: {row['cr_id']} "
                f"reason=missing_workstream_registration workstream={workstream}",
                file=sys.stderr,
            )
    print(
        f"review_eligibility: submitted_reviews={len(reviews)} "
        f"eligible_reviews={eligible_reviews}",
        file=sys.stderr,
    )


def next_review_for_agent(
    con: sqlite3.Connection,
    role: str,
    agent_id: str,
) -> sqlite3.Row | None:
    return con.execute(
        """
        select cr_id, title, file_path, reviewer_workstream, review_claimed_by
        from change_requests cr
        where status = 'submitted' and reviewer_role = ?
          and (review_claimed_by is null or review_claimed_by = ?)
          and (
            reviewer_workstream is null
            or exists (
              select 1 from agent_workstreams route
              where route.agent_id = ? and route.role_id = cr.reviewer_role
                and route.workstream = cr.reviewer_workstream
            )
          )
        order by submitted_at, created_at, cr_id
        limit 1
        """,
        (role, agent_id, agent_id),
    ).fetchone()


def command_cr_wait_review(args: argparse.Namespace) -> int:
    deadline = None if args.timeout == 0 else time.monotonic() + args.timeout
    waiter_id, role, active_waiters = start_waiter(args, "cr_review", "cr.review")
    agent_id = optional_agent_id(args)
    try:
        while True:
            with connect(args.db) as con:
                init_schema(con)
                require_permission(con, role, "cr.review")
                stopped = get_stop_control(con, role)
                row = next_review_for_agent(con, role, agent_id)
            if stopped:
                reason = f" reason={stopped['reason']}" if stopped["reason"] else ""
                print(f"Stopped waiting for CR review role {role} by {stopped['scope']}.{reason}")
                return 3
            if row:
                with connect(args.db) as con:
                    resolved_path = project_file_path(con, row["file_path"])
                print(
                    f"{row['cr_id']}\t{row['title']}\t{resolved_path}\t"
                    f"{row['reviewer_workstream'] or ''}\t{row['review_claimed_by'] or ''}"
                )
                return 0
            if deadline is not None and time.monotonic() >= deadline:
                print(f"Timed out waiting for CR review role {role}")
                return 2
            interval = poll_sleep_seconds(args.interval, active_waiters, waiter_id)
            time.sleep(bounded_sleep_seconds(interval, deadline))
            active_waiters = heartbeat_waiter(
                args.db,
                waiter_id,
                "cr_review",
                role,
                waiter_lease_seconds(args.interval),
            )
    finally:
        unregister_waiter(args.db, waiter_id)


def command_watch(args: argparse.Namespace) -> int:
    deadline = None if args.timeout == 0 else time.monotonic() + args.timeout
    waiter_id, role, active_waiters = start_waiter(args, "watch")
    agent_id = optional_agent_id(args)
    with connect(args.db) as con:
        init_schema(con)
        warn_agent_session_role(con, role, agent_id)
    try:
        while True:
            with connect(args.db) as con:
                init_schema(con)
                stopped = get_stop_control(con, role)
            if stopped:
                reason = f" reason={stopped['reason']}" if stopped["reason"] else ""
                print(f"Stopped watching role {role} by {stopped['scope']}.{reason}")
                return 3

            promote_args = argparse.Namespace(db=args.db, actor_role=role, quiet=True)
            command_promote_ready(promote_args)
            with connect(args.db) as con:
                init_schema(con)
                can_review = bool(
                    con.execute(
                        "select 1 from role_permissions where role_id = ? and permission = 'cr.review'",
                        (role,),
                    ).fetchone()
                )
                review = None
                if can_review:
                    review = next_review_for_agent(con, role, agent_id)
                if review:
                    path = project_file_path(con, review["file_path"])
                    print(
                        f"cr_review\t{review['cr_id']}\t{review['title']}\t{path}\t"
                        f"{review['reviewer_workstream'] or ''}\t"
                        f"{review['review_claimed_by'] or ''}"
                    )
                    return 0
                handoff = con.execute(
                    """
                    select job_id, title, workstream
                    from handoff_jobs job
                    where status = 'open' and target_role = ?
                      and (
                        workstream is null
                        or exists (
                          select 1 from agent_workstreams route
                          where route.agent_id = ? and route.role_id = job.target_role
                            and route.workstream = job.workstream
                        )
                      )
                    order by created_at, job_id
                    limit 1
                    """,
                    (role, agent_id),
                ).fetchone()
            if handoff:
                route = f"\t{handoff['workstream']}" if handoff["workstream"] else ""
                print(f"handoff\t{handoff['job_id']}\t{handoff['title']}{route}")
                return 0
            if deadline is not None and time.monotonic() >= deadline:
                if getattr(args, "explain", False):
                    with connect(args.db) as con:
                        init_schema(con)
                        explain_agent_eligibility(con, role, agent_id, include_reviews=True)
                print(f"Timed out watching role {role}")
                return 2
            interval = poll_sleep_seconds(args.interval, active_waiters, waiter_id)
            time.sleep(bounded_sleep_seconds(interval, deadline))
            active_waiters = heartbeat_waiter(
                args.db,
                waiter_id,
                "watch",
                role,
                waiter_lease_seconds(args.interval),
            )
    finally:
        unregister_waiter(args.db, waiter_id)


def handoff_evidence_corrections(
    con: sqlite3.Connection, job_id: str
) -> list[sqlite3.Row]:
    return con.execute(
        """
        select id, previous_commit, corrected_commit, commit_resolution,
               reason, actor_role, actor_id, created_at
        from handoff_evidence_corrections
        where job_id = ?
        order by id
        """,
        (job_id,),
    ).fetchall()


BLOCKING_CONTEXT_KEYS = (
    "with_open_cr",
    "with_implemented_cr",
    "with_terminal_unimplemented_cr",
    "without_cr",
)


def blocking_context_for_status(
    completion_blocking: object,
    outcome_cr_id: object,
    outcome_cr_status: object,
) -> str:
    if not completion_blocking:
        return "not_blocking"
    if not outcome_cr_id or not outcome_cr_status:
        return "no_outcome_cr"
    if outcome_cr_status == "implemented":
        return "implemented_cr"
    if outcome_cr_status in {"draft", "submitted", "revision_requested", "approved"}:
        return "open_cr"
    return "terminal_unimplemented_cr"


def blocking_outcome_context_counts(
    con: sqlite3.Connection,
) -> dict[str, int]:
    counts = {"total": 0, **{key: 0 for key in BLOCKING_CONTEXT_KEYS}}
    rows = con.execute(
        """
        select
          case
            when h.outcome_cr_id is null or cr.status is null then 'without_cr'
            when cr.status = 'implemented' then 'with_implemented_cr'
            when cr.status in ('draft', 'submitted', 'revision_requested', 'approved')
              then 'with_open_cr'
            else 'with_terminal_unimplemented_cr'
          end as context,
          count(*) as count
        from handoff_jobs h
        left join change_requests cr on cr.cr_id = h.outcome_cr_id
        where h.status = 'finished' and h.completion_blocking = 1
        group by context
        """
    ).fetchall()
    for row in rows:
        counts[str(row["context"])] = int(row["count"])
        counts["total"] += int(row["count"])
    return counts


def command_status(args: argparse.Namespace) -> int:
    with connect(args.db) as con:
        init_schema(con)
        rows = con.execute(
            "select status, count(*) as count from handoff_jobs group by status order by status"
        ).fetchall()
        outcome_rows = con.execute(
            """
            select completion_outcome as outcome, count(*) as count
            from handoff_jobs
            where status = 'finished'
            group by completion_outcome
            order by completion_outcome
            """
        ).fetchall()
        blocking_counts = blocking_outcome_context_counts(con)
    counts = {status: 0 for status in sorted(STATUSES)}
    counts.update({row["status"]: row["count"] for row in rows})
    for status, count in counts.items():
        print(f"{status}: {count}")
    for row in outcome_rows:
        print(f"outcome.{row['outcome']}: {row['count']}")
    print(f"outcome.blocking: {blocking_counts['total']}")
    for key in ("total", *BLOCKING_CONTEXT_KEYS):
        print(f"outcome.blocking_{key}: {blocking_counts[key]}")
    return 0


def command_handoff_show(args: argparse.Namespace) -> int:
    with connect(args.db) as con:
        init_schema(con)
        row = con.execute(
            "select * from handoff_jobs where job_id = ?",
            (args.job_id,),
        ).fetchone()
        if not row:
            raise SystemExit(f"ERROR: unknown job: {args.job_id}")
        dependencies = [
            item["depends_on_job_id"]
            for item in con.execute(
                "select depends_on_job_id from handoff_dependencies where job_id = ? order by depends_on_job_id",
                (args.job_id,),
            ).fetchall()
        ]
        gates = [
            item["gate_name"]
            for item in con.execute(
                "select gate_name from handoff_gate_dependencies where job_id = ? order by gate_name",
                (args.job_id,),
            ).fetchall()
        ]
        failures = [
            {key: item[key] for key in item.keys()}
            for item in con.execute(
                """
                select cr_id, failed_by_role, reason, evidence, failed_at,
                       resolution, resolved_by_role, resolved_at, resolution_message
                from handoff_failure_reviews
                where job_id = ?
                order by id
                """,
                (args.job_id,),
            ).fetchall()
        ]
        corrections = [
            {key: item[key] for key in item.keys()}
            for item in handoff_evidence_corrections(con, args.job_id)
        ]
        outcome_cr_status = ""
        if row["outcome_cr_id"]:
            outcome_cr = con.execute(
                "select status from change_requests where cr_id = ?",
                (row["outcome_cr_id"],),
            ).fetchone()
            outcome_cr_status = str(outcome_cr["status"] if outcome_cr else "")
    payload = {key: row[key] for key in row.keys()}
    payload["depends_on"] = dependencies
    payload["depends_on_gates"] = gates
    payload["failure_reviews"] = failures
    payload["evidence_corrections"] = corrections
    payload["outcome_cr_status"] = outcome_cr_status
    payload["blocking_context"] = blocking_context_for_status(
        row["completion_blocking"], row["outcome_cr_id"], outcome_cr_status
    )
    if corrections:
        payload["effective_related_commit"] = corrections[-1]["corrected_commit"]
        payload["effective_commit_resolution"] = corrections[-1]["commit_resolution"]
    else:
        payload["effective_related_commit"] = row["related_commit"] or ""
        payload["effective_commit_resolution"] = row["related_commit_resolution"]
    if args.format == "json":
        print(json.dumps(payload, indent=2, ensure_ascii=False))
        return 0
    ordered_keys = (
        "job_id",
        "status",
        "title",
        "target_role",
        "workstream",
        "attempt",
        "claimed_by",
        "source_ref",
        "objective",
        "exit_criteria",
        "depends_on",
        "depends_on_gates",
        "created_at",
        "started_at",
        "finished_at",
        "closure_evidence",
        "related_commit",
        "related_commit_resolution",
        "related_commit_resolution_reason",
        "effective_related_commit",
        "effective_commit_resolution",
        "completion_outcome",
        "completion_blocking",
        "outcome_cr_id",
        "outcome_cr_status",
        "blocking_context",
        "evidence_corrections",
        "failure_reviews",
    )
    for key in ordered_keys:
        value = payload.get(key)
        if isinstance(value, list):
            value = (
                json.dumps(value, ensure_ascii=False)
                if value and isinstance(value[0], dict)
                else ",".join(value)
            )
        print(f"{key}: {'' if value is None else value}")
    return 0


def command_handoff_list(args: argparse.Namespace) -> int:
    if args.limit < 1:
        raise SystemExit("ERROR: handoff list limit must be at least 1")
    conditions: list[str] = []
    params: list[object] = []
    with connect(args.db) as con:
        init_schema(con)
        if args.role:
            conditions.append("target_role = ?")
            params.append(resolve_role(con, args.role))
        if args.status:
            conditions.append("status = ?")
            params.append(args.status)
        if args.outcome:
            conditions.append("completion_outcome = ?")
            params.append(args.outcome)
        if args.blocking:
            conditions.append("completion_blocking = ?")
            params.append(1 if args.blocking == "yes" else 0)
        where = f"where {' and '.join(conditions)}" if conditions else ""
        params.append(args.limit)
        rows = con.execute(
            f"""
            select job_id, status, target_role, workstream, title, claimed_by,
                   completion_outcome, completion_blocking, outcome_cr_id, created_at
            from handoff_jobs
            {where}
            order by created_at, job_id
            limit ?
            """,
            params,
        ).fetchall()
    if args.format == "json":
        print(json.dumps([{key: row[key] for key in row.keys()} for row in rows], indent=2, ensure_ascii=False))
        return 0
    if not rows:
        print("No handoffs.")
        return 0
    for row in rows:
        claimant = row["claimed_by"] or ""
        print(
            f"{row['job_id']}\t{row['status']}\t{row['target_role']}\t"
            f"{row['workstream'] or ''}\t{claimant}\t"
            f"outcome={row['completion_outcome']}\t"
            f"blocking={row['completion_blocking']}\t"
            f"outcome_cr={row['outcome_cr_id'] or ''}\t{row['title']}"
        )
    return 0


def command_handoff_evidence_correct(args: argparse.Namespace) -> int:
    reason = args.reason.strip()
    if not reason:
        raise SystemExit("ERROR: --reason cannot be blank")
    corrected_commit, resolution, _ = resolve_completion_commit(
        args.commit,
        args.workspace_root,
        allow_unresolved=args.allow_unresolved_commit,
        unresolved_reason=reason,
    )
    if not corrected_commit:
        raise SystemExit("ERROR: --commit cannot be blank")

    with connect(args.db) as con:
        init_schema(con)
        begin_immediate(con)
        actor_role = resolve_role(con, args.role)
        actor_id = claimed_by_value(args, actor_role)
        row = con.execute(
            """
            select status, target_role, claimed_by, related_commit,
                   related_commit_resolution
            from handoff_jobs
            where job_id = ?
            """,
            (args.job_id,),
        ).fetchone()
        if not row:
            raise SystemExit(f"ERROR: unknown job: {args.job_id}")
        if row["status"] != "finished":
            raise SystemExit(
                f"ERROR: evidence correction requires a finished handoff: "
                f"{args.job_id} status={row['status']}"
            )
        is_original_claimant = actor_role == row["target_role"] and actor_id == (
            row["claimed_by"] or row["target_role"]
        )
        has_permission = bool(
            con.execute(
                """
                select 1 from role_permissions
                where role_id = ? and permission = 'handoff.evidence_correct'
                """,
                (actor_role,),
            ).fetchone()
        )
        if not is_original_claimant and not has_permission:
            raise SystemExit(
                f"ERROR: evidence correction requires original claimant "
                f"{row['claimed_by'] or row['target_role']} or permission "
                "handoff.evidence_correct"
            )
        previous = con.execute(
            """
            select corrected_commit, commit_resolution
            from handoff_evidence_corrections
            where job_id = ?
            order by id desc
            limit 1
            """,
            (args.job_id,),
        ).fetchone()
        previous_commit = (
            str(previous["corrected_commit"])
            if previous
            else str(row["related_commit"] or "")
        )
        previous_resolution = (
            str(previous["commit_resolution"])
            if previous
            else str(row["related_commit_resolution"])
        )
        if corrected_commit == previous_commit and resolution == previous_resolution:
            raise SystemExit("ERROR: evidence correction does not change the effective commit")
        cursor = con.execute(
            """
            insert into handoff_evidence_corrections(
              job_id, previous_commit, corrected_commit, commit_resolution,
              reason, actor_role, actor_id, created_at
            ) values (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                args.job_id,
                previous_commit or None,
                corrected_commit,
                resolution,
                reason,
                actor_role,
                actor_id,
                utc_now(),
            ),
        )
        event(
            con,
            "evidence_corrected",
            job_id=args.job_id,
            actor_role=actor_role,
            actor_id=actor_id,
            message=(
                f"correction={cursor.lastrowid} previous_commit={previous_commit} "
                f"corrected_commit={corrected_commit} resolution={resolution}; {reason}"
            ),
        )
        con.commit()
    print(
        f"Evidence correction {cursor.lastrowid} {args.job_id} "
        f"commit={corrected_commit} resolution={resolution}"
    )
    return 0


def command_handoff_successors(args: argparse.Namespace) -> int:
    with connect(args.db) as con:
        init_schema(con)
        source = con.execute(
            "select 1 from handoff_jobs where job_id = ?",
            (args.job_id,),
        ).fetchone()
        if not source:
            raise SystemExit(f"ERROR: unknown job: {args.job_id}")
        rows = con.execute(
            """
            select job.job_id, job.status, job.target_role, job.workstream,
                   job.claimed_by, job.title
            from handoff_jobs job
            join handoff_dependencies dependency on dependency.job_id = job.job_id
            where dependency.depends_on_job_id = ?
            order by job.created_at, job.job_id
            """,
            (args.job_id,),
        ).fetchall()
    payload = [{key: row[key] for key in row.keys()} for row in rows]
    if args.format == "json":
        print(json.dumps(payload, indent=2, ensure_ascii=False))
        return 0
    if not rows:
        print(f"No direct successor handoffs for {args.job_id}.")
        return 0
    for row in rows:
        claimant = row["claimed_by"] or "unassigned"
        print(
            f"{row['job_id']}\tstatus={row['status']}\t"
            f"target_role={row['target_role']}\tworkstream={row['workstream'] or '*'}\t"
            f"claimed_by={claimant}\t"
            f"title={row['title']}"
        )
    return 0


def command_next(args: argparse.Namespace) -> int:
    agent_id = optional_agent_id(args)
    with connect(args.db) as con:
        init_schema(con)
        role = resolve_role(con, args.role)
        if not getattr(args, "quiet", False):
            warn_agent_session_role(con, role, agent_id)
        if getattr(args, "explain", False):
            explain_agent_eligibility(con, role, agent_id)
        row = con.execute(
            """
            select job_id, title, workstream
            from handoff_jobs job
            where status = 'open' and target_role = ?
              and (
                workstream is null
                or exists (
                  select 1 from agent_workstreams route
                  where route.agent_id = ? and route.role_id = job.target_role
                    and route.workstream = job.workstream
                )
              )
            order by created_at, job_id
            limit 1
            """,
            (role, agent_id),
        ).fetchone()
    if not row:
        if not getattr(args, "quiet", False):
            print(f"No ready jobs for role {normalize_role(args.role)}.")
        return 1
    route = f"\t{row['workstream']}" if row["workstream"] else ""
    print(f"{row['job_id']}\t{row['title']}{route}")
    return 0


def require_linked_cr_integrity(con: sqlite3.Connection, job_id: str) -> None:
    rows = con.execute(
        """
        select cr.*
        from cr_handoffs ch
        join change_requests cr on cr.cr_id = ch.cr_id
        where ch.job_id = ? and ch.kind = 'implementation'
        order by cr.cr_id
        """,
        (job_id,),
    ).fetchall()
    for row in rows:
        if row["status"] not in {"approved", "implemented"}:
            raise MigrationError(
                f"implementation handoff {job_id} references CR {row['cr_id']} "
                f"with invalid status {row['status']}"
            )
        require_cr_body_hash(con, row, "approved_body_hash", "approval")


def command_claim(args: argparse.Namespace) -> int:
    baseline = latest_workspace_commit(args.db, args.job_id, ("registered",))
    assessment = assess_workspace(args.db, baseline, args.workspace_root)
    with connect(args.db) as con:
        init_schema(con)
        begin_immediate(con)
        role = resolve_role(con, args.role)
        row = con.execute(
            "select status, target_role, workstream from handoff_jobs where job_id = ?",
            (args.job_id,),
        ).fetchone()
        if not row:
            raise SystemExit(f"ERROR: unknown job: {args.job_id}")
        if row["target_role"] != role:
            raise SystemExit(f"ERROR: job target role is {row['target_role']}, not {role}")
        if row["status"] != "open":
            raise SystemExit(f"ERROR: job is not open: {args.job_id} status={row['status']}")
        require_linked_cr_integrity(con, args.job_id)
        stopped = get_stop_control(con, role)
        if stopped:
            reason = f" reason={stopped['reason']}" if stopped["reason"] else ""
            raise SystemExit(f"ERROR: role {role} is stopped by {stopped['scope']}.{reason}")
        claimant = claimed_by_value(args, role)
        require_workstream_assignment(con, claimant, role, row["workstream"])
        require_agent_capacity(con, claimant, handoff_id=args.job_id)
        apply_workspace_policy(
            con,
            args,
            assessment,
            entity_type="handoff",
            entity_id=args.job_id,
            operation="claimed",
            actor_role=role,
        )
        changed = con.execute(
            """
            update handoff_jobs
            set status = 'in_progress', claimed_by = ?, started_at = ?
            where job_id = ? and status = 'open' and target_role = ?
            """,
            (claimant, utc_now(), args.job_id, role),
        ).rowcount
        if changed != 1:
            raise SystemExit(f"ERROR: claim failed due to concurrent state change: {args.job_id}")
        event(con, "claimed", job_id=args.job_id, actor_role=role, actor_id=claimant, from_status="open", to_status="in_progress")
        con.commit()
    print(f"Claimed {args.job_id}")
    return 0


def command_finish(args: argparse.Namespace) -> int:
    evidence = args.evidence.strip()
    if not evidence:
        raise SystemExit("ERROR: --evidence cannot be blank")
    if args.blocking and args.outcome not in {"fail", "conditional", "inconclusive"}:
        raise SystemExit(
            "ERROR: --blocking requires --outcome fail, conditional, or inconclusive"
        )
    if args.outcome_cr_id and args.outcome == "unspecified":
        raise SystemExit("ERROR: --outcome-cr requires an explicit --outcome")
    related_commit, commit_resolution, commit_resolution_reason = resolve_completion_commit(
        args.commit,
        args.workspace_root,
        allow_unresolved=args.allow_unresolved_commit,
        unresolved_reason=args.unresolved_reason,
    )
    baseline = latest_workspace_commit(args.db, args.job_id, ("claimed", "registered"))
    assessment = assess_workspace(args.db, baseline, args.workspace_root)
    with connect(args.db) as con:
        init_schema(con)
        begin_immediate(con)
        role = resolve_role(con, args.role)
        row = con.execute("select status, target_role from handoff_jobs where job_id = ?", (args.job_id,)).fetchone()
        if not row:
            raise SystemExit(f"ERROR: unknown job: {args.job_id}")
        if row["target_role"] != role:
            raise SystemExit(f"ERROR: job target role is {row['target_role']}, not {role}")
        if row["status"] == "cancel_requested":
            raise SystemExit(
                f"ERROR: cancellation was requested for {args.job_id}; stop work and run cancel-ack"
            )
        if row["status"] != "in_progress":
            raise SystemExit(f"ERROR: job is not in_progress: {args.job_id} status={row['status']}")
        if args.outcome_cr_id:
            outcome_cr = con.execute(
                "select 1 from change_requests where cr_id = ?",
                (args.outcome_cr_id,),
            ).fetchone()
            if not outcome_cr:
                raise SystemExit(f"ERROR: unknown outcome CR: {args.outcome_cr_id}")
        require_linked_cr_integrity(con, args.job_id)
        apply_workspace_policy(
            con,
            args,
            assessment,
            entity_type="handoff",
            entity_id=args.job_id,
            operation="finished",
            actor_role=role,
        )
        con.execute(
            """
            update handoff_jobs
            set status = 'finished', finished_at = ?, closure_evidence = ?,
                related_commit = ?, related_commit_resolution = ?,
                related_commit_resolution_reason = ?, completion_outcome = ?,
                completion_blocking = ?, outcome_cr_id = ?
            where job_id = ?
            """,
            (
                utc_now(),
                evidence,
                related_commit,
                commit_resolution,
                commit_resolution_reason or None,
                args.outcome,
                int(args.blocking),
                args.outcome_cr_id or None,
                args.job_id,
            ),
        )
        event(
            con,
            "finished",
            job_id=args.job_id,
            actor_role=role,
            from_status="in_progress",
            to_status="finished",
            message=(
                f"outcome={args.outcome} blocking={int(args.blocking)} "
                f"outcome_cr={args.outcome_cr_id or ''} "
                f"commit_resolution={commit_resolution}; {evidence}"
            ),
        )
        promote_ready_direct_dependents(con, args.job_id, role)
        con.commit()
    print(f"Finished {args.job_id}")
    return 0


def command_fail(args: argparse.Namespace) -> int:
    reason = args.reason.strip()
    evidence = args.evidence.strip()
    if not reason:
        raise SystemExit("ERROR: --reason cannot be blank")
    baseline = latest_workspace_commit(args.db, args.job_id, ("claimed", "registered"))
    assessment = assess_workspace(args.db, baseline, args.workspace_root)
    with connect(args.db) as con:
        init_schema(con)
        begin_immediate(con)
        role = resolve_role(con, args.role)
        row = con.execute("select * from handoff_jobs where job_id = ?", (args.job_id,)).fetchone()
        if not row:
            raise SystemExit(f"ERROR: unknown job: {args.job_id}")
        if row["target_role"] != role:
            raise SystemExit(f"ERROR: job target role is {row['target_role']}, not {role}")
        if row["status"] == "cancel_requested":
            raise SystemExit(
                f"ERROR: cancellation was requested for {args.job_id}; stop work and run cancel-ack"
            )
        if row["status"] != "in_progress":
            raise SystemExit(f"ERROR: job is not in_progress: {args.job_id} status={row['status']}")

        reviewer_input = args.reviewer_role.strip()
        if reviewer_input:
            reviewer_role = require_failure_reviewer(con, reviewer_input)
        else:
            preferred_role = "sm" if role == "planning" else "planning"
            preferred = con.execute(
                "select role_id from roles where role_id = ? and active = 1",
                (preferred_role,),
            ).fetchone()
            granted = set()
            if preferred:
                granted = {
                    item["permission"]
                    for item in con.execute(
                        "select permission from role_permissions where role_id = ?",
                        (preferred["role_id"],),
                    ).fetchall()
                }
            reviewer_role = (
                preferred["role_id"]
                if preferred and FAILURE_REVIEW_PERMISSIONS <= granted
                else require_failure_reviewer(con, "sm")
            )
        require_distinct_cr_roles(role, reviewer_role)
        reviewer_workstream = (
            normalize_workstream(args.reviewer_workstream)
            if args.reviewer_workstream.strip()
            else None
        )
        apply_workspace_policy(
            con,
            args,
            assessment,
            entity_type="handoff",
            entity_id=args.job_id,
            operation="failed",
            actor_role=role,
        )

        cr_id = next_cr_id(con)
        title = args.title.strip() or f"Resolve failed handoff {args.job_id}: {row['title']}"
        file_path = args.file_path.strip() or str(Path(args.dir) / f"{cr_id}-{slugify(title)}.md")
        if project_file_path(con, file_path).exists():
            raise SystemExit(f"ERROR: CR file already exists: {file_path}")
        now = utc_now()
        con.execute(
            "update handoff_jobs set status = 'failed' where job_id = ?",
            (args.job_id,),
        )
        event(
            con,
            "failed",
            job_id=args.job_id,
            actor_role=role,
            from_status="in_progress",
            to_status="failed",
            message=f"{reason}{f' Evidence: {evidence}' if evidence else ''}",
        )
        con.execute(
            """
            insert into change_requests(
              cr_id, title, status, author_role, reviewer_role, reviewer_workstream, file_path,
              created_at, updated_at, submitted_at
            )
            values (?, ?, 'submitted', ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                cr_id,
                title,
                role,
                reviewer_role,
                reviewer_workstream,
                file_path,
                now,
                now,
                now,
            ),
        )
        cr_event(con, cr_id, "created", actor_role=role, to_status="draft")
        cr_event(
            con,
            cr_id,
            "failure_submitted",
            actor_role=role,
            from_status="draft",
            to_status="submitted",
            message=args.job_id,
        )
        con.execute(
            """
            insert into handoff_failure_reviews(
              job_id, cr_id, failed_by_role, reason, evidence, failed_at
            )
            values (?, ?, ?, ?, ?, ?)
            """,
            (args.job_id, cr_id, role, reason, evidence, now),
        )
        body = (
            f"# {title}\n\n"
            f"## Failed Handoff\n\n- Job: `{args.job_id}`\n- Role: `{role}`\n"
            f"- Source: `{row['source_ref'] or ''}`\n\n"
            f"## Failure Reason\n\n{reason}\n\n"
            f"## Evidence\n\n{evidence or 'No additional evidence was supplied.'}\n\n"
            "## Decision\n\n"
            "Review the failure without releasing dependent handoffs. Approve this CR to allow "
            "`baton retry`, or reject it and explicitly cancel the failed handoff.\n\n"
            "## Acceptance Criteria\n\n"
            "The failed handoff is either retried and finished, or cancelled with its blocked dependency branch.\n"
        )
        sync_cr_file(con, cr_id, new_body=body)
        submitted_hash = cr_body_hash(project_file_path(con, file_path))
        con.execute(
            "update change_requests set submitted_body_hash = ? where cr_id = ?",
            (submitted_hash, cr_id),
        )
        sync_cr_file(con, cr_id)
        con.commit()
    print(f"Failed {args.job_id} cr={cr_id} reviewer={reviewer_role}")
    return 0


def command_retry(args: argparse.Namespace) -> int:
    reason = args.reason.strip()
    if not reason:
        raise SystemExit("ERROR: --reason cannot be blank")
    with connect(args.db) as con:
        init_schema(con)
        begin_immediate(con)
        actor_role = require_permission(con, args.role, "handoff.register")
        row = con.execute(
            "select status, attempt from handoff_jobs where job_id = ?",
            (args.job_id,),
        ).fetchone()
        if not row:
            raise SystemExit(f"ERROR: unknown job: {args.job_id}")
        if row["status"] != "failed":
            raise SystemExit(f"ERROR: job is not failed: {args.job_id} status={row['status']}")
        failure = con.execute(
            """
            select f.id, f.cr_id, c.status as cr_status, c.reviewer_role
            from handoff_failure_reviews f
            join change_requests c on c.cr_id = f.cr_id
            where f.job_id = ? and f.resolution is null
            order by f.id desc
            limit 1
            """,
            (args.job_id,),
        ).fetchone()
        if not failure:
            raise SystemExit(f"ERROR: failed job has no active failure CR: {args.job_id}")
        if args.cr_id and args.cr_id != failure["cr_id"]:
            raise SystemExit(
                f"ERROR: active failure CR is {failure['cr_id']}, not {args.cr_id}"
            )
        if failure["reviewer_role"] != actor_role:
            raise SystemExit(
                f"ERROR: failure CR reviewer role is {failure['reviewer_role']}, not {actor_role}"
            )
        if failure["cr_status"] != "approved":
            raise SystemExit(
                f"ERROR: failure CR must be approved before retry: "
                f"{failure['cr_id']} status={failure['cr_status']}"
            )
        failure_cr = con.execute(
            "select * from change_requests where cr_id = ?",
            (failure["cr_id"],),
        ).fetchone()
        require_cr_body_hash(con, failure_cr, "approved_body_hash", "approval")
        now = utc_now()
        next_attempt = row["attempt"] + 1
        con.execute(
            """
            update handoff_jobs
            set status = 'open', claimed_by = null, started_at = null,
                finished_at = null, closure_evidence = null, related_commit = null,
                related_commit_resolution = 'not_provided',
                related_commit_resolution_reason = null,
                completion_outcome = 'unspecified', completion_blocking = 0,
                outcome_cr_id = null,
                attempt = ?
            where job_id = ? and status = 'failed'
            """,
            (next_attempt, args.job_id),
        )
        con.execute(
            """
            update handoff_failure_reviews
            set resolution = 'retry', resolved_by_role = ?, resolved_at = ?, resolution_message = ?
            where id = ?
            """,
            (actor_role, now, reason, failure["id"]),
        )
        con.execute(
            """
            insert into cr_handoffs(cr_id, job_id, kind, created_at)
            values (?, ?, 'implementation', ?)
            on conflict(cr_id, job_id) do nothing
            """,
            (failure["cr_id"], args.job_id, now),
        )
        event(
            con,
            "retried",
            job_id=args.job_id,
            actor_role=actor_role,
            from_status="failed",
            to_status="open",
            message=f"attempt={next_attempt} {failure['cr_id']}: {reason}",
        )
        cr_event(
            con,
            failure["cr_id"],
            "retry_authorized",
            actor_role=actor_role,
            from_status="approved",
            to_status="approved",
            message=f"{args.job_id}: {reason}",
        )
        con.commit()
    print(f"Retried {args.job_id} attempt={next_attempt} cr={failure['cr_id']}")
    return 0


def command_cancel(args: argparse.Namespace) -> int:
    reason = args.reason.strip()
    if not reason:
        raise SystemExit("ERROR: --reason cannot be blank")
    with connect(args.db) as con:
        init_schema(con)
        begin_immediate(con)
        actor_role = require_permission(con, args.role, "handoff.cancel")
        row = con.execute(
            "select status from handoff_jobs where job_id = ?",
            (args.job_id,),
        ).fetchone()
        if not row:
            raise SystemExit(f"ERROR: unknown job: {args.job_id}")
        if row["status"] == "finished":
            raise SystemExit(f"ERROR: finished job cannot be cancelled: {args.job_id}")
        if row["status"] == "cancelled":
            raise SystemExit(f"ERROR: job is already cancelled: {args.job_id}")
        if row["status"] == "cancel_requested" and not args.force:
            raise SystemExit(
                f"ERROR: cancellation is already requested: {args.job_id}; "
                "the claimant must run cancel-ack, or an authorized role may use cancel --force"
            )
        active_failure = con.execute(
            """
            select f.id, f.cr_id, c.status as cr_status
            from handoff_failure_reviews f
            join change_requests c on c.cr_id = f.cr_id
            where f.job_id = ? and f.resolution is null
            order by f.id desc
            limit 1
            """,
            (args.job_id,),
        ).fetchone()
        if row["status"] == "failed" and active_failure and active_failure["cr_status"] not in {
            "rejected",
            "cancelled",
        }:
            raise SystemExit(
                f"ERROR: failure CR must be rejected or cancelled before handoff cancellation: "
                f"{active_failure['cr_id']} status={active_failure['cr_status']}"
            )
        if args.force:
            cancelled = cancel_handoff_with_dependents(
                con,
                args.job_id,
                actor_role,
                reason,
                event_type="cancellation_forced",
            )
            outcome = "cancelled"
        else:
            outcome, cancelled = request_handoff_cancellation(
                con,
                args.job_id,
                actor_role,
                reason,
            )
        if active_failure:
            con.execute(
                """
                update handoff_failure_reviews
                set resolution = 'cancelled', resolved_by_role = ?, resolved_at = ?, resolution_message = ?
                where id = ?
                """,
                (actor_role, utc_now(), reason, active_failure["id"]),
            )
        con.commit()
    if outcome == "requested":
        print(f"Cancellation requested {args.job_id}")
    else:
        print(f"Cancelled {args.job_id} dependents={len(cancelled) - 1}")
    return 0


def command_cancel_ack(args: argparse.Namespace) -> int:
    evidence = args.evidence.strip()
    if not evidence:
        raise SystemExit("ERROR: --evidence cannot be blank")
    with connect(args.db) as con:
        init_schema(con)
        begin_immediate(con)
        role = resolve_role(con, args.role)
        row = con.execute(
            "select status, target_role, claimed_by from handoff_jobs where job_id = ?",
            (args.job_id,),
        ).fetchone()
        if not row:
            raise SystemExit(f"ERROR: unknown job: {args.job_id}")
        if row["target_role"] != role:
            raise SystemExit(f"ERROR: job target role is {row['target_role']}, not {role}")
        if row["status"] != "cancel_requested":
            raise SystemExit(
                f"ERROR: job is not cancel_requested: {args.job_id} status={row['status']}"
            )
        claimant = claimed_by_value(args, role)
        if row["claimed_by"] and row["claimed_by"] != claimant:
            raise SystemExit(
                f"ERROR: cancellation must be acknowledged by claimant {row['claimed_by']}, not {claimant}"
            )
        con.execute(
            "update handoff_jobs set status = 'cancelled' where job_id = ?",
            (args.job_id,),
        )
        event(
            con,
            "cancellation_acknowledged",
            job_id=args.job_id,
            actor_role=role,
            actor_id=claimant,
            from_status="cancel_requested",
            to_status="cancelled",
            message=evidence,
        )
        descendants = cancel_blocked_dependents(con, args.job_id, role)
        con.commit()
    print(f"Cancelled {args.job_id} dependents={len(descendants)}")
    return 0


def command_cancel_withdraw(args: argparse.Namespace) -> int:
    reason = args.reason.strip()
    if not reason:
        raise SystemExit("ERROR: --reason cannot be blank")
    with connect(args.db) as con:
        init_schema(con)
        begin_immediate(con)
        actor_role = require_permission(con, args.role, "handoff.cancel")
        row = con.execute(
            "select status, claimed_by from handoff_jobs where job_id = ?",
            (args.job_id,),
        ).fetchone()
        if not row:
            raise SystemExit(f"ERROR: unknown job: {args.job_id}")
        if row["status"] != "cancel_requested":
            raise SystemExit(
                f"ERROR: job is not cancel_requested: {args.job_id} status={row['status']}"
            )
        retired_crs = con.execute(
            """
            select c.cr_id, c.status
            from cr_handoffs ch
            join change_requests c on c.cr_id = ch.cr_id
            where ch.job_id = ?
              and ch.kind = 'implementation'
              and c.status in ('cancelled', 'superseded')
            order by c.cr_id
            """,
            (args.job_id,),
        ).fetchall()
        if retired_crs:
            details = ", ".join(f"{item['cr_id']}={item['status']}" for item in retired_crs)
            raise SystemExit(
                "ERROR: cancellation cannot be withdrawn because the implementation source "
                f"is retired: {details}; register a replacement handoff"
            )
        con.execute(
            "update handoff_jobs set status = 'in_progress' where job_id = ?",
            (args.job_id,),
        )
        event(
            con,
            "cancellation_withdrawn",
            job_id=args.job_id,
            actor_role=actor_role,
            from_status="cancel_requested",
            to_status="in_progress",
            message=f"claimant={row['claimed_by'] or ''}; {reason}",
        )
        con.commit()
    print(f"Cancellation withdrawn {args.job_id} claimant={row['claimed_by'] or ''}")
    return 0


def promote_ready_direct_dependents(
    con: sqlite3.Connection,
    source_job_id: str,
    actor_role: str,
) -> list[str]:
    rows = con.execute(
        """
        select distinct job.job_id
        from handoff_jobs job
        join handoff_dependencies direct on direct.job_id = job.job_id
        where direct.depends_on_job_id = ?
          and job.status = 'blocked'
          and not exists (
            select 1
            from handoff_dependencies dependency
            join handoff_jobs upstream on upstream.job_id = dependency.depends_on_job_id
            where dependency.job_id = job.job_id and upstream.status != 'finished'
          )
          and not exists (
            select 1
            from handoff_gate_dependencies gate_dependency
            join workflow_gates gate on gate.gate_name = gate_dependency.gate_name
            where gate_dependency.job_id = job.job_id and gate.status != 'released'
          )
        order by job.job_id
        """,
        (source_job_id,),
    ).fetchall()
    promoted: list[str] = []
    for row in rows:
        changed = con.execute(
            "update handoff_jobs set status = 'open' where job_id = ? and status = 'blocked'",
            (row["job_id"],),
        ).rowcount
        if not changed:
            continue
        event(
            con,
            "promoted",
            job_id=row["job_id"],
            actor_role=actor_role,
            from_status="blocked",
            to_status="open",
            message=f"Ready after {source_job_id}.",
        )
        promoted.append(row["job_id"])
    return promoted


def require_notification_sender(
    con: sqlite3.Connection,
    sender_agent_id: str,
    actor_role: str,
) -> sqlite3.Row:
    sender = active_agent_session(con, sender_agent_id)
    if not sender:
        raise SystemExit(
            f"ERROR: no active session for sender {sender_agent_id}; "
            "run 'baton agent session-set' first"
        )
    if sender["role_id"] != actor_role:
        raise SystemExit(
            f"ERROR: sender {sender_agent_id} session role is {sender['role_id']}, "
            f"not {actor_role}"
        )
    return sender


def notification_candidate_rows(
    con: sqlite3.Connection,
    job: sqlite3.Row,
    sender_agent_id: str,
) -> list[dict[str, object]]:
    notified = con.execute(
        """
        select recipient_agent_id, recipient_thread_id, recipient_model, transport, created_at
        from handoff_notifications
        where job_id = ? and attempt = ? and delivery_status = 'sent'
        order by id desc
        limit 1
        """,
        (job["job_id"], job["attempt"]),
    ).fetchone()
    base = {
        "job_id": job["job_id"],
        "target_role": job["target_role"],
        "workstream": job["workstream"] or "",
        "title": job["title"],
        "attempt": job["attempt"],
    }
    if notified:
        return [
            {
                **base,
                "state": "already_notified",
                "agent_id": notified["recipient_agent_id"],
                "host": notified["transport"],
                "thread_id": notified["recipient_thread_id"],
                "model": notified["recipient_model"],
            }
        ]
    stopped = get_stop_control(con, job["target_role"])
    if stopped:
        return [
            {
                **base,
                "state": "outside_shift",
                "agent_id": "",
                "host": "",
                "thread_id": "",
                "model": "",
            }
        ]
    sessions = con.execute(
        """
        select agent_id, host, thread_id, model
        from agent_sessions session
        where role_id = ? and status = 'active' and agent_id != ?
          and (
            ? is null
            or exists (
              select 1 from agent_workstreams route
              where route.agent_id = session.agent_id
                and route.role_id = session.role_id
                and route.workstream = ?
            )
          )
          and not exists (
            select 1
            from handoff_jobs busy
            where busy.claimed_by = session.agent_id
              and busy.status in ('in_progress', 'cancel_requested')
          )
          and not exists (
            select 1
            from change_requests review
            where review.review_claimed_by = session.agent_id
              and review.status = 'submitted'
          )
        order by updated_at desc, agent_id
        """,
        (
            job["target_role"],
            sender_agent_id,
            job["workstream"],
            job["workstream"],
        ),
    ).fetchall()
    if not sessions:
        return [
            {
                **base,
                "state": "no_active_peer_session",
                "agent_id": "",
                "host": "",
                "thread_id": "",
                "model": "",
            }
        ]
    return [
        {
            **base,
            "state": "candidate",
            "agent_id": session["agent_id"],
            "host": session["host"],
            "thread_id": session["thread_id"],
            "model": session["model"],
        }
        for session in sessions
    ]


def print_notification_candidates(payload: list[dict[str, object]], output_format: str) -> None:
    if output_format == "json":
        print(json.dumps(payload, indent=2, ensure_ascii=False))
        return
    for item in payload:
        print(
            f"{item['job_id']}\t{item['target_role']}\t{item['workstream']}\t"
            f"attempt={item['attempt']}\t{item['state']}\t"
            f"{item['agent_id']}\t{item['host']}\t{item['thread_id']}\t"
            f"{item['model']}\t{item['title']}"
        )


def command_notify_targets(args: argparse.Namespace) -> int:
    sender_agent_id = agent_id_value(args, "from_agent")
    with connect(args.db) as con:
        init_schema(con)
        begin_immediate(con)
        actor_role = resolve_role(con, args.role)
        require_notification_sender(con, sender_agent_id, actor_role)
        source = con.execute(
            "select status, target_role from handoff_jobs where job_id = ?",
            (args.job_id,),
        ).fetchone()
        if not source:
            raise SystemExit(f"ERROR: unknown job: {args.job_id}")
        if source["status"] != "finished":
            raise SystemExit(
                f"ERROR: notification source is not finished: {args.job_id} status={source['status']}"
            )
        if source["target_role"] != actor_role:
            can_register = con.execute(
                "select 1 from role_permissions where role_id = ? and permission = 'handoff.register'",
                (actor_role,),
            ).fetchone()
            if not can_register:
                raise SystemExit(
                    f"ERROR: source role is {source['target_role']}; {actor_role} lacks handoff.register"
                )
        promote_ready_direct_dependents(con, args.job_id, actor_role)
        jobs = con.execute(
            """
            select distinct job.job_id, job.target_role, job.workstream, job.title, job.attempt
            from handoff_jobs job
            join handoff_dependencies direct on direct.job_id = job.job_id
            where direct.depends_on_job_id = ? and job.status = 'open'
            order by job.created_at, job.job_id
            """,
            (args.job_id,),
        ).fetchall()
        payload: list[dict[str, object]] = []
        for job in jobs:
            payload.extend(notification_candidate_rows(con, job, sender_agent_id))
        con.commit()
    if args.format == "json":
        print_notification_candidates(payload, args.format)
    elif not payload:
        print("No ready direct dependent handoffs.")
    else:
        print_notification_candidates(payload, args.format)
    return 0 if payload else 1


def command_notify_candidates(args: argparse.Namespace) -> int:
    sender_agent_id = agent_id_value(args, "from_agent")
    with connect(args.db) as con:
        init_schema(con)
        begin_immediate(con)
        actor_role = resolve_role(con, args.role)
        require_notification_sender(con, sender_agent_id, actor_role)
        job = con.execute(
            """
            select job_id, status, target_role, workstream, title, attempt
            from handoff_jobs where job_id = ?
            """,
            (args.job_id,),
        ).fetchone()
        if not job:
            raise SystemExit(f"ERROR: unknown job: {args.job_id}")
        if job["status"] != "open":
            raise SystemExit(
                f"ERROR: notification candidate handoff must be open: "
                f"{args.job_id} status={job['status']}"
            )
        if actor_role != job["target_role"]:
            can_register = con.execute(
                "select 1 from role_permissions "
                "where role_id = ? and permission = 'handoff.register'",
                (actor_role,),
            ).fetchone()
            if not can_register:
                raise SystemExit(
                    f"ERROR: target role is {job['target_role']}; "
                    f"{actor_role} lacks handoff.register"
                )
        payload = notification_candidate_rows(con, job, sender_agent_id)
        con.commit()
    print_notification_candidates(payload, args.format)
    return 0


def next_notification_delivery_attempt(
    con: sqlite3.Connection,
    job_id: str,
    handoff_attempt: int,
) -> int:
    row = con.execute(
        "select coalesce(max(delivery_attempt), 0) + 1 as next_attempt "
        "from handoff_notifications where job_id = ? and attempt = ?",
        (job_id, handoff_attempt),
    ).fetchone()
    return int(row["next_attempt"])


def require_notification_shift(con: sqlite3.Connection, role: str) -> None:
    stopped = get_stop_control(con, role)
    if stopped:
        raise SystemExit(
            f"ERROR: notification target role is outside shift: role={role} "
            f"scope={stopped['scope']} reason={stopped['reason'] or ''}"
        )


def command_notify_record(args: argparse.Namespace) -> int:
    sender_agent_id = agent_id_value(args, "from_agent")
    recipient_agent_id = args.to_agent.strip()
    detail = args.detail.strip()
    message_ref = args.message_ref.strip()
    if not recipient_agent_id:
        raise SystemExit("ERROR: --to-agent cannot be blank")
    if args.status == "failed" and not detail:
        raise SystemExit("ERROR: --detail is required for a failed notification")
    with connect(args.db) as con:
        init_schema(con)
        begin_immediate(con)
        actor_role = resolve_role(con, args.role)
        job = con.execute(
            "select status, target_role, workstream, claimed_by, attempt "
            "from handoff_jobs where job_id = ?",
            (args.job_id,),
        ).fetchone()
        if not job:
            raise SystemExit(f"ERROR: unknown job: {args.job_id}")
        sender = require_notification_sender(con, sender_agent_id, actor_role)
        recipient = active_agent_session(con, recipient_agent_id)
        if not recipient:
            raise SystemExit(f"ERROR: no active session for recipient {recipient_agent_id}")
        if recipient["role_id"] != job["target_role"]:
            raise SystemExit(
                f"ERROR: recipient role is {recipient['role_id']}, "
                f"but handoff target role is {job['target_role']}"
            )
        require_workstream_assignment(
            con,
            recipient_agent_id,
            recipient["role_id"],
            job["workstream"],
        )
        if sender["session_id"] == recipient["session_id"]:
            raise SystemExit("ERROR: opt-in notification requires a different recipient session")
        existing = con.execute(
            "select recipient_agent_id from handoff_notifications "
            "where job_id = ? and attempt = ? and delivery_status = 'sent'",
            (args.job_id, job["attempt"]),
        ).fetchone()
        if existing:
            raise SystemExit(
                f"ERROR: handoff was already notified successfully to "
                f"{existing['recipient_agent_id']}; use 'baton notify status' and the controlled "
                "'baton notify retry' recovery path"
            )
        if args.status == "sent":
            require_notification_shift(con, job["target_role"])
            require_agent_capacity(con, recipient_agent_id, handoff_id=args.job_id)
            if job["status"] not in {"open", "in_progress"}:
                raise SystemExit(
                    f"ERROR: cannot record a sent notification for {args.job_id} status={job['status']}"
                )
            if job["status"] == "in_progress" and job["claimed_by"] != recipient_agent_id:
                raise SystemExit(
                    f"ERROR: handoff is already claimed by {job['claimed_by']}, not {recipient_agent_id}"
                )
        delivery_attempt = next_notification_delivery_attempt(
            con, args.job_id, int(job["attempt"])
        )
        cursor = con.execute(
            """
            insert into handoff_notifications(
              job_id, sender_session_id, recipient_session_id,
              sender_agent_id, sender_model, recipient_agent_id,
              recipient_thread_id, recipient_model, transport,
              delivery_status, message_ref, detail, created_at, attempt,
              delivery_attempt, retry_of_notification_id, recovery_reason
            ) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, null, null)
            """,
            (
                args.job_id,
                sender["session_id"],
                recipient["session_id"],
                sender_agent_id,
                sender["model"],
                recipient_agent_id,
                recipient["thread_id"],
                recipient["model"],
                recipient["host"],
                args.status,
                message_ref,
                detail,
                utc_now(),
                job["attempt"],
                delivery_attempt,
            ),
        )
        event_message = (
            f"attempt={job['attempt']} delivery_attempt={delivery_attempt} "
            f"recipient={recipient_agent_id} host={recipient['host']} "
            f"thread={recipient['thread_id']} model={recipient['model']}"
        )
        if message_ref:
            event_message += f" message_ref={message_ref}"
        if detail:
            event_message += f" detail={detail}"
        event(
            con,
            f"notification_{args.status}",
            job_id=args.job_id,
            actor_role=actor_role,
            actor_id=sender_agent_id,
            from_status=job["status"],
            to_status=job["status"],
            message=event_message,
        )
        notification_id = cursor.lastrowid
        con.commit()
    print(
        f"notification={notification_id}\t"
        f"{'host_accepted' if args.status == 'sent' else args.status}\t{args.job_id}\t"
        f"attempt={job['attempt']}\t{recipient_agent_id}\t"
        f"delivery_attempt={delivery_attempt}"
    )
    return 0


def command_notify_retry(args: argparse.Namespace) -> int:
    sender_agent_id = agent_id_value(args, "from_agent")
    reason = args.reason.strip()
    detail = args.detail.strip()
    message_ref = args.message_ref.strip()
    if not reason:
        raise SystemExit("ERROR: --reason cannot be blank")
    stale_after = parse_duration(args.stale_after)
    with connect(args.db) as con:
        init_schema(con)
        begin_immediate(con)
        actor_role = resolve_role(con, args.role)
        sender = require_notification_sender(con, sender_agent_id, actor_role)
        job = con.execute(
            "select status, target_role, workstream, claimed_by, attempt "
            "from handoff_jobs where job_id = ?",
            (args.job_id,),
        ).fetchone()
        if not job:
            raise SystemExit(f"ERROR: unknown job: {args.job_id}")
        if job["status"] != "open" or job["claimed_by"]:
            raise SystemExit(
                f"ERROR: recovery notification requires an open, unclaimed handoff: "
                f"{args.job_id} status={job['status']} claimed_by={job['claimed_by'] or ''}"
            )
        original = con.execute(
            """
            select id, attempt, delivery_attempt, delivery_status,
                   sender_agent_id, recipient_session_id, recipient_agent_id, created_at
            from handoff_notifications
            where id = ? and job_id = ?
            """,
            (args.notification, args.job_id),
        ).fetchone()
        if not original:
            raise SystemExit(
                f"ERROR: unknown notification {args.notification} for handoff {args.job_id}"
            )
        if int(original["attempt"]) != int(job["attempt"]):
            raise SystemExit(
                f"ERROR: notification {args.notification} belongs to handoff attempt "
                f"{original['attempt']}, current attempt is {job['attempt']}"
            )
        if original["delivery_status"] != "sent":
            raise SystemExit("ERROR: recovery must reference a host_accepted notification")
        if sender_agent_id != original["sender_agent_id"]:
            can_register = con.execute(
                "select 1 from role_permissions "
                "where role_id = ? and permission = 'handoff.register'",
                (actor_role,),
            ).fetchone()
            if not can_register:
                raise SystemExit(
                    f"ERROR: recovery sender differs from {original['sender_agent_id']}; "
                    f"{actor_role} lacks handoff.register"
                )
        latest = con.execute(
            """
            select id from handoff_notifications
            where job_id = ? and attempt = ? and delivery_status = 'sent'
            order by delivery_attempt desc, id desc limit 1
            """,
            (args.job_id, job["attempt"]),
        ).fetchone()
        if not latest or int(latest["id"]) != int(original["id"]):
            raise SystemExit("ERROR: recovery must reference the latest host_accepted notification")
        recovery = con.execute(
            "select id from handoff_notifications "
            "where job_id = ? and attempt = ? and retry_of_notification_id is not null",
            (args.job_id, job["attempt"]),
        ).fetchone()
        if recovery:
            raise SystemExit(
                f"ERROR: recovery notification already recorded for handoff attempt: "
                f"notification={recovery['id']}"
            )
        age_seconds = max(
            0,
            int(
                (
                    datetime.now(timezone.utc) - parse_utc(str(original["created_at"]))
                ).total_seconds()
            ),
        )
        stale_seconds = int(stale_after.total_seconds())
        if age_seconds < stale_seconds:
            raise SystemExit(
                f"ERROR: notification is not stale: age_seconds={age_seconds} "
                f"stale_after_seconds={stale_seconds}"
            )
        require_notification_shift(con, job["target_role"])
        recipient_agent_id = str(original["recipient_agent_id"])
        recipient = active_agent_session(con, recipient_agent_id)
        if not recipient or recipient["session_id"] != original["recipient_session_id"]:
            raise SystemExit(
                f"ERROR: original recipient session is no longer active: {recipient_agent_id}; "
                "do not redirect a recovery notification"
            )
        if recipient["role_id"] != job["target_role"]:
            raise SystemExit(
                f"ERROR: recipient role is {recipient['role_id']}, "
                f"but handoff target role is {job['target_role']}"
            )
        require_workstream_assignment(
            con,
            recipient_agent_id,
            recipient["role_id"],
            job["workstream"],
        )
        if sender["session_id"] == recipient["session_id"]:
            raise SystemExit("ERROR: opt-in notification requires a different recipient session")
        require_agent_capacity(con, recipient_agent_id, handoff_id=args.job_id)
        delivery_attempt = next_notification_delivery_attempt(
            con, args.job_id, int(job["attempt"])
        )
        cursor = con.execute(
            """
            insert into handoff_notifications(
              job_id, sender_session_id, recipient_session_id,
              sender_agent_id, sender_model, recipient_agent_id,
              recipient_thread_id, recipient_model, transport,
              delivery_status, message_ref, detail, created_at, attempt,
              delivery_attempt, retry_of_notification_id, recovery_reason
            ) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                args.job_id,
                sender["session_id"],
                recipient["session_id"],
                sender_agent_id,
                sender["model"],
                recipient_agent_id,
                recipient["thread_id"],
                recipient["model"],
                recipient["host"],
                args.status,
                message_ref,
                detail,
                utc_now(),
                job["attempt"],
                delivery_attempt,
                original["id"],
                reason,
            ),
        )
        event_message = (
            f"attempt={job['attempt']} delivery_attempt={delivery_attempt} "
            f"retry_of={original['id']} recipient={recipient_agent_id} "
            f"host={recipient['host']} thread={recipient['thread_id']} "
            f"model={recipient['model']} reason={reason}"
        )
        if message_ref:
            event_message += f" message_ref={message_ref}"
        if detail:
            event_message += f" detail={detail}"
        event(
            con,
            f"notification_recovery_{args.status}",
            job_id=args.job_id,
            actor_role=actor_role,
            actor_id=sender_agent_id,
            from_status=job["status"],
            to_status=job["status"],
            message=event_message,
        )
        notification_id = cursor.lastrowid
        con.commit()
    print(
        f"notification={notification_id}\t"
        f"{'host_accepted' if args.status == 'sent' else args.status}\t{args.job_id}\t"
        f"attempt={job['attempt']}\tdelivery_attempt={delivery_attempt}\t"
        f"retry_of={original['id']}\t{recipient_agent_id}"
    )
    return 0


def command_notify_status(args: argparse.Namespace) -> int:
    stale_after = parse_duration(args.stale_after)
    with connect(args.db) as con:
        init_schema(con)
        job = con.execute(
            """
            select job_id, status, target_role, workstream, claimed_by, attempt
            from handoff_jobs
            where job_id = ?
            """,
            (args.job_id,),
        ).fetchone()
        if not job:
            raise SystemExit(f"ERROR: unknown job: {args.job_id}")
        notification = con.execute(
            """
            select id, recipient_agent_id, recipient_thread_id, transport,
                   message_ref, created_at, delivery_attempt,
                   retry_of_notification_id, recovery_reason
            from handoff_notifications
            where job_id = ? and attempt = ? and delivery_status = 'sent'
            order by delivery_attempt desc, id desc
            limit 1
            """,
            (args.job_id, job["attempt"]),
        ).fetchone()
        latest_delivery = con.execute(
            """
            select id, delivery_attempt, delivery_status
            from handoff_notifications
            where job_id = ? and attempt = ?
            order by delivery_attempt desc, id desc
            limit 1
            """,
            (args.job_id, job["attempt"]),
        ).fetchone()
        recovery_delivery_attempts = int(
            con.execute(
                "select count(*) from handoff_notifications "
                "where job_id = ? and attempt = ? and retry_of_notification_id is not null",
                (args.job_id, job["attempt"]),
            ).fetchone()[0]
        )

    age_seconds: int | None = None
    recipient = ""
    last_notification_at = ""
    if not notification:
        state = "not_notified"
        if job["status"] in {"in_progress", "cancel_requested"} and job["claimed_by"]:
            context = "claimed_without_recorded_notification"
        else:
            context = f"{job['status']}_without_recorded_notification"
    else:
        recipient = str(notification["recipient_agent_id"])
        last_notification_at = str(notification["created_at"])
        age_seconds = max(
            0,
            int((datetime.now(timezone.utc) - parse_utc(last_notification_at)).total_seconds()),
        )
        claimant = str(job["claimed_by"] or "")
        if claimant == recipient:
            state = "claimed_by_recipient"
        elif claimant:
            state = "claimed_by_other"
        elif job["status"] == "open" and age_seconds >= int(stale_after.total_seconds()):
            state = "stale_unclaimed"
        elif job["status"] == "open":
            state = "host_accepted_unclaimed"
        else:
            state = f"host_accepted_{job['status']}"
        context = state

    payload = {
        "job_id": job["job_id"],
        "attempt": job["attempt"],
        "handoff_status": job["status"],
        "notification_state": state,
        "notification_context": context,
        "latest_delivery_id": latest_delivery["id"] if latest_delivery else None,
        "latest_delivery_attempt": latest_delivery["delivery_attempt"] if latest_delivery else None,
        "latest_delivery_state": (
            "host_accepted"
            if latest_delivery and latest_delivery["delivery_status"] == "sent"
            else (latest_delivery["delivery_status"] if latest_delivery else "")
        ),
        "latest_host_accepted_notification_id": notification["id"] if notification else None,
        "recovery_delivery_attempts": recovery_delivery_attempts,
        "recipient_agent_id": recipient,
        "claimed_by": job["claimed_by"] or "",
        "last_notification_at": last_notification_at,
        "unclaimed_for_seconds": age_seconds if notification and not job["claimed_by"] else None,
        "stale_after_seconds": int(stale_after.total_seconds()),
    }
    if args.format == "json":
        print(json.dumps(payload, indent=2, ensure_ascii=False))
        return 0
    for key, value in payload.items():
        print(f"{key}: {'' if value is None else value}")
    return 0


def command_notify_list(args: argparse.Namespace) -> int:
    if args.limit is not None and args.limit <= 0:
        raise SystemExit("ERROR: --limit must be greater than zero")
    if args.after_id is not None and args.after_id < 0:
        raise SystemExit("ERROR: --after-id must be zero or greater")
    if args.before_id is not None and args.before_id <= 0:
        raise SystemExit("ERROR: --before-id must be greater than zero")
    if (
        args.after_id is not None
        and args.before_id is not None
        and args.after_id >= args.before_id
    ):
        raise SystemExit("ERROR: --after-id must be less than --before-id")
    conditions: list[str] = []
    params: list[object] = []
    if args.job_id:
        conditions.append("job_id = ?")
        params.append(args.job_id)
    if args.status:
        conditions.append("delivery_status = ?")
        params.append(args.status)
    if args.after_id is not None:
        conditions.append("id > ?")
        params.append(args.after_id)
    if args.before_id is not None:
        conditions.append("id < ?")
        params.append(args.before_id)
    if args.recovery_only:
        conditions.append("retry_of_notification_id is not null")
    order = "desc" if args.order == "newest" else "asc"
    where = f"where {' and '.join(conditions)}" if conditions else ""
    limit = "limit ?" if args.limit is not None else ""
    if args.limit is not None:
        params.append(args.limit)
    with connect(args.db) as con:
        init_schema(con)
        rows = con.execute(
            f"""
            select id, job_id, attempt, delivery_attempt, retry_of_notification_id,
                   recovery_reason, delivery_status, sender_agent_id, sender_model,
                   recipient_agent_id, recipient_thread_id, recipient_model,
                   transport, message_ref, detail, created_at
            from handoff_notifications
            {where}
            order by id {order}
            {limit}
            """,
            params,
        ).fetchall()
    payload = []
    for row in rows:
        item = {key: row[key] for key in row.keys()}
        item["delivery_state"] = (
            "host_accepted" if row["delivery_status"] == "sent" else row["delivery_status"]
        )
        payload.append(item)
    if args.format == "json":
        print(json.dumps(payload, indent=2, ensure_ascii=False))
        return 0
    if not rows:
        print("No notifications.")
        return 0
    for row in rows:
        print(
            f"{row['id']}\t{row['job_id']}\tattempt={row['attempt']}\t"
            f"{'host_accepted' if row['delivery_status'] == 'sent' else row['delivery_status']}\t"
            f"{row['sender_agent_id']}\t{row['recipient_agent_id']}\t"
            f"{row['transport']}\t{row['recipient_thread_id']}\t"
            f"{row['recipient_model']}\t{row['created_at']}\t{row['detail'] or ''}\t"
            f"delivery_attempt={row['delivery_attempt']}\t"
            f"retry_of={row['retry_of_notification_id'] or ''}\t"
            f"reason={row['recovery_reason'] or ''}"
        )
    return 0


def promote_ready_handoffs_for_gate(
    con: sqlite3.Connection,
    gate_name: str,
    actor_role: str,
) -> list[str]:
    rows = con.execute(
        """
        select j.job_id
        from handoff_jobs j
        join handoff_gate_dependencies target_gate on target_gate.job_id = j.job_id
        where j.status = 'blocked'
          and target_gate.gate_name = ?
          and not exists (
            select 1
            from handoff_dependencies d
            join handoff_jobs dep on dep.job_id = d.depends_on_job_id
            where d.job_id = j.job_id
              and dep.status != 'finished'
          )
          and not exists (
            select 1
            from handoff_gate_dependencies gd
            join workflow_gates g on g.gate_name = gd.gate_name
            where gd.job_id = j.job_id
              and g.status != 'released'
          )
        order by j.created_at, j.job_id
        """,
        (gate_name,),
    ).fetchall()
    promoted: list[str] = []
    for row in rows:
        con.execute("update handoff_jobs set status = 'open' where job_id = ?", (row["job_id"],))
        event(
            con,
            "promoted",
            job_id=row["job_id"],
            actor_role=actor_role,
            from_status="blocked",
            to_status="open",
            message=f"Gate {gate_name} was released.",
        )
        promoted.append(row["job_id"])
    return promoted


def reconcile_handoffs(con: sqlite3.Connection, actor_role: str) -> tuple[list[str], list[str]]:
    cancelled_upstreams = con.execute(
        """
        select distinct dep.job_id
        from handoff_jobs dep
        join handoff_dependencies d on d.depends_on_job_id = dep.job_id
        join handoff_jobs child on child.job_id = d.job_id
        where dep.status = 'cancelled' and child.status = 'blocked'
        order by dep.created_at, dep.job_id
        """
    ).fetchall()
    cancelled: list[str] = []
    for upstream in cancelled_upstreams:
        cancelled.extend(cancel_blocked_dependents(con, upstream["job_id"], actor_role))

    cancelled_gates = con.execute(
        """
        select distinct g.gate_name
        from workflow_gates g
        join handoff_gate_dependencies d on d.gate_name = g.gate_name
        join handoff_jobs j on j.job_id = d.job_id
        where g.status = 'cancelled' and j.status = 'blocked'
        order by g.created_at, g.gate_name
        """
    ).fetchall()
    for gate in cancelled_gates:
        cancelled.extend(cancel_jobs_for_gate(con, gate["gate_name"], actor_role))

    rows = con.execute(
        """
        select j.job_id
        from handoff_jobs j
        where j.status = 'blocked'
          and (
            exists (select 1 from handoff_dependencies d where d.job_id = j.job_id)
            or exists (select 1 from handoff_gate_dependencies gd where gd.job_id = j.job_id)
          )
          and not exists (
            select 1
            from handoff_dependencies d
            join handoff_jobs dep on dep.job_id = d.depends_on_job_id
            where d.job_id = j.job_id
              and dep.status != 'finished'
          )
          and not exists (
            select 1
            from handoff_gate_dependencies gd
            join workflow_gates g on g.gate_name = gd.gate_name
            where gd.job_id = j.job_id
              and g.status != 'released'
          )
        order by j.created_at, j.job_id
        """
    ).fetchall()
    promoted: list[str] = []
    for row in rows:
        con.execute("update handoff_jobs set status = 'open' where job_id = ?", (row["job_id"],))
        event(
            con,
            "promoted",
            job_id=row["job_id"],
            actor_role=actor_role,
            from_status="blocked",
            to_status="open",
        )
        promoted.append(row["job_id"])
    return cancelled, promoted


def command_promote_ready(args: argparse.Namespace) -> int:
    with connect(args.db) as con:
        init_schema(con)
        begin_immediate(con)
        cancelled, promoted = reconcile_handoffs(con, args.actor_role)
        con.commit()
    if cancelled or promoted or not getattr(args, "quiet", False):
        for job_id in cancelled:
            print(f"CANCELLED {job_id}")
        for job_id in promoted:
            print(f"PROMOTED {job_id}")
        print(f"Summary: cancelled={len(cancelled)} promoted={len(promoted)}")
    return 0


def command_events(args: argparse.Namespace) -> int:
    with connect(args.db) as con:
        init_schema(con)
        rows = con.execute(
            """
            select created_at, event_type, actor_role, actor_id, from_status, to_status, message
            from handoff_events
            where job_id = ?
            order by id
            """,
            (args.job_id,),
        ).fetchall()
    for row in rows:
        actor = row["actor_role"] or ""
        if row["actor_id"]:
            actor = f"{actor}/{row['actor_id']}" if actor else row["actor_id"]
        print(
            f"{row['created_at']}\t{row['event_type']}\t{actor}\t"
            f"{row['from_status'] or ''}->{row['to_status'] or ''}\t{row['message'] or ''}"
        )
    return 0


def command_stop(args: argparse.Namespace) -> int:
    if not args.all and not args.role:
        raise SystemExit("ERROR: use --all or --role")
    scope = "all" if args.all else f"role:{normalize_role(args.role)}"
    with connect(args.db) as con:
        init_schema(con)
        begin_immediate(con)
        if args.role:
            resolve_role(con, args.role)
        upsert_control(con, scope, True, args.reason)
        event(con, "control_stopped", actor_role="sm", message=f"{scope}: {args.reason}")
        con.commit()
    print(f"Stopped {scope}")
    return 0


def command_resume(args: argparse.Namespace) -> int:
    if not args.all and not args.role:
        raise SystemExit("ERROR: use --all or --role")
    scope = "all" if args.all else f"role:{normalize_role(args.role)}"
    with connect(args.db) as con:
        init_schema(con)
        begin_immediate(con)
        if args.role:
            resolve_role(con, args.role)
        upsert_control(con, scope, False)
        event(con, "control_resumed", actor_role="sm", message=scope)
        con.commit()
    print(f"Resumed {scope}")
    return 0


def control_scope_from_args(con: sqlite3.Connection, args: argparse.Namespace) -> str:
    if not args.all and not args.role:
        raise SystemExit("ERROR: use --all or --role")
    if args.all:
        return "all"
    role = resolve_role(con, args.role)
    return f"role:{role}"


def command_shift_start(args: argparse.Namespace) -> int:
    with connect(args.db) as con:
        init_schema(con)
        begin_immediate(con)
        scope = control_scope_from_args(con, args)
        work_until = format_utc(datetime.now(timezone.utc) + parse_duration(args.duration))
        set_shift_until(con, scope, work_until, "shift active")
        event(con, "shift_started", actor_role="sm", message=f"{scope} until {work_until}")
        con.commit()
    print(f"{scope}\tactive\tuntil={work_until}")
    return 0


def command_shift_extend(args: argparse.Namespace) -> int:
    with connect(args.db) as con:
        init_schema(con)
        begin_immediate(con)
        scope = control_scope_from_args(con, args)
        row = con.execute("select work_until from handoff_controls where scope = ?", (scope,)).fetchone()
        now = datetime.now(timezone.utc)
        base = now
        if row and row["work_until"]:
            current_until = parse_utc(row["work_until"])
            if current_until > now:
                base = current_until
        work_until = format_utc(base + parse_duration(args.duration))
        set_shift_until(con, scope, work_until, "shift active")
        event(con, "shift_extended", actor_role="sm", message=f"{scope} until {work_until}")
        con.commit()
    print(f"{scope}\tactive\tuntil={work_until}")
    return 0


def command_shift_end(args: argparse.Namespace) -> int:
    reason = args.reason or "shift ended"
    with connect(args.db) as con:
        init_schema(con)
        begin_immediate(con)
        scope = control_scope_from_args(con, args)
        upsert_control(con, scope, True, reason)
        event(con, "shift_ended", actor_role="sm", message=f"{scope}: {reason}")
        con.commit()
    print(f"{scope}\tstopped\t{reason}")
    return 0


def command_shift_status(args: argparse.Namespace) -> int:
    with connect(args.db) as con:
        init_schema(con)
        begin_immediate(con)
        if args.role:
            scope = f"role:{resolve_role(con, args.role)}"
            scopes = ("all", scope)
            expire_shift_controls(con, scopes)
            rows = con.execute(
                """
                select scope, stopped, reason, work_until, updated_at
                from handoff_controls
                where scope in (?, ?)
                order by case scope when 'all' then 0 else 1 end
                """,
                scopes,
            ).fetchall()
        else:
            expire_shift_controls(con)
            rows = con.execute(
                """
                select scope, stopped, reason, work_until, updated_at
                from handoff_controls
                order by scope
                """
            ).fetchall()
        con.commit()
    if not rows:
        print("No shift controls.")
        return 0
    now = utc_now()
    for row in rows:
        expired = bool(row["work_until"] and row["work_until"] <= now)
        state = "stopped" if row["stopped"] else "active"
        if expired:
            state = "expired"
        until = f"\tuntil={row['work_until']}" if row["work_until"] else ""
        reason = f"\t{row['reason']}" if row["reason"] else ""
        print(f"{row['scope']}\t{state}\t{row['updated_at']}{until}{reason}")
    return 0


def command_control_status(args: argparse.Namespace) -> int:
    with connect(args.db) as con:
        init_schema(con)
        begin_immediate(con)
        expire_shift_controls(con)
        rows = con.execute(
            """
            select scope, stopped, reason, work_until, updated_at
            from handoff_controls
            order by scope
            """
        ).fetchall()
        con.commit()
    if not rows:
        print("No control flags.")
        return 0
    for row in rows:
        expired = bool(row["work_until"] and row["work_until"] <= utc_now())
        state = "stopped" if row["stopped"] else "running"
        if expired:
            state = "expired"
        until = f"\tuntil={row['work_until']}" if row["work_until"] else ""
        reason = f"\t{row['reason']}" if row["reason"] else ""
        print(f"{row['scope']}\t{state}\t{row['updated_at']}{until}{reason}")
    return 0


def command_wait(args: argparse.Namespace) -> int:
    deadline = None if args.timeout == 0 else time.monotonic() + args.timeout
    waiter_id, role, active_waiters = start_waiter(args, "handoff")
    agent_id = optional_agent_id(args)
    with connect(args.db) as con:
        init_schema(con)
        warn_agent_session_role(con, role, agent_id)
    try:
        while True:
            with connect(args.db) as con:
                init_schema(con)
                stopped = get_stop_control(con, role)
            if stopped:
                reason = f" reason={stopped['reason']}" if stopped["reason"] else ""
                print(f"Stopped waiting for role {role} by {stopped['scope']}.{reason}")
                return 3
            promote_args = argparse.Namespace(db=args.db, actor_role=role, quiet=True)
            command_promote_ready(promote_args)
            next_args = argparse.Namespace(
                db=args.db,
                role=role,
                agent_id=agent_id,
                agent_id_file=getattr(args, "agent_id_file", ""),
                quiet=True,
            )
            if command_next(next_args) == 0:
                return 0
            if deadline is not None and time.monotonic() >= deadline:
                if getattr(args, "explain", False):
                    with connect(args.db) as con:
                        init_schema(con)
                        explain_agent_eligibility(con, role, agent_id)
                print(f"Timed out waiting for role {role}")
                return 2
            interval = poll_sleep_seconds(args.interval, active_waiters, waiter_id)
            time.sleep(bounded_sleep_seconds(interval, deadline))
            active_waiters = heartbeat_waiter(
                args.db,
                waiter_id,
                "handoff",
                role,
                waiter_lease_seconds(args.interval),
            )
    finally:
        unregister_waiter(args.db, waiter_id)


def add_workspace_override_arguments(command: argparse.ArgumentParser) -> None:
    command.add_argument(
        "--accept-workspace-change",
        action="store_true",
        help="override a strict workspace mismatch with workspace.override authority",
    )
    command.add_argument(
        "--workspace-reason",
        default="",
        help="audited reason required when accepting a strict workspace mismatch",
    )
    command.add_argument(
        "--workspace-authorized-by-role",
        default="",
        help="role with workspace.override authorizing an intentional mismatch",
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="SQLite-backed Baton CLI",
        epilog=(
            "Agent operating guides: run 'baton guide list', then "
            "'baton guide show bootstrap|worker|planner|git|upgrade|changelog'. Read the upgrade guide "
            "after every executable change. Read-only project audit is "
            "available through the separate 'baton-report audit|summary' executable."
        ),
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {BATON_VERSION}")
    parser.add_argument(
        "--db",
        default=os.environ.get("BATON_DB", ""),
        help=(
            "SQLite database path; default: BATON_DB or "
            "<nearest-baton-marker>/.baton/baton.sqlite3"
        ),
    )
    parser.add_argument(
        "--workspace-root",
        default=os.environ.get("BATON_WORKSPACE_ROOT", ""),
        help=(
            "source workspace inspected by optional VCS policy; default: "
            "BATON_WORKSPACE_ROOT or current directory"
        ),
    )
    parser.add_argument("--agent-id-file", default="", help="local non-shared agent identity file")
    sub = parser.add_subparsers(dest="command", required=True)

    help_command = sub.add_parser("help", help="show command help; optionally name a command path")
    help_command.add_argument("command_path", nargs="*", metavar="COMMAND")
    help_command.set_defaults(func=command_help, root_parser=parser)

    init = sub.add_parser("init", help="initialize or adopt a Baton project in the selected directory")
    init.add_argument(
        "--project-root",
        default="",
        help="project root to initialize; default: current directory or discovered Baton project",
    )
    init.set_defaults(func=command_init)
    migrate = sub.add_parser(
        "migrate",
        help="apply or verify database migrations",
        description="Apply pending database migrations, or verify the current schema without changing it.",
    )
    migrate.add_argument("--check", action="store_true", help="verify schema version and integrity without migrating")
    migrate.set_defaults(func=command_migrate)
    sub.add_parser("update", help="deprecated alias for migrate").set_defaults(func=command_update)

    upgrade = sub.add_parser(
        "upgrade",
        help="inspect project workflow readiness before replacing the Baton executable",
    )
    upgrade_sub = upgrade.add_subparsers(dest="upgrade_command", required=True)
    upgrade_preflight = upgrade_sub.add_parser(
        "preflight",
        help="list workflow blockers without requiring the latest database schema",
        description=(
            "Inspect upgrade blockers without modifying workflow state. Exit 0 means the project "
            "has a global maintenance stop and no active waiters, handoffs, or claimed CR reviews."
        ),
    )
    upgrade_preflight.add_argument("--format", choices=("text", "json"), default="text")
    upgrade_preflight.set_defaults(func=command_upgrade_preflight)

    project = sub.add_parser("project", help="inspect or migrate project-local Baton state")
    project_sub = project.add_subparsers(dest="project_command", required=True)
    project_info = project_sub.add_parser("info", help="show project marker, database, and version metadata")
    project_info.add_argument("--format", choices=("text", "json"), default="text")
    project_info.set_defaults(func=command_project_info)
    project_migrate = project_sub.add_parser(
        "migrate",
        help="check and apply a project database migration plan",
        description=(
            "Discover a standard or legacy Baton database, prove migration compatibility without "
            "writing, then apply only with the plan token returned by --check."
        ),
    )
    project_migrate_action = project_migrate.add_mutually_exclusive_group(required=True)
    project_migrate_action.add_argument("--check", action="store_true", help="perform a read-only migration rehearsal")
    project_migrate_action.add_argument(
        "--apply", action="store_true", help="apply a previously checked migration plan"
    )
    project_migrate.add_argument(
        "--project-root",
        default="",
        help="project root; defaults to the nearest Baton marker, legacy database, or current directory",
    )
    project_migrate.add_argument(
        "--source-db",
        default="",
        help="explicit existing Baton database path when discovery fails",
    )
    project_migrate.add_argument("--plan-token", default="", help="token printed by --check; required with --apply")
    project_migrate.set_defaults(func=command_project_migrate)

    workspace = sub.add_parser("workspace", help="inspect optional source-control provenance")
    workspace_sub = workspace.add_subparsers(dest="workspace_command", required=True)
    workspace_check = workspace_sub.add_parser("check", help="compare the current workspace with Baton provenance")
    workspace_check.add_argument("--job", dest="job_id", default="", help="handoff whose latest baseline to check")
    workspace_check.add_argument("--format", choices=("text", "json"), default="text")
    workspace_check.set_defaults(func=command_workspace_check)
    workspace_events = workspace_sub.add_parser("events", help="show recorded workspace provenance")
    workspace_events.add_argument("--job", dest="job_id", default="")
    workspace_events.add_argument("--limit", type=int, default=100)
    workspace_events.add_argument("--format", choices=("text", "json"), default="text")
    workspace_events.set_defaults(func=command_workspace_events)

    guide = sub.add_parser("guide", help="show agent instructions bundled with this Baton version")
    guide_sub = guide.add_subparsers(dest="guide_command", required=True)
    guide_sub.add_parser("list", help="list bundled agent guides").set_defaults(func=command_guide_list)
    guide_show = guide_sub.add_parser("show", help="print one bundled agent guide")
    guide_show.add_argument("guide_name", choices=tuple(GUIDE_FILES))
    guide_show.set_defaults(func=command_guide_show)

    sub.add_parser("status", help="show handoff counts by status").set_defaults(func=command_status)
    handoff = sub.add_parser("handoff", help="inspect handoff jobs")
    handoff_sub = handoff.add_subparsers(dest="handoff_command", required=True)
    handoff_show = handoff_sub.add_parser("show", help="show one handoff including its work payload")
    handoff_show.add_argument("job_id")
    handoff_show.add_argument("--format", choices=("text", "json"), default="text")
    handoff_show.set_defaults(func=command_handoff_show)
    handoff_list = handoff_sub.add_parser("list", help="list handoffs with optional role and status filters")
    handoff_list.add_argument("--role", default="")
    handoff_list.add_argument("--status", choices=tuple(sorted(STATUSES)), default="")
    handoff_list.add_argument(
        "--outcome", choices=tuple(sorted(COMPLETION_OUTCOMES)), default=""
    )
    handoff_list.add_argument("--blocking", choices=("yes", "no"), default="")
    handoff_list.add_argument("--limit", type=int, default=100)
    handoff_list.add_argument("--format", choices=("text", "json"), default="text")
    handoff_list.set_defaults(func=command_handoff_list)
    evidence_correct = handoff_sub.add_parser(
        "evidence-correct",
        help="append a commit correction without changing original completion evidence",
    )
    evidence_correct.add_argument("job_id")
    evidence_correct.add_argument("--role", required=True)
    evidence_correct.add_argument("--claimed-by", default="")
    evidence_correct.add_argument("--commit", required=True)
    evidence_correct.add_argument("--reason", required=True)
    evidence_correct.add_argument("--allow-unresolved-commit", action="store_true")
    evidence_correct.set_defaults(func=command_handoff_evidence_correct)
    handoff_successors = handoff_sub.add_parser(
        "successors",
        help="inspect direct successor handoffs without assigning work",
        description="Inspect direct successor handoffs without assigning work.",
    )
    handoff_successors.add_argument("job_id", help="upstream handoff")
    handoff_successors.add_argument("--format", choices=("text", "json"), default="text")
    handoff_successors.set_defaults(func=command_handoff_successors)
    sub.add_parser("promote-ready", help="promote dependency-ready handoffs").set_defaults(
        func=command_promote_ready,
        actor_role="sm",
    )

    role = sub.add_parser("role", help="manage roles, aliases, and permissions")
    role_sub = role.add_subparsers(dest="role_command", required=True)
    role_sub.add_parser("list").set_defaults(func=command_role_list)
    role_add = role_sub.add_parser("add")
    role_add.add_argument("role_id")
    role_add.add_argument("--display-name", default="")
    role_add.add_argument("--description", default="")
    role_add.set_defaults(func=command_role_add)
    alias_add = role_sub.add_parser("alias-add")
    alias_add.add_argument("alias")
    alias_add.add_argument("role_id")
    alias_add.set_defaults(func=command_role_alias_add)
    permission_list = role_sub.add_parser("permission-list")
    permission_list.add_argument("role_id", nargs="?")
    permission_list.set_defaults(func=command_role_permission_list)
    permission_add = role_sub.add_parser("permission-add")
    permission_add.add_argument("role_id")
    permission_add.add_argument("permission")
    permission_add.set_defaults(func=command_role_permission_add)
    permission_remove = role_sub.add_parser("permission-remove")
    permission_remove.add_argument("role_id")
    permission_remove.add_argument("permission")
    permission_remove.set_defaults(func=command_role_permission_remove)

    agent = sub.add_parser("agent", help="manage local agent identity")
    agent_sub = agent.add_subparsers(dest="agent_command", required=True)
    agent_init = agent_sub.add_parser("init")
    agent_init.add_argument("--role", required=True)
    agent_init.add_argument("--label", default="")
    agent_init.add_argument("--agent-id", default="")
    agent_init.add_argument("--force", action="store_true")
    agent_init.set_defaults(func=command_agent_init)
    agent_sub.add_parser("show").set_defaults(func=command_agent_show)
    agent_workstream_add = agent_sub.add_parser(
        "workstream-add",
        help="register an agent for a specialized route within a role",
    )
    agent_workstream_add.add_argument("workstream")
    agent_workstream_add.add_argument("--role", required=True)
    agent_workstream_add.add_argument("--agent-id", default="")
    agent_workstream_add.set_defaults(func=command_agent_workstream_add)
    agent_workstream_remove = agent_sub.add_parser(
        "workstream-remove",
        help="remove an agent's specialized route",
    )
    agent_workstream_remove.add_argument("workstream")
    agent_workstream_remove.add_argument("--role", required=True)
    agent_workstream_remove.add_argument("--agent-id", default="")
    agent_workstream_remove.set_defaults(func=command_agent_workstream_remove)
    agent_workstream_list = agent_sub.add_parser(
        "workstream-list",
        help="list agent workstream registrations",
    )
    agent_workstream_list.add_argument("--role", default="")
    agent_workstream_list.add_argument("--agent-id", default="")
    agent_workstream_list.add_argument("--format", choices=("text", "json"), default="text")
    agent_workstream_list.set_defaults(func=command_agent_workstream_list)
    agent_session_set = agent_sub.add_parser(
        "session-set",
        help="register this agent's opt-in host thread and model",
    )
    agent_session_set.add_argument("--role", required=True)
    agent_session_set.add_argument("--agent-id", default="")
    agent_session_set.add_argument("--host", default="codex")
    agent_session_set.add_argument("--thread-id", required=True)
    agent_session_set.add_argument("--model", required=True)
    agent_session_set.add_argument(
        "--replace",
        action="store_true",
        help="deactivate this agent's previous active session after verifying the new thread",
    )
    agent_session_set.set_defaults(func=command_agent_session_set)
    agent_session_end = agent_sub.add_parser(
        "session-end",
        help="deactivate this agent's current notification endpoint",
    )
    agent_session_end.add_argument("--agent-id", default="")
    agent_session_end.add_argument("--reason", required=True)
    agent_session_end.set_defaults(func=command_agent_session_end)
    agent_session_list = agent_sub.add_parser(
        "session-list",
        help="list recorded runtime sessions and model metadata",
    )
    agent_session_list.add_argument("--role", default="")
    agent_session_list.add_argument("--agent-id", default="")
    agent_session_list.add_argument("--status", choices=("active", "inactive"), default="")
    agent_session_list.add_argument("--format", choices=("text", "json"), default="text")
    agent_session_list.set_defaults(func=command_agent_session_list)

    register = sub.add_parser("register", help="register a handoff job")
    register.add_argument("--title", required=True)
    register.add_argument("--role", required=True)
    register.add_argument("--workstream", default="")
    register.add_argument("--source-ref", default="")
    register.add_argument("--objective", required=True)
    register.add_argument("--exit-criteria", required=True)
    register.add_argument("--depends-on", action="append", default=[])
    register.add_argument("--depends-on-gate", action="append", default=[])
    register.add_argument("--actor-role", default="sm")
    add_workspace_override_arguments(register)
    register.set_defaults(func=command_register)

    gate = sub.add_parser("gate", help="manage named workflow gates")
    gate_sub = gate.add_subparsers(dest="gate_command", required=True)
    gate_create = gate_sub.add_parser("create", help="create a pending named gate")
    gate_create.add_argument("gate_name")
    gate_create.add_argument("--role", required=True, help="role creating the gate")
    gate_create.add_argument(
        "--owner-role",
        action="append",
        default=[],
        help="role allowed to resolve or transfer the gate; repeat for joint ownership; defaults to --role",
    )
    gate_create.set_defaults(func=command_gate_create)
    gate_status = gate_sub.add_parser("status", help="show one or all gates")
    gate_status.add_argument("gate_name", nargs="?")
    gate_status.set_defaults(func=command_gate_status)
    gate_release = gate_sub.add_parser("release", help="release a pending gate")
    gate_release.add_argument("gate_name")
    gate_release.add_argument("--role", required=True)
    gate_release.add_argument("--evidence", required=True)
    gate_release.set_defaults(func=command_gate_release)
    gate_cancel = gate_sub.add_parser("cancel", help="cancel a pending gate and blocked dependents")
    gate_cancel.add_argument("gate_name")
    gate_cancel.add_argument("--role", required=True)
    gate_cancel.add_argument("--reason", required=True)
    gate_cancel.set_defaults(func=command_gate_cancel)
    gate_transfer = gate_sub.add_parser("transfer", help="replace owners of a pending gate")
    gate_transfer.add_argument("gate_name")
    gate_transfer.add_argument("--role", required=True, help="current owner or role with gate.manage")
    gate_transfer.add_argument("--owner-role", action="append", required=True)
    gate_transfer.add_argument("--reason", required=True)
    gate_transfer.set_defaults(func=command_gate_transfer)
    gate_events = gate_sub.add_parser("events", help="show gate audit history")
    gate_events.add_argument("gate_name")
    gate_events.set_defaults(func=command_gate_events)

    cr = sub.add_parser("cr", help="manage change requests")
    cr_sub = cr.add_subparsers(dest="cr_command", required=True)
    cr_list = cr_sub.add_parser("list", help="list change requests with review and integrity filters")
    cr_list.add_argument("--status", choices=tuple(sorted(CR_STATUSES)), default="")
    cr_list.add_argument("--reviewer-role", default="")
    cr_list.add_argument("--reviewer-workstream", default="")
    cr_list.add_argument(
        "--claimed-by",
        default="",
        help="active submitted-review claimant, or 'unassigned'",
    )
    cr_list.add_argument(
        "--body-integrity",
        choices=("editable", "legacy-unsealed", "missing", "mismatch", "ok", "unreadable"),
        default="",
    )
    cr_list.add_argument("--limit", type=int, default=100)
    cr_list.add_argument("--format", choices=("text", "json"), default="text")
    cr_list.set_defaults(func=command_cr_list)
    cr_create = cr_sub.add_parser("create")
    cr_create.add_argument("--title", required=True)
    cr_create.add_argument("--author-role", required=True)
    cr_create.add_argument("--reviewer-role", default="sm")
    cr_create.add_argument("--reviewer-workstream", default="")
    cr_create.add_argument(
        "--dir",
        default=DEFAULT_CR_DIRECTORY,
        help=(
            "CR body directory relative to the Baton control root; "
            f"default: {DEFAULT_CR_DIRECTORY}"
        ),
    )
    cr_create.add_argument("--file-path", default="", help="explicit CR Markdown path")
    cr_create.set_defaults(func=command_cr_create)

    cr_submit = cr_sub.add_parser("submit")
    cr_submit.add_argument("cr_id")
    cr_submit.add_argument("--role", required=True)
    cr_submit.set_defaults(func=command_cr_submit)

    cr_resubmit = cr_sub.add_parser("resubmit")
    cr_resubmit.add_argument("cr_id")
    cr_resubmit.add_argument("--role", required=True)
    cr_resubmit.add_argument("--evidence", default="")
    cr_resubmit.set_defaults(func=command_cr_resubmit)

    cr_request_revision = cr_sub.add_parser("request-revision")
    cr_request_revision.add_argument("cr_id")
    cr_request_revision.add_argument("--role", required=True)
    cr_request_revision.add_argument("--claimed-by", default="")
    cr_request_revision.add_argument("--reason", required=True)
    cr_request_revision.add_argument("--assign-back", default="")
    cr_request_revision.add_argument("--title", default="")
    cr_request_revision.set_defaults(func=command_cr_request_revision)

    cr_approve = cr_sub.add_parser("approve")
    cr_approve.add_argument("cr_id")
    cr_approve.add_argument("--role", required=True)
    cr_approve.add_argument("--claimed-by", default="")
    cr_approve.add_argument("--evidence", default="")
    cr_approve.set_defaults(func=command_cr_approve)

    cr_seal = cr_sub.add_parser(
        "seal",
        help="seal the current body of a legacy approved CR for integrity checks",
    )
    cr_seal.add_argument("cr_id", metavar="CR_ID")
    cr_seal.add_argument("--role", required=True)
    cr_seal.add_argument("--evidence", required=True)
    cr_seal.set_defaults(func=command_cr_seal)

    cr_reject = cr_sub.add_parser("reject")
    cr_reject.add_argument("cr_id")
    cr_reject.add_argument("--role", required=True)
    cr_reject.add_argument("--claimed-by", default="")
    cr_reject.add_argument("--reason", required=True)
    cr_reject.set_defaults(func=command_cr_reject)

    cr_reassign_reviewer = cr_sub.add_parser("reassign-reviewer")
    cr_reassign_reviewer.add_argument("cr_id")
    cr_reassign_reviewer.add_argument("--role", required=True)
    cr_reassign_reviewer.add_argument("--reviewer-role", required=True)
    cr_reassign_reviewer.add_argument("--reviewer-workstream", default="")
    cr_reassign_reviewer.add_argument("--reason", required=True)
    cr_reassign_reviewer.set_defaults(func=command_cr_reassign_reviewer)

    cr_cancel = cr_sub.add_parser("cancel")
    cr_cancel.add_argument("cr_id")
    cr_cancel.add_argument("--role", required=True)
    cr_cancel.add_argument("--reason", required=True)
    cr_cancel.set_defaults(func=command_cr_cancel)

    cr_supersede = cr_sub.add_parser(
        "supersede",
        help="replace an approved CR and retire its unfinished implementation work",
    )
    cr_supersede.add_argument("cr_id", metavar="OLD_CR_ID")
    cr_supersede_target = cr_supersede.add_mutually_exclusive_group(required=True)
    cr_supersede_target.add_argument("--by", dest="by_cr", default="", metavar="NEW_CR_ID")
    cr_supersede_target.add_argument(
        "--by-source-ref",
        default="",
        metavar="SOURCE_REF",
        help="immutable authoritative design reference when no replacement CR is needed",
    )
    cr_supersede.add_argument("--role", required=True)
    cr_supersede.add_argument("--reason", required=True)
    cr_supersede.set_defaults(func=command_cr_supersede)

    cr_create_handoff = cr_sub.add_parser("create-handoff")
    cr_create_handoff.add_argument("cr_id")
    cr_create_handoff.add_argument("--by-role", required=True)
    cr_create_handoff.add_argument("--role", required=True)
    cr_create_handoff.add_argument("--workstream", default="")
    cr_create_handoff.add_argument("--title", required=True)
    cr_create_handoff.add_argument("--objective", required=True)
    cr_create_handoff.add_argument("--exit-criteria", required=True)
    cr_create_handoff.add_argument("--depends-on", action="append", default=[])
    cr_create_handoff.add_argument("--depends-on-gate", action="append", default=[])
    cr_create_handoff.set_defaults(func=command_cr_create_handoff)

    cr_link_handoff = cr_sub.add_parser(
        "link-handoff",
        help="link an existing finished handoff as an approved CR implementation",
    )
    cr_link_handoff.add_argument("cr_id")
    cr_link_handoff.add_argument("job_id")
    cr_link_handoff.add_argument("--role", required=True)
    cr_link_handoff.add_argument("--reason", required=True)
    cr_link_handoff.set_defaults(func=command_cr_link_handoff)

    cr_supersede_handoff = cr_sub.add_parser(
        "supersede-handoff",
        help="replace a cancelled implementation handoff while preserving CR audit history",
    )
    cr_supersede_handoff.add_argument("cr_id")
    cr_supersede_handoff.add_argument("retired_job_id", metavar="CANCELLED_JOB_ID")
    cr_supersede_handoff.add_argument("--replacement", dest="replacement_job_id", required=True)
    cr_supersede_handoff.add_argument("--role", required=True)
    cr_supersede_handoff.add_argument("--reason", required=True)
    cr_supersede_handoff.set_defaults(func=command_cr_supersede_handoff)

    cr_mark_implemented = cr_sub.add_parser("mark-implemented")
    cr_mark_implemented.add_argument("cr_id")
    cr_mark_implemented.add_argument("--role", required=True)
    cr_mark_implemented.add_argument("--evidence", required=True)
    cr_mark_implemented.set_defaults(func=command_cr_mark_implemented)

    cr_status = cr_sub.add_parser("status")
    cr_status.add_argument("cr_id")
    cr_status.add_argument(
        "--include-related-handoffs",
        action="store_true",
        help="show exact-source unlinked handoffs as neutral related records",
    )
    cr_status.set_defaults(func=command_cr_status)

    cr_show = cr_sub.add_parser("show", help="show CR metadata and the shared Markdown body")
    cr_show.add_argument("cr_id", metavar="CR_ID")
    cr_show.add_argument(
        "--include-related-handoffs",
        action="store_true",
        help="show exact-source unlinked handoffs as neutral related records",
    )
    cr_show.set_defaults(func=command_cr_show)

    cr_sync = cr_sub.add_parser("sync", help="reconcile managed Markdown frontmatter from SQLite state")
    cr_sync.add_argument("cr_id")
    cr_sync.set_defaults(func=command_cr_sync)

    cr_events = cr_sub.add_parser("events")
    cr_events.add_argument("cr_id")
    cr_events.set_defaults(func=command_cr_events)

    cr_claim_review = cr_sub.add_parser(
        "claim-review",
        help="claim one submitted CR review for a concrete agent",
    )
    cr_claim_review.add_argument("cr_id")
    cr_claim_review.add_argument("--role", required=True)
    cr_claim_review.add_argument("--claimed-by", default="")
    cr_claim_review.set_defaults(func=command_cr_claim_review)

    cr_release_review = cr_sub.add_parser(
        "release-review",
        help="release a claimed submitted CR review without changing its status",
    )
    cr_release_review.add_argument("cr_id")
    cr_release_review.add_argument("--role", required=True)
    cr_release_review.add_argument("--claimed-by", default="")
    cr_release_review.add_argument("--reason", required=True)
    cr_release_review.set_defaults(func=command_cr_release_review)

    cr_wait_review = cr_sub.add_parser("wait-review")
    cr_wait_review.add_argument("--role", required=True)
    cr_wait_review.add_argument("--agent-id", default="")
    cr_wait_review.add_argument("--timeout", type=int, default=900, help="seconds; default: 900; 0 means forever")
    cr_wait_review.add_argument(
        "--interval",
        type=parse_poll_interval,
        default=None,
        help="poll seconds or auto; default: auto (3 seconds per active waiter, maximum 30); minimum fixed value: 1",
    )
    cr_wait_review.set_defaults(func=command_cr_wait_review)

    next_parser = sub.add_parser("next", help="inspect the next ready handoff without waiting")
    next_parser.add_argument("--role", required=True)
    next_parser.add_argument("--agent-id", default="")
    next_parser.add_argument(
        "--explain",
        action="store_true",
        help="show resolved identity, session role, ownership, and workstream eligibility",
    )
    next_parser.set_defaults(func=command_next)

    claim = sub.add_parser("claim", help="claim an open handoff")
    claim.add_argument("job_id")
    claim.add_argument("--role", required=True)
    claim.add_argument("--claimed-by", default="")
    add_workspace_override_arguments(claim)
    claim.set_defaults(func=command_claim)

    finish = sub.add_parser("finish", help="finish a claimed handoff")
    finish.add_argument("job_id")
    finish.add_argument("--role", required=True)
    finish.add_argument("--evidence", required=True)
    finish.add_argument("--commit", default="")
    finish.add_argument(
        "--outcome",
        choices=tuple(sorted(COMPLETION_OUTCOMES)),
        default="unspecified",
        help="structured completion result; default: unspecified",
    )
    finish.add_argument(
        "--blocking",
        action="store_true",
        help="mark a fail, conditional, or inconclusive outcome as blocking",
    )
    finish.add_argument("--outcome-cr", dest="outcome_cr_id", default="")
    finish.add_argument("--allow-unresolved-commit", action="store_true")
    finish.add_argument(
        "--unresolved-reason",
        default="",
        help="audited reason required for unresolved cross-repository commit evidence",
    )
    add_workspace_override_arguments(finish)
    finish.set_defaults(func=command_finish)

    fail = sub.add_parser("fail", help="fail a claimed handoff and submit a failure CR")
    fail.add_argument("job_id")
    fail.add_argument("--role", required=True)
    fail.add_argument("--reason", required=True)
    fail.add_argument("--evidence", default="")
    fail.add_argument("--reviewer-role", default="")
    fail.add_argument("--reviewer-workstream", default="")
    fail.add_argument("--title", default="")
    fail.add_argument(
        "--dir",
        default=DEFAULT_CR_DIRECTORY,
        help=(
            "failure CR directory relative to the Baton control root; "
            f"default: {DEFAULT_CR_DIRECTORY}"
        ),
    )
    fail.add_argument("--file-path", default="", help="explicit failure CR Markdown path")
    add_workspace_override_arguments(fail)
    fail.set_defaults(func=command_fail)

    retry = sub.add_parser("retry", help="retry a failed handoff after its failure CR is approved")
    retry.add_argument("job_id")
    retry.add_argument("--role", required=True)
    retry.add_argument("--cr-id", default="")
    retry.add_argument("--reason", required=True)
    retry.set_defaults(func=command_retry)

    cancel = sub.add_parser(
        "cancel",
        help="cancel queued work or request cooperative cancellation of claimed work",
    )
    cancel.add_argument("job_id")
    cancel.add_argument("--role", required=True)
    cancel.add_argument("--reason", required=True)
    cancel.add_argument(
        "--force",
        action="store_true",
        help="force final cancellation when the claimant cannot acknowledge",
    )
    cancel.set_defaults(func=command_cancel)

    cancel_ack = sub.add_parser(
        "cancel-ack",
        help="acknowledge a cancellation request as the handoff claimant",
    )
    cancel_ack.add_argument("job_id")
    cancel_ack.add_argument("--role", required=True)
    cancel_ack.add_argument("--claimed-by", default="")
    cancel_ack.add_argument("--evidence", required=True)
    cancel_ack.set_defaults(func=command_cancel_ack)

    cancel_withdraw = sub.add_parser(
        "cancel-withdraw",
        help="withdraw a cancellation request and resume the existing claim",
    )
    cancel_withdraw.add_argument("job_id")
    cancel_withdraw.add_argument("--role", required=True)
    cancel_withdraw.add_argument("--reason", required=True)
    cancel_withdraw.set_defaults(func=command_cancel_withdraw)

    notify = sub.add_parser(
        "notify",
        help="plan and record opt-in peer-thread notifications",
    )
    notify_sub = notify.add_subparsers(dest="notify_command", required=True)
    notify_targets = notify_sub.add_parser(
        "targets",
        help="show ready direct dependents and ranked active peer sessions",
        description=(
            "Show ready direct dependents and ranked active peer sessions. "
            "A stopped or expired target role returns outside_shift with no delivery candidate."
        ),
    )
    notify_targets.add_argument("job_id", help="finished source handoff")
    notify_targets.add_argument("--role", required=True, help="role planning the notification")
    notify_targets.add_argument("--from-agent", default="")
    notify_targets.add_argument("--format", choices=("text", "json"), default="text")
    notify_targets.set_defaults(func=command_notify_targets)
    notify_candidates = notify_sub.add_parser(
        "candidates",
        help="show ranked active peer sessions for one ready handoff",
        description=(
            "Show ranked active peer sessions for one open handoff without requiring a "
            "predecessor edge. A stopped or expired target role returns outside_shift."
        ),
    )
    notify_candidates.add_argument("job_id", help="open handoff to deliver")
    notify_candidates.add_argument("--role", required=True, help="role planning the notification")
    notify_candidates.add_argument("--from-agent", default="")
    notify_candidates.add_argument("--format", choices=("text", "json"), default="text")
    notify_candidates.set_defaults(func=command_notify_candidates)
    notify_record = notify_sub.add_parser(
        "record",
        help="record the result after the agent attempts a host message",
    )
    notify_record.add_argument("job_id", help="ready handoff named in the message")
    notify_record.add_argument("--role", required=True, help="sender role")
    notify_record.add_argument("--from-agent", default="")
    notify_record.add_argument("--to-agent", required=True)
    notify_record.add_argument("--status", choices=("sent", "failed"), required=True)
    notify_record.add_argument("--message-ref", default="")
    notify_record.add_argument("--detail", default="")
    notify_record.set_defaults(func=command_notify_record)
    notify_retry = notify_sub.add_parser(
        "retry",
        help="record one controlled recovery delivery for a stale unclaimed handoff",
        description=(
            "Record one same-recipient recovery delivery after a host-accepted notification "
            "becomes stale and remains unclaimed. This command records a host attempt; it does "
            "not send the message."
        ),
    )
    notify_retry.add_argument("job_id", help="open unclaimed handoff named in the recovery message")
    notify_retry.add_argument("--notification", type=int, required=True, help="latest host-accepted notification ID")
    notify_retry.add_argument("--role", required=True, help="sender role")
    notify_retry.add_argument("--from-agent", default="")
    notify_retry.add_argument("--status", choices=("sent", "failed"), required=True)
    notify_retry.add_argument("--reason", required=True)
    notify_retry.add_argument("--stale-after", default="15m")
    notify_retry.add_argument("--message-ref", default="")
    notify_retry.add_argument("--detail", default="")
    notify_retry.set_defaults(func=command_notify_retry)
    notify_status = notify_sub.add_parser(
        "status",
        help="derive host acceptance, claim, and stale-unclaimed state for one handoff",
    )
    notify_status.add_argument("job_id")
    notify_status.add_argument(
        "--stale-after",
        default="15m",
        help="duration before an accepted but unclaimed notification is stale; default: 15m",
    )
    notify_status.add_argument("--format", choices=("text", "json"), default="text")
    notify_status.set_defaults(func=command_notify_status)
    notify_list = notify_sub.add_parser("list", help="list notification delivery audit records")
    notify_list.add_argument("--job", dest="job_id", default="")
    notify_list.add_argument("--status", choices=("sent", "failed"), default="")
    notify_list.add_argument(
        "--order",
        choices=("oldest", "newest"),
        default="newest",
        help="sort by notification ID; default: newest",
    )
    notify_list.add_argument("--limit", type=int, default=None)
    notify_list.add_argument(
        "--after-id",
        type=int,
        default=None,
        help="show notification IDs greater than this exclusive cursor",
    )
    notify_list.add_argument(
        "--before-id",
        type=int,
        default=None,
        help="show notification IDs less than this exclusive cursor",
    )
    notify_list.add_argument(
        "--recovery-only",
        action="store_true",
        help="show only controlled recovery delivery records",
    )
    notify_list.add_argument("--format", choices=("text", "json"), default="text")
    notify_list.set_defaults(func=command_notify_list)

    events = sub.add_parser("events", help="show one handoff audit history")
    events.add_argument("job_id")
    events.set_defaults(func=command_events)

    stop = sub.add_parser("stop", help="stop role or global wait loops")
    stop_scope = stop.add_mutually_exclusive_group(required=True)
    stop_scope.add_argument("--all", action="store_true")
    stop_scope.add_argument("--role")
    stop.add_argument("--reason", default="")
    stop.set_defaults(func=command_stop)

    resume = sub.add_parser("resume", help="resume role or global wait loops")
    resume_scope = resume.add_mutually_exclusive_group(required=True)
    resume_scope.add_argument("--all", action="store_true")
    resume_scope.add_argument("--role")
    resume.set_defaults(func=command_resume)

    shift = sub.add_parser("shift", help="manage bounded agent work shifts")
    shift_sub = shift.add_subparsers(dest="shift_command", required=True)
    shift_start = shift_sub.add_parser("start")
    shift_start_scope = shift_start.add_mutually_exclusive_group(required=True)
    shift_start_scope.add_argument("--all", action="store_true")
    shift_start_scope.add_argument("--role")
    shift_start.add_argument("--duration", default="4h", help="duration such as 900, 30m, 8h, or 1d; default: 4h")
    shift_start.set_defaults(func=command_shift_start)
    shift_extend = shift_sub.add_parser("extend")
    shift_extend_scope = shift_extend.add_mutually_exclusive_group(required=True)
    shift_extend_scope.add_argument("--all", action="store_true")
    shift_extend_scope.add_argument("--role")
    shift_extend.add_argument("--duration", default="1h", help="duration such as 900, 30m, 8h, or 1d; default: 1h")
    shift_extend.set_defaults(func=command_shift_extend)
    shift_end = shift_sub.add_parser("end")
    shift_end_scope = shift_end.add_mutually_exclusive_group(required=True)
    shift_end_scope.add_argument("--all", action="store_true")
    shift_end_scope.add_argument("--role")
    shift_end.add_argument("--reason", default="shift ended")
    shift_end.set_defaults(func=command_shift_end)
    shift_status = shift_sub.add_parser("status")
    shift_status.add_argument("--role", default="")
    shift_status.set_defaults(func=command_shift_status)

    control = sub.add_parser("control", help="inspect wait control state")
    control_sub = control.add_subparsers(dest="control_command", required=True)
    control_sub.add_parser("status").set_defaults(func=command_control_status)

    wait = sub.add_parser("wait", help="wait for a ready handoff")
    wait.add_argument("--role", required=True)
    wait.add_argument("--agent-id", default="")
    wait.add_argument(
        "--explain",
        action="store_true",
        help="show eligibility details when the bounded wait times out",
    )
    wait.add_argument("--timeout", type=int, default=900, help="seconds; default: 900; 0 means forever")
    wait.add_argument(
        "--interval",
        type=parse_poll_interval,
        default=None,
        help="poll seconds or auto; default: auto (3 seconds per active waiter, maximum 30); minimum fixed value: 1",
    )
    wait.set_defaults(func=command_wait)

    watch = sub.add_parser(
        "watch",
        help="wait for assigned CR review first, then a ready handoff",
        description="Wait for assigned CR review first, then a ready handoff.",
    )
    watch.add_argument("--role", required=True)
    watch.add_argument("--agent-id", default="")
    watch.add_argument(
        "--explain",
        action="store_true",
        help="show handoff and CR eligibility details when the bounded watch times out",
    )
    watch.add_argument("--timeout", type=int, default=900, help="seconds; default: 900; 0 means forever")
    watch.add_argument(
        "--interval",
        type=parse_poll_interval,
        default=None,
        help="poll seconds or auto; default: auto (3 seconds per active waiter, maximum 30); minimum fixed value: 1",
    )
    watch.set_defaults(func=command_watch)
    return parser


def main() -> int:
    parser = build_parser()
    argv = sys.argv[1:]
    if argv and argv[0] == "help":
        argv = [*argv[1:], "--help"]
    args = parser.parse_args(argv)
    args.db_explicit = bool(os.environ.get("BATON_DB")) or any(
        token == "--db" or token.startswith("--db=") for token in argv
    )
    try:
        needs_default_database = args.command not in {"help", "guide"} and not (
            args.command == "project" and args.project_command == "migrate"
        ) and not (
            args.command == "init" and args.project_root
        )
        if needs_default_database and not args.db:
            args.db = default_database_path()
        return args.func(args)
    except MigrationError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
