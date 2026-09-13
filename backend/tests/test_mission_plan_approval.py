import sqlite3

import pytest

from services import executor


def _setup_database(tmp_path, monkeypatch):
    db_path = tmp_path / "mission-plan-approval.db"
    mission_id = 15001

    def get_connection():
        connection = sqlite3.connect(
            db_path,
            timeout=5.0,
        )
        connection.row_factory = sqlite3.Row
        connection.execute(
            "PRAGMA foreign_keys = ON"
        )
        connection.execute(
            "PRAGMA busy_timeout = 5000"
        )
        return connection

    conn = get_connection()

    try:
        conn.executescript(
            """
            CREATE TABLE missions (
                id INTEGER PRIMARY KEY,
                title TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'Waiting',
                progress INTEGER NOT NULL DEFAULT 0,
                assigned_agent TEXT DEFAULT '',
                priority TEXT DEFAULT 'Normal',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE mission_tasks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                mission_id INTEGER NOT NULL,
                position INTEGER NOT NULL,
                title TEXT NOT NULL,
                instructions TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL DEFAULT 'Pending',
                result TEXT NOT NULL DEFAULT '',
                execution_token TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                started_at TIMESTAMP,
                completed_at TIMESTAMP,
                FOREIGN KEY (mission_id)
                    REFERENCES missions(id)
                    ON DELETE CASCADE,
                UNIQUE (mission_id, position)
            );
            """
        )

        conn.execute(
            """
            INSERT INTO missions (
                id,
                title,
                status,
                progress,
                assigned_agent,
                priority
            )
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                mission_id,
                "Approval gate regression mission",
                "Running",
                20,
                "Planner",
                "Normal",
            ),
        )

        conn.executemany(
            """
            INSERT INTO mission_tasks (
                mission_id,
                position,
                title,
                instructions,
                status
            )
            VALUES (?, ?, ?, ?, 'Pending')
            """,
            [
                (
                    mission_id,
                    1,
                    "Create application",
                    "Create main.py",
                ),
                (
                    mission_id,
                    2,
                    "Verify application",
                    "Run controlled verification",
                ),
            ],
        )

        conn.commit()

    finally:
        conn.close()

    monkeypatch.setattr(
        executor,
        "get_connection",
        get_connection,
    )

    return mission_id, get_connection


def test_unapproved_plan_is_rejected(
    tmp_path,
    monkeypatch,
):
    mission_id, _ = _setup_database(
        tmp_path,
        monkeypatch,
    )

    status = executor.get_mission_approval_status(
        mission_id
    )

    assert status["approved"] is False
    assert status["approved_at"] is None
    assert status["approved_plan_fingerprint"] is None
    assert status["current_plan_fingerprint"]
    assert status["task_count"] == 2

    with pytest.raises(
        RuntimeError,
        match="explicit approval",
    ):
        executor.require_mission_plan_approval(
            mission_id
        )


def test_approved_plan_is_accepted(
    tmp_path,
    monkeypatch,
):
    mission_id, _ = _setup_database(
        tmp_path,
        monkeypatch,
    )

    approval = executor.approve_mission_plan(
        mission_id
    )

    assert approval["approved"] is True
    assert approval["approved_at"] is not None
    assert approval["task_count"] == 2

    assert (
        approval["approved_plan_fingerprint"]
        == approval["current_plan_fingerprint"]
    )

    required = (
        executor.require_mission_plan_approval(
            mission_id
        )
    )

    assert required["approved"] is True


def test_task_content_change_invalidates_approval(
    tmp_path,
    monkeypatch,
):
    mission_id, get_connection = _setup_database(
        tmp_path,
        monkeypatch,
    )

    approved = executor.approve_mission_plan(
        mission_id
    )

    original_fingerprint = (
        approved["current_plan_fingerprint"]
    )

    conn = get_connection()

    try:
        conn.execute(
            """
            UPDATE mission_tasks
            SET instructions=?
            WHERE mission_id=?
              AND position=2
            """,
            (
                "Run DIFFERENT controlled verification",
                mission_id,
            ),
        )
        conn.commit()
    finally:
        conn.close()

    status = executor.get_mission_approval_status(
        mission_id
    )

    assert status["approved"] is False
    assert (
        status["approved_plan_fingerprint"]
        == original_fingerprint
    )
    assert (
        status["current_plan_fingerprint"]
        != original_fingerprint
    )

    with pytest.raises(
        RuntimeError,
        match="task plan changed",
    ):
        executor.require_mission_plan_approval(
            mission_id
        )


def test_status_only_change_preserves_approval(
    tmp_path,
    monkeypatch,
):
    mission_id, get_connection = _setup_database(
        tmp_path,
        monkeypatch,
    )

    approved = executor.approve_mission_plan(
        mission_id
    )

    fingerprint = (
        approved["current_plan_fingerprint"]
    )

    conn = get_connection()

    try:
        conn.execute(
            """
            UPDATE mission_tasks
            SET status='Blocked',
                result='Synthetic recovery state'
            WHERE mission_id=?
              AND position=1
            """,
            (mission_id,),
        )
        conn.commit()
    finally:
        conn.close()

    status = executor.get_mission_approval_status(
        mission_id
    )

    assert status["approved"] is True
    assert (
        status["current_plan_fingerprint"]
        == fingerprint
    )
    assert (
        status["approved_plan_fingerprint"]
        == fingerprint
    )


def test_start_worker_rejects_unapproved_plan(
    tmp_path,
    monkeypatch,
):
    from services import autonomous_worker

    mission_id, _ = _setup_database(
        tmp_path,
        monkeypatch,
    )

    with pytest.raises(
        RuntimeError,
        match="explicit approval",
    ):
        autonomous_worker.start_worker(
            mission_id,
            delay_seconds=0.01,
        )

    worker = autonomous_worker.get_worker_status()

    # Rejection must occur before this mission acquires worker
    # ownership or starts a new worker thread. The status field is
    # process-global and may reflect a previous completed test.
    assert worker["mission_id"] is None
    assert worker["current_task_id"] is None
    assert worker["thread_alive"] is False



def test_execute_next_task_rejects_unapproved_plan(
    tmp_path,
    monkeypatch,
):
    mission_id, _ = _setup_database(
        tmp_path,
        monkeypatch,
    )

    with pytest.raises(
        RuntimeError,
        match="explicit approval",
    ):
        executor.execute_next_task(mission_id)
