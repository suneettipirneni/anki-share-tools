from datetime import datetime
from pathlib import Path
import sqlite3
from typing import cast

import pytest

from share_tools.tracker_database import (
    StoredTrackerState,
    StoredUnsuspendEvent,
    TrackerDatabase,
    TrackerStorageError,
)


def create_v2_schema(connection: sqlite3.Connection) -> None:
    connection.executescript(
        """
        CREATE TABLE tracker (
            singleton INTEGER PRIMARY KEY,
            locked_scope_query TEXT,
            retention_days INTEGER NOT NULL
        );
        CREATE TABLE suspended_baseline (cid INTEGER PRIMARY KEY);
        CREATE TABLE fresh_unsuspends (
            cid INTEGER PRIMARY KEY,
            nid INTEGER NOT NULL,
            detected_at TEXT NOT NULL,
            scope_query TEXT NOT NULL
        );
        PRAGMA user_version = 2;
        """
    )


def assert_storage_error(
    path: Path,
    category: str,
) -> TrackerStorageError:
    with pytest.raises(TrackerStorageError) as error:
        TrackerDatabase(path).load()
    assert error.value.category == category
    assert error.value.path == path
    return error.value


def test_random_bytes_are_classified_as_database_failure(tmp_path) -> None:
    path = tmp_path / "tracker.sqlite3"
    contents = b"private row bytes that must not be shown"
    path.write_bytes(contents)

    error = assert_storage_error(path, "database")

    assert isinstance(error.__cause__, sqlite3.DatabaseError)
    assert contents.decode() not in str(error)


def test_missing_table_is_malformed_schema(tmp_path) -> None:
    path = tmp_path / "tracker.sqlite3"
    with sqlite3.connect(path) as connection:
        connection.executescript(
            """
            CREATE TABLE tracker (
                singleton INTEGER PRIMARY KEY,
                locked_scope_query TEXT,
                retention_days INTEGER
            );
            PRAGMA user_version = 2;
            """
        )

    assert_storage_error(path, "malformed-schema")


def test_partial_version_one_schema_is_rejected_before_migration(tmp_path) -> None:
    path = tmp_path / "tracker.sqlite3"
    with sqlite3.connect(path) as connection:
        connection.executescript(
            """
            CREATE TABLE tracker (
                singleton INTEGER PRIMARY KEY,
                locked_scope_query TEXT
            );
            CREATE TABLE suspended_baseline (cid INTEGER PRIMARY KEY);
            PRAGMA user_version = 1;
            """
        )

    assert_storage_error(path, "malformed-schema")


def test_version_two_tracker_missing_retention_is_malformed(tmp_path) -> None:
    path = tmp_path / "tracker.sqlite3"
    with sqlite3.connect(path) as connection:
        connection.executescript(
            """
            CREATE TABLE tracker (
                singleton INTEGER PRIMARY KEY,
                locked_scope_query TEXT
            );
            CREATE TABLE suspended_baseline (cid INTEGER PRIMARY KEY);
            CREATE TABLE fresh_unsuspends (
                cid INTEGER PRIMARY KEY,
                nid INTEGER,
                detected_at TEXT,
                scope_query TEXT
            );
            PRAGMA user_version = 2;
            """
        )

    assert_storage_error(path, "malformed-schema")


@pytest.mark.parametrize("retention_days", [-1, "thirty"])
def test_invalid_retention_row_is_corrupt(tmp_path, retention_days) -> None:
    path = tmp_path / "tracker.sqlite3"
    with sqlite3.connect(path) as connection:
        create_v2_schema(connection)
        connection.execute(
            """
            INSERT INTO tracker(singleton, locked_scope_query, retention_days)
            VALUES (1, 'deck:current', ?)
            """,
            (retention_days,),
        )

    assert_storage_error(path, "corrupt-row")


def test_future_schema_has_distinct_category(tmp_path) -> None:
    path = tmp_path / "tracker.sqlite3"
    with sqlite3.connect(path) as connection:
        connection.execute("PRAGMA user_version = 99")

    assert_storage_error(path, "future-schema")


def test_invalid_event_timestamp_is_corrupt_row(tmp_path) -> None:
    path = tmp_path / "tracker.sqlite3"
    with sqlite3.connect(path) as connection:
        create_v2_schema(connection)
        connection.execute(
            """
            INSERT INTO tracker(singleton, locked_scope_query, retention_days)
            VALUES (1, 'deck:current', 30)
            """
        )
        connection.execute(
            """
            INSERT INTO fresh_unsuspends(cid, nid, detected_at, scope_query)
            VALUES (10, 1, 'not-a-timestamp', 'deck:current')
            """
        )

    assert_storage_error(path, "corrupt-row")


def test_version_zero_with_partial_tables_is_malformed(tmp_path) -> None:
    path = tmp_path / "tracker.sqlite3"
    with sqlite3.connect(path) as connection:
        connection.execute("CREATE TABLE unrelated(value TEXT)")

    assert_storage_error(path, "malformed-schema")


def test_failed_snapshot_transaction_preserves_preexisting_durable_state(
    tmp_path,
) -> None:
    path = tmp_path / "tracker.sqlite3"
    database = TrackerDatabase(path)
    original_state = StoredTrackerState(
        locked_scope_query="deck:current",
        previous_suspended_cids=(10,),
        captured_events=(
            StoredUnsuspendEvent(
                cid=30,
                nid=3,
                detected_at=datetime(2026, 7, 26, 12),
                scope_query="deck:current",
            ),
        ),
        retention_days=30,
    )
    database.save(original_state)

    invalid_event = StoredUnsuspendEvent(
        cid=40,
        nid=4,
        detected_at=datetime(2026, 7, 26, 13),
        scope_query=cast(str, None),
    )
    with pytest.raises(TrackerStorageError) as error:
        database.apply_snapshot(
            baseline_added={20},
            baseline_removed={10},
            removed_event_cids={30},
            added_events=[invalid_event],
        )

    assert error.value.category == "database"
    assert isinstance(error.value.__cause__, sqlite3.IntegrityError)
    assert TrackerDatabase(path).load() == original_state
