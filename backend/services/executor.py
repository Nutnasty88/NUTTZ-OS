import json
import re
from typing import Any

from app.database.database import get_connection
from app.services.events import log_event
from services.builder import build_task
from services.ollama_service import chat_with_ollama
from services.tool_runner import run_tool
from services.workspace_executor import execute_python_artifact
from services.workspace_manager import list_workspace_files


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
            SELECT id, status
            FROM missions
            WHERE id=?
            """,
            (mission_id,),
        ).fetchone()

        if mission is None:
            raise ValueError(f"Mission {mission_id} was not found.")

        blocked_task = conn.execute(
            """
            SELECT id, position, title
            FROM mission_tasks
            WHERE
                mission_id=?
                AND status='Blocked'
            ORDER BY position ASC
            LIMIT 1
            """,
            (mission_id,),
        ).fetchone()

        if mission["status"] == "Blocked" or blocked_task is not None:
            if blocked_task is not None:
                blocked_detail = (
                    f"task {blocked_task['position']} "
                    f"({blocked_task['title']})"
                )
            else:
                blocked_detail = "the mission Evidence Gate"

            raise RuntimeError(
                f"Mission {mission_id} is blocked by {blocked_detail}. "
                "Blocked task evidence must be resolved or explicitly reset "
                "before task synchronization."
            )

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



WORKSPACE_EXECUTION_PATTERN = re.compile(
    r"""
    \b(
        run|
        execute|
        test|
        verify
    )\b
    .{0,220}
    \b(
        python|
        \.py|
        script|
        program
    )\b
    """,
    flags=re.IGNORECASE | re.DOTALL | re.VERBOSE,
)


PYTHON_ARTIFACT_PATTERN = re.compile(
    r"""
    (?P<path>
        [A-Za-z0-9_.\-/]+
        \.py
    )
    """,
    flags=re.IGNORECASE | re.VERBOSE,
)


def _is_workspace_execution_task(task: Any) -> bool:
    """Detect explicit requests to execute a Python Builder artifact."""
    task_text = (
        f"{task['title']}\n"
        f"{task['instructions']}"
    )

    return bool(
        WORKSPACE_EXECUTION_PATTERN.search(task_text)
    )


def _select_python_artifact(
    mission_id: int,
    task: Any,
) -> str:
    """
    Resolve the Python artifact deterministically.

    Prefer an explicit .py path in the task. Otherwise v1 requires
    exactly one Python artifact in the mission workspace.
    """
    task_text = (
        f"{task['title']}\n"
        f"{task['instructions']}"
    )

    explicit_matches = [
        match.group("path")
        for match in PYTHON_ARTIFACT_PATTERN.finditer(
            task_text
        )
    ]

    if explicit_matches:
        return explicit_matches[0]

    workspace_name = f"mission-{mission_id}"

    listing = list_workspace_files(
        workspace_name
    )

    python_files = [
        item["path"]
        for item in listing.get("files", [])
        if isinstance(item, dict)
        and str(item.get("path", "")).lower().endswith(".py")
    ]

    if not python_files:
        raise RuntimeError(
            "Workspace execution requested, but no Python "
            "artifact exists in the mission workspace."
        )

    if len(python_files) != 1:
        raise RuntimeError(
            "Workspace Executor v1 requires exactly one Python "
            "artifact when the task does not name a specific file."
        )

    return python_files[0]


def _complete_workspace_execution_task(
    mission: Any,
    task: Any,
) -> dict[str, Any]:
    """Execute and persist verified Builder workspace evidence."""
    mission_id = int(mission["id"])

    artifact_path = _select_python_artifact(
        mission_id,
        task,
    )

    log_event(
        mission_id,
        "Executor",
        "routing",
        (
            f"Task {task['position']} routed to Workspace Executor "
            f"for verified execution of {artifact_path}"
        ),
    )

    try:
        evidence = execute_python_artifact(
            mission_id,
            artifact_path,
        )

        if not evidence.get("verified"):
            raise RuntimeError(
                "Workspace artifact execution did not verify "
                "successfully.\n\n"
                + json.dumps(
                    evidence,
                    indent=2,
                    sort_keys=True,
                )
            )

        if evidence.get("exit_code") != 0:
            raise RuntimeError(
                "Workspace artifact returned a non-zero exit code."
            )

        result = (
            "WORKSPACE EXECUTION: VERIFIED\n\n"
            f"Artifact: {artifact_path}\n"
            f"Exit code: {evidence.get('exit_code')}\n"
            f"Stdout: {evidence.get('stdout', '').strip()}\n\n"
            "VERIFIED EXECUTION EVIDENCE:\n"
            + json.dumps(
                evidence,
                indent=2,
                sort_keys=True,
            )
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

        log_event(
            mission_id,
            "Workspace Executor",
            "error",
            (
                f"Execution verification failed for task "
                f"{task['position']}: {error}"
            ),
        )

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

        progress = 20 + int(
            (completed / total) * 80
        )

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

    log_event(
        mission_id,
        "Workspace Executor",
        "completed",
        (
            f"Task {task['position']} verified {artifact_path}: "
            f"exit code {evidence['exit_code']}"
        ),
    )

    return {
        "mission_id": mission_id,
        "task_id": task["id"],
        "position": task["position"],
        "title": task["title"],
        "status": "Completed",
        "result": result,
        "mission_status": mission_status,
        "progress": progress,
        "tool_results": [],
        "evidence": evidence,
        "agent": "Workspace Executor",
    }


BUILDER_TASK_PATTERN = re.compile(
    r"""
    \b(
        build|
        implement|
        create|
        generate|
        write|
        scaffold|
        code|
        develop|
        modify|
        update|
        save|
        edit|
        append|
        replace|
        rewrite
    )\b
    .{0,180}
    \b(
        file|
        files|
        code|
        script|
        program|
        application|
        app|
        module|
        package|
        component|
        project|
        source|
        artifact
    )\b
    """,
    flags=re.IGNORECASE | re.DOTALL | re.VERBOSE,
)


def _is_builder_task(task: Any) -> bool:
    """Return True only for tasks that explicitly request build artifacts."""
    task_text = (
        f"{task['title']}\n"
        f"{task['instructions']}"
    )

    return bool(BUILDER_TASK_PATTERN.search(task_text))


def _complete_builder_task(
    mission: Any,
    task: Any,
) -> dict[str, Any]:
    """Execute a Builder task and persist its verified artifacts."""
    mission_id = int(mission["id"])

    log_event(
        mission_id,
        "Executor",
        "routing",
        (
            f"Task {task['position']} routed to Builder Agent "
            "for isolated workspace execution"
        ),
    )

    try:
        builder_result = build_task(
            mission_id=mission_id,
            mission_title=mission["title"],
            task_id=task["id"],
            task_position=task["position"],
            task_title=task["title"],
            task_instructions=task["instructions"],
        )

        evidence = builder_result.get("evidence", {})

        if builder_result.get("status") != "Completed":
            raise RuntimeError(
                "Builder Agent did not complete the task."
            )

        if not evidence.get("verified"):
            raise RuntimeError(
                "Builder Agent returned no verified artifact evidence."
            )

        artifacts = builder_result.get("artifacts", [])

        if not artifacts:
            raise RuntimeError(
                "Builder Agent completed without creating artifacts."
            )

        result = (
            "BUILDER AGENT: COMPLETED\n\n"
            f"{builder_result.get('summary', 'Builder task completed.')}\n\n"
            "VERIFIED BUILDER EVIDENCE:\n"
            + json.dumps(
                evidence,
                indent=2,
                sort_keys=True,
            )
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

        log_event(
            mission_id,
            "Executor",
            "error",
            (
                f"Builder Agent failed task "
                f"{task['position']}: {error}"
            ),
        )

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

        progress = 20 + int(
            (completed / total) * 80
        )

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

    log_event(
        mission_id,
        "Executor",
        "completed",
        (
            f"Builder task {task['position']} completed "
            f"with {len(artifacts)} verified artifact(s)"
        ),
    )

    return {
        "mission_id": mission_id,
        "task_id": task["id"],
        "position": task["position"],
        "title": task["title"],
        "status": "Completed",
        "result": result,
        "mission_status": mission_status,
        "progress": progress,
        "tool_results": [],
        "builder": builder_result,
        "evidence": evidence,
        "model": builder_result.get("model"),
        "agent": "Builder",
    }


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


EVIDENCE_REQUIRED_RULES = (
    (
        "External research or source collection requires an approved "
        "research or network tool.",
        (
            r"\b(gather(?:ed|ing)?|collect(?:ed|ing)?|research(?:ed|ing)?|"
            r"search(?:ed|ing)?|browse(?:d|ing)?|look\s+up|fetch(?:ed|ing)?|"
            r"download(?:ed|ing)?|scrape(?:d|ing)?)\b"
        ),
    ),
    (
        "External delivery requires a submission receipt or approved "
        "delivery-tool result.",
        (
            r"\b(submit(?:ted|ting)?|send(?:ing)?|sent|email(?:ed|ing)?|"
            r"upload(?:ed|ing)?|publish(?:ed|ing)?|deploy(?:ed|ing)?|"
            r"post(?:ed|ing)?|deliver(?:ed|ing)?)\b"
        ),
    ),
    (
        "System-changing work requires an approved execution tool result.",
        (
            r"\b(run|execute|install|uninstall|start|stop|restart|configure|"
            r"modify|delete|copy|move|build|implement)\b"
        ),
    ),
    (
        "File or document production requires a verified artifact.",
        (
            r"\b(save|export|render|convert|format|finalize)\b"
            r".{0,160}\b(file|report|document|pdf|spreadsheet|archive|image)\b"
        ),
    ),
    (
        "External validation requires evidence from an approved tool.",
        (
            r"\b(test|inspect|scan|cross[- ]check|validate|verify|confirm)\b"
            r".{0,180}\b(files?|systems?|services?|servers?|networks?|"
            r"websites?|databases?|installations?|deployments?|submissions?|"
            r"citations?|sources?|facts?|records?)\b"
        ),
    ),
    (
        "Data-dependent analysis requires verified input data.",
        (
            r"\b(analy[sz]e|review|synthesi[sz]e)\b"
            r".{0,140}\b(data|sources?|documents?|evidence|records?|"
            r"findings?|information)\b"
        ),
    ),
)


def _evidence_requirement(task: Any) -> tuple[str, bool]:
    """
    Return the evidence requirement and whether current Safe Tools can
    satisfy it.
    """
    task_text = f"{task['title']}\n{task['instructions']}"

    for reason, pattern in EVIDENCE_REQUIRED_RULES:
        if re.search(
            pattern,
            task_text,
            flags=re.IGNORECASE | re.DOTALL,
        ):
            return reason, False

    if _task_safe_tool_names(task):
        return (
            "The task requests an allowlisted local diagnostic.",
            True,
        )

    return "", False


def _safe_tool_evidence_verified(
    results: list[dict[str, Any]],
) -> bool:
    return bool(results) and all(
        result.get("status") == "success"
        and result.get("exit_code") == 0
        for result in results
    )


def _block_task_for_missing_evidence(
    mission_id: int,
    task: Any,
    reason: str,
    tool_results: list[dict[str, Any]],
    safe_tools_can_satisfy: bool,
) -> dict[str, Any]:
    evidence_record = {
        "required": True,
        "verified": False,
        "requirement": reason,
        "safe_tools_can_satisfy": safe_tools_can_satisfy,
        "selected_tools": _task_safe_tool_names(task),
        "tool_results": tool_results,
    }

    result = (
        "EVIDENCE GATE: BLOCKED\n\n"
        "This task was not marked Completed because NUTTZ-OS did not "
        "receive verified evidence for the requested real-world action.\n\n"
        f"Requirement: {reason}\n\n"
        "No language-model statement can substitute for executed tool "
        "evidence, a verified artifact, or a delivery receipt.\n\n"
        "EVIDENCE RECORD:\n"
        + json.dumps(evidence_record, indent=2, sort_keys=True)
    )

    conn = get_connection()

    try:
        mission_row = conn.execute(
            """
            SELECT progress
            FROM missions
            WHERE id=?
            """,
            (mission_id,),
        ).fetchone()

        progress = (
            int(mission_row["progress"] or 0)
            if mission_row is not None
            else 0
        )

        conn.execute(
            """
            UPDATE mission_tasks
            SET
                status='Blocked',
                result=?,
                completed_at=NULL
            WHERE id=?
            """,
            (result, task["id"]),
        )

        conn.execute(
            """
            UPDATE missions
            SET
                status='Blocked',
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
        "Evidence Gate",
        "blocked",
        f'Task {task["position"]} blocked: verified evidence required.',
    )

    return {
        "mission_id": mission_id,
        "task_id": task["id"],
        "position": task["position"],
        "title": task["title"],
        "status": "Blocked",
        "result": result,
        "mission_status": "Blocked",
        "progress": progress,
        "tool_results": tool_results,
        "evidence_required": True,
        "evidence_verified": False,
        "model": EXECUTOR_MODEL,
    }


