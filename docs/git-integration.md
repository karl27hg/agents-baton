# Optional Git Workspace Integration

English (primary) | [한국어](git-integration.ko.md)

Baton remains independent of Git. Its SQLite database records live workflow state, while Git remains the authority for source and document history. Optional workspace integration records only commit provenance and detects likely checkout mismatches; it does not copy commits, diffs, or file contents into Baton.

## Enable The Integration

Create a tracked `baton.toml` in the Baton project root, beside the `.baton/` directory:

```toml
[baton]
required_version = ">=0.6.0.dev0,<0.7"

[vcs]
provider = "git"
policy = "warn"
```

`required_version` accepts comma-separated numeric version comparisons using `==`, `!=`, `<`, `<=`, `>`, and `>=`. Prerelease phases `dev`, `a`, `b`, and `rc` are supported. Keep `baton.toml` tracked in Git; keep `.baton/` ignored.

If `baton.toml` is absent, the effective policy is `off` and Baton does not execute Git. If `provider = "git"` is present and `policy` is omitted, the policy defaults to `warn`.

After a configured project has recorded workspace provenance, removing `baton.toml` does not silently disable protection. Baton retains the most recently recorded `warn` or `strict` behavior for inspection, reports the missing tracked config as a mismatch, and requires the file to be restored or an intentional strict override to be audited. A project can disable integration deliberately by committing `policy = "off"` before the transition.

## Policy Modes

| Policy | Behavior |
| --- | --- |
| `off` | Do not inspect Git or record automatic workspace provenance. This is the backward-compatible default without Git configuration. |
| `warn` | Inspect and record the workspace. Allow transitions when an issue is found, print one warning, and record a `warning` workspace event. |
| `strict` | Inspect and record the workspace. Reject an incompatible state transition unless a role with `workspace.override` supplies an audited override reason. |

`warn` is the default after Git integration is enabled because rebases, cherry-picks, intentional branch transfers, and older handoffs without a baseline can require operator judgment. Promote a project to `strict` only after its branch workflow has been validated.

## Recorded Provenance

For configured `warn` and `strict` projects, Baton records a workspace event when a handoff is registered, claimed, or finished:

- immutable HEAD commit
- informational branch name, or `DETACHED`
- dirty working-tree flag
- baseline commit used for the check
- policy and outcome
- actor role, warning, or override reason

The dirty flag is evidence, not an automatic mismatch. Existing user changes can be legitimate, so policy decisions currently use Baton version compatibility, Git availability, and commit ancestry. Baton never stores `git diff`, source contents, or the Git log.

## Ancestry Rules

Registration captures the initial handoff commit. Claim checks that the current HEAD descends from the registration commit. Finish checks that the current HEAD descends from the claim commit, falling back to the registration commit for a legacy handoff without claim provenance.

Moving forward through normal commits is accepted. Switching to an ancestor or a divergent branch produces a warning or strict rejection. Branch names are not authoritative because they can be renamed or moved.

The current integration guards handoff `register`, `claim`, and `finish`. CR Markdown continues to use Baton's existing atomic frontmatter and concurrent-edit checks; Git ancestry is not yet a CR approval rule.

## Inspect The Workspace

Inspect the current project:

```bash
baton workspace check
```

Compare it with a handoff's latest recorded baseline:

```bash
baton workspace check --job HO-YYYY-MM-DD-001
```

Show provenance directly or through the read-only audit report:

```bash
baton workspace events
baton workspace events --job HO-YYYY-MM-DD-001
baton-report audit --job HO-YYYY-MM-DD-001
```

Workspace checks run only for explicit inspection and the three state-changing handoff commands. They do not run inside `wait` or `cr wait-review` polling loops.

## Strict Override

Do not bypass `strict` for ordinary branch drift. For an intentional transfer, provide a concrete reason and an authorizing role with `workspace.override`:

```bash
baton claim HO-YYYY-MM-DD-001 \
  --role backend \
  --accept-workspace-change \
  --workspace-reason "Intentional transfer to the release branch" \
  --workspace-authorized-by-role sm
```

The default `sm` role receives `workspace.override` through schema migration 6. Grant it to another role only when that role is expected to authorize source-context changes. A successful override is recorded in `workspace_events`.

## Checkout Procedure

Before changing the active branch in a shared working directory:

```bash
baton stop --all --reason "Git checkout maintenance"
baton handoff list --status in_progress
git status --short
git switch <branch>
baton workspace check
baton project info
baton shift status
```

Finish or explicitly cancel in-progress handoffs before the checkout. Resume only after reviewing the new branch and applicable shift controls. An expired shift requires an authorized `shift start` or `shift extend`; `resume` alone does not extend it.

## Limits

- Baton does not watch `.git/` or detect checkout events in the background.
- A Git branch intended for Baton work should contain the tracked `baton.toml`; a missing file after prior use is treated as a policy issue rather than a fresh `off` project.
- Baton does not install Git hooks.
- An external `--db` has no implicit project root, so project `baton.toml` integration is disabled for that database.
- Separate Git worktrees should use separate project-local `.baton/` databases. Do not point concurrent worktrees at one external SQLite file.
- `git clean -fdx` can delete ignored `.baton/` state. Back up the database before destructive workspace cleanup.
- A schema migration is not reversible by Git checkout. Do not use an older Baton binary after migrating unless it explicitly supports that schema.
