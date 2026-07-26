# Plan 001: Gate changes and releases with the full verification suite

> **Executor instructions**: Follow this plan step by step. Run every
> verification command and confirm the expected result before moving on. If a
> STOP condition occurs, stop and report instead of improvising. When done,
> update this plan's status row in `plans/README.md`.
>
> **Drift check (run first)**:
> `git diff --stat 512478c..HEAD -- .github/workflows/release.yml README.md pyproject.toml`
> and
> `shasum -a 256 .github/workflows/release.yml README.md pyproject.toml`.
> At planning time the hashes were respectively
> `943f4d2023672c449c46299ece1951f09970fab65a54b9a1cb8679cc90dd4c5f`,
> `78b3da58851951147ad1ff45edb596579338a2f9c5cd72db50414c960bd40a72`,
> and `001a9d85dd7dc1b87f113e87b96eda301438f1ec58b80e58e674ccccd3f7996e`.
> A hash mismatch means the dirty working-tree snapshot has drifted; compare
> the live files with Current state and stop if the workflow intent changed.

## Status

- **Priority**: P1
- **Effort**: S
- **Risk**: LOW
- **Depends on**: none
- **Category**: tests, dx
- **Planned at**: commit `512478c`, dirty working-tree snapshot, 2026-07-26

## Why this matters

The only GitHub workflow runs after a release tag is pushed, and even that
workflow does not run the existing behavioral tests. A regression can therefore
land without feedback and be published despite failing pytest. This plan makes
the repository's already-green verification suite a required change and release
gate without expanding test scope or changing runtime behavior.

## Current state

- `.github/workflows/release.yml:3-6` triggers only for tags matching `*.*.*`.
- `.github/workflows/release.yml:28-35` runs Ruff, Pyright, and the packager but
  omits pytest.
- `README.md:59-65` documents the same incomplete verification sequence.
- `pyproject.toml:27-28` configures pytest, and the baseline is 55 passing tests.
- Workflow style is small named steps using `uv`; keep that convention.

```yaml
# .github/workflows/release.yml:28-35
- name: Lint
  run: uv run ruff check .
- name: Type check
  run: uv run pyright
- name: Build .ankiaddon
  run: uv run python scripts/build_ankiaddon.py
```

## Commands you will need

| Purpose | Command | Expected on success |
|---|---|---|
| Sync | `uv sync --locked` | exit 0; lockfile unchanged |
| Lint | `uv run ruff check .` | `All checks passed!` |
| Typecheck | `uv run pyright` | 0 errors |
| Tests | `uv run pytest -q` | all tests pass |
| Build | `uv run python scripts/build_ankiaddon.py` | archive built under `dist/` |

## Scope

**In scope**:
- `.github/workflows/release.yml`
- `.github/workflows/verify.yml` (create)
- `README.md`

**Out of scope**:
- Python/Anki version matrix decisions; plan 011 owns that.
- New application tests.
- Changes to action version pinning or release naming.
- Runtime code under `addon/`.

## Git workflow

- Branch: `codex/001-gate-changes-and-releases`
- Use the repository's imperative commit style, for example:
  `Gate releases on tests`.
- Do not push or open a PR unless instructed.

## Steps

### Step 1: Add a normal change-verification workflow

Create `.github/workflows/verify.yml` for pushes to the default branch and pull
requests. Use Ubuntu, checkout, setup-uv, Python 3.13, `uv sync --locked`, Ruff,
Pyright, and pytest. Do not build or publish an add-on in this workflow. Use
least privilege (`contents: read`) and cancel superseded runs for the same
branch/PR.

**Verify**: `rg -n "pull_request|pytest|pyright|ruff" .github/workflows/verify.yml`
→ all four concerns are present.

### Step 2: Gate the release job on pytest

Add `uv run pytest -q` after typecheck and before package creation in
`.github/workflows/release.yml`. Do not change the release trigger or publishing
permissions in this plan.

**Verify**:
`awk '/Type check/{seen_type=1} /pytest/{seen_test=1} /Build .ankiaddon/{if (seen_type && seen_test) ok=1} END{exit !ok}' .github/workflows/release.yml`
→ exit 0.

### Step 3: Document the canonical local sequence

Add `uv run pytest -q` between Pyright and the build command in the README.

**Verify**: `sed -n '59,70p' README.md` → sync, Ruff, Pyright, pytest, and build
appear in that order.

### Step 4: Run the entire gate locally

Run every command in the Commands table. Building may change the ignored `dist/`
artifact; it must not add tracked source changes.

**Verify**: `git diff --check && git status --short` → no whitespace errors and
only the three in-scope files plus pre-existing user changes/plans are modified.

## Test plan

No new Python tests are required. The behavioral acceptance test is that the
existing 55-test suite is invoked locally and appears in both verification
workflows. If GitHub workflow linting is already available in the environment,
run it; do not add a new dependency solely for YAML linting.

## Done criteria

- [ ] Push/PR workflow runs Ruff, Pyright, and pytest after locked sync.
- [ ] Release workflow runs pytest before packaging and publishing.
- [ ] README documents `uv run pytest -q`.
- [ ] Ruff, Pyright, pytest, build, and `git diff --check` all succeed.
- [ ] No runtime source files are modified.
- [ ] `plans/README.md` status is updated.

## STOP conditions

- The default branch cannot be determined from repository metadata.
- The repository already gained an equivalent push/PR workflow after planning.
- The existing pytest suite fails twice without changes made by this plan.
- Making checks required would need repository-admin changes rather than files.

## Maintenance notes

Plan 011 will decide which Python/Anki versions the workflow should cover. Keep
this plan focused on ensuring every change and release executes the existing
checks.
