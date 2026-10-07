import sqlite3

import pytest

from services import executor


def _setup_database(tmp_path, monkeypatch):
    db_path = tmp_path / "pending-task-revision.db"
    mission_id = 16001

    def get_connection():
        connection = sqlite3.connect(
            db_path,
            timeout=5.0,
        )
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 5000")
        return connection

    conn = get_connection()

    try:
        conn.executescript(
            """
            CREATE TABLE missions (
                id INTEGER PRIMARY KEY,
                title TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'Running',
                progress INTEGER NOT NULL DEFAULT 0,
                assigned_agent TEXT DEFAULT '',
                priority TEXT DEFAULT 'Normal',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE mission_plans (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                mission_id INTEGER NOT NULL UNIQUE,
                model TEXT NOT NULL,
                plan TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'Ready',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (mission_id)
                    REFERENCES missions(id)
                    ON DELETE CASCADE
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
            VALUES (?, ?, 'Running', 82, 'Planner', 'Normal')
            """,
            (
                mission_id,
                "Legacy persistence mission",
            ),
        )

        conn.execute(
            """
            INSERT INTO mission_tasks (
                mission_id,
                position,
                title,
                instructions,
                status,
                result,
                completed_at
            )
            VALUES (?, 1, ?, ?, 'Completed', ?, CURRENT_TIMESTAMP)
            """,
            (
                mission_id,
                "Completed task",
                "Do completed work",
                "verified evidence",
            ),
        )

        conn.execute(
            """
            INSERT INTO mission_tasks (
                mission_id,
                position,
                title,
                instructions,
                status,
                result
            )
            VALUES (?, 2, ?, ?, 'Pending', ?)
            """,
            (
                mission_id,
                "Legacy verification",
                "Verify persistence",
                "preserved old evidence",
            ),
        )

        conn.execute(
            """
            INSERT INTO mission_plans (
                mission_id,
                model,
                plan,
                status
            )
            VALUES (?, ?, ?, 'Ready')
            """,
            (
                mission_id,
                "qwen3:8b",
                (
                    "1. Completed task\n"
                    "   Do completed work\n"
                    "2. Legacy verification\n"
                    "   Verify persistence"
                ),
            ),
        )

        conn.commit()

    finally:
        conn.close()

    monkeypatch.setattr(
        executor,
        "get_connection",
        get_connection,
    )

    executor.ensure_mission_approval_columns()

    return mission_id, get_connection


def test_revises_only_pending_task_and_preserves_completed_work(
    tmp_path,
    monkeypatch,
):
    mission_id, get_connection = _setup_database(
        tmp_path,
        monkeypatch,
    )

    approval = executor.approve_mission_plan(mission_id)
    assert approval["approved"] is True

    conn = get_connection()

    try:
        before = conn.execute(
            """
            SELECT *
            FROM mission_tasks
            WHERE mission_id=?
            ORDER BY position
            """,
            (mission_id,),
        ).fetchall()

        completed_id = before[0]["id"]
        pending_id = before[1]["id"]
    finally:
        conn.close()

    revised = executor.revise_pending_task(
        mission_id,
        pending_id,
        title="Verify persistence across fresh executions",
        instructions=(
            'Run `python main.py add "Buy milk"` in one process. '
            "Then run `python main.py list` in a separate process. "
            "Verify stdout is exactly `Buy milk`."
        ),
    )

    assert revised["id"] == pending_id
    assert revised["position"] == 2
    assert revised["status"] == "Pending"
    assert (
        revised["title"]
        == "Verify persistence across fresh executions"
    )

    conn = get_connection()

    try:
        after = conn.execute(
            """
            SELECT *
            FROM mission_tasks
            WHERE mission_id=?
            ORDER BY position
            """,
            (mission_id,),
        ).fetchall()
    finally:
        conn.close()

    assert len(after) == 2

    assert after[0]["id"] == completed_id
    assert after[0]["status"] == "Completed"
    assert after[0]["result"] == "verified evidence"

    assert after[1]["id"] == pending_id
    assert after[1]["status"] == "Pending"
    assert after[1]["result"] == "preserved old evidence"

    status = executor.get_mission_approval_status(mission_id)

    assert status["approved"] is False
    assert status["approved_plan_fingerprint"] is None
    assert status["approved_at"] is None


