# Plan 009: Centralize tracker refresh and avoid hidden full-table polling

> **Executor instructions**: Execute only after dependencies are DONE. Verify
> each step and stop on any STOP condition. Update the plan index when complete.
>
> **Drift check (run first)**:
> `git diff --stat 512478c..HEAD -- addon/share_tools/browser_actions.py addon/share_tools/browser_widget.py addon/share_tools/unsuspend_tracker.py tests/test_browser_widget_helpers.py`
> and inspect the live profile/snapshot services introduced by plans 002–004.

## Status

- **Priority**: P2
- **Effort**: M
- **Risk**: MED
- **Depends on**: plans/002-isolate-tracker-state-by-profile.md, plans/003-prune-deleted-baseline-cards.md, plans/004-distinguish-scope-departures.md
- **Category**: perf, architecture
- **Planned at**: commit `512478c`, dirty working-tree snapshot, 2026-07-26

## Why this matters

Every Browser creates a hidden tracker dock whose widget starts a two-second
timer. Each tick performs collection searches on the GUI thread and rebuilds
every visible row, including card/note metadata lookups and column resizing.
Refresh ownership should be profile-scoped and singular, while views should
render only when the model or selected date range changes.

## Current state

- `browser_widget.py:51-57,121-123` creates and starts one timer per widget.
- `browser_widget.py:201-251` rebuilds the full table on every update.
- `browser_widget.py:382-417` creates the dock hidden on Browser show.
- `browser_actions.py:51-56` registers Anki's
  `gui_hooks.operation_did_execute`.
- `browser_actions.py:81-87` immediately refreshes every tracker widget when
  `changes.card` or `changes.study_queues` is true.
- The native operation hook is the correct primary trigger and must be
  preserved; the remaining issue is that it fans out into per-widget collection
  refreshes while each widget also owns a two-second timer.

## Commands you will need

| Purpose | Command | Expected |
|---|---|---|
| Widget tests | `uv run pytest -q tests/test_browser_widget_helpers.py tests/test_browser_actions_helpers.py tests/test_tracker_refresh.py` | all pass |
| Full/static | `uv run pytest -q && uv run ruff check . && uv run pyright` | exit 0 |

## Scope

**In scope**:
- `addon/share_tools/browser_actions.py`
- `addon/share_tools/browser_widget.py`
- `addon/share_tools/unsuspend_tracker.py`
- `tests/test_browser_widget_helpers.py`
- `tests/test_browser_actions_helpers.py`
- `tests/test_tracker_refresh.py` (create)

**Out of scope**:
- Eliminating fallback polling entirely.
- Replacing QTableWidget with a different UI framework.
- Changing date-range semantics or persisted schema.
- New product features such as named scopes.

## Git workflow

- Branch: `codex/009-centralize-tracker-refresh`
- Commit message: `Centralize tracker refresh`

## Steps

### Step 1: Add a profile-scoped refresh coordinator

Create one coordinator for the active profile. It owns one fallback QTimer and
publishes immutable tracker snapshots to registered widgets. Start it only when
tracking is enabled and at least one live Browser consumer exists; stop it on
profile close or when the last consumer is destroyed. Do not let widgets own
collection polling.

**Verify**: deterministic fake-timer tests prove two widgets still create only
one active poller and profile close stops it.

### Step 2: Route `operation_did_execute` through the coordinator

Keep `gui_hooks.operation_did_execute` as the native primary trigger. Change
`on_operation_did_execute()` so relevant `changes.card` or
`changes.study_queues` values call the active coordinator's
`request_refresh(reason="operation")` instead of refreshing every widget.
Coalesce bursts so multiple relevant operation notifications in one event-loop
turn yield one collection snapshot. Retain a slower fallback poll for direct DB
writes, older add-ons, or other mutations that do not emit a relevant operation
event.

**Verify**: tests simulate duplicate triggers and assert one refresh call.

### Step 3: Separate model refresh from view rendering

Publish a version/revision only when baseline/events/retention/health change.
Widgets should not rebuild tables on unchanged timer ticks. Date-range and
retention controls may rerender locally after their explicit mutation path
publishes the new snapshot; they must not wait for
`operation_did_execute`, because changing an add-on setting is not an Anki
collection operation. Cache display metadata per event ID and invalidate it
when the event disappears or the collection/profile changes.

**Verify**: tests assert repeated identical snapshots cause zero additional
metadata lookups/table renders; changing the date range still rerenders.

### Step 4: Avoid work for never-shown panels

Do not construct a full tracker widget merely to keep tracking alive; the
coordinator owns tracking. Lazily construct/render the dock contents when first
shown, or at minimum avoid row metadata work while the dock is hidden.

**Verify**: a hidden Browser panel performs coordinator polling only and no
table metadata calls.

### Step 5: Run checks and a manual smoke test

Run automated checks. In Anki, if available:

1. open two Browser windows;
2. unsuspend one card;
3. confirm both visible panels update once;
4. hide both panels and confirm tracking continues without table rebuilds;
5. switch profiles and confirm the old coordinator stops.

Record manual results in the PR, not in source.

## Test plan

Create fakes for timer, snapshot provider, and view subscriber. Cover:

- singleton coordinator per profile;
- register/unregister lifecycle;
- duplicate trigger debounce;
- relevant vs unrelated `OpChanges` routing through
  `operation_did_execute`;
- fallback timer;
- unchanged snapshot no-op;
- hidden vs visible rendering;
- profile close cancellation;
- collection error surfaces once without tight retry.

## Done criteria

- [ ] At most one collection poller exists per active profile.
- [ ] Widgets do no collection polling.
- [ ] Duplicate action triggers coalesce.
- [ ] Unchanged snapshots do not rebuild tables.
- [ ] Hidden panels avoid metadata/table work.
- [ ] Automated checks and available smoke test pass.

## STOP conditions

- Plans 002–004 are not complete.
- No reliable way exists to keep fallback polling off the UI thread without
  violating Anki collection serialization; preserve safe main-thread behavior
  and report measurements.
- The supported Anki version does not emit `operation_did_execute` after native
  suspend/unsuspend operations; retain the hook but report the observed gap
  before adding any fallback action wiring.

## Maintenance notes

Keep the fallback interval configurable in one constant. Future named-scope
work must batch/coalesce scope queries rather than create one timer per scope.
