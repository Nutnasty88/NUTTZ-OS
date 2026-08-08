from typing import Any

from app.database.database import get_connection
from services.ollama_service import chat_with_ollama


PLANNER_MODEL = "qwen3:8b"


def ensure_plan_table() -> None:
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
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (mission_id) REFERENCES missions(id)
            )
            """
        )
        conn.commit()
    finally:
        conn.close()


def extract_plan(response: dict[str, Any]) -> str:
    if response.get("status") == "error":
        error_message = response.get("error", "Unknown Ollama error")
        raise RuntimeError(error_message)

    message = response.get("message")

    if not isinstance(message, dict):
        raise RuntimeError("Ollama returned no message.")

    plan = message.get("content", "").strip()

    if not plan:
        raise RuntimeError("Planner Agent returned an empty plan.")

    return plan


def create_plan(mission_id: int) -> dict[str, Any]:
    ensure_plan_table()

    conn = get_connection()

    try:
        mission = conn.execute(
            """
            SELECT
                id,
                title,
                status,
                assigned_agent,
                priority
            FROM missions
            WHERE id=?
            """,
            (mission_id,),
        ).fetchone()
    finally:
        conn.close()

    if mission is None:
        raise ValueError(f"Mission {mission_id} was not found.")

    system_prompt = """
You are Planner Agent v1 inside NUTTZ-OS.

Your job is to convert a mission into a clear, practical execution plan.

Rules:
- Return only the finished plan.
- Do not reveal internal reasoning.
- Do not include <think> tags.
- Use numbered tasks in execution order.
- Give each task a short title and clear action.
- Include a final success-check section.
- Keep the plan focused and practical.
""".strip()

    user_prompt = f"""
Create an execution plan for this NUTTZ-OS mission.

Mission ID: {mission["id"]}
Mission title: {mission["title"]}
Assigned agent: {mission["assigned_agent"]}
Priority: {mission["priority"]}
Current status: {mission["status"]}
""".strip()

    response = chat_with_ollama(
        model=PLANNER_MODEL,
        messages=[
            {
                "role": "system",
                "content": system_prompt,
            },
            {
                "role": "user",
                "content": user_prompt,
            },
        ],
        stream=False,
    )

    plan = extract_plan(response)

    conn = get_connection()

    try:
        conn.execute(
            """
            INSERT INTO mission_plans
                (mission_id, model, plan, status)
            VALUES
                (?, ?, ?, 'Ready')
            ON CONFLICT(mission_id)
            DO UPDATE SET
                model=excluded.model,
                plan=excluded.plan,
                status='Ready',
                updated_at=CURRENT_TIMESTAMP
            """,
            (
                mission_id,
                PLANNER_MODEL,
                plan,
            ),
        )

        conn.execute(
            """
            UPDATE missions
            SET
                progress=20,
                updated_at=CURRENT_TIMESTAMP
            WHERE id=?
            """,
            (mission_id,),
        )

        conn.commit()
    finally:
        conn.close()

    return {
        "mission_id": mission_id,
        "model": PLANNER_MODEL,
        "status": "Ready",
        "plan": plan,
    }


def get_plan(mission_id: int) -> dict[str, Any] | None:
    ensure_plan_table()

    conn = get_connection()

    try:
        row = conn.execute(
            """
            SELECT
                mission_id,
                model,
                plan,
                status,
                created_at,
                updated_at
            FROM mission_plans
            WHERE mission_id=?
            """,
            (mission_id,),
        ).fetchone()
    finally:
        conn.close()

    if row is None:
        return None

    return {
        "mission_id": row["mission_id"],
        "model": row["model"],
        "plan": row["plan"],
        "status": row["status"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }
