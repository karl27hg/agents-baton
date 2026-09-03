#!/usr/bin/env python3
"""Read-only Baton reporting CLI."""

from __future__ import annotations

import argparse
import csv
import json
import sqlite3
import sys
from pathlib import Path

from agents_baton.cli import MigrationError, check_schema, default_database_path


def connect_readonly(db_path: str) -> sqlite3.Connection:
    path = Path(db_path)
    if not path.exists():
        raise SystemExit(f"ERROR: database does not exist: {db_path}")
    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    return con


def row_dict(row: sqlite3.Row) -> dict[str, object]:
    return {key: row[key] for key in row.keys()}


def print_rows(rows: list[dict[str, object]], fmt: str) -> None:
    if fmt == "json":
        print(json.dumps(rows, indent=2, ensure_ascii=False))
        return
    if fmt == "csv":
        if not rows:
            return
        writer = csv.DictWriter(sys.stdout, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)
        return
    for row in rows:
        print(
            f"{row['created_at']}\t{row['source']}\t{row['target_id'] or ''}\t"
            f"{row['event_type']}\t{row['actor'] or ''}\t"
            f"{row['from_status'] or ''}->{row['to_status'] or ''}\t{row['message'] or ''}"
        )


def command_audit(args: argparse.Namespace) -> int:
    conditions = []
    params: list[str] = []
    if args.since:
        conditions.append("created_at >= ?")
        params.append(args.since)
    if args.role:
        conditions.append("actor_role = ?")
        params.append(args.role)
    handoff_conditions = list(conditions)
    handoff_params = list(params)
    cr_conditions = list(conditions)
    cr_params = list(params)
    gate_conditions = list(conditions)
    gate_params = list(params)
    workspace_conditions = list(conditions)
    workspace_params = list(params)
    if args.job:
        handoff_conditions.append("job_id = ?")
        handoff_params.append(args.job)
        cr_conditions.append("0")
        gate_conditions.append("0")
        workspace_conditions.extend(("entity_type = 'handoff'", "entity_id = ?"))
        workspace_params.append(args.job)
    if args.cr:
        cr_conditions.append("cr_id = ?")
        cr_params.append(args.cr)
        handoff_conditions.append("0")
        gate_conditions.append("0")
        workspace_conditions.extend(("entity_type = 'cr'", "entity_id = ?"))
        workspace_params.append(args.cr)
    if args.gate:
        gate_conditions.append("gate_name = ?")
        gate_params.append(args.gate)
        handoff_conditions.append("0")
        cr_conditions.append("0")
        workspace_conditions.append("0")

    handoff_where = " and ".join(handoff_conditions) if handoff_conditions else "1"
    cr_where = " and ".join(cr_conditions) if cr_conditions else "1"
    gate_where = " and ".join(gate_conditions) if gate_conditions else "1"
    workspace_where = " and ".join(workspace_conditions) if workspace_conditions else "1"
    limit = " limit ?" if args.limit else ""
    limit_params: list[int] = [args.limit] if args.limit else []

    with connect_readonly(args.db) as con:
        check_schema(con)
        rows = con.execute(
            f"""
            select *
            from (
              select
                id as event_id,
                created_at,
                'handoff' as source,
                job_id as target_id,
                event_type,
                case
                  when actor_id is not null and actor_role is not null then actor_role || '/' || actor_id
                  when actor_id is not null then actor_id
                  else actor_role
                end as actor,
                actor_role,
                from_status,
                to_status,
                message
              from handoff_events
              where {handoff_where}
              union all
              select
                id as event_id,
                created_at,
                'cr' as source,
                cr_id as target_id,
                event_type,
                actor_role as actor,
                actor_role,
                from_status,
                to_status,
                message
              from cr_events
              where {cr_where}
              union all
              select
                id as event_id,
                created_at,
                'gate' as source,
                gate_name as target_id,
                event_type,
                actor_role as actor,
                actor_role,
                from_status,
                to_status,
                message
              from gate_events
              where {gate_where}
              union all
              select
                id as event_id,
                created_at,
                'workspace' as source,
                entity_id as target_id,
                operation || ':' || outcome as event_type,
                actor_role as actor,
                actor_role,
                null as from_status,
                null as to_status,
                trim(
                  'head=' || coalesce(head_commit, '') ||
                  ' baseline=' || coalesce(baseline_commit, '') ||
                  ' branch=' || coalesce(branch, '') ||
                  ' dirty=' || dirty ||
                  ' ' || coalesce(message, '')
                ) as message
              from workspace_events
              where {workspace_where}
            )
            order by created_at, source, target_id, event_id
            {limit}
            """,
            handoff_params + cr_params + gate_params + workspace_params + limit_params,
        ).fetchall()
    output_rows = [
        {
            key: value
            for key, value in row_dict(row).items()
            if key not in {"actor_role", "event_id"}
        }
        for row in rows
    ]
    print_rows(output_rows, args.format)
    return 0


def count_by_status(con: sqlite3.Connection, table: str) -> list[dict[str, object]]:
    rows = con.execute(
        f"select status, count(*) as count from {table} group by status order by status"
    ).fetchall()
    return [row_dict(row) for row in rows]


def command_summary(args: argparse.Namespace) -> int:
    with connect_readonly(args.db) as con:
        check_schema(con)
        summary = {
            "handoffs": count_by_status(con, "handoff_jobs"),
            "change_requests": count_by_status(con, "change_requests"),
            "gates": count_by_status(con, "workflow_gates"),
            "events": {
                "handoff_events": con.execute("select count(*) from handoff_events").fetchone()[0],
                "cr_events": con.execute("select count(*) from cr_events").fetchone()[0],
                "gate_events": con.execute("select count(*) from gate_events").fetchone()[0],
                "workspace_events": con.execute("select count(*) from workspace_events").fetchone()[0],
            },
        }
    if args.format == "json":
        print(json.dumps(summary, indent=2, ensure_ascii=False))
        return 0
    print("Handoffs:")
    for row in summary["handoffs"]:
        print(f"{row['status']}: {row['count']}")
    print("")
    print("Change Requests:")
    for row in summary["change_requests"]:
        print(f"{row['status']}: {row['count']}")
    print("")
    print("Gates:")
    for row in summary["gates"]:
        print(f"{row['status']}: {row['count']}")
    print("")
    print("Events:")
    print(f"handoff_events: {summary['events']['handoff_events']}")
    print(f"cr_events: {summary['events']['cr_events']}")
    print(f"gate_events: {summary['events']['gate_events']}")
    print(f"workspace_events: {summary['events']['workspace_events']}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Read-only Baton reporting CLI")
    parser.add_argument(
        "--db",
        default="",
        help="SQLite database path; default: <nearest-baton-marker>/.baton/baton.sqlite3",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    audit = sub.add_parser("audit")
    audit.add_argument("--job", default="")
    audit.add_argument("--cr", default="")
    audit.add_argument("--gate", default="")
    audit.add_argument("--role", default="")
    audit.add_argument("--since", default="", help="UTC timestamp lower bound")
    audit.add_argument("--limit", type=int, default=0)
    audit.add_argument("--format", choices=("text", "json", "csv"), default="text")
    audit.set_defaults(func=command_audit)

    summary = sub.add_parser("summary")
    summary.add_argument("--format", choices=("text", "json"), default="text")
    summary.set_defaults(func=command_summary)
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    try:
        if not args.db:
            args.db = default_database_path()
        return args.func(args)
    except MigrationError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
