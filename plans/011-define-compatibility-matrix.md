# Plan 011: Define and verify the supported Anki and Python compatibility matrix

> **Executor instructions**: This is an evidence-gathering compatibility plan
> followed by configuration changes. Do not guess a version floor. Execute all
> steps, stop on a STOP condition, and update the index.
>
> **Drift check (run first)**:
> `git diff --stat 512478c..HEAD -- pyproject.toml uv.lock addon/manifest.json README.md .github/workflows/verify.yml .github/workflows/release.yml`
> and confirm the project still declares Python 3.9, current-only `anki/aqt`
> lower bounds, no manifest floor, and Python 3.13-only CI.

## Status

- **Priority**: P2
- **Effort**: M
- **Risk**: MED
- **Depends on**: plans/001-gate-changes-and-releases.md, plans/002-isolate-tracker-state-by-profile.md, plans/006-batch-patch-collection-operation.md
- **Category**: dependencies, dx, tests
- **Planned at**: commit `512478c`, dirty working-tree snapshot, 2026-07-26

## Why this matters

The package claims Python 3.9 compatibility while development resolves only
Anki/AQT 25.9.4 or newer, CI runs only Python 3.13, and the add-on manifest lets
all Anki versions install it. Compatibility needs one explicit contract tied to
the Anki APIs actually used. This plan determines the floor from evidence,
tests it where practical, declares it to users/installers, and avoids pretending
that Python syntax checks alone prove old Anki compatibility.

## Current state

- `.python-version` and `pyproject.toml:6,21` target Python 3.9.
- `pyproject.toml:11-12` requires `anki>=25.9.4` and `aqt>=25.9.4` for dev.
- `addon/manifest.json` contains only `name` and `package`.
- `.github/workflows/release.yml:22-23` installs Python 3.13.
- Runtime uses modern Browser hooks, Qt6 enums/widgets, scheduler sequence APIs,
  `CollectionOp` after plan 006, and profile lifecycle hooks after plan 002.
- Git history includes `df5d3db downgrade to python 3.9 for older anki
  versions`, but no maintained compatibility document.

## Commands you will need

| Purpose | Command | Expected |
|---|---|---|
| Current checks | `uv run ruff check . && uv run pyright && uv run pytest -q` | exit 0 |
| Lock validation | `uv lock --check` | exit 0 |
| Build | `uv run python scripts/build_ankiaddon.py` | archive built |

## Scope

**In scope**:
- `pyproject.toml`
- `uv.lock` only if dependency constraints change
- `addon/manifest.json`
- `README.md`
- `.github/workflows/verify.yml`
- `.github/workflows/release.yml` only if the release runtime changes
- `docs/compatibility.md` (create if details do not fit README)

**Out of scope**:
- Adding compatibility shims or monkey patches for unsupported Anki releases.
- Supporting Qt5.
- Changing product behavior to preserve an arbitrary old version.
- Claiming compatibility based only on successful dependency installation.

## Git workflow

- Branch: `codex/011-define-compatibility`
- Commit message: `Define supported Anki versions`

## Steps

### Step 1: Inventory runtime API introduction constraints

List every Anki/AQT API that determines the minimum supported release, including
hooks, Browser methods, Qt enum names, scheduler batch methods, `CollectionOp`,
custom undo APIs, and manifest fields. Check the installed source plus official
Anki release/source history. Record evidence in `docs/compatibility.md`, not in
code comments.

**Verify**: the document names each constraining API and an evidenced earliest
supported Anki release or explicitly says “not established.”

### Step 2: Select one support contract

Choose the newest of the evidenced API floors as the minimum supported Anki
release. Derive its embedded Python expectation; do not independently promise
Python versions Anki does not ship. Keep “current Anki” as the upper tested
target. If the historical Python 3.9 goal conflicts with required current APIs,
prefer an honest higher Anki floor over compatibility hacks.

**Verify**: README and compatibility doc state:

- minimum supported Anki release;
- currently tested release;
- automated vs manual verification;
- unsupported older versions fail installation rather than at runtime.

### Step 3: Declare the installer floor correctly

Use Anki's official manifest schema for the selected release family to add the
minimum-version field in the correct representation. Do not copy the zero-valued
local `meta.json` fields; that file is Anki-managed state, not the distribution
contract.

**Verify**: inspect the built archive's `manifest.json` and validate it through
the installed Anki manifest reader or an equivalent unit test.

### Step 4: Exercise floor and current environments

Extend `.github/workflows/verify.yml` with a matrix that runs pure tests/static
checks on the supported floor and current environment where wheels/runners are
available. If full AQT installation at the floor is impossible on hosted CI,
separate pure Python tests from a documented manual Anki smoke test; do not mark
an unexercised combination as automated.

**Verify**: matrix jobs are explicit and the README matches what they actually
run.

### Step 5: Pin dev bounds to the contract

Change `anki`/`aqt` constraints only if needed to make the floor reproducible,
then regenerate/verify `uv.lock`. Avoid upper pins unless an observed
incompatibility requires one.

**Verify**: `uv lock --check`, current checks, and build all pass.

## Test plan

- Manifest compatibility-field test.
- Import/pure unit tests under the floor environment.
- Current environment full suite.
- Manual smoke checklist: load add-on, open Browser panel, lock scope, detect
  unsuspend, filter date range, export, preview full ledger, apply, undo, switch
  profiles.

## Done criteria

- [ ] Minimum Anki release is evidenced and documented.
- [ ] Manifest prevents installation below the supported floor.
- [ ] CI and manual claims match actual verification.
- [ ] Dependency constraints/lockfile represent the chosen floor.
- [ ] Current checks and build pass.

## STOP conditions

- Official manifest semantics for modern Anki versioning cannot be established.
- The chosen floor has no installable dependencies and cannot receive a
  meaningful manual smoke test.
- Supporting the historical floor requires runtime hacks or monkey patches.
- Plans 002 or 006 introduced APIs whose minimum release is not yet known.

## Maintenance notes

Update this matrix when adopting a new Anki API, not merely when upgrading the
development lockfile. A current-only feature should explicitly raise the floor.
