import json
import re
from typing import Any

from app.database.database import get_connection
from services.ollama_service import chat_with_ollama
from services.tool_runner import run_tool


EXECUTOR_MODEL = "qwen3:8b"


def ensure_task_table() -> None:
    conn = get_connection()

    try:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS mission_tasks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                mission_id INTEGER NOT NULL,
                position INTEGER NOT NULL,
                title TEXT NOT NULL,
                instructions TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'Pending',
                result TEXT DEFAULT '',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                started_at TIMESTAMP,
                completed_at TIMESTAMP,
                FOREIGN KEY (mission_id) REFERENCES missions(id),
                UNIQUE (mission_id, position)
            )
            """
        )
        conn.commit()
    finally:
        conn.close()


def parse_plan_tasks(plan: str) -> list[dict[str, Any]]:
    pattern = re.compile(
        r"^\s*(\d+)\.\s*(.+?)(?=^\s*\d+\.\s*|\Z)",
        re.MULTILINE | re.DOTALL,
    )

    tasks = []

    for match in pattern.finditer(plan):
        position = int(match.group(1))
        block = match.group(2).strip()

        bold_title = re.match(
            r"\*\*(.+?)\*\*\s*(.*)",
            block,
            re.DOTALL,
        )

        if bold_title:
            title = bold_title.group(1).strip()
            instructions = bold_title.group(2).strip()
        else:
            lines = block.splitlines()
            title = lines[0].strip()
            instructions = "\n".join(lines[1:]).strip()

        title = re.sub(r"[*_`#]", "", title).strip()

        if not instructions:
            instructions = title

        tasks.append(
            {
                "position": position,
                "title": title,
                "instructions": instructions,
            }
        )

    if not tasks and plan.strip():
        tasks.append(
            {
                "position": 1,
                "title": "Execute mission plan",
                "instructions": plan.strip(),
            }
        )

    return tasks


def sync_tasks(mission_id: int, plan: str) -> list[dict[str, Any]]:
    ensure_task_table()

    tasks = parse_plan_tasks(plan)

    if not tasks:
        raise ValueError("The mission plan contained no executable tasks.")

    conn = get_connection()

    try:
        mission = conn.execute(
            """
            SELECT id
            FROM missions
            WHERE id=?
            """,
            (mission_id,),
        ).fetchone()

        if mission is None:
            raise ValueError(f"Mission {mission_id} was not found.")

        conn.execute(
            """
            DELETE FROM mission_tasks
            WHERE mission_id=?
            """,
            (mission_id,),
        )

        for task in tasks:
            conn.execute(
                """
                INSERT INTO mission_tasks
                    (
                        mission_id,
                        position,
                        title,
                        instructions,
                        status
                    )
                VALUES
                    (?, ?, ?, ?, 'Pending')
                """,
                (
                    mission_id,
                    task["position"],
                    task["title"],
                    task["instructions"],
                ),
            )

        conn.execute(
            """
            UPDATE missions
            SET
                status='Running',
                progress=20,
                updated_at=CURRENT_TIMESTAMP
            WHERE id=?
            """,
            (mission_id,),
        )

        conn.commit()
    finally:
        conn.close()

    return get_tasks(mission_id)


def get_tasks(mission_id: int) -> list[dict[str, Any]]:
    ensure_task_table()

    conn = get_connection()

    try:
        rows = conn.execute(
            """
            SELECT
                id,
                mission_id,
                position,
                title,
                instructions,
                status,
                result,
                created_at,
                started_at,
                completed_at
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
            "id": row["id"],
            "mission_id": row["mission_id"],
            "position": row["position"],
            "title": row["title"],
            "instructions": row["instructions"],
            "status": row["status"],
            "result": row["result"],
            "created_at": row["created_at"],
            "started_at": row["started_at"],
            "completed_at": row["completed_at"],
        }
        for row in rows
    ]


def extract_result(response: dict[str, Any]) -> str:
    if response.get("status") == "error":
        error_message = response.get(
            "error",
            "Unknown Ollama error",
        )
        raise RuntimeError(error_message)

    message = response.get("message")

    if not isinstance(message, dict):
        raise RuntimeError("Executor Agent received no Ollama message.")

    result = message.get("content", "").strip()

    if not result:
        raise RuntimeError("Executor Agent returned an empty result.")

    return result



SAFE_TOOL_RULES = (
    (
        "system.kernel",
        ("kernel", "operating system", "os version", "system status", "system health"),
    ),
    (
        "system.uptime",
        ("uptime", "load average", "system status", "system health"),
    ),
    (
        "system.disk",
        ("disk", "storage", "filesystem", "system status", "system health"),
    ),
    (
        "system.memory",
        ("memory", "ram", "system status", "system health"),
    ),
    (
        "python.version",
        ("python",),
    ),
    (
        "docker.version",
        ("docker", "container"),
    ),
    (
        "virtualbox.version",
        ("virtualbox", "vbox", "virtual machine"),
    ),
    (
        "pytest.version",
        ("pytest",),
    ),
)


def _task_safe_tool_names(task: Any) -> list[str]:
    """Select only predefined allowlisted tools using deterministic keywords."""
    task_text = f"{task['title']}\n{task['instructions']}".lower()
    selected = []

    for tool_name, keywords in SAFE_TOOL_RULES:
        if any(keyword in task_text for keyword in keywords):
            selected.append(tool_name)

    return selected


def _collect_safe_tool_results(task: Any) -> list[dict[str, Any]]:
    """Run only tools selected from the fixed Safe Tools registry."""
    return [
        run_tool(tool_name)
        for tool_name in _task_safe_tool_names(task)
    ]


