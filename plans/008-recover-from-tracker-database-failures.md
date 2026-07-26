# Plan 008: Contain tracker database failures and provide safe recovery

> **Executor instructions**: Follow the plan in order, verify every step, and
> stop on a STOP condition. Update the index when complete.
>
> **Drift check (run first)**:
> `git diff --stat 512478c..HEAD -- addon/share_tools/tracker_database.py addon/share_tools/unsuspend_tracker.py addon/share_tools/browser_widget.py tests/test_unsuspend_tracker.py`
> and confirm database initialization/loading exceptions still escape
> `load_tracker_state_once()` or its post-plan-002 replacement.

## Status

- **Priority**: P2
- **Effort**: M
- **Risk**: MED
- **Depends on**: plans/002-isolate-tracker-state-by-profile.md, plans/007-preserve-failed-legacy-migration.md
- **Category**: bug, tests
- **Planned at**: commit `512478c`, dirty working-tree snapshot, 2026-07-26

## Why this matters

SQLite corruption, a partial schema, an invalid retention value, a timestamp
that cannot be decoded, or a database created by a newer add-on version
currently propagates through the Browser hook. The panel then fails repeatedly
with no safe recovery path. The add-on must preserve the original file, disable
tracker mutation for that profile, and present one actionable message rather
than silently recreating or repeatedly crashing.

## Current state

- `tracker_database.py` is currently schema version 2 and may raise SQLite
  errors or a newer-schema
  `RuntimeError`.
- `tracker_database.py` decodes timestamps and coerces persisted scalar values
  without a typed row-validation boundary.
- `browser_widget.py:594-601` initializes storage without a recovery state.
- `browser_widget.py:383-388` calls initialization from the Browser-show hook.
- There is no direct `tests/test_tracker_database.py`.

## Commands you will need

| Purpose | Command | Expected |
|---|---|---|
| DB tests | `uv run pytest -q tests/test_tracker_database.py` | all pass |
| Tracker tests | `uv run pytest -q tests/test_unsuspend_tracker.py` | all pass |
| Full/static | `uv run pytest -q && uv run ruff check . && uv run pyright` | exit 0 |

## Scope

**In scope**:
- `addon/share_tools/tracker_database.py`
- `addon/share_tools/unsuspend_tracker.py`
- `addon/share_tools/browser_widget.py`
- `tests/test_tracker_database.py` (create)
- `tests/test_unsuspend_tracker.py`

**Out of scope**:
- Automatically repairing unknown SQLite corruption.
- Deleting a database without explicit user action.
- Adding telemetry or uploading diagnostic data.
- Tracker profile key design.

## Git workflow

- Branch: `codex/008-recover-tracker-database`
- Commit message: `Contain tracker database failures`

## Steps

### Step 1: Define typed storage failures

Create focused exception types or one structured exception with categories:
unsupported newer schema, malformed/partial schema, corrupt row data, and
SQLite I/O/database failure. Preserve the original exception as `__cause__`.
Messages may include the local database path but never row contents.

**Verify**: direct database tests assert categories for each fixture.

### Step 2: Validate schema and persisted rows

Before loading state, verify the expected schema-version/table contract.
Validate scalar types, the non-negative retention policy, and timestamps while
constructing `StoredTrackerState`. Do not coerce arbitrary corrupt values into
defaults.

**Verify**: temp-database fixtures cover random bytes, missing table, malformed
version 1, version 2 with a missing/invalid retention column, future version,
and invalid timestamp.

### Step 3: Add a disabled storage state at the tracker boundary

If activation fails, ensure `_database` is `None`, clear collection-specific
runtime state without writing, and expose a read-only health result to the UI.
Only one notification should appear per profile activation. The panel may still
open, but tracking/clear/export controls that require valid state must be
disabled with an explanation.

**Verify**: tests prove repeated Browser attachment does not retry or repeat the
notification until an explicit retry/profile reopen.

### Step 4: Offer non-destructive recovery actions

Provide:

- “Retry” after the user fixes/restores the file;
- “Open data folder” if a supported Anki/Qt helper exists;
- “Start fresh” only behind confirmation, implemented by renaming the failed
  database to a timestamped backup before creating a new one.

Do not add a destructive automatic path.

**Verify**: test the pure recovery orchestration with a temp directory: backup
exists, new DB initializes, and cancellation changes nothing.

### Step 5: Run all checks

Run direct DB tests, tracker tests, full suite, Ruff, Pyright, and
`git diff --check`.

## Test plan

Create `tests/test_tracker_database.py` using `tmp_path` and direct `sqlite3`.
Cover:

- clean initialize/load;
- future schema;
- corrupt bytes;
- partial version-0, version-1, and version-2 schema;
- malformed timestamp/type/retention policy;
- failed transaction rollback;
- backup-before-reset;
- one notification/retry lifecycle.

## Done criteria

- [ ] Storage failures are classified and preserve the original cause.
- [ ] Browser remains usable with tracker controls safely disabled.
- [ ] Original DB is never overwritten automatically.
- [ ] Retry and confirmed backup/reset work.
- [ ] Full verification passes.

## STOP conditions

- The proposed recovery path would overwrite the only copy.
- The current UI has no supported way to disable tracker actions independently.
- Plan 007's migration error is not distinguishable from database corruption.
- A failure fixture reveals a real credential or sensitive content; reference
  only its type/location.

## Maintenance notes

Any future schema version must add explicit migrations and fixtures here.
Reviewers should reject broad `except Exception: clear_all()` recovery.
