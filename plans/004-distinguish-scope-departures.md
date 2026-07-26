# Plan 004: Record only genuine unsuspensions inside the locked scope

> **Executor instructions**: Execute in order, verify each step, and stop rather
> than improvising. Update the plan index on completion.
>
> **Drift check (run first)**:
> `git diff --stat 512478c..HEAD -- addon/share_tools/browser_widget.py addon/share_tools/unsuspend_tracker.py tests/test_unsuspend_tracker.py tests/test_browser_widget_helpers.py`.
> Compare the live snapshot/resolver contract with plans 002 and 003; those
> plans must be DONE before starting.

## Status

- **Priority**: P1
- **Effort**: M
- **Risk**: MED
- **Depends on**: plans/002-isolate-tracker-state-by-profile.md, plans/003-prune-deleted-baseline-cards.md
- **Category**: bug, tests
- **Planned at**: commit `512478c`, dirty working-tree snapshot, 2026-07-26

## Why this matters

The tracker currently compares two sets of cards that are both suspended and
match the locked search. Disappearance from that set can mean unsuspension, but
also a deck/tag/flag change that moves a still-suspended card out of scope. The
tracker must model scope membership and suspension state separately so it never
exports a false “fresh unsuspend.”

## Current state

- `browser_widget.py:292-304` captures a normalized Browser search as the scope.
- `browser_widget.py:320-326` fetches only `find_suspended_cids_in_scope()`.
- `unsuspend_tracker.py:77-105` interprets every prior ID absent from that set as
  newly unsuspended.
- Tests directly supply only `current_suspended_cids`, so they cannot express
  “still suspended but no longer in scope.”

## Commands you will need

| Purpose | Command | Expected |
|---|---|---|
| Target | `uv run pytest -q tests/test_unsuspend_tracker.py tests/test_browser_widget_helpers.py` | all pass |
| Full | `uv run pytest -q` | all pass |
| Static | `uv run ruff check . && uv run pyright` | both exit 0 |

## Scope

**In scope**:
- `addon/share_tools/browser_widget.py`
- `addon/share_tools/unsuspend_tracker.py`
- `tests/test_unsuspend_tracker.py`
- `tests/test_browser_widget_helpers.py`

**Out of scope**:
- Rewriting arbitrary Anki Boolean search syntax.
- Tracking cards that were not suspended when the scope was locked.
- Multiple named scopes.
- Polling architecture and frequency.

## Git workflow

- Branch: `codex/004-distinguish-scope-departures`
- Commit message: `Distinguish scope departures from unsuspensions`

## Steps

### Step 1: Query scope membership independently

Add a helper that finds all card IDs matching the locked normalized scope,
without appending `is:suspended`. For the whole-collection empty scope, use an
Anki-supported all-cards query or collection API; do not construct raw SQL.
Continue querying the suspended subset separately.

**Verify**: helper tests cover empty and non-empty scope query composition.

### Step 2: Introduce explicit snapshot classification

Change the tracker input to receive:

- IDs currently in scope;
- IDs currently suspended in that scope;
- the optional resolver from plan 003.

For each prior suspended ID:

- still in scope + no longer suspended → capture an unsuspension;
- still suspended in scope → retain baseline;
- no longer in scope → remove from baseline without an event;
- missing → prune without an event.

IDs newly entering the scope while suspended must join the baseline without
creating events.

**Verify**: targeted unit tests assert every branch above.

### Step 3: Keep persistence atomic

Extend the existing `persist_snapshot()` delta inputs only as necessary.
Database changes must commit before corresponding runtime state changes, and one
failed write must leave the in-memory baseline/events unchanged.

**Verify**: existing automatic persistence tests plus a new failed-write
characterization test pass.

### Step 4: Run all checks

Run target/full tests, Ruff, Pyright, and `git diff --check`.

## Test plan

Add explicit tests named around:

- `still_suspended_card_leaving_scope_is_not_captured`;
- `card_unsuspended_while_remaining_in_scope_is_captured`;
- `suspended_card_entering_scope_joins_baseline`;
- `deleted_card_is_pruned`;
- simultaneous enter/leave/unsuspend updates persist correctly.

Follow the small pure-state tests at the top of `tests/test_unsuspend_tracker.py`.

## Done criteria

- [ ] Only cards still matching the locked scope can be newly captured.
- [ ] Scope departures and deletions create no events.
- [ ] Scope entrants establish baseline without false events.
- [ ] Runtime and SQLite state update atomically.
- [ ] All verification commands pass.

## STOP conditions

- The locked search cannot be executed without an `is:suspended` filter using a
  supported Anki collection API.
- Plan 003 did not preserve a distinct missing-card outcome.
- Correct behavior would require parsing/reconstructing arbitrary search ASTs;
  report representative queries instead.

## Maintenance notes

Reviewers should scrutinize empty-scope semantics and dynamic scopes. Plan 009
may later consolidate the two collection queries, but must preserve these four
classification states.
