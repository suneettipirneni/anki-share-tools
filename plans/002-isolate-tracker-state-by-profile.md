# Plan 002: Isolate tracker state by active Anki profile

> **Executor instructions**: Execute and verify each step in order. Stop on any
> listed STOP condition. Update `plans/README.md` when complete.
>
> **Drift check (run first)**:
> `git diff --stat 512478c..HEAD -- addon/share_tools/browser_actions.py addon/share_tools/browser_widget.py addon/share_tools/unsuspend_tracker.py addon/share_tools/tracker_database.py tests/test_unsuspend_tracker.py tests/test_browser_actions_helpers.py`
> then compare the live files with the inlined Current state below. Stop if the
> profile/storage contracts no longer match.

## Status

- **Priority**: P1
- **Effort**: M
- **Risk**: MED
- **Depends on**: plans/001-gate-changes-and-releases.md
- **Category**: bug, migration, architecture
- **Planned at**: commit `512478c`, dirty working-tree snapshot, 2026-07-26

## Why this matters

Tracker state contains collection-specific card IDs, note IDs, a search query,
a suspension baseline, and the user's retention policy, but it is stored once
per add-on process. Opening or switching profiles can therefore interpret one
collection's state against another collection and leak configuration between
profiles. The fix must bind runtime and persisted state to one active profile
and cleanly tear it down during profile transitions.

## Current state

- `browser_widget.py:40-48` defines one `DATABASE_FILE`, one `_state_loaded`
  boolean, and one process-wide widget registry.
- `browser_widget.py:594-601` loads that database only once.
- `unsuspend_tracker.py:38-42` holds all tracker state in module globals.
- `browser_actions.py:51-55` registers Browser/main-window hooks but no
  profile-open/profile-close hooks.
- `tracker_database.py` uses schema version 2 with a singleton `tracker` row;
  `retention_days` is persisted alongside the locked scope.
- Anki exposes `gui_hooks.profile_did_open` and
  `gui_hooks.profile_will_close` in the installed supported API.
- Persist files under the add-on root's `user_files/` folder; Anki preserves
  this special folder during upgrades.

```python
# addon/share_tools/browser_widget.py:594-601
def load_tracker_state_once() -> None:
    global _state_loaded
    if _state_loaded:
        return
    unsuspend_tracker.initialize_storage(DATABASE_FILE, LEGACY_STATE_FILE)
    _state_loaded = True
```

## Commands you will need

| Purpose | Command | Expected |
|---|---|---|
| Target tests | `uv run pytest -q tests/test_unsuspend_tracker.py tests/test_browser_widget_helpers.py` | all pass |
| Full tests | `uv run pytest -q` | all pass |
| Lint | `uv run ruff check .` | all checks pass |
| Types | `uv run pyright` | 0 errors |

## Scope

**In scope**:
- `addon/share_tools/browser_actions.py`
- `addon/share_tools/browser_widget.py`
- `addon/share_tools/unsuspend_tracker.py`
- `addon/share_tools/tracker_database.py`
- `tests/test_unsuspend_tracker.py`
- `tests/test_browser_widget_helpers.py` if a pure key helper is added
- `tests/test_browser_actions_helpers.py` for profile-hook orchestration tests

**Out of scope**:
- Correcting scope-departure or deleted-card classification (plans 003–004).
- Polling frequency/architecture (plan 009).
- Multiple simultaneous named tracking scopes.
- Syncing tracker data between devices.

## Git workflow

- Branch: `codex/002-profile-scoped-tracker`
- Commit message style: imperative, e.g. `Isolate tracker state by profile`.
- Do not push or open a PR unless instructed.

## Steps

### Step 1: Define a deterministic profile storage key

Add a pure helper that derives a filesystem-safe opaque key from the active
collection path using SHA-256. Do not put raw profile names or paths in
filenames. The safety contract is more important than preserving state after a
profile directory is manually moved: a moved profile may start fresh, but must
never inherit another profile's card IDs.

**Verify**: add tests proving identical paths yield the same key, different
paths yield different keys, and the key contains only lowercase hex.

### Step 2: Make storage activation and shutdown explicit

Replace `_state_loaded` with an active profile key/path. Add functions that:

1. stop/shutdown the prior `TrackerDatabase`,
2. clear runtime state without writing it into the next profile,
3. initialize `user_files/profiles/<opaque-key>/fresh_card_state.sqlite3`,
4. remember which profile is active.

Calling activation twice for the same profile must be idempotent. Switching keys
must reload rather than reuse globals.

**Verify**: unit tests activate profile A, persist events and a non-default
retention policy, activate profile B with colliding card IDs and a different
policy, then reactivate A; each profile must recover only its own scope,
baseline, events, and retention setting.

### Step 3: Bind activation to Anki profile lifecycle hooks

Register profile-open and profile-close handlers in `browser_actions.py`.
Profile open must obtain the live collection path only after `mw.col` exists,
then activate storage. Profile close must stop widget timers, clear subscriptions,
and call `shutdown_storage()` without persisting state into another profile.
Browser attachment may assert/ensure the current profile is already activated,
but must not independently choose a profile.

**Verify**: a focused test with faked hooks proves open A → close A → open B
invokes activation/shutdown in order and never leaves A active.

### Step 4: Migrate the existing singleton database safely

When no profile-specific database exists but the legacy singleton SQLite file
does, move or copy it into the first activated profile's directory only after a
successful read and write. Keep a recoverable backup until the destination
round-trip is verified. Record a small migration marker so another profile
cannot claim the same singleton later. Do not delete the older JSON legacy file;
plan 007 owns failed migration policy.

**Verify**: tests cover successful one-time claim, second-profile non-claim,
restart idempotency, and failure leaving the original intact.

### Step 5: Run regression checks

Run target tests, then the full suite, Ruff, and Pyright.

**Verify**: all commands exit 0 and `git diff --check` passes.

## Test plan

Model persistence tests after
`tests/test_unsuspend_tracker.py:test_initialized_storage_persists_mutations_automatically`.
Add:

- isolated profile A/B round trips with overlapping IDs and different
  retention settings;
- switching profiles in one process;
- duplicate activation idempotency;
- close clears active runtime/database reference;
- one-time singleton SQLite migration;
- migration failure preserves the source.

## Done criteria

- [ ] No collection-specific state survives a profile switch in module globals.
- [ ] Profile A and B persist independent baselines, events, and retention
  policies.
- [ ] Legacy singleton data is claimed at most once and remains recoverable.
- [ ] Profile lifecycle hooks activate and shut down storage.
- [ ] Target/full tests, Ruff, Pyright, and `git diff --check` pass.
- [ ] Only in-scope files changed.

## STOP conditions

- Current Anki no longer exposes profile open/close hooks.
- No collection path or other deterministic profile-local identity is available.
- Existing user data cannot be migrated without choosing between two populated
  destination profiles; report both states instead of overwriting either.
- Implementing isolation requires storing raw profile names in committed files
  or logs.

## Maintenance notes

Review lifecycle ordering carefully: background timers must not access `mw.col`
between profile close and the next profile open. Plan 009 may later replace
per-widget timers, but must preserve this activation boundary.
