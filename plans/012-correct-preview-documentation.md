# Plan 012: Document the complete ankipatch review ledger accurately

> **Executor instructions**: Follow the small scope exactly, verify, and update
> the plan index. Stop if product behavior differs from this plan.
>
> **Drift check (run first)**:
> `git diff --stat 512478c..HEAD -- README.md addon/share_tools/browser_actions.py`
> and compare README's Apply description with the live preview statuses.

## Status

- **Priority**: P3
- **Effort**: S
- **Risk**: LOW
- **Depends on**: plans/005-show-unresolved-patch-rows.md
- **Category**: docs
- **Planned at**: commit `512478c`, dirty working-tree snapshot, 2026-07-26

## Why this matters

README currently says the apply flow previews only cards whose state would
change. The intentional design is a review ledger: pending rows are selectable,
unchanged resolved rows remain visible as disabled `Same state` entries, and
after plan 005 unresolved rows are visible but non-actionable. Documentation
should set that expectation precisely.

## Current state

```markdown
<!-- README.md:44 -->
- Apply ankipatch: previews only cards whose state would change, lets you
  choose which changes to apply, and reports successful and unsuccessful
  applications.
```

The code at `browser_actions.py:317-344` already preserves unchanged resolved
rows. Plan 005 adds missing/error rows to the same complete ledger.

## Commands you will need

| Purpose | Command | Expected |
|---|---|---|
| Search | `rg -n "previews only|Same state|unresolved|Will change" README.md` | no stale claim; new terms present |
| Format | `git diff --check` | exit 0 |

## Scope

**In scope**:
- `README.md`

**Out of scope**:
- Runtime code or tests.
- A full public `.ankipatch` format specification.
- Screenshots/video replacement.
- Compatibility documentation owned by plan 011.

## Git workflow

- Branch: `codex/012-document-patch-ledger`
- Commit message: `Document the patch review ledger`

## Steps

### Step 1: Correct the product overview

Rewrite the Apply bullet to say the preview shows every patch row:

- changing cards are checked and labeled `Will change`;
- already-matching cards remain visible, disabled, and labeled `Same state`;
- unresolved/error rows remain visible but cannot be applied;
- only selected pending changes are applied;
- final results report applied and unresolved outcomes.

Keep the bullet readable; use a short nested list or a compact paragraph.

**Verify**: `rg -n "previews only" README.md` → no matches.

### Step 2: Align the Browser-panel wording

Review the nearby Browser-panel bullet at README line 37. Remove duplication and
ensure it does not imply every displayed row is actionable.

**Verify**: read `sed -n '23,50p' README.md`; the two sections are consistent.

### Step 3: Run documentation checks

Run `git diff --check`. If a Markdown linter already exists, run it; do not add
a dependency for this one edit.

## Test plan

No code tests. Compare the final copy against the status branches in
`show_ankipatch_preview_dialog()` and the structured decision/results flow from
plan 005.

## Done criteria

- [ ] README no longer says only changing cards are previewed.
- [ ] Pending, unchanged, and unresolved behavior is accurate.
- [ ] Copy clearly states only selected pending rows are applied.
- [ ] `git diff --check` passes and only README changed.

## STOP conditions

- Plan 005 was rejected or implemented with different unresolved-row behavior.
- The live UI no longer labels unchanged rows `Same state`.
- Product terminology changed after planning.

## Maintenance notes

Any future patch status must be reflected in both the ledger and this overview.
Do not document a status that users cannot actually inspect.