@pytest.mark.parametrize(
    (
        "status",
        "started_at",
        "completed_at",
        "execution_token",
    ),
    [
        ("Completed", None, "2026-01-01 00:00:00", None),
        ("Running", "2026-01-01 00:00:00", None, "token-a"),
        ("Pending", "2026-01-01 00:00:00", None, None),
        ("Pending", None, None, "token-b"),
    ],
)
def test_rejects_non_pristine_pending_task(
    tmp_path,
    monkeypatch,
    status,
    started_at,
    completed_at,
    execution_token,
):
    mission_id, get_connection = _setup_database(
        tmp_path,
        monkeypatch,
    )

    conn = get_connection()

    try:
        task = conn.execute(
            """
            SELECT id
            FROM mission_tasks
            WHERE mission_id=?
              AND position=2
            """,
            (mission_id,),
        ).fetchone()

        conn.execute(
            """
            UPDATE mission_tasks
            SET
                status=?,
                started_at=?,
                completed_at=?,
                execution_token=?
            WHERE id=?
            """,
            (
                status,
                started_at,
                completed_at,
                execution_token,
                task["id"],
            ),
        )

        conn.commit()
        task_id = task["id"]

    finally:
        conn.close()

    with pytest.raises(
        RuntimeError,
        match="unstarted Pending",
    ):
        executor.revise_pending_task(
            mission_id,
            task_id,
            title="Revised title",
            instructions="Revised instructions",
        )


def test_rejects_task_from_another_mission(
    tmp_path,
    monkeypatch,
):
    mission_id, get_connection = _setup_database(
        tmp_path,
        monkeypatch,
    )

    conn = get_connection()

    try:
        conn.execute(
            """
            INSERT INTO missions (
                id,
                title,
                status,
                progress
            )
            VALUES (?, ?, 'Running', 20)
            """,
            (
                mission_id + 1,
                "Other mission",
            ),
        )

        cursor = conn.execute(
            """
            INSERT INTO mission_tasks (
                mission_id,
                position,
                title,
                instructions,
                status
            )
            VALUES (?, 1, ?, ?, 'Pending')
            """,
            (
                mission_id + 1,
                "Other task",
                "Other instructions",
            ),
        )

        conn.commit()
        other_task_id = cursor.lastrowid

    finally:
        conn.close()

    with pytest.raises(
        ValueError,
        match="was not found in mission",
    ):
        executor.revise_pending_task(
            mission_id,
            other_task_id,
            title="Do not revise",
            instructions="Do not revise",
        )


def test_pending_revision_updates_stored_plan_atomically(
    tmp_path,
    monkeypatch,
):
    mission_id, get_connection = _setup_database(
        tmp_path,
        monkeypatch,
    )

    conn = get_connection()

    try:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS mission_plans (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                mission_id INTEGER NOT NULL UNIQUE,
                model TEXT NOT NULL,
                plan TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'Ready',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """
        )

        conn.execute(
            """
            UPDATE mission_plans
            SET plan=?
            WHERE mission_id=?
            """,
            (
                (
                    "1. Completed setup\n"
                    "2. Verify persistence\n"
                    "3. Final verification"
                ),
                mission_id,
            ),
        )

        rows = conn.execute(
            """
            SELECT id, position
            FROM mission_tasks
            WHERE mission_id=?
            ORDER BY position
            """,
            (mission_id,),
        ).fetchall()

        pending_id = rows[1]["id"]

        conn.commit()

    finally:
        conn.close()

    revised = executor.revise_pending_task(
        mission_id,
        pending_id,
        title=(
            "Verify persistence across separate program executions"
        ),
        instructions=(
            'Run `python main.py add "Buy milk"` in one program '
            "execution. Then run `python main.py list` in a "
            "separate program execution. Verify stdout is "
            "exactly `Buy milk`."
        ),
    )

    assert revised["id"] == pending_id
    assert revised["status"] == "Pending"

    conn = get_connection()

    try:
        plan = conn.execute(
            """
            SELECT plan
            FROM mission_plans
            WHERE mission_id=?
            """,
            (mission_id,),
        ).fetchone()["plan"]

        tasks = conn.execute(
            """
            SELECT
                id,
                position,
                title,
                instructions,
                status
            FROM mission_tasks
            WHERE mission_id=?
            ORDER BY position
            """,
            (mission_id,),
        ).fetchall()

    finally:
        conn.close()

    assert "1. Completed setup" in plan

    assert (
        "2. Verify persistence across separate program executions"
        in plan
    )

    assert (
        'Run `python main.py add "Buy milk"` in one program '
        "execution."
        in plan
    )

    assert "3. Final verification" in plan

    assert tasks[0]["status"] == "Completed"
    assert tasks[0]["title"] == "Completed task"

    assert tasks[1]["id"] == pending_id
    assert (
        tasks[1]["title"]
        == "Verify persistence across separate program executions"
    )


def test_pending_revision_rolls_back_if_plan_step_is_missing(
    tmp_path,
    monkeypatch,
):
    mission_id, get_connection = _setup_database(
        tmp_path,
        monkeypatch,
    )

    conn = get_connection()

    try:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS mission_plans (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                mission_id INTEGER NOT NULL UNIQUE,
                model TEXT NOT NULL,
                plan TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'Ready',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """
        )

        conn.execute(
            """
            UPDATE mission_plans
            SET plan=?
            WHERE mission_id=?
            """,
            (
                "1. Completed setup",
                mission_id,
            ),
        )

        pending = conn.execute(
            """
            SELECT *
            FROM mission_tasks
            WHERE
                mission_id=?
                AND status='Pending'
            ORDER BY position
            LIMIT 1
            """,
            (mission_id,),
        ).fetchone()

        original_title = pending["title"]
        original_instructions = pending["instructions"]
        pending_id = pending["id"]

        conn.commit()

    finally:
        conn.close()

    with pytest.raises(
        RuntimeError,
        match="exactly one numbered step",
    ):
        executor.revise_pending_task(
            mission_id,
            pending_id,
            title="Must not persist",
            instructions="Must not persist",
        )

    conn = get_connection()

    try:
        after = conn.execute(
            """
            SELECT title, instructions
            FROM mission_tasks
            WHERE id=?
            """,
            (pending_id,),
        ).fetchone()

        plan = conn.execute(
            """
            SELECT plan
            FROM mission_plans
            WHERE mission_id=?
            """,
            (mission_id,),
        ).fetchone()["plan"]

    finally:
        conn.close()

    assert after["title"] == original_title
    assert after["instructions"] == original_instructions
    assert plan == "1. Completed setup"


