import sqlite3

from app.services import events
from app.services import reporter
from services import autonomous_worker
from services import executor


def test_golden_worker_completes_tasks_and_deliverable(
    tmp_path,
    monkeypatch,
):
    db_path = tmp_path / "golden-mission.db"
    mission_id = 13001

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
                started_at TIMESTAMP,
                completed_at TIMESTAMP,
                execution_token TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (mission_id)
                    REFERENCES missions(id)
                    ON DELETE CASCADE,
                UNIQUE (mission_id, position)
            );

            CREATE TABLE mission_deliverables (
                mission_id INTEGER PRIMARY KEY,
                model TEXT NOT NULL,
                status TEXT NOT NULL,
                content TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (mission_id)
                    REFERENCES missions(id)
                    ON DELETE CASCADE
            );

            CREATE TABLE mission_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                mission_id INTEGER,
                agent TEXT NOT NULL,
                event_type TEXT NOT NULL,
                message TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (mission_id)
                    REFERENCES missions(id)
                    ON DELETE CASCADE
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
                "Golden persistent task mission",
                "Running",
                0,
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
                    "Verify persistent behavior",
                ),
            ],
        )

        conn.commit()
    finally:
        conn.close()

    for module in (
        autonomous_worker,
        executor,
        reporter,
        events,
    ):
        monkeypatch.setattr(
            module,
            "get_connection",
            get_connection,
        )

    def fake_execute_next_task(
        mission_id,
        worker_owner_token=None,
    ):
        conn = get_connection()

        try:
            lease = conn.execute(
                """
                SELECT owner_token
                FROM mission_worker_leases
                WHERE mission_id=?
                """,
                (mission_id,),
            ).fetchone()

            assert lease is not None
            assert (
                lease["owner_token"]
                == worker_owner_token
            )

            task = conn.execute(
                """
                SELECT id, position, title
                FROM mission_tasks
                WHERE mission_id=?
                  AND status='Pending'
                ORDER BY position
                LIMIT 1
                """,
                (mission_id,),
            ).fetchone()

            assert task is not None

            conn.execute(
                """
                UPDATE mission_tasks
                SET
                    status='Completed',
                    result='VERIFIED',
                    completed_at=CURRENT_TIMESTAMP
                WHERE id=?
                """,
                (task["id"],),
            )

            counts = conn.execute(
                """
                SELECT
                    COUNT(*) AS total,
                    SUM(
                        CASE
                            WHEN status='Completed'
                            THEN 1
                            ELSE 0
                        END
                    ) AS completed
                FROM mission_tasks
                WHERE mission_id=?
                """,
                (mission_id,),
            ).fetchone()

            total = counts["total"]
            completed = counts["completed"]

            progress = int(
                completed * 100 / total
            )

            conn.execute(
                """
                UPDATE missions
                SET
                    progress=?,
                    updated_at=CURRENT_TIMESTAMP
                WHERE id=?
                """,
                (
                    min(progress, 99),
                    mission_id,
                ),
            )

            conn.commit()

            return {
                "mission_id": mission_id,
                "task_id": task["id"],
                "position": task["position"],
                "title": task["title"],
                "status": "Completed",
                "progress": min(progress, 99),
            }
        finally:
            conn.close()

    def fake_create_deliverable(
        mission_id,
        worker_owner_token=None,
    ):
        conn = get_connection()

        try:
            lease = conn.execute(
                """
                SELECT owner_token
                FROM mission_worker_leases
                WHERE mission_id=?
                """,
                (mission_id,),
            ).fetchone()

            assert lease is not None
            assert (
                lease["owner_token"]
                == worker_owner_token
            )

            conn.execute(
                """
                INSERT OR REPLACE INTO mission_deliverables (
                    mission_id,
                    model,
                    status,
                    content,
                    updated_at
                )
                VALUES (?, ?, 'Ready', ?, CURRENT_TIMESTAMP)
                """,
                (
                    mission_id,
                    "golden-test",
                    "Golden mission deliverable",
                ),
            )

            conn.commit()
        finally:
            conn.close()

        return {
            "mission_id": mission_id,
            "model": "golden-test",
            "status": "Ready",
            "content": "Golden mission deliverable",
        }

    monkeypatch.setattr(
        autonomous_worker,
        "execute_next_task",
        fake_execute_next_task,
    )
    monkeypatch.setattr(
        autonomous_worker,
        "create_deliverable",
        fake_create_deliverable,
    )

    autonomous_worker._stop_event.clear()

    lease = autonomous_worker.acquire_worker_lease(
        mission_id
    )

    autonomous_worker._run_worker(
        mission_id,
        0,
        lease["owner_token"],
    )

    conn = get_connection()

    try:
        mission = conn.execute(
            """
            SELECT status, progress
            FROM missions
            WHERE id=?
            """,
            (mission_id,),
        ).fetchone()

        tasks = conn.execute(
            """
            SELECT position, status, result
            FROM mission_tasks
            WHERE mission_id=?
            ORDER BY position
            """,
            (mission_id,),
        ).fetchall()

        deliverable = conn.execute(
            """
            SELECT model, status, content
            FROM mission_deliverables
            WHERE mission_id=?
            """,
            (mission_id,),
        ).fetchone()

        lease_row = conn.execute(
            """
            SELECT mission_id
            FROM mission_worker_leases
            WHERE mission_id=?
            """,
            (mission_id,),
        ).fetchone()
    finally:
        conn.close()

    state = autonomous_worker.get_worker_status()

    print("\n===== GOLDEN WORKER STATE =====")
    print(state)
    print("mission:", dict(mission))
    print(
        "tasks:",
        [dict(row) for row in tasks],
    )
    print(
        "deliverable:",
        dict(deliverable)
        if deliverable is not None
        else None,
    )
    print(
        "lease:",
        dict(lease_row)
        if lease_row is not None
        else None,
    )

    assert state["last_error"] == ""
    state = autonomous_worker.get_worker_status()

    print("\n===== GOLDEN WORKER STATE =====")
    print(state)
    print("mission:", dict(mission))
    print(
        "tasks:",
        [dict(row) for row in tasks],
    )
    print(
        "deliverable:",
        dict(deliverable)
        if deliverable is not None
        else None,
    )
    print(
        "lease:",
        dict(lease_row)
        if lease_row is not None
        else None,
    )

    assert state["last_error"] == ""
    assert mission["status"] == "Completed"
    assert mission["progress"] == 100

    assert [
        (
            row["position"],
            row["status"],
            row["result"],
        )
        for row in tasks
    ] == [
        (1, "Completed", "VERIFIED"),
        (2, "Completed", "VERIFIED"),
    ]

    assert deliverable["model"] == "golden-test"
    assert deliverable["status"] == "Ready"
    assert (
        deliverable["content"]
        == "Golden mission deliverable"
    )

    assert lease_row is None

    state = autonomous_worker.get_worker_status()

    assert state["status"] == "Completed"
    assert state["completed_tasks"] == 2
    assert state["total_tasks"] == 2
    assert state["last_error"] == ""
