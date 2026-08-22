import json
from typing import Any

from app.database.database import get_connection
from app.services.events import log_event
from services.ollama_service import chat_with_ollama


REPORTER_MODEL = "qwen3:8b"


def ensure_deliverable_table() -> None:
    conn = get_connection()

    try:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS mission_deliverables (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                mission_id INTEGER NOT NULL UNIQUE,
                model TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'Ready',
                content TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (mission_id) REFERENCES missions(id)
            )
            """
        )
        conn.commit()
    finally:
        conn.close()


def _get_mission(mission_id: int):
    conn = get_connection()

    try:
        return conn.execute(
            """
            SELECT
                id,
                title,
                status,
                progress,
                assigned_agent,
                priority
            FROM missions
            WHERE id=?
            """,
            (mission_id,),
        ).fetchone()
    finally:
        conn.close()


def _get_plan(mission_id: int) -> str:
    conn = get_connection()

    try:
        row = conn.execute(
            """
            SELECT plan
            FROM mission_plans
            WHERE mission_id=?
            """,
            (mission_id,),
        ).fetchone()
    finally:
        conn.close()

    return row["plan"] if row else ""


def _get_research(mission_id: int) -> dict[str, Any]:
    conn = get_connection()

    try:
        row = conn.execute(
            """
            SELECT report_json
            FROM mission_research
            WHERE mission_id=?
            """,
            (mission_id,),
        ).fetchone()
    finally:
        conn.close()

    if row is None:
        return {}

    try:
        return json.loads(row["report_json"])
    except json.JSONDecodeError:
        return {"summary": row["report_json"]}


def _get_tasks(mission_id: int) -> list[dict[str, Any]]:
    conn = get_connection()

    try:
        rows = conn.execute(
            """
            SELECT
                position,
                title,
                instructions,
                status,
                result
            FROM mission_tasks
            WHERE mission_id=?
            ORDER BY position ASC
            """,
            (mission_id,),
        ).fetchall()
    finally:
        conn.close()

    return [
        {
            "position": row["position"],
            "title": row["title"],
            "instructions": row["instructions"],
            "status": row["status"],
            "result": row["result"] or "",
        }
        for row in rows
    ]


def _extract_content(response: dict[str, Any]) -> str:
    if response.get("status") == "error":
        raise RuntimeError(
            response.get("error", "Unknown Ollama error")
        )

    message = response.get("message")

    if not isinstance(message, dict):
        raise RuntimeError(
            "Reporter Agent received no Ollama message."
        )

    content = message.get("content", "").strip()

    if not content:
        raise RuntimeError(
            "Reporter Agent returned an empty deliverable."
        )

    return content


def _compact_text(
    value: Any,
    limit: int,
) -> str:
    if value is None:
        return ""

    if isinstance(value, str):
        text = value
    else:
        text = json.dumps(
            value,
            ensure_ascii=False,
            default=str,
        )

    text = text.strip()

    if len(text) <= limit:
        return text

    return text[:limit].rstrip() + "\n...[truncated]"


def _compact_tasks(
    tasks: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    compact: list[dict[str, Any]] = []

    for task in tasks:
        compact.append(
            {
                "position": task.get("position"),
                "title": task.get("title"),
                "status": task.get("status"),
                "instructions": _compact_text(
                    task.get("instructions", ""),
                    300,
                ),
                "result": _compact_text(
                    task.get("result", ""),
                    600,
                ),
            }
        )

    return compact


def create_deliverable(mission_id: int) -> dict[str, Any]:
    ensure_deliverable_table()

    mission = _get_mission(mission_id)

    if mission is None:
        raise ValueError(
            f"Mission {mission_id} was not found."
        )

    plan = _get_plan(mission_id)
    research = _get_research(mission_id)
    tasks = _get_tasks(mission_id)

    if not tasks:
        raise ValueError(
            f"Mission {mission_id} has no execution tasks."
        )

    incomplete = [
        task
        for task in tasks
        if task["status"] != "Completed"
    ]

    if incomplete:
        raise ValueError(
            "Final deliverable cannot be generated until "
            "all mission tasks are completed."
        )

    log_event(
        mission_id,
        "Reporter",
        "started",
        "Reporter started final deliverable",
    )

    system_prompt = """
You are Reporter Agent v1 inside NUTTZ-OS.

Your job is to synthesize the completed mission into a polished
final deliverable.

Rules:
- Return only the finished deliverable.
- Do not reveal internal reasoning.
- Do not include <think> tags.
- Use the supplied mission evidence.
- Do not invent actions, tests, files, commands, sources, or results.
- Clearly distinguish verified results from recommendations.
- Organize the result with useful Markdown headings.
- Include a brief executive summary.
- Include only the most important findings or completed work.
- Include verification/results when evidence exists.
- Include limitations or unresolved items when appropriate.
- Keep the entire deliverable under 350 words.
- Prefer concise synthesis over repeating every task.
- End with a concise mission outcome.
""".strip()

    evidence = {
        "mission": {
            "id": mission["id"],
            "title": mission["title"],
            "status": mission["status"],
            "progress": mission["progress"],
            "assigned_agent": mission["assigned_agent"],
            "priority": mission["priority"],
        },
        "plan": _compact_text(plan, 1200),
        "research": _compact_text(research, 1200),
        "tasks": _compact_tasks(tasks),
    }

    user_prompt = (
        "Create the final deliverable for this completed "
        "NUTTZ-OS mission.\n\n"
        "MISSION EVIDENCE:\n"
        + json.dumps(
            evidence,
            indent=2,
            ensure_ascii=False,
        )
    )

    try:
        response = chat_with_ollama(
            model=REPORTER_MODEL,
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
            think=False,
            options={
                "num_predict": 500,
            },
        )

        content = _extract_content(response)

        conn = get_connection()

        try:
            conn.execute(
                """
                INSERT INTO mission_deliverables
                    (
                        mission_id,
                        model,
                        status,
                        content
                    )
                VALUES
                    (?, ?, 'Ready', ?)
                ON CONFLICT(mission_id)
                DO UPDATE SET
                    model=excluded.model,
                    status='Ready',
                    content=excluded.content,
                    updated_at=CURRENT_TIMESTAMP
                """,
                (
                    mission_id,
                    REPORTER_MODEL,
                    content,
                ),
            )
            conn.commit()
        finally:
            conn.close()

        log_event(
            mission_id,
            "Reporter",
            "completed",
            "Final mission deliverable created",
        )

        return get_deliverable(mission_id)

    except Exception:
        log_event(
            mission_id,
            "Reporter",
            "error",
            "Final deliverable generation failed",
        )
        raise


def get_deliverable(
    mission_id: int,
) -> dict[str, Any] | None:
    ensure_deliverable_table()

    conn = get_connection()

    try:
        row = conn.execute(
            """
            SELECT
                mission_id,
                model,
                status,
                content,
                created_at,
                updated_at
            FROM mission_deliverables
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
        "status": row["status"],
        "content": row["content"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }
