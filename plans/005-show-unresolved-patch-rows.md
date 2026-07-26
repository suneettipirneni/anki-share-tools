# Plan 005: Keep unresolved patch rows in the pre-apply ledger

> **Executor instructions**: Follow the steps exactly, run every verification,
> and update `plans/README.md` on completion. Stop on any STOP condition.
>
> **Drift check (run first)**:
> `git diff --stat 512478c..HEAD -- addon/share_tools/browser_actions.py tests/test_ankipatch.py tests/test_browser_actions_helpers.py README.md`
> then compare `show_ankipatch_preview_dialog()` with the Current state below.

## Status

- **Priority**: P1
- **Effort**: S–M
- **Risk**: LOW
- **Depends on**: plans/001-gate-changes-and-releases.md
- **Category**: bug, tests
- **Planned at**: commit `512478c`, dirty working-tree snapshot, 2026-07-26

## Why this matters

The review UI intentionally acts as a complete ledger: unchanged resolved rows
stay visible but disabled as `Same state`. Missing and errored rows currently
receive weaker treatment: they are reduced to a count and discarded before the
results dialog. Keeping them visible makes cross-collection failures
identifiable without making them actionable.

## Current state

```python
# addon/share_tools/browser_actions.py:262-270
preview_rows = sorted(
    (result for result in results if result.status in {"pending", "unchanged"}),
    ...
)
unavailable_count = len(results) - len(preview_rows)
```

- `browser_actions.py:317-344` renders pending and unchanged rows.
- `browser_actions.py:363-368` returns only selected pending rows.
- `browser_actions.py:503-538` already contains an unresolved-row table builder,
  but normal selective apply discards those results.
- Intentional UX constraint: resolved unchanged rows remain visible, disabled,
  gray, and explicitly labeled `Same state`; do not regress it.

## Commands you will need

| Purpose | Command | Expected |
|---|---|---|
| Target | `uv run pytest -q tests/test_ankipatch.py` | all pass |
| Full | `uv run pytest -q` | all pass |
| Static | `uv run ruff check . && uv run pyright` | exit 0 |

## Scope

**In scope**:
- `addon/share_tools/browser_actions.py`
- `tests/test_ankipatch.py`
- `tests/test_browser_actions_helpers.py`

**Out of scope**:
- Changing `.ankipatch` identity or format.
- Applying missing/error rows.
- Patch size limits (plan 010).
- Batching/background mutation (plan 006).
- README wording (plan 012).

## Git workflow

- Branch: `codex/005-show-unresolved-patch-rows`
- Commit message: `Show unresolved rows in patch review`

## Steps

### Step 1: Extract a pure preview ledger model

Create a pure function that takes all `PatchApplyResult` values and returns
ordered display rows plus the selectable pending rows. Preserve all statuses:
`pending`, `unchanged`, `missing`, and `error`. Sorting must be deterministic;
resolved rows may use display metadata, while unresolved rows fall back to
`note_guid` and `card_ord`.

**Verify**: pure unit tests prove no input result is lost and pending rows alone
are selectable.

### Step 2: Render every row with explicit status

Update the preview dialog so:

- pending: enabled, checked, `Will change`;
- unchanged: disabled, unchecked, `Same state`;
- missing: disabled, unchecked, `Missing`;
- error: disabled, unchecked, `Error`.

Unresolved rows must show patch GUID, card ordinal, target state, and details
even when collection metadata is unavailable. A tabbed resolved/unresolved
preview is acceptable if it retains one dialog and one complete summary.

**Verify**: dialog/model tests assert status labels and disabled selection.

### Step 3: Preserve unresolved rows for final reporting

Return a structured preview decision containing selected rows and the original
non-actionable results, rather than only `list[CardPatchRow]`. After apply,
combine apply results with preview-only missing/error results for the final
results dialog. Do not duplicate unchanged rows that were not selected.

**Verify**: orchestration test proves a patch with pending + unchanged + missing
produces a final unresolved entry for the missing row.

### Step 4: Run all checks

Run target/full tests, Ruff, Pyright, and `git diff --check`.

## Test plan

Model patch result fixtures after `tests/test_ankipatch.py:141-190`. Cover:

- all four statuses retained;
- all-unresolved patch still opens an inspectable ledger;
- missing/error toggles disabled;
- unchanged remains disabled `Same state`;
- final results receive preview-only unresolved rows;
- cancel performs no mutation.

## Done criteria

- [ ] Every patch row is visible before apply.
- [ ] Only pending rows can be selected.
- [ ] Missing/error identity and details survive into results.
- [ ] Existing unchanged-row behavior is preserved.
- [ ] Full verification passes; no out-of-scope files changed.

## STOP conditions

- Implementing the test requires driving a real modal Qt event loop; first
  extract a pure model seam and test it from
  `tests/test_browser_actions_helpers.py` instead of adding brittle timing
  tests.
- Patch statuses changed since planning.
- Any proposed design makes missing/error rows selectable.

## Maintenance notes

Plan 006 may change apply orchestration to asynchronous callbacks. Preserve the
structured preview decision and carry all preview-only results through that
callback boundary.
