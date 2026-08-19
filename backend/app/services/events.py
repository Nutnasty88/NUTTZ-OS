from app.database.database import get_connection


def log_event(mission_id, agent, event_type, message):
    conn = get_connection()

    try:
        cursor = conn.execute(
            """
            INSERT INTO mission_events (
                mission_id,
                agent,
                event_type,
                message
            )
            VALUES (?, ?, ?, ?)
            """,
            (
                mission_id,
                agent,
                event_type,
                message,
            ),
        )

        conn.commit()
        return cursor.lastrowid

    finally:
        conn.close()


def get_events(mission_id=None, limit=100):
    conn = get_connection()

    try:
        if mission_id is None:
            rows = conn.execute(
                """
                SELECT
                    id,
                    mission_id,
                    agent,
                    event_type,
                    message,
                    created_at
                FROM mission_events
                ORDER BY id DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        else:
            rows = conn.execute(
                """
                SELECT
                    id,
                    mission_id,
                    agent,
                    event_type,
                    message,
                    created_at
                FROM mission_events
                WHERE mission_id = ?
                ORDER BY id ASC
                LIMIT ?
                """,
                (mission_id, limit),
            ).fetchall()

        return [dict(row) for row in rows]

    finally:
        conn.close()
