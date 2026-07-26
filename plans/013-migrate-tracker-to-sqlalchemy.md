# Plan 013: Migrate tracker persistence to strictly typed SQLAlchemy dataclass mappings

> **Executor instructions**: Follow this plan step by step. Run every
> verification command and confirm the expected result before moving to the
> next step. If anything in the STOP conditions occurs, stop and report instead
> of improvising. When done, update this plan's status row in
> `plans/README.md`.
>
> **Drift check (run first)**:
> `git diff --stat bde2e99..HEAD -- pyproject.toml uv.lock addon/__init__.py addon/share_tools/tracker_models.py addon/share_tools/tracker_database.py addon/share_tools/unsuspend_tracker.py scripts/build_ankiaddon.py tests/test_tracker_database.py tests/test_unsuspend_tracker.py tests/test_build_ankiaddon.py pyright.strictconfig.json .github/workflows/verify.yml .github/workflows/release.yml README.md`
> and compare the live persistence, compatibility, and recovery contracts with
> the Current state below. At planning time, the key hashes were:
>
> ```text
> 001a9d85dd7dc1b87f113e87b96eda301438f1ec58b80e58e674ccccd3f7996e  pyproject.toml
> 4e066e9029a7687d8e14040c0637a918930f284e78938558866d99522ea67562  uv.lock
> 91731e82d327340c25c723deb2f8727f4abb2e1408920f2852119ffee0a9aa37  addon/__init__.py
> 645fc8ee321bbf38a4148212c1fb0c438bfb7eb4df30fb12b3dc5ce126b3c256  addon/share_tools/tracker_database.py
> 4bcb480d37e268dd30924eae8f81cc7a4746e8be875592cdc107d6850e1514e4  addon/share_tools/unsuspend_tracker.py
> 08c1d35af4ba71bcec19fab12cf64960b7d94fdac3160ee4bf7f5a3bed7dd4cc  tests/test_unsuspend_tracker.py
> 92a01789d294a2fe9d4a34f4853f8155a96758666335be5b42e85591131d6a49  scripts/build_ankiaddon.py
> ```
>
> A hash mismatch is not automatically a blocker after a dependency plan
> executes. Reconcile the final profile, migration, recovery, and compatibility
> APIs first; stop if their behavior differs materially from this plan.

## Status

- **Priority**: P2
- **Effort**: L
- **Risk**: MED
- **Depends on**: plans/001-gate-changes-and-releases.md, plans/002-isolate-tracker-state-by-profile.md, plans/007-preserve-failed-legacy-migration.md, plans/008-recover-from-tracker-database-failures.md, plans/011-define-compatibility-matrix.md
- **Category**: tech-debt, migration, dependencies, tests
- **Planned at**: commit `bde2e99`, 2026-07-26

## Why this matters

`TrackerDatabase` currently repeats hand-written SQL, tuple indexing, scalar
coercion, and datetime serialization across every transaction. SQLAlchemy 2's
annotated declarative ORM can centralize the schema and provide PEP 681
dataclass-transform constructors that Pyright understands. The migration must
not trade that maintainability for an incompatible database, implicit session
lifetime, row-by-row bulk work, or an add-on that imports only in the
development virtual environment.

## Current state

- `pyproject.toml:6-7` supports Python 3.9 and has no runtime dependencies.
  SQLAlchemy is absent from `uv.lock`.
- `pyproject.toml:18-25` runs Pyright in `basic` mode and disables three
  diagnostics; this does not provide an isolated strict gate for persistence.
- `addon/share_tools/tracker_database.py:9-26` declares schema version 2 and
  frozen domain transfer dataclasses.
- `tracker_database.py:34-84` creates/migrates three tables with raw
  `sqlite3`, using `PRAGMA user_version` as the schema authority.
- `tracker_database.py:86-272` opens one connection per operation and uses
  explicit transactions, bulk `executemany()`, bound parameters, and ISO
  datetime text.
- `scripts/build_ankiaddon.py:59-68` archives only files already under
  `addon/`; a dependency installed by `uv sync` is not included in the
  `.ankiaddon`.
- `addon/__init__.py:1-3` imports and registers hooks immediately, so any
  vendored dependency must be importable before `browser_actions` reaches
  tracker persistence.
- Plans 002, 007, and 008 own profile isolation, legacy migration safety, and
  typed recovery errors. Preserve those public contracts rather than
  redesigning them here.

Current model and raw-row boundary:

```python
# addon/share_tools/tracker_database.py:13-26
@dataclass(frozen=True)
class StoredUnsuspendEvent:
    cid: int
    nid: int
    detected_at: datetime
    scope_query: str


@dataclass(frozen=True)
class StoredTrackerState:
    locked_scope_query: Optional[str]
    previous_suspended_cids: tuple[int, ...]
    captured_events: tuple[StoredUnsuspendEvent, ...]
    retention_days: int = DEFAULT_RETENTION_DAYS
```

```python
# addon/share_tools/tracker_database.py:101-121
baseline = tuple(
    int(row[0])
    for row in connection.execute(
        "SELECT cid FROM suspended_baseline ORDER BY cid"
    )
)
events = tuple(
    StoredUnsuspendEvent(
        cid=int(row[0]),
        nid=int(row[1]),
        detected_at=datetime.fromisoformat(str(row[2])),
        scope_query=str(row[3]),
    )
    for row in connection.execute(...)
)
```

Use SQLAlchemy 2.x's documented annotated declarative style:
`Mapped[...]`, `mapped_column()`, and a base inheriting
`MappedAsDataclass` and `DeclarativeBase`. This is the library's PEP 681
dataclass-transform path; do not use legacy `declarative_base()`, the obsolete
SQLAlchemy mypy plugin, or manually typed unannotated `Column` attributes.
Reference:
<https://docs.sqlalchemy.org/en/20/orm/dataclasses.html> and
<https://docs.sqlalchemy.org/en/20/orm/declarative_tables.html>.

## Commands you will need

| Purpose | Command | Expected on success |
|---|---|---|
| Lock | `uv lock --check` | exit 0 after the lock is regenerated |
| Strict persistence types | `uv run pyright --project pyright.strictconfig.json` | 0 errors |
| Repository types | `uv run pyright` | 0 errors |
| Database tests | `uv run pytest -q tests/test_tracker_database.py tests/test_unsuspend_tracker.py` | all pass |
| Packaging tests | `uv run pytest -q tests/test_build_ankiaddon.py` | all pass |
| Full suite | `uv run pytest -q` | all pass |
| Lint | `uv run ruff check .` | all checks pass |
| Build | `uv run python scripts/build_ankiaddon.py` | archive contains importable vendored runtime |

## Suggested executor toolkit

- Use SQLAlchemy's official 2.0 ORM/dataclass documentation linked above.
- Use uv's official `--target` and lock/export documentation when implementing
  build-time vendoring:
  <https://docs.astral.sh/uv/reference/settings/#target> and
  <https://docs.astral.sh/uv/concepts/projects/sync/#exporting-the-lockfile>.
- Inspect the compatibility contract produced by plan 011 before choosing the
  SQLAlchemy constraint or resolving environment markers.

## Scope

**In scope**:
- `pyproject.toml`
- `uv.lock`
- `pyright.strictconfig.json` (create)
- `addon/__init__.py` only for the smallest required vendored-import bootstrap
- `addon/share_tools/tracker_models.py` (create)
- `addon/share_tools/tracker_database.py`
- `addon/share_tools/unsuspend_tracker.py` only if imports or domain conversion
  types must move
- `scripts/build_ankiaddon.py`
- `tests/test_tracker_database.py`
- `tests/test_unsuspend_tracker.py`
- `tests/test_build_ankiaddon.py` (create)
- `.github/workflows/verify.yml`
- `.github/workflows/release.yml`
- `README.md` for the reproducible build/typecheck commands

**Out of scope**:
- Changing the schema-v2 table or column names.
- Changing profile storage paths, retention semantics, event ordering, or
  recovery UX.
- Returning live/session-bound ORM instances from `TrackerDatabase`.
- Adding Alembic for this three-table embedded schema; keep the established
  explicit `PRAGMA user_version` migrator.
- Async SQLAlchemy, `aiosqlite`, or background database access.
- ORM relationships, lazy loading, or cascades; this aggregate is clearer with
  explicit typed queries.
- Committing generated vendored packages under `addon/`.
- Using an Anki/system-installed SQLAlchemy whose version another add-on may
  control.

## Git workflow

- Branch: `codex/013-sqlalchemy-tracker`
- Use logical commits, for example `Add typed tracker ORM models`, `Migrate
  tracker persistence to SQLAlchemy`, and `Vendor tracker runtime dependency`.
- Do not push or open a PR unless instructed.

## Steps

### Step 1: Add a locked runtime dependency and strict type gate

Add a SQLAlchemy 2.0 constraint compatible with the Anki/Python matrix selected
by plan 011. Prefer a bounded 2.0 series such as `SQLAlchemy>=2.0.44,<2.1`
unless the compatibility evidence requires a different lower bound. Regenerate
`uv.lock`; do not leave SQLAlchemy in the dev-only dependency group.

Create `pyright.strictconfig.json` with `typeCheckingMode: "strict"`,
`pythonVersion` matching the support contract, `extraPaths: ["addon"]`, and an
include list limited to the new model module, persistence adapter, and a
positive typing fixture under `tests/typecheck/`. Do not weaken strict
diagnostics in this config to make SQLAlchemy pass.

Add both normal and strict Pyright commands to the verification workflow
created by plan 001. Keep the normal repository check because the strict
project intentionally covers only the persistence boundary.

**Verify**:

- `uv lock --check` exits 0;
- `uv tree | rg "sqlalchemy"` shows exactly one resolved SQLAlchemy line;
- `uv run pyright --project pyright.strictconfig.json` runs, even if it reports
  expected errors until later steps.

### Step 2: Define PEP 681 ORM dataclass models without changing storage

Create `addon/share_tools/tracker_models.py` with:

- `class Base(MappedAsDataclass, DeclarativeBase): ...`;
- `TrackerSettingsModel` mapped to `tracker`;
- `SuspendedBaselineModel` mapped to `suspended_baseline`;
- `FreshUnsuspendModel` mapped to `fresh_unsuspends`;
- every mapped attribute annotated as `Mapped[T]`;
- `mapped_column(init=False, ...)` for the singleton/generated fields that
  callers must not supply;
- explicit dataclass defaults on the right-hand side where constructor defaults
  are intended.

Do not request `frozen=True` or `slots=True`; SQLAlchemy's native dataclass
mapping does not support those options. Keep `StoredTrackerState` and
`StoredUnsuspendEvent` as frozen domain transfer objects so session-bound ORM
entities never escape the persistence adapter.

Preserve `detected_at TEXT NOT NULL` exactly. Implement one strictly typed
`TypeDecorator[datetime]` that binds `datetime.isoformat()` and loads with
`datetime.fromisoformat()`, using `Text` as its SQL implementation and
`cache_ok = True`. Do not change fresh databases to a dialect-specific
`DATETIME` declaration while old databases use `TEXT`.

Add `tests/typecheck/tracker_models_typing.py` using `typing.assert_type()` to
exercise each generated constructor and mapped attribute. Constructor calls
must require the intended non-default fields and infer concrete Python types,
not `Any`.

**Verify**:
`uv run pyright --project pyright.strictconfig.json` reports 0 errors for the
model and typing fixture before database-call migration begins.

### Step 3: Introduce explicit engine, session, and schema boundaries

Refactor `TrackerDatabase` to own one SQLite `Engine` created from its exact
`Path`, with `NullPool` unless measurement or profile lifecycle requires a
different pool. Add `dispose()` and connect it to the shutdown/profile-close
contract from plan 002. Do not retain ORM `Session` objects on
`TrackerDatabase`.

Use:

