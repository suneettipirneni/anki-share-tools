# Plan 006: Apply selected patch changes as one batched Anki collection operation

> **Executor instructions**: Run each verification gate and stop on a STOP
> condition. Update the index when complete.
>
> **Drift check (run first)**:
> `git diff --stat 512478c..HEAD -- addon/share_tools/ankipatch.py addon/share_tools/browser_actions.py tests/test_ankipatch.py tests/test_browser_actions_helpers.py`
> and confirm the per-row `set_card_suspended()` loop still exists.

## Status

- **Priority**: P1
- **Effort**: M
- **Risk**: MED
- **Depends on**: plans/001-gate-changes-and-releases.md, plans/005-show-unresolved-patch-rows.md
- **Category**: bug, perf, architecture
- **Planned at**: commit `512478c`, dirty working-tree snapshot, 2026-07-26

## Why this matters

Preview and apply currently resolve the same rows repeatedly, and apply invokes
the scheduler once per card on the GUI thread. Anki provides `CollectionOp` for
mutating work so it is serialized, committed, reflected in UI state, and
finished through callbacks. One custom undo entry plus batched suspend and
unsuspend calls gives the operation native behavior and predictable performance.

## Current state

```python
# addon/share_tools/ankipatch.py:195-258
for row in patch.cards:
    card_id = resolve_card_id(col, row)
    ...
    set_card_suspended(col, card_id, row.suspended)
save_collection(col)
```

- `browser_actions.py:242-255` previews synchronously, applies synchronously,
  manually syncs the tracker, resets the main window, then opens results.
- Installed Anki exposes `aqt.operations.CollectionOp`,
  `Collection.add_custom_undo_entry()`, and
  `Collection.merge_undo_entries()`.
- `col.sched.suspend_cards(ids)` and `unsuspend_cards(ids)` accept sequences.
- Preserve per-row `updated`, `unchanged`, `missing`, and `error` reporting.

## Commands you will need

| Purpose | Command | Expected |
|---|---|---|
| Target | `uv run pytest -q tests/test_ankipatch.py tests/test_browser_actions_helpers.py` | all pass |
| Full | `uv run pytest -q` | all pass |
| Static | `uv run ruff check . && uv run pyright` | exit 0 |

## Suggested executor toolkit

- Read the installed `aqt.operations.CollectionOp` implementation and
  `anki.collection.Collection.merge_undo_entries()` before coding.
- Follow Anki's official background-operation guidance; do not access Qt from
  the operation callback running on the collection thread.

## Scope

**In scope**:
- `addon/share_tools/ankipatch.py`
- `addon/share_tools/browser_actions.py`
- `tests/test_ankipatch.py`
- `tests/test_browser_actions_helpers.py`

**Out of scope**:
- Patch file format or size limits.
- Tracker polling architecture.
- Direct SQL writes to Anki's collection.
- Changing the full-ledger preview statuses from plan 005.

## Git workflow

- Branch: `codex/006-batch-patch-operation`
- Commit message: `Batch ankipatch collection updates`

## Steps

### Step 1: Separate resolution from mutation

Introduce immutable resolved-operation records carrying row, card ID, note ID,
previous state, and target state. Resolve selected rows once immediately before
mutation so changes between preview and apply are rechecked. Group only still
pending IDs into suspend and unsuspend batches; retain unchanged/missing/error
results.

**Verify**: unit tests cover preview drift where a selected card becomes
unchanged or missing before apply.

### Step 2: Implement one collection-thread operation

Create an operation result dataclass containing:

- `changes: OpChanges`;
- final per-row results.

Inside the operation:

1. resolve/recheck selected rows;
2. create `undo_target = col.add_custom_undo_entry("Apply ankipatch")`;
3. call `col.sched.suspend_cards(all_suspend_ids)` at most once;
4. call `col.sched.unsuspend_cards(all_unsuspend_ids)` at most once;
5. call `col.merge_undo_entries(undo_target)` and return its `OpChanges` with
   results.

If there are no mutations after recheck, do not create an empty undo entry;
return an appropriate no-change result using the supported Anki type. Do not
call `col.save()` or Qt routines from this function.

**Verify**: fakes assert at most one scheduler call per target state and one
merged undo entry.

### Step 3: Orchestrate with CollectionOp

In `browser_actions.py`, run the operation via `CollectionOp(parent, op)`.
The success callback, on the UI thread, must sync the tracker baseline before
`CollectionOp` emits `operation_did_execute`, then show the combined results
from plan 005. Let the existing `operation_did_execute` hook request the UI
refresh; avoid redundant `mw.reset()` or direct refresh calls. The failure
callback must show one actionable error and must not claim success.

**Verify**: orchestration tests fake CollectionOp and assert success/failure
callbacks, no pre-completion dialog, and one tracker sync after success.

### Step 4: Remove legacy per-card mutation/save paths

Remove `set_card_suspended()` and `save_collection()` only if repository-wide
search confirms no remaining callers. Do not retain an untested fallback that
directly assigns `card.queue`; current supported Anki provides scheduler APIs.

**Verify**:
`rg -n "set_card_suspended|save_collection|card\\.queue =" addon/share_tools`
→ no obsolete mutation fallback remains.

### Step 5: Run all checks

Run target/full tests, Ruff, Pyright, and `git diff --check`.

## Test plan

Extend fake scheduler/collection support to record batch calls and undo merges.
Cover:

- mixed suspend/unsuspend selections;
- multiple cards produce one call per group;
- one custom undo entry is merged;
- all rows becoming unchanged creates no undo entry;
- missing/error results retained;
- operation failure calls failure UI and not success UI;
- preview-only unresolved rows from plan 005 survive.

## Done criteria

- [ ] Patch mutation runs via one `CollectionOp`.
- [ ] Suspend and unsuspend calls are batched.
- [ ] One logical undo entry covers the applied patch.
- [ ] No manual `col.save()` or direct queue fallback remains.
- [ ] Result ledger is complete after async completion.
- [ ] Full verification passes.

## STOP conditions

- Installed supported Anki lacks custom undo merge APIs.
- `CollectionOp` cannot return a result carrying `OpChanges`.
- Batching would lose required per-row failure information for a reachable
  scheduler behavior; report the exact API behavior before choosing semantics.
- Plan 005 is not complete and unresolved rows would still be discarded.

## Maintenance notes

Future format versions should resolve into the same immutable operation model.
Reviewers should verify no Qt access occurs on the collection thread and that
the final `changes` value includes both suspend and unsuspend mutations.
