from contextlib import closing, contextmanager
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
import sqlite3
from typing import Iterator, Optional


SCHEMA_VERSION = 2
DEFAULT_RETENTION_DAYS = 30
EXPECTED_V1_COLUMNS = {
    "tracker": (
        ("singleton", "INTEGER", 0, 1),
        ("locked_scope_query", "TEXT", 0, 0),
    ),
    "suspended_baseline": (("cid", "INTEGER", 0, 1),),
    "fresh_unsuspends": (
        ("cid", "INTEGER", 0, 1),
        ("nid", "INTEGER", 1, 0),
        ("detected_at", "TEXT", 1, 0),
        ("scope_query", "TEXT", 1, 0),
    ),
}
EXPECTED_V2_COLUMNS = {
    **EXPECTED_V1_COLUMNS,
    "tracker": (
        *EXPECTED_V1_COLUMNS["tracker"],
        ("retention_days", "INTEGER", 1, 0),
    ),
}


class TrackerStorageError(RuntimeError):
    def __init__(self, category: str, path: Path) -> None:
        self.category = category
        self.path = path
        super().__init__(f"Tracker storage unavailable ({category}): {path}")


class TrackerMigrationError(RuntimeError):
    def __init__(
        self,
        category: str,
        source_path: Path,
        destination_path: Path,
        quarantine_path: Optional[Path] = None,
    ) -> None:
        self.category = category
        self.source_path = source_path
        self.destination_path = destination_path
        self.quarantine_path = quarantine_path
        message = (
            f"Tracker migration failed ({category}): "
            f"{source_path} -> {destination_path}"
        )

        if quarantine_path is not None:
            message += f"; destination quarantined at {quarantine_path}"

        super().__init__(message)


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


