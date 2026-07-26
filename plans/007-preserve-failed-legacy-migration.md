# Plan 007: Preserve legacy tracker data when migration fails

> **Executor instructions**: Execute each step and verification gate. Stop and
> report on any STOP condition. Update `plans/README.md` on completion.
>
> **Drift check (run first)**:
> `git diff --stat 512478c..HEAD -- addon/share_tools/unsuspend_tracker.py addon/share_tools/tracker_database.py tests/test_unsuspend_tracker.py`
> and confirm `initialize_storage()` still calls `clear_all()` from its legacy
> exception handler.

## Status

- **Priority**: P1
- **Effort**: S
- **Risk**: LOW
- **Depends on**: plans/002-isolate-tracker-state-by-profile.md
- **Category**: bug, migration
- **Planned at**: commit `512478c`, dirty working-tree snapshot, 2026-07-26

## Why this matters

A malformed legacy JSON record or transient read failure currently writes a
valid empty SQLite tracker state. On the next startup that empty database wins,
so migration is never retried even though the legacy file still contains the
only copy of user history. Migration must become authoritative only after a
validated source has been transactionally written and read back.

## Current state

```python
# addon/share_tools/unsuspend_tracker.py:260-268
if legacy_json_path is not None and legacy_json_path.exists():
    try:
        apply_state(json.loads(legacy_json_path.read_text(encoding="utf-8")))
        return
    except (OSError, ValueError, TypeError, KeyError):
        clear_all()
        return
persist_state()
```

`clear_all()` calls `persist_state()`, and `TrackerDatabase.load()` treats the
resulting singleton row as authoritative. Existing tests cover only successful
legacy migration.

## Commands you will need

| Purpose | Command | Expected |
|---|---|---|
| Target | `uv run pytest -q tests/test_unsuspend_tracker.py -k migration` | all pass |
| Full | `uv run pytest -q` | all pass |
| Static | `uv run ruff check . && uv run pyright` | exit 0 |

## Scope

**In scope**:
- `addon/share_tools/unsuspend_tracker.py`
- `addon/share_tools/tracker_database.py`
- `tests/test_unsuspend_tracker.py`

**Out of scope**:
- General corrupt-SQLite recovery UI (plan 008).
- Profile key selection (plan 002).
- Deleting or rewriting the legacy source.
- A new schema migration framework beyond what this transition needs.

## Git workflow

- Branch: `codex/007-preserve-failed-migration`
- Commit message: `Preserve tracker state on migration failure`

## Steps

### Step 1: Parse legacy data without mutating runtime or SQLite

Extract a pure decoder that validates the complete legacy payload and returns a
`StoredTrackerState`. It must validate collection types, integer IDs, timestamp
strings, scope strings, duplicate card IDs, and a non-negative integer
`retention_days` value before changing globals. Missing `retention_days` must
use the existing 30-day compatibility default. Reuse the existing dataclasses
rather than adding a parallel migration model.

**Verify**: tests for valid data and malformed top-level/row/timestamp values.

### Step 2: Make destination creation transactional and verifiable

Write the decoded state to the target database, reload it, and compare it with
the decoded state before activating it in memory. Only a successful round trip
marks migration complete. On failure, close/remove only a newly created empty
destination if doing so is safe; otherwise quarantine it with a non-destructive
suffix. Never modify the legacy source.

**Verify**: simulated write and readback failures leave the legacy file intact
and no authoritative tracker row at the normal destination.

### Step 3: Surface a typed migration failure

Raise a focused exception carrying safe context (paths and failure category, no
file contents). Plan 008 will decide how the GUI presents it. Do not silently
replace state with empty defaults.

**Verify**: target tests assert the exception and subsequent retry succeeds
after the transient failure is removed.

### Step 4: Run all checks

Run target/full tests, Ruff, Pyright, and `git diff --check`.

## Test plan

Add cases for:

- valid migration round trip;
- malformed JSON;
- structurally invalid but valid JSON, including invalid retention values;
- invalid ISO timestamp;
- simulated `OSError` reading source;
- simulated SQLite write/readback failure;
- retry after failure;
- legacy source remains byte-for-byte unchanged.
- missing legacy retention policy migrates to the documented 30-day default.

## Done criteria

- [ ] Failed migration never writes authoritative empty state.
- [ ] Runtime state changes only after destination readback succeeds.
- [ ] Legacy input remains untouched on success and failure.
- [ ] A later retry can succeed.
- [ ] Full verification passes.

## STOP conditions

- Plan 002 changed the one-time profile migration contract in a conflicting way.
- Destination already contains populated state different from the legacy source.
- Recovery would require deleting or overwriting the only source copy.

## Maintenance notes

Keep the pure decoder available for explicit recovery tooling. Plan 008 should
catch the typed failure at the GUI boundary without discarding its source file.
