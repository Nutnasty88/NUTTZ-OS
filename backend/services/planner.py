from typing import Any
from app.services.events import log_event
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
    log_event(
    mission_id,
    "Planner",
    "started",
    "Planner received mission and started planning",
    )

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
- For any task that creates or changes application behavior, explicitly
  describe it as an implementation task using a verb such as Implement,
  Modify, Update, or Create.
- Every implementation task must name the source file it creates or changes
  explicitly in the task action. If the source file is already known from
  earlier plan steps, repeat its filename rather than relying on context.
- Do not describe required code changes only as desired runtime behavior.
  For example, prefer "Implement task insertion in task_manager.py" over
  "When add is called, insert the task into the database."
- Keep execution or verification tasks separate from implementation tasks.
- Treat databases and other mutable runtime state as runtime artifacts, not
  Builder-created files.
- For SQLite-backed applications, plan implementation work that creates or
  initializes the database from source code at runtime. Do not create a task
  whose action is to synthesize a .db, .sqlite, or .sqlite3 file directly.
- When the mission requires data to persist across program or process restarts,
  use a file-backed SQLite database created by application source code at
  runtime. Do not plan an in-memory SQLite database for persistent state.
- Include a final success-check section.
- When success depends on a sequence of command-line executions, write every
  required command explicitly and in execution order in the success-check.
  Do not summarize a required command as prose such as "after adding a task".
- When a success-check verifies persistence across a restart or fresh process,
  explicitly include the command that creates the state, the command that
  verifies it before restart when required by the mission, and the command
  that verifies it again after restart.
- Preserve concrete sample arguments from the mission in those commands.
  For example, if the mission requires main.py add "Buy milk" followed by
  main.py list, include those literal commands in the success-check.
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
        timeout=300,
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

    log_event(
        mission_id,
        "Planner",
        "completed",
        "Mission plan created",
    )

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
