# Plan 010: Bound ankipatch file size and row count before rendering

> **Executor instructions**: Follow and verify each step. Stop on a STOP
> condition. Update `plans/README.md` when complete.
>
> **Drift check (run first)**:
> `git diff --stat 512478c..HEAD -- addon/share_tools/ankipatch.py addon/share_tools/browser_actions.py tests/test_ankipatch.py`
> and confirm `read_patch()` still reads the entire file without limits.

## Status

- **Priority**: P2
- **Effort**: S
- **Risk**: LOW
- **Depends on**: plans/001-gate-changes-and-releases.md
- **Category**: security, perf
- **Planned at**: commit `512478c`, dirty working-tree snapshot, 2026-07-26

## Why this matters

`.ankipatch` files are portable and may be received from another person.
Currently the whole file and any number of rows are parsed and then synchronously
resolved/rendered. A pathologically large input can exhaust memory or freeze
Anki before the user reaches the review dialog. Explicit documented limits turn
that into a cheap, testable rejection.

## Current state

```python
# addon/share_tools/ankipatch.py:91-92
def read_patch(path: Path) -> AnkiPatch:
    return parse_patch_text(path.read_text(encoding="utf-8"))
```

`parse_patch_text()` validates row shape but not count. `browser_actions.py`
catches read errors and shows them to the local user. JSON parsing is otherwise
the correct standard-library boundary; do not replace it with ad-hoc parsing.

## Commands you will need

| Purpose | Command | Expected |
|---|---|---|
| Target | `uv run pytest -q tests/test_ankipatch.py` | all pass |
| Full/static | `uv run pytest -q && uv run ruff check . && uv run pyright` | exit 0 |

## Scope

**In scope**:
- `addon/share_tools/ankipatch.py`
- `addon/share_tools/browser_actions.py`
- `tests/test_ankipatch.py`
- `README.md` only for documenting limits if plan 012 has not already edited
  the same section

**Out of scope**:
- Streaming JSON parsing.
- Compressing patch files.
- Batch collection mutation (plan 006).
- Changing format version 1 or card identity.

## Git workflow

- Branch: `codex/010-bound-ankipatch-input`
- Commit message: `Bound ankipatch input size`

## Steps

### Step 1: Choose named conservative limits

Define module constants for maximum UTF-8 file bytes and maximum normalized
card rows. Choose values large enough for legitimate decks while bounding UI
work; recommended starting values are 10 MiB and 50,000 rows. Document the
reason near the constants. If repository/product evidence shows legitimate
patches exceed these values, stop and report measurements before choosing
larger limits.

**Verify**: constants are imported/used by both file and parser boundaries.

### Step 2: Reject oversized files before reading

Use `Path.stat().st_size` before `read_text()`. Also guard against a file that
grows between stat and read by checking encoded/read length after reading.
Raise a focused `ValueError` containing the limit and observed size, not file
contents.

**Verify**: temp-file tests at limit and limit+1.

### Step 3: Reject excessive row counts before row construction

After confirming `cards` is a list but before constructing `CardPatchRow`
objects, reject lists over the row limit. Keep duplicate normalization and
conflict validation unchanged.

**Verify**: parser tests at limit and limit+1; monkeypatch the limit to a small
number rather than allocating 50,001 complex rows.

### Step 4: Keep the UI error actionable

Ensure the existing file-open flow shows a concise message explaining the
limit. Do not display raw patch contents or create the preview dialog after
rejection.

**Verify**: a pure orchestration test or mocked `showInfo` asserts one message
and zero preview calls.

### Step 5: Run all checks

Run target/full tests, Ruff, Pyright, and `git diff --check`.

## Test plan

Cover:

- byte size just below/at/above the limit;
- multibyte UTF-8 size;
- row count at/above limit;
- malformed JSON still receives its existing error;
- oversized input never invokes preview;
- normal round trip remains unchanged.

## Done criteria

- [ ] File bytes are bounded before parsing.
- [ ] Row count is bounded before row conversion/rendering.
- [ ] Limits and errors are named and documented.
- [ ] No patch content appears in errors.
- [ ] Full verification passes.

## STOP conditions

- Real project fixtures or stated requirements exceed proposed limits.
- Enforcing the limit would require reading the full file first.
- A new format version/compression change landed and this plan's byte semantics
  no longer apply.

## Maintenance notes

Plan 006 reduces per-row mutation cost but does not replace this boundary.
Review limits when the UI gains pagination or streaming inspection.
