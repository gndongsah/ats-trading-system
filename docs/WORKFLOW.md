# ATS Cross-Tool Delivery Workflow

This document defines how **ChatGPT, Notion, GitHub, and VS Code** work together without creating multiple competing sources of truth.

## Tool responsibilities

| Tool | Role | Source of truth for |
|---|---|---|
| ChatGPT project | Planning, analysis, review, orchestration | Current conversation context and execution guidance |
| Notion | Product management | Vision, backlog, sprints, acceptance criteria, ADR status |
| GitHub | Engineering record | Code, tests, architecture docs, PRs, accepted implementation history |
| VS Code | Local implementation cockpit | Editing, running, debugging, tests, lint, Docker, developer feedback |

## Source-of-truth rules

1. **Strategy rules:** `playbook/playbook_v3.yaml`.
2. **Product scope/status:** Notion.
3. **Accepted code and implementation architecture:** GitHub `main`.
4. **Local working state:** VS Code checkout/branch.
5. **Conversation decisions:** must be promoted to Notion/GitHub when they affect product or architecture; chat alone is not a durable implementation record.

## Standard story lifecycle

### 1. Select work
- Start from the Notion sprint/backlog.
- Confirm acceptance criteria and dependencies.
- ChatGPT checks the current Notion item and GitHub `main` before recommending implementation.

### 2. Create implementation branch
Branch naming:

`feature/ats-NNN-short-description`

Architecture/documentation-only work may use:

`docs/short-description`

### 3. Implement in VS Code
Use the repository tasks:
- `ATS: Install Dev`
- `ATS: Ruff`
- `ATS: Pytest`
- `ATS: Pre-PR Validation`
- `ATS: API`
- Docker and PostgreSQL tasks when needed.

Never put credentials in committed VS Code configuration.

### 4. Validate locally
Minimum:
- Ruff passes.
- Relevant focused tests pass.
- Full pytest suite passes before PR unless an explicitly documented environment-dependent test is unavailable.
- Database/point-in-time/risk changes run their dedicated acceptance tests.
- Architecture invariants affected by the change have explicit tests.

### 5. Open pull request
PR body must include:
- Notion story / ATS ID.
- What changed.
- Acceptance criteria mapping.
- Tests/evidence.
- Architecture impact.
- Data migration impact.
- Risk/safety impact.
- Any deferred work.

### 6. Review
ChatGPT may review the PR diff, tests, unresolved threads, and acceptance criteria.

Do not mark the Notion story Done because a PR exists.

### 7. Merge
Merge only after required CI/review is green.

GitHub `main` becomes the accepted implementation record.

### 8. Reconcile Notion
After merge:
- update story status to Done only if Definition of Done is met;
- attach/record the PR or merge evidence where supported;
- update sprint status/blockers;
- create or update ADRs when architecture changed.

### 9. Continue in ChatGPT
When the user says `merged done`, ChatGPT should:
1. inspect the accepted GitHub tree/CI when available;
2. reconcile Notion status;
3. identify the next dependency-ready story;
4. preserve architecture and safety constraints.

## Architecture change workflow

A material architecture change requires all four surfaces:

1. **ChatGPT:** analyze tradeoffs and impact.
2. **Notion:** ADR + backlog/sprint impact.
3. **GitHub:** update `docs/architecture` plus code/tests.
4. **VS Code:** run local architecture-relevant validation before PR.

Architecture baseline changes should use a version increment such as v1.1 → v1.2.

## Trading-safety workflow

No tool may create a second path around the Risk Engine.

- AI research outputs evidence/judgments only.
- Portfolio State normalizes account state.
- Risk Engine returns ALLOW / RESIZE / DENY.
- Human Approval/Autonomy Policy authorizes execution separately.
- Execution Service alone owns broker credentials.
- LIVE, PAPER, and REPLAY records remain distinguishable.

## Daily solo-developer loop

1. Open Notion active sprint.
2. Pick one dependency-ready ATS story.
3. Pull `main` in VS Code and create/switch to the story branch.
4. Ask ChatGPT to review the story + current code before major implementation.
5. Implement and test in VS Code.
6. Run `ATS: Pre-PR Validation`.
7. Push and open PR in GitHub.
8. Use ChatGPT for PR/acceptance review.
9. Merge after CI.
10. Reconcile Notion and select the next story.

This loop is intentionally boring: it keeps product scope, code, architecture, and local work synchronized.