def test_pending_revision_preserves_other_plan_steps(
    tmp_path,
    monkeypatch,
):
    mission_id, get_connection = _setup_database(
        tmp_path,
        monkeypatch,
    )

    conn = get_connection()

    try:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS mission_plans (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                mission_id INTEGER NOT NULL UNIQUE,
                model TEXT NOT NULL,
                plan TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'Ready',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """
        )

        original_plan = (
            "1. Completed setup  \n"
            "2. Old pending verification  \n"
            "3. Final verification"
        )

        conn.execute(
            """
            UPDATE mission_plans
            SET plan=?
            WHERE mission_id=?
            """,
            (
                original_plan,
                mission_id,
            ),
        )

        pending_id = conn.execute(
            """
            SELECT id
            FROM mission_tasks
            WHERE
                mission_id=?
                AND status='Pending'
            ORDER BY position
            LIMIT 1
            """,
            (mission_id,),
        ).fetchone()["id"]

        conn.commit()

    finally:
        conn.close()

    executor.revise_pending_task(
        mission_id,
        pending_id,
        title="New pending verification",
        instructions="Run deterministic verification.",
    )

    conn = get_connection()

    try:
        revised_plan = conn.execute(
            """
            SELECT plan
            FROM mission_plans
            WHERE mission_id=?
            """,
            (mission_id,),
        ).fetchone()["plan"]

    finally:
        conn.close()

    original_lines = original_plan.splitlines()
    revised_lines = revised_plan.splitlines()

    assert revised_lines[0] == original_lines[0]
    assert revised_lines[-1] == original_lines[-1]
    assert "2. New pending verification" in revised_plan
    assert "Run deterministic verification." in revised_plan


def test_pending_revision_updates_stored_plan_atomically(
    tmp_path,
    monkeypatch,
):
    mission_id, get_connection = _setup_database(
        tmp_path,
        monkeypatch,
    )

    conn = get_connection()

    try:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS mission_plans (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                mission_id INTEGER NOT NULL UNIQUE,
                model TEXT NOT NULL,
                plan TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'Ready',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """
        )

        conn.execute(
            """
            UPDATE mission_plans
            SET plan=?
            WHERE mission_id=?
            """,
            (
                (
                    "1. Completed setup\n"
                    "2. Verify persistence\n"
                    "3. Final verification"
                ),
                mission_id,
            ),
        )

        rows = conn.execute(
            """
            SELECT id, position
            FROM mission_tasks
            WHERE mission_id=?
            ORDER BY position
            """,
            (mission_id,),
        ).fetchall()

        pending_id = rows[1]["id"]

        conn.commit()

    finally:
        conn.close()

    revised = executor.revise_pending_task(
        mission_id,
        pending_id,
        title=(
            "Verify persistence across separate program executions"
        ),
        instructions=(
            'Run `python main.py add "Buy milk"` in one program '
            "execution. Then run `python main.py list` in a "
            "separate program execution. Verify stdout is "
            "exactly `Buy milk`."
        ),
    )

    assert revised["id"] == pending_id
    assert revised["status"] == "Pending"

    conn = get_connection()

    try:
        plan = conn.execute(
            """
            SELECT plan
            FROM mission_plans
            WHERE mission_id=?
            """,
            (mission_id,),
        ).fetchone()["plan"]

        tasks = conn.execute(
            """
            SELECT
                id,
                position,
                title,
                instructions,
                status
            FROM mission_tasks
            WHERE mission_id=?
            ORDER BY position
            """,
            (mission_id,),
        ).fetchall()

    finally:
        conn.close()

    assert "1. Completed setup" in plan

    assert (
        "2. Verify persistence across separate program executions"
        in plan
    )

    assert (
        'Run `python main.py add "Buy milk"` in one program '
        "execution."
        in plan
    )

    assert "3. Final verification" in plan

    assert tasks[0]["status"] == "Completed"
    assert tasks[0]["title"] == "Completed task"

    assert tasks[1]["id"] == pending_id
    assert (
        tasks[1]["title"]
        == "Verify persistence across separate program executions"
    )