- `with engine.begin() as connection:` for schema inspection/migration;
- `with Session(engine) as session, session.begin():` for each repository
  operation;
- SQLAlchemy `select()`, `delete()`, and SQLite dialect `insert()` constructs;
- bound expressions only—no formatted SQL values.

Keep `PRAGMA user_version` as the migration authority. Use
`Base.metadata.create_all(connection)` only for a confirmed version-0 empty
database. Keep the version-1-to-version-2 migration explicit and transactional
using `Connection.exec_driver_sql()` where SQLite DDL/PRAGMA has no clearer ORM
equivalent. A schema-preserving backend replacement does not itself justify
schema version 3.

Map SQLAlchemy exceptions into the typed storage failures introduced by plan
008 while retaining each original exception as `__cause__`.

**Verify**: direct tests prove:

- a freshly created database still reports `PRAGMA user_version = 2`;
- `sqlite_master` contains the same three table/column/check contracts;
- a database produced by the pre-ORM implementation loads without migration;
- version 1 still upgrades to version 2;
- `dispose()` permits profile switch, rename, and backup on all supported
  platforms.

### Step 4: Migrate reads and writes while preserving aggregate behavior

Convert each `TrackerDatabase` method independently:

1. `load()` uses typed `select()` statements with deterministic ordering and
   converts detached row values into the frozen domain transfer objects before
   the session closes.
2. `save()` replaces the complete aggregate in one transaction.
3. `apply_snapshot()` retains set-based insert/delete/upsert behavior. Use
   SQLite `insert(...).on_conflict_do_update()` or
   `on_conflict_do_nothing()` rather than one ORM flush per row.
4. `remove_events()`, `clear_events()`, retention updates, and sweeps use
   explicit bulk DML and return the same counts.

Set `expire_on_commit=False` only if conversion outside the transaction is
unavoidable; the preferred shape converts inside the session and does not
expose mapped objects. Do not introduce lazy relationships or hidden queries.

After all methods are converted, `tracker_database.py` must have no direct
`sqlite3.connect()` calls and no positional result indexing such as `row[0]`.
Direct `sqlite3` may remain in test fixtures solely to prove on-disk
compatibility.

**Verify**:

- `uv run pytest -q tests/test_tracker_database.py tests/test_unsuspend_tracker.py`
  passes;
- `rg -n "sqlite3\\.connect|row\\[[0-9]+\\]" addon/share_tools/tracker_database.py`
  returns no matches;
- the strict and normal Pyright commands both report 0 errors.

### Step 5: Vendor the locked pure-Python runtime into the add-on archive

Extend the build pipeline to stage runtime dependencies in a temporary
directory from the lockfile and include them under a private `_vendor/`
directory in the archive. Resolve environment markers for the minimum embedded
Python selected by plan 011, so dependencies such as `typing_extensions` are
not accidentally omitted merely because the release runner uses a newer
Python.

The build must:

- use locked versions and fail if resolution drifts;
- include SQLAlchemy and all required transitive runtime packages;
- omit `.dist-info`, tests, caches, source maps, and compiled extensions
  (`.so`, `.pyd`, `.dylib`);
- leave the working tree clean except for the ignored `dist/` artifact;
- bootstrap the private vendor directory before importing `browser_actions`;
- never silently use a system/Anki SQLAlchemy.

Keep the vendor path insertion as narrow as possible: add it for the initial
dependency import and remove the path entry afterward. If the supported Anki
process already has a different top-level `sqlalchemy` in `sys.modules`, fail
with the typed initialization/recovery path instead of replacing another
add-on's module.

Add a packaging test that builds/extracts the archive into `tmp_path`, asserts
the dependency allowlist and binary denylist, and imports SQLAlchemy plus
`share_tools.tracker_models` with only the extracted add-on and standard
library available. Run this smoke test under every Python environment promised
by plan 011.

**Verify**:

- `uv run python scripts/build_ankiaddon.py` succeeds;
- `uv run pytest -q tests/test_build_ankiaddon.py` passes;
- `unzip -l dist/share_tools.ankiaddon | rg "_vendor/sqlalchemy/__init__\\.py"`
  finds the vendored package;
