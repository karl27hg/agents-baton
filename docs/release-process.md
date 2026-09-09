# Release Process

This checklist is mandatory before approving or publishing any Baton version.

## Release Gate

1. Freeze the intended code and schema scope. Do not include project-local `.baton/` data or feedback unless the release explicitly changes those files.
2. Run focused tests for each changed behavior, then the complete test suite and isolated pipx lifecycle test.
3. Compare the implementation and CLI help with `README.md`, `README.ko.md`, `CHANGELOG.md`, `docs/schema.md`, `docs/schema.ko.md`, and every affected operating guide.
4. Verify command names, options, defaults, exit behavior, lifecycle semantics, permissions, migration numbers, compatibility boundaries, and recovery instructions.
5. Update the canonical guides and their packaged copies. `tests/guides.sh` must confirm that each `baton guide show` result is byte-for-byte identical to its source document.
6. Search for stale current-version and current-schema claims. Historical release notes may retain older values when they are clearly scoped to that release.
7. Run `git diff --check`, syntax or compile checks, and the complete test suite again after documentation changes.
8. Inspect the final diff and package version. Only then commit, open or update the pull request, merge, tag, push, and replace the local pipx installation when requested.

A release is blocked when documented behavior differs from the implementation, a bundled guide differs from its canonical document, a migration path is untested, or a required test fails. Fix the gap before version approval; do not defer the correction to the next release.

## Database Release Rules

- Append a migration and increment `LATEST_SCHEMA_VERSION`; never edit a released migration in place.
- Preserve existing workflow and audit rows. Backfill new fields with explicit compatibility values rather than inferring historical decisions.
- Verify direct upgrade from the immediately previous release schema and keep older released migration steps available.
- Confirm `upgrade preflight`, `migrate`, `migrate --check`, backup creation, and project-local isolation.
- Do not migrate user project databases merely by installing or uninstalling the executable.

## Publication Check

Record the release version in the package and changelog, verify the commit on the intended release branch, and create the tag from the reviewed merge commit. A release candidate may be published from `main` when it is the shared integration baseline; the tag, not the branch name, identifies the immutable release.
