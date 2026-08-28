from __future__ import annotations

import sqlite3
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

SCHEMA_VERSION = 9


@contextmanager
def connect(database_path: Path) -> Iterator[sqlite3.Connection]:
    """Open an independent SQLite connection using the operational safety defaults."""
    database_path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(database_path, timeout=5, isolation_level=None)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA journal_mode = WAL")
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA busy_timeout = 5000")
    try:
        yield connection
    finally:
        connection.close()


@contextmanager
def immediate(database_path: Path) -> Iterator[sqlite3.Connection]:
    with connect(database_path) as connection:
        connection.execute("BEGIN IMMEDIATE")
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise


SCHEMA = """
CREATE TABLE IF NOT EXISTS schema_metadata (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS plans (
    id TEXT NOT NULL,
    revision INTEGER NOT NULL,
    parent_revision INTEGER,
    title TEXT NOT NULL,
    status TEXT NOT NULL,
    version INTEGER NOT NULL DEFAULT 0,
    content_json TEXT NOT NULL,
    approved_by TEXT,
    approved_at TEXT,
    created_at TEXT NOT NULL,
    PRIMARY KEY (id, revision)
);
CREATE TABLE IF NOT EXISTS plan_groups (
    id TEXT PRIMARY KEY,
    plan_id TEXT NOT NULL,
    plan_revision INTEGER NOT NULL,
    kind TEXT NOT NULL,
    title TEXT NOT NULL,
    position INTEGER NOT NULL,
    FOREIGN KEY (plan_id, plan_revision) REFERENCES plans(id, revision)
);
CREATE TABLE IF NOT EXISTS tasks (
    id TEXT PRIMARY KEY,
    plan_id TEXT NOT NULL,
    plan_revision INTEGER NOT NULL,
    title TEXT NOT NULL,
    objective TEXT NOT NULL DEFAULT '',
    scope TEXT NOT NULL DEFAULT '',
    acceptance_json TEXT NOT NULL DEFAULT '[]',
    tests_json TEXT NOT NULL DEFAULT '[]',
    priority TEXT NOT NULL DEFAULT 'P2',
    risk TEXT NOT NULL DEFAULT 'MEDIUM',
    suggested_profile TEXT NOT NULL DEFAULT 'worker',
    group_id TEXT,
    evidence_profile TEXT NOT NULL DEFAULT 'default',
    evidence_policy_json TEXT NOT NULL DEFAULT '{}',
    state TEXT NOT NULL DEFAULT 'BACKLOG',
    version INTEGER NOT NULL DEFAULT 0,
    plan_order INTEGER NOT NULL DEFAULT 0,
    critical_path INTEGER NOT NULL DEFAULT 0,
    downstream_count INTEGER NOT NULL DEFAULT 0,
    git_required INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY (plan_id, plan_revision) REFERENCES plans(id, revision)
);
CREATE TABLE IF NOT EXISTS task_paths (
    task_id TEXT NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
    path TEXT NOT NULL,
    PRIMARY KEY (task_id, path)
);
CREATE TABLE IF NOT EXISTS dependencies (
    task_id TEXT NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
    depends_on_task_id TEXT NOT NULL REFERENCES tasks(id),
    kind TEXT NOT NULL,
    PRIMARY KEY (task_id, depends_on_task_id, kind),
    CHECK (task_id <> depends_on_task_id)
);
CREATE TABLE IF NOT EXISTS dependency_waivers (
    id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL,
    depends_on_task_id TEXT NOT NULL,
    kind TEXT NOT NULL,
    reason TEXT NOT NULL,
    actor_id TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE (task_id, depends_on_task_id, kind),
    FOREIGN KEY (task_id, depends_on_task_id, kind)
        REFERENCES dependencies(task_id, depends_on_task_id, kind)
);
CREATE TABLE IF NOT EXISTS agents (
    id TEXT PRIMARY KEY,
    profile TEXT NOT NULL,
    capacity INTEGER NOT NULL DEFAULT 1,
    enabled INTEGER NOT NULL DEFAULT 1,
    version INTEGER NOT NULL DEFAULT 0,
    last_heartbeat_at TEXT
);
CREATE TABLE IF NOT EXISTS agent_instances (
    id TEXT PRIMARY KEY,
    agent_id TEXT NOT NULL REFERENCES agents(id),
    thread_id TEXT,
    status TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS assignments (
    id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL REFERENCES tasks(id),
    agent_id TEXT NOT NULL REFERENCES agents(id),
    instance_id TEXT REFERENCES agent_instances(id),
    status TEXT NOT NULL,
    lease_generation INTEGER NOT NULL,
    reserved_at TEXT NOT NULL,
    accepted_at TEXT,
    expires_at TEXT NOT NULL,
    released_at TEXT,
    worktree_path TEXT,
    branch_ref TEXT,
    start_sha TEXT,
    config_revision_id TEXT,
    policy_snapshot_json TEXT NOT NULL DEFAULT '{}',
    UNIQUE (task_id, lease_generation)
);
CREATE UNIQUE INDEX IF NOT EXISTS one_active_assignment_per_task
ON assignments(task_id) WHERE status IN ('RESERVED', 'ACCEPTED');
CREATE TABLE IF NOT EXISTS leases (
    id TEXT PRIMARY KEY,
    assignment_id TEXT NOT NULL REFERENCES assignments(id),
    task_id TEXT NOT NULL REFERENCES tasks(id),
    generation INTEGER NOT NULL,
    status TEXT NOT NULL,
    expires_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS lease_paths (
    lease_id TEXT NOT NULL REFERENCES leases(id) ON DELETE CASCADE,
    path TEXT NOT NULL,
    PRIMARY KEY (lease_id, path)
);
CREATE TABLE IF NOT EXISTS runs (
    id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL REFERENCES tasks(id),
    assignment_id TEXT NOT NULL REFERENCES assignments(id),
    actor_id TEXT NOT NULL,
    status TEXT NOT NULL,
    lease_generation INTEGER NOT NULL,
    plan_revision INTEGER NOT NULL,
    attempt INTEGER NOT NULL,
    started_at TEXT NOT NULL,
    heartbeat_at TEXT NOT NULL,
    finished_at TEXT,
    failure_kind TEXT,
    failure_reason TEXT,
    result_summary TEXT,
    worktree_path TEXT,
    branch_ref TEXT,
    start_sha TEXT,
    checkpoint_sha TEXT,
    integration_sha TEXT,
    input_tokens INTEGER,
    output_tokens INTEGER,
    completed_at TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS one_running_run_per_task
ON runs(task_id) WHERE status = 'RUNNING';
CREATE TABLE IF NOT EXISTS checkpoints (
    id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES runs(id),
    kind TEXT NOT NULL,
    summary TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS blockers (
    id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL REFERENCES tasks(id),
    run_id TEXT,
    status TEXT NOT NULL,
    reason TEXT NOT NULL,
    owner TEXT,
    previous_state TEXT NOT NULL,
    created_at TEXT NOT NULL,
    resolved_at TEXT
);
CREATE TABLE IF NOT EXISTS reviews (
    id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL REFERENCES tasks(id),
    run_id TEXT NOT NULL REFERENCES runs(id),
    reviewer_id TEXT,
    reviewer_instance_id TEXT REFERENCES agent_instances(id),
    reviewer_thread_id TEXT,
    status TEXT NOT NULL,
    decision_reason TEXT,
    human_approved INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    started_at TEXT,
    decided_at TEXT
);
CREATE TABLE IF NOT EXISTS review_approvals (
    review_id TEXT NOT NULL REFERENCES reviews(id),
    actor_id TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (review_id, actor_id)
);
CREATE TABLE IF NOT EXISTS evidence (
    id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL REFERENCES tasks(id),
    run_id TEXT NOT NULL REFERENCES runs(id),
    kind TEXT NOT NULL,
    summary TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS config_revisions (
    id TEXT PRIMARY KEY,
    revision INTEGER NOT NULL,
    status TEXT NOT NULL,
    version INTEGER NOT NULL DEFAULT 0,
    content_hash TEXT NOT NULL,
    content_json TEXT NOT NULL,
    validation_json TEXT NOT NULL DEFAULT '{}',
    actor_id TEXT NOT NULL,
    created_at TEXT NOT NULL,
    applied_at TEXT
);
CREATE TABLE IF NOT EXISTS capabilities (
    token_hash TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    actor_id TEXT NOT NULL,
    roles_json TEXT NOT NULL,
    task_id TEXT,
    run_id TEXT,
    instance_id TEXT,
    assignment_id TEXT,
    lease_generation INTEGER,
    operations_json TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    created_at TEXT NOT NULL,
    revoked_at TEXT
);
CREATE TABLE IF NOT EXISTS git_operations (
    id TEXT PRIMARY KEY,
    task_id TEXT,
    run_id TEXT,
    kind TEXT NOT NULL,
    status TEXT NOT NULL,
    target_sha TEXT,
    result_sha TEXT,
    actor_id TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS idempotency (
    key TEXT PRIMARY KEY,
    operation TEXT NOT NULL,
    actor_id TEXT NOT NULL,
    request_hash TEXT NOT NULL,
    response_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS events (
    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id TEXT NOT NULL UNIQUE,
    entity_type TEXT NOT NULL,
    entity_id TEXT NOT NULL,
    event_type TEXT NOT NULL,
    actor_id TEXT NOT NULL,
    task_version INTEGER,
    payload_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);
"""


