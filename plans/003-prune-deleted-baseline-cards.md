# Plan 003: Prune deleted baseline cards without stalling tracking

> **Executor instructions**: Follow every step and verification gate. Stop and
> report on a STOP condition. Update the index when finished.
>
> **Drift check (run first)**:
> `git diff --stat 512478c..HEAD -- addon/share_tools/unsuspend_tracker.py addon/share_tools/browser_widget.py tests/test_unsuspend_tracker.py`
> and compare current excerpts around `record_snapshot()` and `refresh()` with
> this plan. Planning hashes are recorded in `plans/README.md`.

## Status

- **Priority**: P1
- **Effort**: S
- **Risk**: LOW
- **Depends on**: plans/002-isolate-tracker-state-by-profile.md
- **Category**: bug, tests
- **Planned at**: commit `512478c`, dirty working-tree snapshot, 2026-07-26

## Why this matters

When a suspended baseline card is deleted, `record_snapshot()` calls
`cid_to_nid()` for the missing ID. That exception is suppressed by automatic
refresh, the baseline is never advanced, and every later tick fails on the same
card. A missing card must be pruned atomically while valid unsuspensions in the
same snapshot continue to be captured.

## Current state

```python
# addon/share_tools/unsuspend_tracker.py:81-94
newly_unsuspended = sorted(previous_suspended - current_suspended)
for cid in newly_unsuspended:
    event = UnsuspendEvent(
        cid=cid,
        nid=int(cid_to_nid(cid)),
        ...
    )
```

`browser_widget.py:320-329` catches the resulting exception and only displays it
for a manually requested refresh. Persistence uses `persist_snapshot()` before
mutating runtime collections; preserve that database-first atomicity.

## Commands you will need

| Purpose | Command | Expected |
|---|---|---|
| Target | `uv run pytest -q tests/test_unsuspend_tracker.py` | all pass |
| Full | `uv run pytest -q` | all pass |
| Static | `uv run ruff check . && uv run pyright` | both exit 0 |

## Scope

**In scope**:
- `addon/share_tools/unsuspend_tracker.py`
- `addon/share_tools/browser_widget.py`
- `tests/test_unsuspend_tracker.py`

**Out of scope**:
- Detecting cards that merely left the search scope (plan 004).
- Profile storage design.
- Changing refresh frequency or UI error presentation.
- Recovering corrupt SQLite databases.

## Git workflow

- Branch: `codex/003-prune-deleted-baseline`
- Commit message: `Prune deleted tracker baseline cards`
- Do not push or open a PR unless instructed.

## Steps

### Step 1: Make card resolution explicitly optional

Replace the callback contract with a clearly named resolver returning
`Optional[int]` for the note ID. `None` means the card no longer exists and must
not produce an `UnsuspendEvent`. Do not broadly catch every resolver exception:
only translate Anki's known missing-card condition to `None` at the
`browser_widget.py` adapter boundary; unexpected database/runtime failures must
still surface.

**Verify**: Pyright passes with no ignored type errors.

### Step 2: Persist deletion pruning with the valid snapshot

Partition departed baseline IDs into resolvable events and missing IDs. Missing
IDs must be included in `baseline_removed`, never added to captured events, and
must not prevent other valid events in the same snapshot from persisting.
Preserve the sequence: database transaction succeeds first, then runtime state
updates.

**Verify**: a test with one missing ID and one valid newly unsuspended ID returns
only the valid event and removes both from the suspended baseline.

### Step 3: Add restart coverage

With initialized SQLite storage, process a snapshot containing a deletion,
restart storage/runtime state, and prove the deleted ID is not retried.

**Verify**: `uv run pytest -q tests/test_unsuspend_tracker.py -k deleted` → all
new deletion tests pass.

### Step 4: Run all checks

Run target/full tests, Ruff, Pyright, and `git diff --check`.

## Test plan

Use the existing `cid_to_nid()` fake style. Add cases for:

- only a deleted baseline card;
- deleted plus valid unsuspended cards in one tick;
- missing resolution does not create an event;
- persisted baseline after restart excludes the deleted ID;
- unexpected resolver exception still propagates and does not partially mutate
  runtime/database state.

## Done criteria

- [ ] A deleted card cannot stall later snapshots.
- [ ] Missing cards never become captured unsuspension events.
- [ ] Other events in the same tick are retained.
- [ ] Database/runtime state agree after restart.
- [ ] Full verification passes and only in-scope files changed.

## STOP conditions

- Anki's missing-card API cannot be distinguished from storage corruption.
- The implementation would catch all `Exception` values and hide real failures.
- Plan 002 changed the resolver/service interface and this plan no longer
  matches it; report the new interface before adapting.

## Maintenance notes

Plan 004 adds separate scope-membership classification. Keep `missing` distinct
from `out_of_scope` even though neither produces an unsuspension event.
