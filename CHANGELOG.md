# Changelog

## v0.1.3

- Changed default wait polling interval from 30 seconds to 3 seconds for faster handoff and CR review response.
- Added validation that `--interval` must be at least 1 second.
- Updated wait interval documentation and agent prompts.
- Removed remaining prototype wording from user-facing Baton documentation and CLI help.

## v0.1.2

- Documented how to use Baton from another project.
- Added copyable Codex agent prompts for handoff workers, CR reviewers, and revision workers.
- Clarified stable distribution through release tags, submodules, plain clones, and release archives.
- Documented default wait settings: `--timeout 900` and `--interval 30`.
- Updated the README introduction from prototype wording to Baton CLI wording.

## v0.1.1

- Aligned CR reviewer permission behavior with the documented workflow.

## v0.1.0

- Added the SQLite-backed Baton CLI for handoff, CR review, shift control, stop/resume, and agent identity workflows.
