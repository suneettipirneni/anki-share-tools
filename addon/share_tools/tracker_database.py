from contextlib import closing
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
import sqlite3
from typing import Optional


SCHEMA_VERSION = 2
DEFAULT_RETENTION_DAYS = 30


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

        self.path.parent.mkdir(parents=True, exist_ok=True)

        with closing(self._connect()) as connection, connection:
            version = int(connection.execute("PRAGMA user_version").fetchone()[0])

            if version > SCHEMA_VERSION:
                raise RuntimeError(
                    "The fresh-card database was created by a newer Share Tools "
                    f"version (schema {version})."
                )

            if version == 0:
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
                connection.executescript(
                    """
                    ALTER TABLE tracker
                    ADD COLUMN retention_days INTEGER NOT NULL DEFAULT 30
                        CHECK (retention_days >= 0);

                    PRAGMA user_version = 2;
                    """
                )

        self._initialized = True

    def load(self) -> Optional[StoredTrackerState]:
        self.initialize()

        with closing(self._connect()) as connection, connection:
            tracker_row = connection.execute(
                """
                SELECT locked_scope_query, retention_days
                FROM tracker
                WHERE singleton = 1
                """
            ).fetchone()

            if tracker_row is None:
                return None

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
                for row in connection.execute(
                    """
                    SELECT cid, nid, detected_at, scope_query
                    FROM fresh_unsuspends
                    ORDER BY detected_at, cid
                    """
                )
            )

        return StoredTrackerState(
            locked_scope_query=(
                str(tracker_row[0]) if tracker_row[0] is not None else None
            ),
            previous_suspended_cids=baseline,
            captured_events=events,
            retention_days=int(tracker_row[1]),
        )

    def save(self, state: StoredTrackerState) -> None:
        self.initialize()

        with closing(self._connect()) as connection, connection:
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

        with closing(self._connect()) as connection, connection:
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

        with closing(self._connect()) as connection, connection:
            connection.executemany(
                "DELETE FROM fresh_unsuspends WHERE cid = ?",
                ((cid,) for cid in card_ids),
            )

    def clear_events(self) -> None:
        self.initialize()

        with closing(self._connect()) as connection, connection:
            connection.execute("DELETE FROM fresh_unsuspends")

    def set_retention_days_and_sweep(
        self,
        retention_days: int,
        cutoff: Optional[datetime],
    ) -> int:
        self.initialize()

        with closing(self._connect()) as connection, connection:
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

        with closing(self._connect()) as connection, connection:
            cursor = connection.execute(
                "DELETE FROM fresh_unsuspends WHERE detected_at < ?",
                (cutoff.isoformat(),),
            )
            return cursor.rowcount

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.path)
