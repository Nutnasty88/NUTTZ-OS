import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

from services import executor


MISSION_ID = 17501

EVIDENCE = (
    "VERIFIED EXECUTION EVIDENCE:\n"
    "Service contract changed.\n"
    "REPLAN REQUIRED: remaining checks use old contract."
)


@pytest.fixture
def recovery_db(tmp_path, monkeypatch):
    path = tmp_path / "recovery-guard.db"

    def connect():
        conn = sqlite3.connect(path, timeout=5)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    conn = connect()
    try:
        conn.executescript("""
            CREATE TABLE missions (
                id INTEGER PRIMARY KEY,
                status TEXT NOT NULL,
                progress INTEGER NOT NULL DEFAULT 0,
                updated_at TEXT DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE mission_worker_leases (
                mission_id INTEGER PRIMARY KEY,
                owner_token TEXT NOT NULL,
                expires_at TEXT NOT NULL
            );
        """)

        conn.execute(
            "INSERT INTO missions (id, status, progress) "
            "VALUES (?, 'Interrupted', 50)",
            (MISSION_ID,),
        )
        conn.commit()
    finally:
        conn.close()

    monkeypatch.setattr(executor, "get_connection", connect)
    monkeypatch.setattr(
        executor,
        "log_event",
        lambda *args, **kwargs: None,
    )

    executor.ensure_task_table()
    executor.ensure_mission_approval_columns()
    executor.ensure_mission_replan_history_table()

    conn = connect()
    try:
        conn.execute(
            """
            INSERT INTO mission_tasks
                (mission_id, position, title, instructions,
                 status, result)
            VALUES (?, 1, 'Build', 'Build service',
                    'Completed', ?)
            """,
            (MISSION_ID, EVIDENCE),
        )
        conn.execute(
            """
            INSERT INTO mission_tasks
                (mission_id, position, title, instructions,
                 status, result)
            VALUES (?, 2, 'Verify', 'Verify service',
                    'Interrupted', 'Previous attempt')
            """,
            (MISSION_ID,),
        )
        conn.commit()
    finally:
        conn.close()

    return connect


def snapshot(connect):
    conn = connect()
    try:
        mission = conn.execute(
            "SELECT status, progress FROM missions WHERE id=?",
            (MISSION_ID,),
        ).fetchone()
        tasks = conn.execute(
            """
            SELECT position, status, result, started_at,
                   completed_at, execution_token
            FROM mission_tasks
            WHERE mission_id=?
            ORDER BY position
            """,
            (MISSION_ID,),
        ).fetchall()
        return dict(mission), [dict(row) for row in tasks]
    finally:
        conn.close()


def test_unapproved_recovery_preserves_state(recovery_db):
    before = snapshot(recovery_db)

    with pytest.raises(RuntimeError, match="approval"):
        executor.reset_interrupted_task(MISSION_ID)

    assert snapshot(recovery_db) == before


def test_unresolved_evidence_preserves_state(recovery_db):
    assert executor.approve_mission_plan(
        MISSION_ID
    )["approved"]

    before = snapshot(recovery_db)

    with pytest.raises(RuntimeError, match="replan review"):
        executor.reset_interrupted_task(MISSION_ID)

    assert snapshot(recovery_db) == before


def test_acknowledged_evidence_allows_reset(recovery_db):
    assert executor.approve_mission_plan(
        MISSION_ID
    )["approved"]

    conn = recovery_db()
    try:
        conn.execute(
            """
            INSERT INTO mission_replan_history (
                mission_id, original_plan, replacement_plan,
                reason, triggering_evidence, state_fingerprint
            )
            VALUES (?, 'old', 'new', 'Reviewed', ?, 'test')
            """,
            (MISSION_ID, EVIDENCE),
        )
        conn.commit()
    finally:
        conn.close()

    result = executor.reset_interrupted_task(MISSION_ID)

    assert result["status"] == "Pending"

    mission, tasks = snapshot(recovery_db)
    assert mission["status"] == "Running"
    assert tasks[0]["status"] == "Completed"
    assert tasks[0]["result"] == EVIDENCE
    assert tasks[1]["status"] == "Pending"
    assert tasks[1]["result"] == "Previous attempt"


def test_active_lease_rejects_recovery(recovery_db):
    assert executor.approve_mission_plan(
        MISSION_ID
    )["approved"]

    expiry = (
        datetime.now(timezone.utc) + timedelta(hours=1)
    ).isoformat()

    conn = recovery_db()
    try:
        conn.execute(
            """
            INSERT INTO mission_worker_leases
                (mission_id, owner_token, expires_at)
            VALUES (?, 'other-worker', ?)
            """,
            (MISSION_ID, expiry),
        )
        conn.commit()
    finally:
        conn.close()

    before = snapshot(recovery_db)

    with pytest.raises(RuntimeError, match="active worker"):
        executor.reset_interrupted_task(MISSION_ID)

    assert snapshot(recovery_db) == before
