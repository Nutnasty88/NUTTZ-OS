from app.database.database import get_connection
from services.executor import ensure_task_table, reset_error_task


MISSION_ID = 9901


def cleanup():
    conn = get_connection()

    try:
        conn.execute(
            "DELETE FROM mission_worker_leases WHERE mission_id=?",
            (MISSION_ID,),
        )
        conn.execute(
            "DELETE FROM mission_events WHERE mission_id=?",
            (MISSION_ID,),
        )
        conn.execute(
            "DELETE FROM mission_tasks WHERE mission_id=?",
            (MISSION_ID,),
        )
        conn.execute(
            "DELETE FROM missions WHERE id=?",
            (MISSION_ID,),
        )
        conn.commit()
    finally:
        conn.close()


def test_reset_error_task_allows_running_mission():
    ensure_task_table()
    cleanup()

    conn = get_connection()

    try:
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
                MISSION_ID,
                "Error reset running mission regression",
                "Running",
                86,
                "Planner",
                "Normal",
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
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                MISSION_ID,
                1,
                "Historical Error Task",
                "Retry this task.",
                "Error",
                "historical failure",
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
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                MISSION_ID,
                2,
                "Later Completed Task",
                "Already completed.",
                "Completed",
                "ok",
            ),
        )

        conn.commit()

    finally:
        conn.close()

    try:
        result = reset_error_task(MISSION_ID)

        assert result["status"] == "Pending"
        assert result["mission_status"] == "Running"
        assert result["previous_result_preserved"] is True

        conn = get_connection()

        try:
            task = conn.execute(
                """
                SELECT status, result
                FROM mission_tasks
                WHERE mission_id=?
                  AND position=1
                """,
                (MISSION_ID,),
            ).fetchone()

            mission = conn.execute(
                """
                SELECT status
                FROM missions
                WHERE id=?
                """,
                (MISSION_ID,),
            ).fetchone()

            assert task["status"] == "Pending"
            assert task["result"] == "historical failure"
            assert mission["status"] == "Running"

        finally:
            conn.close()

    finally:
        cleanup()