- `unzip -l dist/share_tools.ankiaddon | rg "\\.(so|pyd|dylib)$"` returns no
  matches.

### Step 6: Run regression and manual compatibility checks

Run the full suite, both typecheck modes, Ruff, locked build, and
`git diff --check`. In each supported Anki version available:

1. install the built archive in a clean profile;
2. load an existing schema-v2 tracker database;
3. lock a scope and detect an unsuspend;
4. change retention and restart;
5. switch profiles and confirm the prior engine is disposed;
6. trigger the plan-008 backup/recovery path;
7. inspect the debug console to confirm the imported SQLAlchemy resolves from
   this add-on's private vendor directory.

Record manual results in the PR description rather than source comments.

**Verify**: every automated command exits 0, the manual smoke result is
recorded, and `git status --short` contains only in-scope source changes plus
pre-existing work and the ignored build artifact.

## Test plan

Model new direct database tests after plan 008's
`tests/test_tracker_database.py` fixtures and retain the end-to-end persistence
tests in `tests/test_unsuspend_tracker.py`.

Cover:

- generated dataclass constructor and attribute inference under strict Pyright;
- exact fresh schema and version;
- byte-for-byte logical compatibility with a pre-ORM schema-v2 fixture;
- version-1 migration;
- full state save/load and deterministic ordering;
- empty and multi-row bulk snapshot upsert/delete;
- retention update plus sweep in one transaction;
- rollback after an injected mid-operation failure;
- typed error wrapping with original cause;
- engine disposal during profile close/recovery rename;
- archive dependency presence, denylist, and isolated import;
- minimum/current supported Python import smoke tests.

## Done criteria

- [ ] SQLAlchemy is a locked runtime dependency compatible with plan 011.
- [ ] All three tables have `Mapped[...]` declarative models transformed
  through `MappedAsDataclass`.
- [ ] Strict Pyright verifies model constructors and the persistence adapter
  without `Any`, ignores, casts around ORM APIs, or disabled diagnostics.
- [ ] Existing schema-v2 files open unchanged; schema names, columns, checks,
  ordering, and retention behavior remain compatible.
- [ ] Every public `TrackerDatabase` mutation remains one explicit transaction
  and bulk operations remain set-based.
- [ ] No live ORM object or `Session` escapes `TrackerDatabase`.
- [ ] The `.ankiaddon` contains locked pure-Python runtime dependencies and no
  platform-specific extension.
- [ ] Archive imports pass under the supported Python matrix.
- [ ] Full tests, Ruff, normal Pyright, strict Pyright, build, and
  `git diff --check` pass.
- [ ] `plans/README.md` status is updated.

## STOP conditions

- Plan 011's minimum Anki/Python runtime is incompatible with a supported
  SQLAlchemy 2.0 release.
- Plans 002, 007, or 008 are not complete or their profile/migration/recovery
  contracts cannot be preserved behind the ORM adapter.
- A pre-ORM schema-v2 fixture cannot be loaded without destructive migration.
- SQLAlchemy requires a platform-specific extension for the selected usage;
  do not ship a build-machine binary in a cross-platform add-on.
- Locked vendoring cannot include target-Python conditional dependencies
  reproducibly.
- A different SQLAlchemy is already loaded in supported Anki startup and the
  vendored import cannot be isolated without replacing global modules.
- Strict typing requires the obsolete SQLAlchemy mypy plugin, `Any`-typed
  repository APIs, blanket ignores, or disabling diagnostics.
- Preserving bulk semantics would require one ORM round trip per card/event.

## Maintenance notes

Treat ORM models as an internal storage schema, not domain objects. Future
schema changes must update the declarative mapping, explicit
`PRAGMA user_version` migration, compatibility fixtures, and archive smoke
tests together. Review dependency upgrades for Python-floor changes, vendored
size, new binary artifacts, and PEP 681/type-checker regressions before
updating the lock.
