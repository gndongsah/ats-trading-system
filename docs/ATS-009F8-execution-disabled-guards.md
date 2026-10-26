# ATS-009F8 - Execution-disabled guards

Base: `d18070a` (origin/feature/sprint-3-risk-engine). Coding-agent evidence only; **no independent review yet**.
Sprint 3 is not accepted. Trading, Layer 3 authorization, ATS-009 enforcement and paper/live order submission remain disabled.

## Changes
1. `src/layer3.py`: `Layer3Decision.execution_enabled` is `Literal[False]`; `model_construct` and `model_copy` are overridden to re-check/revalidate; `authorize` re-checks before persisting. Replay of a stored decision with `execution_enabled=True` fails closed (`ValidationError`).
2. `tests/test_execution_capability_guard.py`: AST regression guard over `src/` rejecting order-verb identifiers, order endpoint strings, non-allowlisted `webull.*` imports, and SDK calls other than `get_account_list`, `get_account_balance`, `get_account_position`, `list_activities`. The only allowed order-named definition is the always-raising `submit_order_to_broker` stub in `layer3.py`.
3. `tests/fail_on_skip.py` + root `conftest.py`: opt-in `--fail-on-skip` pytest flag covering collection-time and runtime skips (xfail excluded). Preserves existing failures; does not mask usage errors. `ci.yml` passes it in both the PostgreSQL step and the full-suite step.
4. Tests: `test_layer3_execution_invariant.py`, `test_fail_on_skip_guard.py` (pytester), `test_execution_capability_guard.py`, `test_ci_workflow_guards.py`.

Unchanged: risk thresholds, heat equity basis, broker trust flags, order enablement, credentials, network calls.

## Results (exact)
| Run | Result |
|---|---|
| Disposable PostgreSQL 16, `--fail-on-skip` | **702 passed, 0 skipped**, exit 0 |
| No PostgreSQL, no flag | 630 passed, 72 skipped (DB-gated), exit 0 |
| No PostgreSQL, `--fail-on-skip` | 630 passed, 72 skipped, **exit 1** (guard works) |
| `ruff check .` / `ruff format --check .` / `git diff --check` | pass (84 files formatted) |

Baseline at `d18070a` was 648 passed / 0 skipped (DB) and 576 / 72 (no DB); +54 tests added.

## Limitations
- A Python model is not a security boundary: `object.__setattr__`, `__dict__` writes and subclassing can forge `execution_enabled` (documented by a test; only `authorize`'s check catches it).
- Static scans are regression guards only; they miss computed `getattr`, `eval`, string-built URLs and third-party code.
- `--fail-on-skip` is opt-in; local runs without PostgreSQL still skip explicitly.
- The full SDK `TradeClient` is still constructed (F7 finding not addressed); heat-vs-lock equity basis question (PR #10) is untouched.

## Reproduce
```
docker run -d --rm --name ats-f8-pg -e POSTGRES_DB=ats_test -e POSTGRES_PASSWORD=<pw> -p 127.0.0.1:55432:5432 postgres:16-alpine
$env:ATS_TEST_DATABASE_URL="postgresql://postgres:<pw>@127.0.0.1:55432/ats_test"
python -m pytest -q -rs --fail-on-skip
ruff check . ; ruff format --check . ; git diff --check
```


## Work review revision

The original head e3c7d53 left inherited deprecated `copy(update=...)` able to
produce execution_enabled=True. Work reproduced this bypass, disabled the
legacy copy API explicitly (use guarded model_copy), and added two regression
cases. Include/exclude projections through deprecated copy are unsupported.
The original 702-case results remain coding-agent evidence for the original
head; final revision validation is recorded in the PR. This guard remains a
Python regression invariant, not protection against deliberate object mutation.