def _current_version(connection: sqlite3.Connection) -> int:
    exists = connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='schema_metadata'"
    ).fetchone()
    if not exists:
        return 0
    row = connection.execute(
        "SELECT value FROM schema_metadata WHERE key = 'schema_version'"
    ).fetchone()
    return int(row["value"]) if row else 0


def initialize(database_path: Path) -> None:
    """Apply numbered forward migrations; preserve an on-disk backup before upgrades."""
    database_path.parent.mkdir(parents=True, exist_ok=True)
    existed = database_path.exists() and database_path.stat().st_size > 0
    with connect(database_path) as connection:
        old_version = _current_version(connection)
    if old_version > SCHEMA_VERSION:
        raise RuntimeError(
            f"Database schema {old_version} is newer than supported {SCHEMA_VERSION}."
        )
    if existed and old_version < SCHEMA_VERSION:
        backup_path = database_path.with_suffix(
            database_path.suffix + f".bak-v{old_version}-{time.time_ns()}"
        )
        with (
            sqlite3.connect(database_path) as source,
            sqlite3.connect(backup_path) as target,
        ):
            source.backup(target)
    if old_version == SCHEMA_VERSION:
        return
    upgrades: list[str] = []
    if old_version == 1:
        upgrades.append("""
        ALTER TABLE capabilities ADD COLUMN actor_id TEXT NOT NULL DEFAULT '';
        ALTER TABLE capabilities ADD COLUMN roles_json TEXT NOT NULL DEFAULT '[]';
        ALTER TABLE capabilities ADD COLUMN assignment_id TEXT;
        ALTER TABLE capabilities ADD COLUMN created_at TEXT NOT NULL DEFAULT '';
        ALTER TABLE assignments ADD COLUMN worktree_path TEXT;
        ALTER TABLE assignments ADD COLUMN branch_ref TEXT;
        ALTER TABLE assignments ADD COLUMN start_sha TEXT;
        ALTER TABLE reviews ADD COLUMN reviewer_thread_id TEXT;
        ALTER TABLE reviews ADD COLUMN started_at TEXT;
        ALTER TABLE runs ADD COLUMN worktree_path TEXT;
        ALTER TABLE runs ADD COLUMN branch_ref TEXT;
        ALTER TABLE runs ADD COLUMN start_sha TEXT;
        ALTER TABLE runs ADD COLUMN checkpoint_sha TEXT;
        ALTER TABLE runs ADD COLUMN integration_sha TEXT;
        ALTER TABLE config_revisions ADD COLUMN version INTEGER NOT NULL DEFAULT 0;
        ALTER TABLE config_revisions ADD COLUMN validation_json TEXT NOT NULL DEFAULT '{}';
        ALTER TABLE config_revisions ADD COLUMN applied_at TEXT;
        """)
    if old_version in {1, 2}:
        upgrades.append(
            "ALTER TABLE runs ADD COLUMN failure_reason TEXT;"
        )
    if old_version in {1, 2, 3}:
        upgrades.append(
            """
            ALTER TABLE tasks ADD COLUMN evidence_profile TEXT NOT NULL DEFAULT 'default';
            ALTER TABLE tasks ADD COLUMN evidence_policy_json TEXT NOT NULL DEFAULT '{}';
            """
        )
    if old_version in {1, 2, 3, 4}:
        upgrades.append(
            "ALTER TABLE reviews ADD COLUMN reviewer_instance_id TEXT;"
        )
    if old_version in {1, 2, 3, 4, 5}:
        upgrades.append("ALTER TABLE tasks ADD COLUMN group_id TEXT;")
    if old_version in {1, 2, 3, 4, 5, 6}:
        upgrades.append(
            """
            ALTER TABLE assignments ADD COLUMN config_revision_id TEXT;
            ALTER TABLE assignments
                ADD COLUMN policy_snapshot_json TEXT NOT NULL DEFAULT '{}';
            """
        )
    if old_version in {1, 2, 3, 4, 5, 6, 7}:
        upgrades.append("ALTER TABLE runs ADD COLUMN result_summary TEXT;")
    if old_version in {1, 2, 3, 4, 5, 6, 7, 8}:
        upgrades.append(
            """
            ALTER TABLE runs ADD COLUMN input_tokens INTEGER;
            ALTER TABLE runs ADD COLUMN output_tokens INTEGER;
            ALTER TABLE runs ADD COLUMN completed_at TEXT;
            """
        )
    upgrade = "\n".join(upgrades)
    migration = (
        "BEGIN IMMEDIATE;\n"
        + SCHEMA
        + "\n"
        + upgrade
        + "\n"
        + f"""
        INSERT INTO schema_metadata(key, value)
        VALUES ('schema_version', '{SCHEMA_VERSION}')
        ON CONFLICT(key) DO UPDATE SET value = excluded.value;
        COMMIT;
        """
    )
    with connect(database_path) as connection:
        try:
            connection.executescript(migration)
        except Exception:
            if connection.in_transaction:
                connection.rollback()
            raise