def execute_next_task(mission_id: int) -> dict[str, Any]:
    ensure_task_table()

    conn = get_connection()

    try:
        mission = conn.execute(
            """
            SELECT
                id,
                title,
                priority
            FROM missions
            WHERE id=?
            """,
            (mission_id,),
        ).fetchone()

        if mission is None:
            raise ValueError(f"Mission {mission_id} was not found.")

        task = conn.execute(
            """
            SELECT
                id,
                position,
                title,
                instructions
            FROM mission_tasks
            WHERE
                mission_id=?
                AND status='Pending'
            ORDER BY position ASC
            LIMIT 1
            """,
            (mission_id,),
        ).fetchone()

        if task is None:
            counts = conn.execute(
                """
                SELECT
                    COUNT(*) AS total,
                    SUM(
                        CASE
                            WHEN status='Completed' THEN 1
                            ELSE 0
                        END
                    ) AS completed
                FROM mission_tasks
                WHERE mission_id=?
                """,
                (mission_id,),
            ).fetchone()

            total = counts["total"] or 0
            completed = counts["completed"] or 0

            if total > 0 and completed == total:
                conn.execute(
                    """
                    UPDATE missions
                    SET
                        status='Completed',
                        progress=100,
                        updated_at=CURRENT_TIMESTAMP
                    WHERE id=?
                    """,
                    (mission_id,),
                )
                conn.commit()

            return {
                "mission_id": mission_id,
                "status": "Completed",
                "message": "No pending tasks remain.",
                "progress": 100 if total > 0 else 20,
            }

        conn.execute(
            """
            UPDATE mission_tasks
            SET
                status='Running',
                started_at=CURRENT_TIMESTAMP
            WHERE id=?
            """,
            (task["id"],),
        )
        conn.commit()
    finally:
        conn.close()

    system_prompt = """
You are Executor Agent v1 inside NUTTZ-OS.

Complete the assigned task by producing a concrete execution report.

Rules:
- Return only the finished task result.
- Do not reveal internal reasoning.
- Do not include <think> tags.
- Be practical, specific, and concise.
- State what was completed.
- Include verification steps or success checks.
- Never claim that you accessed files, ran commands, or used the
  network unless that access is explicitly provided in the task.
- When direct system access is required, provide exact proposed
  actions and verification commands.
""".strip()

    user_prompt = f"""
Mission ID: {mission["id"]}
Mission title: {mission["title"]}
Mission priority: {mission["priority"]}

Task number: {task["position"]}
Task title: {task["title"]}

Task instructions:
{task["instructions"]}
""".strip()

    safe_tool_results = _collect_safe_tool_results(task)

    if safe_tool_results:
        user_prompt += (
            "\n\nVerified Safe Tools evidence (JSON):\n"
            + json.dumps(safe_tool_results, indent=2, sort_keys=True)
            + "\n\nThese diagnostics were executed by NUTTZ-OS, not by "
            "the language model. Report only observations supported by this "
            "evidence. Include failures, unavailable tools, and denied access "
            "exactly as reported."
        )
    else:
        user_prompt += (
            "\n\nNo allowlisted Safe Tools diagnostic matched this task. "
            "Do not claim that any command, file, network, or system check "
            "was executed."
        )

    try:
        response = chat_with_ollama(
            model=EXECUTOR_MODEL,
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

        result = extract_result(response)

        if safe_tool_results:
            result += (
                "\n\nSAFE TOOLS EVIDENCE (verified):\n"
                + json.dumps(safe_tool_results, indent=2, sort_keys=True)
            )
    except Exception as error:
        conn = get_connection()

        try:
            conn.execute(
                """
                UPDATE mission_tasks
                SET
                    status='Error',
                    result=?
                WHERE id=?
                """,
                (str(error), task["id"]),
            )

            conn.execute(
                """
                UPDATE missions
                SET
                    status='Error',
                    updated_at=CURRENT_TIMESTAMP
                WHERE id=?
                """,
                (mission_id,),
            )

            conn.commit()
        finally:
            conn.close()

        raise

    conn = get_connection()

    try:
        conn.execute(
            """
            UPDATE mission_tasks
            SET
                status='Completed',
                result=?,
                completed_at=CURRENT_TIMESTAMP
            WHERE id=?
            """,
            (result, task["id"]),
        )

        counts = conn.execute(
            """
            SELECT
                COUNT(*) AS total,
                SUM(
                    CASE
                        WHEN status='Completed' THEN 1
                        ELSE 0
                    END
                ) AS completed
            FROM mission_tasks
            WHERE mission_id=?
            """,
            (mission_id,),
        ).fetchone()

        total = counts["total"] or 1
        completed = counts["completed"] or 0
        progress = 20 + int((completed / total) * 80)

        mission_status = (
            "Completed"
            if completed == total
            else "Running"
        )

        conn.execute(
            """
            UPDATE missions
            SET
                status=?,
                progress=?,
                updated_at=CURRENT_TIMESTAMP
            WHERE id=?
            """,
            (
                mission_status,
                progress,
                mission_id,
            ),
        )

        conn.commit()
    finally:
        conn.close()

    return {
        "mission_id": mission_id,
        "task_id": task["id"],
        "position": task["position"],
        "title": task["title"],
        "status": "Completed",
        "result": result,
        "mission_status": mission_status,
        "progress": progress,
        "tool_results": safe_tool_results,
        "model": EXECUTOR_MODEL,
    }