def test_pending_revision_rolls_back_if_plan_step_is_missing(
    tmp_path,
    monkeypatch,
):
    mission_id, get_connection = _setup_database(
        tmp_path,
        monkeypatch,
    )

    conn = get_connection()

    try:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS mission_plans (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                mission_id INTEGER NOT NULL UNIQUE,
                model TEXT NOT NULL,
                plan TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'Ready',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """
        )

        conn.execute(
            """
            UPDATE mission_plans
            SET plan=?
            WHERE mission_id=?
            """,
            (
                "1. Completed setup",
                mission_id,
            ),
        )

        pending = conn.execute(
            """
            SELECT *
            FROM mission_tasks
            WHERE
                mission_id=?
                AND status='Pending'
            ORDER BY position
            LIMIT 1
            """,
            (mission_id,),
        ).fetchone()

        original_title = pending["title"]
        original_instructions = pending["instructions"]
        pending_id = pending["id"]

        conn.commit()

    finally:
        conn.close()

    with pytest.raises(
        RuntimeError,
        match="exactly one numbered step",
    ):
        executor.revise_pending_task(
            mission_id,
            pending_id,
            title="Must not persist",
            instructions="Must not persist",
        )

    conn = get_connection()

    try:
        after = conn.execute(
            """
            SELECT title, instructions
            FROM mission_tasks
            WHERE id=?
            """,
            (pending_id,),
        ).fetchone()

        plan = conn.execute(
            """
            SELECT plan
            FROM mission_plans
            WHERE mission_id=?
            """,
            (mission_id,),
        ).fetchone()["plan"]

    finally:
        conn.close()

    assert after["title"] == original_title
    assert after["instructions"] == original_instructions
    assert plan == "1. Completed setup"


def test_pending_revision_preserves_other_plan_steps(
    tmp_path,
    monkeypatch,
):
    mission_id, get_connection = _setup_database(
        tmp_path,
        monkeypatch,
    )

    conn = get_connection()

    try:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS mission_plans (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                mission_id INTEGER NOT NULL UNIQUE,
                model TEXT NOT NULL,
                plan TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'Ready',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """
        )

        original_plan = (
            "1. Completed setup  \n"
            "2. Old pending verification  \n"
            "3. Final verification"
        )

        conn.execute(
            """
            UPDATE mission_plans
            SET plan=?
            WHERE mission_id=?
            """,
            (
                original_plan,
                mission_id,
            ),
        )

        pending_id = conn.execute(
            """
            SELECT id
            FROM mission_tasks
            WHERE
                mission_id=?
                AND status='Pending'
            ORDER BY position
            LIMIT 1
            """,
            (mission_id,),
        ).fetchone()["id"]

        conn.commit()

    finally:
        conn.close()

    executor.revise_pending_task(
        mission_id,
        pending_id,
        title="New pending verification",
        instructions="Run deterministic verification.",
    )

    conn = get_connection()

    try:
        revised_plan = conn.execute(
            """
            SELECT plan
            FROM mission_plans
            WHERE mission_id=?
            """,
            (mission_id,),
        ).fetchone()["plan"]

    finally:
        conn.close()

    original_lines = original_plan.splitlines()
    revised_lines = revised_plan.splitlines()

    assert revised_lines[0] == original_lines[0]
    assert revised_lines[-1] == original_lines[-1]
    assert "2. New pending verification" in revised_plan
    assert "Run deterministic verification." in revised_plan