def reset_blocked_task(
    mission_id: int,
) -> dict[str, Any]:
    """Reset only the first blocked task while preserving completed work."""
    ensure_task_table()

    conn = get_connection()

    try:
        mission = conn.execute(
            """
            SELECT id, status, progress
            FROM missions
            WHERE id=?
            """,
            (mission_id,),
        ).fetchone()

        if mission is None:
            raise ValueError(f"Mission {mission_id} was not found.")

        blocked_task = conn.execute(
            """
            SELECT
                id,
                position,
                title,
                result
            FROM mission_tasks
            WHERE
                mission_id=?
                AND status='Blocked'
            ORDER BY position ASC
            LIMIT 1
            """,
            (mission_id,),
        ).fetchone()

        if blocked_task is None:
            raise RuntimeError(
                f"Mission {mission_id} has no blocked task to reset."
            )

        conn.execute(
            """
            UPDATE mission_tasks
            SET
                status='Pending',
                started_at=NULL,
                completed_at=NULL
            WHERE id=?
              AND status='Blocked'
            """,
            (blocked_task["id"],),
        )

        conn.execute(
            """
            UPDATE missions
            SET
                status='Running',
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
        "Evidence Gate",
        "reset",
        (
            f'Task {blocked_task["position"]} was explicitly reset '
            "for another verified-evidence attempt."
        ),
    )

    return {
        "mission_id": mission_id,
        "task_id": blocked_task["id"],
        "position": blocked_task["position"],
        "title": blocked_task["title"],
        "status": "Pending",
        "mission_status": "Running",
        "progress": int(mission["progress"] or 0),
        "previous_evidence_preserved": bool(
            blocked_task["result"]
        ),
    }


def execute_next_task(mission_id: int) -> dict[str, Any]:
    ensure_task_table()

    conn = get_connection()

    try:
        mission = conn.execute(
            """
            SELECT
                id,
                title,
                priority,
                progress
            FROM missions
            WHERE id=?
            """,
            (mission_id,),
        ).fetchone()

        if mission is None:
            raise ValueError(f"Mission {mission_id} was not found.")

        blocked_task = conn.execute(
            """
            SELECT
                id,
                position,
                title
            FROM mission_tasks
            WHERE
                mission_id=?
                AND status='Blocked'
            ORDER BY position ASC
            LIMIT 1
            """,
            (mission_id,),
        ).fetchone()

        if blocked_task is not None:
            return {
                "mission_id": mission_id,
                "task_id": blocked_task["id"],
                "position": blocked_task["position"],
                "title": blocked_task["title"],
                "status": "Blocked",
                "message": "A task is waiting for verified evidence.",
                "mission_status": "Blocked",
                "progress": int(mission["progress"] or 0),
            }

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

    log_event(
        mission_id,
        "Executor",
        "started",
        "Task execution started",
    )

    if _is_builder_task(task):
        return _complete_builder_task(
            mission=mission,
            task=task,
        )

    if _is_workspace_execution_task(task):
        return _complete_workspace_execution_task(
            mission=mission,
            task=task,
        )

    system_prompt = """
You are Executor Agent v2 inside NUTTZ-OS.

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
    evidence_reason, safe_tools_can_satisfy = (
        _evidence_requirement(task)
    )
    evidence_verified = (
        safe_tools_can_satisfy
        and _safe_tool_evidence_verified(safe_tool_results)
    )

    if evidence_reason and not evidence_verified:
        return _block_task_for_missing_evidence(
            mission_id=mission_id,
            task=task,
            reason=evidence_reason,
            tool_results=safe_tool_results,
            safe_tools_can_satisfy=safe_tools_can_satisfy,
        )

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

        log_event(
            mission_id,
            "Executor",
            "error",
            "Task execution failed",
        )

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

    log_event(
        mission_id,
        "Executor",
        "completed",
        "Task execution completed",
    )

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
