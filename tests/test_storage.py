import sqlite3
from pathlib import Path

from agentboard.storage import SCHEMA_VERSION, connect, initialize


def test_initialize_creates_complete_schema_and_pragmas(tmp_path: Path) -> None:
    database_path = tmp_path / "state.db"

    initialize(database_path)

    with connect(database_path) as connection:
        row = connection.execute(
            "SELECT value FROM schema_metadata WHERE key = 'schema_version'"
        ).fetchone()
        tables = {
            item["name"]
            for item in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        foreign_keys = connection.execute("PRAGMA foreign_keys").fetchone()[0]
        journal_mode = connection.execute("PRAGMA journal_mode").fetchone()[0]
        busy_timeout = connection.execute("PRAGMA busy_timeout").fetchone()[0]

    assert row["value"] == str(SCHEMA_VERSION)
    assert {
        "plans",
        "tasks",
        "dependencies",
        "dependency_waivers",
        "agents",
        "assignments",
        "leases",
        "runs",
        "checkpoints",
        "blockers",
        "reviews",
        "evidence",
        "config_revisions",
        "capabilities",
        "git_operations",
        "idempotency",
        "events",
    } <= tables
    assert foreign_keys == 1
    assert journal_mode == "wal"
    assert busy_timeout == 5000


def test_initialization_is_idempotent(tmp_path: Path) -> None:
    database_path = tmp_path / "state.db"

    initialize(database_path)
    initialize(database_path)

    with connect(database_path) as connection:
        versions = connection.execute(
            "SELECT COUNT(*) FROM schema_metadata WHERE key='schema_version'"
        ).fetchone()[0]
    assert versions == 1


def test_v2_migration_is_transactional_and_creates_unique_backup(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "state.db"
    with sqlite3.connect(database_path) as connection:
        connection.executescript(
            """
            CREATE TABLE schema_metadata (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
            INSERT INTO schema_metadata(key, value) VALUES ('schema_version', '2');
                CREATE TABLE runs (
                    id TEXT PRIMARY KEY,
                    task_id TEXT NOT NULL,
                    status TEXT NOT NULL,
                    failure_kind TEXT
                );
                CREATE TABLE tasks (id TEXT PRIMARY KEY);
                CREATE TABLE reviews (id TEXT PRIMARY KEY);
                CREATE TABLE assignments (
                    id TEXT PRIMARY KEY,
                    task_id TEXT NOT NULL,
                    status TEXT NOT NULL
                );
                """
            )

    initialize(database_path)

    with connect(database_path) as connection:
        columns = {
            row["name"] for row in connection.execute("PRAGMA table_info(runs)")
        }
        assignment_columns = {
            row["name"]
            for row in connection.execute("PRAGMA table_info(assignments)")
        }
        version = connection.execute(
            "SELECT value FROM schema_metadata WHERE key='schema_version'"
        ).fetchone()["value"]
    backups = list(tmp_path.glob("state.db.bak-v2-*"))
    assert "failure_reason" in columns
    assert {"input_tokens", "output_tokens", "completed_at"} <= columns
    assert {"config_revision_id", "policy_snapshot_json"} <= assignment_columns
    assert version == str(SCHEMA_VERSION)
    assert len(backups) == 1