class TrackerDatabase:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._initialized = False

    def initialize(self) -> None:
        if self._initialized:
            return

        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise TrackerStorageError("database", self.path) from exc

        with self._connection() as connection:
            version = int(connection.execute("PRAGMA user_version").fetchone()[0])

            if version > SCHEMA_VERSION:
                raise TrackerStorageError("future-schema", self.path)

            if version == 0:
                if self._user_tables(connection):
                    raise TrackerStorageError("malformed-schema", self.path)
                connection.executescript(
                    """
                    CREATE TABLE tracker (
                        singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
                        locked_scope_query TEXT,
                        retention_days INTEGER NOT NULL DEFAULT 30
                            CHECK (retention_days >= 0)
                    );

                    CREATE TABLE suspended_baseline (
                        cid INTEGER PRIMARY KEY
                    );

                    CREATE TABLE fresh_unsuspends (
                        cid INTEGER PRIMARY KEY,
                        nid INTEGER NOT NULL,
                        detected_at TEXT NOT NULL,
                        scope_query TEXT NOT NULL
                    );

                    PRAGMA user_version = 2;
                    """
                )
            elif version == 1:
                self._validate_schema(connection, EXPECTED_V1_COLUMNS)
                connection.executescript(
                    """
                    ALTER TABLE tracker
                    ADD COLUMN retention_days INTEGER NOT NULL DEFAULT 30
                        CHECK (retention_days >= 0);

                    PRAGMA user_version = 2;
                    """
                )

            self._validate_schema(connection, EXPECTED_V2_COLUMNS)

        self._initialized = True

    def load(self) -> Optional[StoredTrackerState]:
        self.initialize()

        with self._connection() as connection:
            tracker_row = connection.execute(
                """
                SELECT locked_scope_query, retention_days
                FROM tracker
                WHERE singleton = 1
                """
            ).fetchone()

            if tracker_row is None:
                return None

            locked_scope_query = tracker_row[0]
            if locked_scope_query is not None and not isinstance(
                locked_scope_query,
                str,
            ):
                raise TrackerStorageError("corrupt-row", self.path)

            retention_days = self._row_integer(tracker_row[1])
            if retention_days < 0:
                raise TrackerStorageError("corrupt-row", self.path)

            baseline = tuple(
                self._row_integer(row[0])
                for row in connection.execute(
                    "SELECT cid FROM suspended_baseline ORDER BY cid"
                )
            )
            events: list[StoredUnsuspendEvent] = []
            for row in connection.execute(
                """
                SELECT cid, nid, detected_at, scope_query
                FROM fresh_unsuspends
                ORDER BY detected_at, cid
                """
            ):
                detected_at_value = self._row_string(row[2])
                try:
                    detected_at = datetime.fromisoformat(detected_at_value)
                except ValueError as exc:
                    raise TrackerStorageError("corrupt-row", self.path) from exc

                events.append(
                    StoredUnsuspendEvent(
                        cid=self._row_integer(row[0]),
                        nid=self._row_integer(row[1]),
                        detected_at=detected_at,
                        scope_query=self._row_string(row[3]),
                    )
                )

        return StoredTrackerState(
            locked_scope_query=locked_scope_query,
            previous_suspended_cids=baseline,
            captured_events=tuple(events),
            retention_days=retention_days,
        )

    def save(self, state: StoredTrackerState) -> None:
        self.initialize()

        with self._connection() as connection:
            connection.execute(
                """
                INSERT INTO tracker(singleton, locked_scope_query, retention_days)
                VALUES (1, ?, ?)
                ON CONFLICT(singleton) DO UPDATE SET
                    locked_scope_query = excluded.locked_scope_query,
                    retention_days = excluded.retention_days
                """,
                (state.locked_scope_query, state.retention_days),
            )
            connection.execute("DELETE FROM suspended_baseline")
            connection.executemany(
                "INSERT INTO suspended_baseline(cid) VALUES (?)",
                ((cid,) for cid in state.previous_suspended_cids),
            )
            connection.execute("DELETE FROM fresh_unsuspends")
            connection.executemany(
                """
                INSERT INTO fresh_unsuspends(
                    cid,
                    nid,
                    detected_at,
                    scope_query
                )
                VALUES (?, ?, ?, ?)
                """,
                (
                    (
                        event.cid,
                        event.nid,
                        event.detected_at.isoformat(),
                        event.scope_query,
                    )
                    for event in state.captured_events
                ),
            )

    def apply_snapshot(
        self,
        baseline_added: set[int],
        baseline_removed: set[int],
        removed_event_cids: set[int],
        added_events: list[StoredUnsuspendEvent],
    ) -> None:
        self.initialize()

        with self._connection() as connection:
            connection.executemany(
                "INSERT OR IGNORE INTO suspended_baseline(cid) VALUES (?)",
                ((cid,) for cid in baseline_added),
            )
            connection.executemany(
                "DELETE FROM suspended_baseline WHERE cid = ?",
                ((cid,) for cid in baseline_removed),
            )
            connection.executemany(
                "DELETE FROM fresh_unsuspends WHERE cid = ?",
                ((cid,) for cid in removed_event_cids),
            )
            connection.executemany(
                """
                INSERT INTO fresh_unsuspends(
                    cid,
                    nid,
                    detected_at,
                    scope_query
                )
                VALUES (?, ?, ?, ?)
                ON CONFLICT(cid) DO UPDATE SET
                    nid = excluded.nid,
                    detected_at = excluded.detected_at,
                    scope_query = excluded.scope_query
                """,
                (
                    (
                        event.cid,
                        event.nid,
                        event.detected_at.isoformat(),
                        event.scope_query,
                    )
                    for event in added_events
                ),
            )

    def remove_events(self, card_ids: set[int]) -> None:
        self.initialize()

        with self._connection() as connection:
            connection.executemany(
                "DELETE FROM fresh_unsuspends WHERE cid = ?",
                ((cid,) for cid in card_ids),
            )

    def clear_events(self) -> None:
        self.initialize()

        with self._connection() as connection:
            connection.execute("DELETE FROM fresh_unsuspends")

    def set_retention_days_and_sweep(
        self,
        retention_days: int,
        cutoff: Optional[datetime],
    ) -> int:
        self.initialize()

        with self._connection() as connection:
            connection.execute(
                """
                UPDATE tracker
                SET retention_days = ?
                WHERE singleton = 1
                """,
                (retention_days,),
            )

            if cutoff is None:
                return 0

            cursor = connection.execute(
                "DELETE FROM fresh_unsuspends WHERE detected_at < ?",
                (cutoff.isoformat(),),
            )
            return cursor.rowcount

    def sweep_events_before(self, cutoff: datetime) -> int:
        self.initialize()

        with self._connection() as connection:
            cursor = connection.execute(
                "DELETE FROM fresh_unsuspends WHERE detected_at < ?",
                (cutoff.isoformat(),),
            )
            return cursor.rowcount

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.path)

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        try:
            with closing(self._connect()) as connection, connection:
                yield connection
        except TrackerStorageError:
            raise
        except (sqlite3.Error, OSError) as exc:
            raise TrackerStorageError("database", self.path) from exc

    def _user_tables(self, connection: sqlite3.Connection) -> set[str]:
        return {
            self._row_string(row[0])
            for row in connection.execute(
                """
                SELECT name
                FROM sqlite_master
                WHERE type = 'table' AND name NOT LIKE 'sqlite_%'
                """
            )
        }

    def _validate_schema(
        self,
        connection: sqlite3.Connection,
        expected_columns: dict[str, tuple[tuple[str, str, int, int], ...]],
    ) -> None:
        if self._user_tables(connection) != set(expected_columns):
            raise TrackerStorageError("malformed-schema", self.path)

        for table_name, columns in expected_columns.items():
            actual_columns = tuple(
                (
                    self._row_string(row[1]),
                    self._row_string(row[2]).upper(),
                    self._row_integer(row[3]),
                    self._row_integer(row[5]),
                )
                for row in connection.execute(f"PRAGMA table_info({table_name})")
            )
            if actual_columns != columns:
                raise TrackerStorageError("malformed-schema", self.path)

    def _row_integer(self, value: object) -> int:
        if not isinstance(value, int) or isinstance(value, bool):
            raise TrackerStorageError("corrupt-row", self.path)
        return value

    def _row_string(self, value: object) -> str:
        if not isinstance(value, str):
            raise TrackerStorageError("corrupt-row", self.path)
        return value
