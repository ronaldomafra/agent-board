from pathlib import Path

from agentboard.storage import connect, initialize


def test_initialize_creates_schema_metadata(tmp_path: Path) -> None:
    database_path = tmp_path / "state.db"

    initialize(database_path)

    with connect(database_path) as connection:
        row = connection.execute(
            "SELECT value FROM schema_metadata WHERE key = 'schema_version'"
        ).fetchone()

    assert row["value"] == "0"

