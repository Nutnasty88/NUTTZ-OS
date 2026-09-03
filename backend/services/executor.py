import hashlib
import json
import re
import shlex
import sqlite3
import uuid
from datetime import datetime, timezone
from typing import Any

from app.database.database import get_connection
from app.services.events import log_event
from services.builder import build_task, repair_artifact
from services.ollama_service import chat_with_ollama
from services.tool_runner import run_tool
from services.workspace_executor import execute_python_artifact
from services.workspace_manager import (
    list_workspace_files,
    read_workspace_file,
    write_project_manifest,
    write_workspace_file,
)


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
                execution_token TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                started_at TIMESTAMP,
                completed_at TIMESTAMP,
                FOREIGN KEY (mission_id) REFERENCES missions(id),
                UNIQUE (mission_id, position)
            )
            """
        )

        columns = {
            row["name"]
            for row in conn.execute(
                "PRAGMA table_info(mission_tasks)"
            ).fetchall()
        }

        if "execution_token" not in columns:
            conn.execute(
                """
                ALTER TABLE mission_tasks
                ADD COLUMN execution_token TEXT
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

        repair_history = conn.execute(
            """
            SELECT id, task_id, task_position
            FROM mission_repair_history
            WHERE mission_id=?
            ORDER BY id ASC
            LIMIT 1
            """,
            (mission_id,),
        ).fetchone()

        if repair_history is not None:
            raise RuntimeError(
                f"Mission {mission_id} has durable Builder repair history "
                f"(history {repair_history['id']} for task "
                f"{repair_history['task_position']}). "
                "Task synchronization is blocked to preserve task identity "
                "and verified repair evidence."
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
        program|
        project|
        application|
        app
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


EXACT_STDOUT_PATTERNS = (
    re.compile(
        r"""
        \b(?:prints?|outputs?)\s+exactly\s+
        (?P<quote>["']?)
        (?P<expected>[^\r\n"'`]+?)
        (?P=quote)
        (?=\s*(?:[.!?]|$))
        """,
        flags=re.IGNORECASE | re.VERBOSE,
    ),
    re.compile(
        r"""
        \bstdout\s+(?:must\s+)?(?:equal|equals|be)\s+
        (?P<quote>["']?)
        (?P<expected>[^\r\n"'`]+?)
        (?P=quote)
        (?=\s*(?:[.!?]|$))
        """,
        flags=re.IGNORECASE | re.VERBOSE,
    ),
    re.compile(
        r"""
        \bstdout\s+exactly\s*:?\s*
        (?P<quote>["']?)
        (?P<expected>[^\r\n"'`]+?)
        (?P=quote)
        (?=\s*(?:[.!?]|$))
        """,
        flags=re.IGNORECASE | re.VERBOSE,
    ),
    re.compile(
        r"""
        \boutput\s+exactly\s*:?\s*
        (?P<quote>["']?)
        (?P<expected>[^\r\n"'`]+?)
        (?P=quote)
        (?=\s*(?:[.!?]|$))
        """,
        flags=re.IGNORECASE | re.VERBOSE,
    ),
)


OUTPUT_MATCH_STDOUT_PATTERN = re.compile(
    r"""
    \boutput\s+(?:must\s+)?matches?\s+
    (?P<quote>["'])
    (?P<expected>[^\r\n"'`]+)
    (?P=quote)
    """,
    flags=re.IGNORECASE | re.VERBOSE,
)


CONTROLLED_NAME_STDIN_PATTERN = re.compile(
    r"""
    \b(?:input|enter|provide|supply)\s+
    (?:(?:a|the)\s+)?
    name\b
    """,
    flags=re.IGNORECASE | re.VERBOSE,
)


OUTPUT_IS_EXACTLY_STDOUT_PATTERN = re.compile(
    r"""
    \boutput\s+(?:is\s+)?exactly\s+
    (?P<quote>["'`])
    (?P<expected>[^\r\n"'`]+)
    (?P=quote)
    """,
    flags=re.IGNORECASE | re.VERBOSE,
)


DISPLAY_EXACTLY_STDOUT_PATTERN = re.compile(
    r"""
    \b(?:terminal\s+)?displays?\s+
    (?P<quote>["'`])
    (?P<expected>[^\r\n"'`]+)
    (?P=quote)
    \s+exactly\b
    """,
    flags=re.IGNORECASE | re.VERBOSE,
)


OUTPUT_VALUE_EXACTLY_STDOUT_PATTERN = re.compile(
    r"""
    \boutputs?\s+
    `?
    (?P<quote>["'])
    (?P<expected>[^\r\n"'`]+)
    (?P=quote)
    `?
    \s+exactly\b
    """,
    flags=re.IGNORECASE | re.VERBOSE,
)


CONTROLLED_PYTHON_COMMAND_PATTERN = re.compile(
    r"""
    `
    (?P<command>
        (?:python|python3)
        \s+
        [A-Za-z0-9_.\-/]+\.py
        (?:\s+[A-Za-z0-9_.-]+){1,8}
    )
    `
    """,
    flags=re.IGNORECASE | re.VERBOSE,
)


SAFE_TASK_ARGUMENT_PATTERN = re.compile(
    r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$"
)


PROVIDED_NAME_PATTERN = re.compile(
    r"\bprovided\s+with\s+(?:a\s+)?(?:valid\s+)?name\b",
    flags=re.IGNORECASE,
)


NAME_ARGUMENT_PATTERN = re.compile(
    r"\bname\s+argument\b",
    flags=re.IGNORECASE,
)


EXACT_GREETING_PATTERN = re.compile(
    r"^Hello,\s+(?P<name>[A-Za-z0-9][A-Za-z0-9_.-]{0,63})!$"
)


def _exact_stdout_requirement(
    task: Any,
) -> str | None:
    """
    Extract one explicit exact-stdout acceptance requirement.

    This intentionally recognizes only narrow, deterministic wording.
    Ambiguous task language is not converted into an acceptance rule.
    """
    task_text = (
        f"{task['title']}\n"
        f"{task['instructions']}"
    )

    for pattern in (
        *EXACT_STDOUT_PATTERNS,
        OUTPUT_MATCH_STDOUT_PATTERN,
        OUTPUT_IS_EXACTLY_STDOUT_PATTERN,
        DISPLAY_EXACTLY_STDOUT_PATTERN,
        OUTPUT_VALUE_EXACTLY_STDOUT_PATTERN,
    ):
        match = pattern.search(task_text)

        if not match:
            continue

        expected = match.group("expected").strip()

        if expected:
            return expected

    return None


def _controlled_workspace_stdin(
    task: Any,
) -> str | None:
    """
    Return one narrow deterministic stdin fixture when a task explicitly
    requests a name.

    Arbitrary model-generated stdin is never accepted here.
    """
    task_text = (
        f"{task['title']}\n"
        f"{task['instructions']}"
    )

    example_match = re.search(
        r"""
        \b(?:input|enter|provide|supply)\s+
        (?:(?:a|the)\s+)?
        name\b
        .{0,80}?
        \b(?:e\.g\.|example)\s*[,=:]?\s*
        [("']*
        (?P<name>[A-Za-z0-9][A-Za-z0-9_.-]{0,63})
        """,
        task_text,
        flags=re.IGNORECASE | re.DOTALL | re.VERBOSE,
    )

    if example_match is not None:
        return f"{example_match.group('name')}\n"

    expected_stdout = _exact_stdout_requirement(task)

    if expected_stdout is None:
        return None

    if re.search(
        r"\bNAME\b",
        expected_stdout,
    ) is None:
        return None

    if CONTROLLED_NAME_STDIN_PATTERN.search(
        task_text
    ) is None:
        return None

    return "NAME\n"


def _controlled_workspace_arguments(
    task: Any,
    artifact_path: str,
) -> list[str] | None:
    """
    Extract bounded positional arguments from one explicit backticked
    Python sample command.

    This parser never executes or returns shell syntax. The Workspace
    Executor independently validates every returned argument again.
    """
    task_text = (
        f"{task['title']}\n"
        f"{task['instructions']}"
    )

    artifact_name = artifact_path.rsplit(
        "/",
        maxsplit=1,
    )[-1]

    for match in CONTROLLED_PYTHON_COMMAND_PATTERN.finditer(
        task_text
    ):
        try:
            tokens = shlex.split(
                match.group("command"),
                posix=True,
            )
        except ValueError:
            continue

        if len(tokens) < 3:
            continue

        executable = tokens[0].lower()
        command_artifact = tokens[1].rsplit(
            "/",
            maxsplit=1,
        )[-1]

        arguments = tokens[2:]

        if executable not in {"python", "python3"}:
            continue

        if command_artifact != artifact_name:
            continue

        if not 1 <= len(arguments) <= 8:
            continue

        flagged_name_arguments = (
            len(arguments) == 2
            and arguments[0] == "--name"
            and SAFE_TASK_ARGUMENT_PATTERN.fullmatch(
                arguments[1]
            )
            is not None
        )

        positional_arguments = all(
            SAFE_TASK_ARGUMENT_PATTERN.fullmatch(argument)
            for argument in arguments
        )

        if not (
            flagged_name_arguments
            or positional_arguments
        ):
            continue

        return arguments

    expected_stdout = _exact_stdout_requirement(
        task
    )

    if (
        expected_stdout is None
        or PROVIDED_NAME_PATTERN.search(task_text) is None
    ):
        return None

    greeting_match = EXACT_GREETING_PATTERN.fullmatch(
        expected_stdout
    )

    if greeting_match is None:
        return None

    name = greeting_match.group("name")

    if NAME_ARGUMENT_PATTERN.search(task_text) is not None:
        return ["--name", name]

    return [name]


def _evaluate_execution_acceptance(
    task: Any,
    execution_evidence: dict[str, Any],
) -> dict[str, Any]:
    """
    Evaluate deterministic task acceptance criteria against factual
    Workspace Executor evidence.
    """
    expected_stdout = _exact_stdout_requirement(task)

    if expected_stdout is None:
        return {
            "applicable": False,
            "verified": None,
            "type": None,
            "reason": (
                "No deterministic exact-stdout acceptance criterion "
                "was found in the task."
            ),
        }

    actual_stdout = execution_evidence.get("stdout", "")

    if not isinstance(actual_stdout, str):
        actual_stdout = str(actual_stdout)

    actual_stdout = actual_stdout.strip()
    verified = actual_stdout == expected_stdout

    return {
        "applicable": True,
        "verified": verified,
        "type": "exact_stdout",
        "expected": expected_stdout,
        "actual": actual_stdout,
        "reason": (
            "Exact stdout matched the task requirement."
            if verified
            else "Exact stdout did not match the task requirement."
        ),
    }


def _is_workspace_execution_task(task: Any) -> bool:
    """Detect explicit requests to execute a Python Builder artifact."""
    task_text = (
        f"{task['title']}\n"
        f"{task['instructions']}"
    )

    normalized = task_text.lower()

    python_environment_check = (
        "python" in normalized
        and (
            "installed" in normalized
            or "installation" in normalized
            or "available" in normalized
            or "accessible" in normalized
        )
        and not PYTHON_ARTIFACT_PATTERN.search(task_text)
        and not re.search(
            r"\b(?:run|execute)\b.{0,120}\b"
            r"(?:script|program|application|app|project)\b",
            task_text,
            flags=re.IGNORECASE | re.DOTALL,
        )
    )

    if python_environment_check:
        return False

    if _exact_stdout_requirement(task) is not None:
        return True

    return bool(
        WORKSPACE_EXECUTION_PATTERN.search(task_text)
    )


def _latest_builder_entrypoint(
    mission_id: int,
) -> str | None:
    """
    Return the newest validated Builder entrypoint recorded for
    this mission.

    Builder task results are persisted by NUTTZ-OS after Workspace
    Manager has verified the referenced file exists.
    """
    conn = get_connection()

    try:
        rows = conn.execute(
            """
            SELECT result
            FROM mission_tasks
            WHERE
                mission_id=?
                AND status='Completed'
                AND result LIKE 'BUILDER AGENT: COMPLETED%'
            ORDER BY position DESC, id DESC
            """,
            (mission_id,),
        ).fetchall()
    finally:
        conn.close()

    marker = "VERIFIED BUILDER EVIDENCE:\n"

    for row in rows:
        result = row["result"]

        if (
            not isinstance(result, str)
            or marker not in result
        ):
            continue

        evidence_text = result.split(
            marker,
            1,
        )[1].lstrip()

        try:
            evidence, _ = json.JSONDecoder().raw_decode(
                evidence_text
            )
        except json.JSONDecodeError:
            continue

        if (
            not isinstance(evidence, dict)
            or evidence.get("verified") is not True
        ):
            continue

        entrypoint = evidence.get("entrypoint")

        if (
            isinstance(entrypoint, str)
            and entrypoint.strip()
            and entrypoint.lower().endswith(".py")
        ):
            return entrypoint.strip()

    return None


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

    builder_entrypoint = _latest_builder_entrypoint(
        mission_id
    )

    if builder_entrypoint:
        return builder_entrypoint

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
            "Workspace Executor could not determine the project "
            "entrypoint. The execution task did not name a Python "
            "file, no validated Builder entrypoint was available, "
            "and the workspace contains multiple Python files."
        )

    return python_files[0]


def ensure_repair_history_table() -> None:
    """
    Ensure durable Builder repair history storage exists.

    Repair history is intentionally separate from mission_tasks.result
    and mission_events so verified repair evidence remains structured
    and queryable across the lifetime of a mission.
    """
    conn = get_connection()

    try:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS mission_repair_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                mission_id INTEGER NOT NULL,
                task_id INTEGER NOT NULL,
                task_position INTEGER NOT NULL,
                workspace TEXT NOT NULL,
                entrypoint TEXT NOT NULL,
                summary TEXT NOT NULL DEFAULT '',
                confidence_score INTEGER,
                confidence_level TEXT,
                verified INTEGER NOT NULL DEFAULT 0,
                primary_target TEXT,
                primary_target_repaired INTEGER NOT NULL DEFAULT 0,
                traceback_targets TEXT NOT NULL DEFAULT '[]',
                changed_files TEXT NOT NULL DEFAULT '[]',
                repair_evidence TEXT NOT NULL DEFAULT '{}',
                final_execution TEXT NOT NULL DEFAULT '{}',
                confidence_evidence TEXT NOT NULL DEFAULT '{}',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (mission_id) REFERENCES missions(id),
                FOREIGN KEY (task_id) REFERENCES mission_tasks(id)
            )
            """
        )

        columns = {
            row["name"]
            for row in conn.execute(
                "PRAGMA table_info(mission_repair_history)"
            ).fetchall()
        }

        if "outcome" not in columns:
            conn.execute(
                """
                ALTER TABLE mission_repair_history
                ADD COLUMN outcome TEXT NOT NULL
                DEFAULT 'verified'
                """
            )

        if "rollback_evidence" not in columns:
            conn.execute(
                """
                ALTER TABLE mission_repair_history
                ADD COLUMN rollback_evidence TEXT NOT NULL
                DEFAULT '{}'
                """
            )

        if "fingerprint" not in columns:
            conn.execute(
                """
                ALTER TABLE mission_repair_history
                ADD COLUMN fingerprint TEXT
                """
            )

        conn.execute(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS
            idx_mission_repair_history_fingerprint
            ON mission_repair_history(fingerprint)
            WHERE fingerprint IS NOT NULL
            """
        )

        conn.commit()

    finally:
        conn.close()


def _repair_history_fingerprint(
    mission_id: int,
    task_id: int,
    task_position: int,
    workspace: str,
    entrypoint: str,
    outcome: str,
    repair_evidence: dict[str, Any],
    final_execution: dict[str, Any],
    acceptance_evidence: dict[str, Any],
    rollback_evidence: dict[str, Any],
) -> str:
    """
    Build a deterministic identity for one Builder repair event.

    Canonical JSON makes logically identical evidence produce the
    same fingerprint regardless of dictionary insertion order.
    """
    payload = {
        "mission_id": mission_id,
        "task_id": task_id,
        "task_position": task_position,
        "workspace": workspace,
        "entrypoint": entrypoint,
        "outcome": outcome,
        "repair_evidence": repair_evidence,
        "final_execution": final_execution,
        "acceptance_evidence": acceptance_evidence,
        "rollback_evidence": rollback_evidence,
    }

    canonical = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )

    return hashlib.sha256(
        canonical.encode("utf-8")
    ).hexdigest()


def record_repair_history(
    mission_id: int,
    task_id: int,
    task_position: int,
    repair_result: dict[str, Any],
    final_execution: dict[str, Any],
    confidence: dict[str, Any] | None,
    outcome: str = "verified",
    rollback_evidence: dict[str, Any] | None = None,
    acceptance_evidence: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """
    Persist one independently verified Builder repair attempt.

    JSON evidence is stored verbatim so future UI/reporting code can
    inspect the original deterministic evidence without reparsing the
    human-readable mission task result.
    """
    ensure_repair_history_table()

    repair_evidence = repair_result.get("evidence", {})

    if not isinstance(repair_evidence, dict):
        repair_evidence = {}

    if not isinstance(confidence, dict):
        confidence = {}

    if not isinstance(rollback_evidence, dict):
        rollback_evidence = {}

    if not isinstance(acceptance_evidence, dict):
        acceptance_evidence = {}

    allowed_outcomes = {
        "verified",
        "failed_rolled_back",
        "failed_rollback_incomplete",
    }

    if outcome not in allowed_outcomes:
        raise ValueError(
            f"Unsupported Builder repair outcome: {outcome}"
        )

    traceback_targets = confidence.get(
        "traceback_targets",
        repair_evidence.get(
            "traceback_targets",
            [],
        ),
    )

    if not isinstance(traceback_targets, list):
        traceback_targets = []

    changed_files = confidence.get(
        "changed_files",
        [],
    )

    if not isinstance(changed_files, list):
        changed_files = []

    workspace = repair_result.get(
        "workspace",
        repair_evidence.get("workspace", ""),
    )

    entrypoint = repair_result.get(
        "entrypoint",
        repair_evidence.get("entrypoint", ""),
    )

    summary = repair_result.get("summary", "")

    primary_target = confidence.get(
        "primary_target",
        repair_evidence.get(
            "traceback_primary_target"
        ),
    )

    execution_verified = (
        final_execution.get("verified") is True
        and final_execution.get("exit_code") == 0
    )

    acceptance_failed = (
        acceptance_evidence.get("applicable") is True
        and acceptance_evidence.get("verified") is not True
    )

    verified = (
        execution_verified
        and not acceptance_failed
    )

    rollback_restored = (
        rollback_evidence.get("restored") is True
    )

    if outcome == "verified":
        if not verified:
            raise ValueError(
                "Verified Builder repair history requires "
                "successful final execution evidence."
            )

        if rollback_evidence:
            raise ValueError(
                "Verified Builder repair history must not "
                "contain rollback evidence."
            )

    elif outcome == "failed_rolled_back":
        if verified:
            raise ValueError(
                "Failed Builder repair history cannot contain "
                "successful final execution evidence."
            )

        if not rollback_restored:
            raise ValueError(
                "failed_rolled_back requires rollback evidence "
                "with restored=true."
            )

    elif outcome == "failed_rollback_incomplete":
        if verified:
            raise ValueError(
                "Incomplete rollback history cannot contain "
                "successful final execution evidence."
            )

        if rollback_evidence.get("restored") is not False:
            raise ValueError(
                "failed_rollback_incomplete requires rollback "
                "evidence with restored=false."
            )

    fingerprint = _repair_history_fingerprint(
        mission_id=mission_id,
        task_id=task_id,
        task_position=task_position,
        workspace=str(workspace),
        entrypoint=str(entrypoint),
        outcome=outcome,
        repair_evidence=repair_evidence,
        final_execution=final_execution,
        acceptance_evidence=acceptance_evidence,
        rollback_evidence=rollback_evidence,
    )

    conn = get_connection()

    try:
        existing = conn.execute(
            """
            SELECT id
            FROM mission_repair_history
            WHERE fingerprint = ?
            LIMIT 1
            """,
            (fingerprint,),
        ).fetchone()

        if existing is not None:
            history_id = int(existing["id"])

            print(
                "Builder repair history duplicate suppressed:",
                history_id,
            )

            row = conn.execute(
                """
                SELECT *
                FROM mission_repair_history
                WHERE id = ?
                """,
                (history_id,),
            ).fetchone()

            result = dict(row)
            result["created"] = False
            return result

        try:
            cursor = conn.execute(
                """
                INSERT INTO mission_repair_history (
                    mission_id,
                    task_id,
                    task_position,
                    workspace,
                    entrypoint,
                    summary,
                    confidence_score,
                    confidence_level,
                    verified,
                    primary_target,
                    primary_target_repaired,
                    traceback_targets,
                    changed_files,
                    repair_evidence,
                    final_execution,
                    confidence_evidence,
                    outcome,
                    rollback_evidence,
                    fingerprint
                )
                VALUES (
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                    ?, ?, ?, ?, ?, ?, ?, ?, ?
                )
                """,
                (
                    mission_id,
                    task_id,
                    task_position,
                    str(workspace),
                    str(entrypoint),
                    str(summary),
                    confidence.get("score"),
                    confidence.get("level"),
                    1 if verified else 0,
                    primary_target,
                    (
                        1
                        if confidence.get(
                            "primary_target_repaired"
                        ) is True
                        else 0
                    ),
                    json.dumps(
                        traceback_targets,
                        sort_keys=True,
                    ),
                    json.dumps(
                        changed_files,
                        sort_keys=True,
                    ),
                    json.dumps(
                        repair_evidence,
                        sort_keys=True,
                    ),
                    json.dumps(
                        final_execution,
                        sort_keys=True,
                    ),
                    json.dumps(
                        confidence,
                        sort_keys=True,
                    ),
                    outcome,
                    json.dumps(
                        rollback_evidence,
                        sort_keys=True,
                    ),
                    fingerprint,
                ),
            )

            conn.commit()

            history_id = cursor.lastrowid

        except sqlite3.IntegrityError:
            conn.rollback()

            row = conn.execute(
                """
                SELECT *
                FROM mission_repair_history
                WHERE fingerprint = ?
                LIMIT 1
                """,
                (fingerprint,),
            ).fetchone()

            if row is None:
                raise

            history_id = int(row["id"])

            print(
                "Builder repair history concurrent "
                "duplicate suppressed:",
                history_id,
            )

            result = dict(row)
            result["created"] = False
            return result

    finally:
        conn.close()

    return {
        "id": history_id,
        "created": True,
        "mission_id": mission_id,
        "task_id": task_id,
        "task_position": task_position,
        "workspace": str(workspace),
        "entrypoint": str(entrypoint),
        "summary": str(summary),
        "confidence_score": confidence.get(
            "score"
        ),
        "confidence_level": confidence.get(
            "level"
        ),
        "verified": verified,
        "primary_target": primary_target,
        "primary_target_repaired": (
            confidence.get(
                "primary_target_repaired"
            ) is True
        ),
        "traceback_targets": traceback_targets,
        "changed_files": changed_files,
        "outcome": outcome,
        "rollback_evidence": rollback_evidence,
    }


def get_repair_history(
    mission_id: int,
) -> list[dict[str, Any]]:
    """
    Return structured Builder repair history for one mission.
    """
    ensure_repair_history_table()

    conn = get_connection()

    try:
        rows = conn.execute(
            """
            SELECT
                id,
                mission_id,
                task_id,
                task_position,
                workspace,
                entrypoint,
                summary,
                confidence_score,
                confidence_level,
                verified,
                primary_target,
                primary_target_repaired,
                traceback_targets,
                changed_files,
                repair_evidence,
                final_execution,
                confidence_evidence,
                outcome,
                rollback_evidence,
                created_at
            FROM mission_repair_history
            WHERE mission_id = ?
            ORDER BY id ASC
            """,
            (mission_id,),
        ).fetchall()

    finally:
        conn.close()

    history = []

    for row in rows:
        history.append(
            {
                "id": row["id"],
                "mission_id": row["mission_id"],
                "task_id": row["task_id"],
                "task_position": row[
                    "task_position"
                ],
                "workspace": row["workspace"],
                "entrypoint": row["entrypoint"],
                "summary": row["summary"],
                "confidence_score": row[
                    "confidence_score"
                ],
                "confidence_level": row[
                    "confidence_level"
                ],
                "verified": bool(row["verified"]),
                "primary_target": row[
                    "primary_target"
                ],
                "primary_target_repaired": bool(
                    row[
                        "primary_target_repaired"
                    ]
                ),
                "traceback_targets": json.loads(
                    row["traceback_targets"]
                    or "[]"
                ),
                "changed_files": json.loads(
                    row["changed_files"]
                    or "[]"
                ),
                "repair_evidence": json.loads(
                    row["repair_evidence"]
                    or "{}"
                ),
                "final_execution": json.loads(
                    row["final_execution"]
                    or "{}"
                ),
                "confidence": json.loads(
                    row["confidence_evidence"]
                    or "{}"
                ),
                "outcome": row["outcome"] or "verified",
                "rollback_evidence": json.loads(
                    row["rollback_evidence"]
                    or "{}"
                ),
                "created_at": row["created_at"],
            }
        )

    return history


def _repair_confidence(
    repair_result: dict[str, Any] | None,
    final_execution: dict[str, Any],
    acceptance_evidence: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """
    Score confidence in a Builder repair using only verified repair and
    execution evidence.

    This score is deterministic. The Builder model does not choose it.
    """
    if not repair_result:
        return None

    if not isinstance(acceptance_evidence, dict):
        acceptance_evidence = {}

    repair_evidence = repair_result.get("evidence", {})

    if not isinstance(repair_evidence, dict):
        repair_evidence = {}

    files = repair_evidence.get("files", [])

    if not isinstance(files, list):
        files = []

    changed_paths = [
        item.get("path")
        for item in files
        if (
            isinstance(item, dict)
            and item.get("changed") is True
            and isinstance(item.get("path"), str)
        )
    ]

    traceback_targets = repair_evidence.get(
        "traceback_targets",
        [],
    )

    if not isinstance(traceback_targets, list):
        traceback_targets = []

    traceback_targets = [
        item
        for item in traceback_targets
        if isinstance(item, str)
    ]

    primary_target = repair_evidence.get(
        "traceback_primary_target"
    )

    execution_verified = (
        final_execution.get("verified") is True
        and final_execution.get("exit_code") == 0
    )

    acceptance_failed = (
        acceptance_evidence.get("applicable") is True
        and acceptance_evidence.get("verified") is not True
    )

    final_verified = (
        execution_verified
        and not acceptance_failed
    )

    score = 60 if final_verified else 0
    reasons = []
    deductions = []

    if final_verified:
        reasons.append(
            "Repaired project passed final execution and acceptance "
            "verification."
        )
    elif acceptance_failed:
        deductions.append(
            "Repaired project passed execution but failed deterministic "
            "task acceptance."
        )
    else:
        deductions.append(
            "Repaired project did not pass final execution verification."
        )

    if (
        isinstance(primary_target, str)
        and primary_target
        and primary_target in changed_paths
    ):
        score += 20
        reasons.append(
            "Primary traceback target was repaired."
        )
    elif primary_target:
        deductions.append(
            "Primary traceback target was not modified."
        )

    if traceback_targets and changed_paths:
        non_traceback_paths = [
            item
            for item in changed_paths
            if item not in traceback_targets
        ]

        if not non_traceback_paths:
            score += 10
            reasons.append(
                "All repaired files were traceback-linked."
            )
        else:
            penalty = min(
                20,
                10 * len(non_traceback_paths),
            )
            score -= penalty
            deductions.append(
                "Repair modified non-traceback file(s): "
                + ", ".join(non_traceback_paths)
            )

    elif not traceback_targets:
        score -= 10
        deductions.append(
            "No workspace-local traceback target was available."
        )

    if len(changed_paths) == 1:
        score += 10
        reasons.append(
            "Repair was limited to one project file."
        )
    elif len(changed_paths) > 1:
        reasons.append(
            f"Repair changed {len(changed_paths)} project files."
        )

    score = max(0, min(100, score))

    if score >= 85:
        level = "High"
    elif score >= 65:
        level = "Medium"
    else:
        level = "Low"

    return {
        "score": score,
        "level": level,
        "verified": final_verified,
        "primary_target": primary_target,
        "primary_target_repaired": (
            isinstance(primary_target, str)
            and primary_target in changed_paths
        ),
        "traceback_targets": traceback_targets,
        "changed_files": changed_paths,
        "reasons": reasons,
        "deductions": deductions,
    }


def _repair_result_for_evidence(
    repair_result: dict[str, Any] | None,
) -> dict[str, Any] | None:
    """
    Return repair data safe for persisted/logged evidence.

    Internal rollback snapshots contain complete pre-repair file
    contents and must never be copied into task result evidence.
    """
    if not repair_result:
        return None

    return {
        key: value
        for key, value in repair_result.items()
        if key != "rollback_snapshot"
    }


def _rollback_failed_builder_repair(
    mission_id: int,
    task_position: int,
    repair_result: dict[str, Any] | None,
) -> dict[str, Any] | None:
    """
    Restore the exact pre-repair files when repaired execution fails.

    Builder repair is only allowed to modify files that already existed,
    so every rollback target must have a verified pre-repair snapshot.
    """
    if not repair_result:
        return None

    workspace_name = repair_result.get("workspace")
    rollback_snapshot = repair_result.get("rollback_snapshot")

    if (
        not isinstance(workspace_name, str)
        or not workspace_name
        or not isinstance(rollback_snapshot, dict)
        or not rollback_snapshot
    ):
        raise RuntimeError(
            "Builder post-repair rollback snapshot is missing or invalid."
        )

    restored_files = []
    rollback_errors = []

    for relative_path, snapshot in reversed(
        list(rollback_snapshot.items())
    ):
        if (
            not isinstance(relative_path, str)
            or not relative_path
            or not isinstance(snapshot, dict)
            or not isinstance(snapshot.get("content"), str)
            or not isinstance(snapshot.get("sha256"), str)
        ):
            rollback_errors.append(
                f"{relative_path}: invalid rollback snapshot"
            )
            continue

        try:
            write_result = write_workspace_file(
                workspace_name,
                relative_path,
                snapshot["content"],
            )

            verification = read_workspace_file(
                workspace_name,
                relative_path,
            )

            if verification["sha256"] != snapshot["sha256"]:
                raise RuntimeError(
                    "restored SHA256 does not match "
                    "pre-repair snapshot"
                )

            if write_result["sha256"] != snapshot["sha256"]:
                raise RuntimeError(
                    "rollback write SHA256 does not match "
                    "pre-repair snapshot"
                )

            restored_files.append(
                {
                    "path": relative_path,
                    "sha256": verification["sha256"],
                    "verified": True,
                }
            )

        except Exception as rollback_error:
            rollback_errors.append(
                f"{relative_path}: {rollback_error}"
            )

    rollback_complete = not rollback_errors

    if rollback_complete:
        log_event(
            mission_id,
            "Workspace Executor",
            "repair_execution_rollback",
            (
                f"Restored {len(restored_files)} pre-repair file(s) "
                f"after repaired execution failed for task "
                f"{task_position}"
            ),
        )
    else:
        log_event(
            mission_id,
            "Workspace Executor",
            "repair_execution_rollback_incomplete",
            (
                f"Builder rollback was incomplete for task "
                f"{task_position}. Restored "
                f"{len(restored_files)} file(s); "
                f"{len(rollback_errors)} rollback error(s)."
            ),
        )

    return {
        "workspace": workspace_name,
        "restored": rollback_complete,
        "file_count": len(restored_files),
        "files": restored_files,
        "errors": rollback_errors,
    }


def _assert_terminal_worker_ownership(
    conn: Any,
    mission_id: int,
    worker_owner_token: str | None,
) -> None:
    """
    Fence terminal transitions against conflicting worker ownership.

    Worker-owned transitions require the exact active lease token.
    Tokenless manual transitions are allowed only when no active
    worker lease owns the mission.
    """
    lease = conn.execute(
        """
        SELECT
            owner_token,
            expires_at
        FROM mission_worker_leases
        WHERE mission_id=?
        """,
        (mission_id,),
    ).fetchone()

    if worker_owner_token is None:
        if lease is None:
            return

        try:
            expires_at = datetime.fromisoformat(
                lease["expires_at"]
            )
        except (TypeError, ValueError) as error:
            raise RuntimeError(
                f"Mission {mission_id} worker lease expiry is invalid."
            ) from error

        if expires_at > datetime.now(timezone.utc):
            raise RuntimeError(
                f"Mission {mission_id} acquired an active worker "
                "lease before the manual terminal transition."
            )

        return

    if lease is None:
        raise RuntimeError(
            f"Mission {mission_id} worker lease was lost before "
            "the task terminal transition."
        )

    try:
        expires_at = datetime.fromisoformat(
            lease["expires_at"]
        )
    except (TypeError, ValueError) as error:
        raise RuntimeError(
            f"Mission {mission_id} worker lease expiry is invalid."
        ) from error

    if (
        lease["owner_token"] != worker_owner_token
        or expires_at <= datetime.now(timezone.utc)
    ):
        raise RuntimeError(
            f"Mission {mission_id} worker ownership was lost before "
            "the task terminal transition."
        )


def _complete_workspace_execution_task(
    mission: Any,
    task: Any,
    execution_token: str,
    worker_owner_token: str | None,
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

    controlled_arguments = (
        _controlled_workspace_arguments(
            task,
            artifact_path,
        )
        or []
    )

    if controlled_arguments:
        log_event(
            mission_id,
            "Workspace Executor",
            "argument_fixture",
            (
                f"Task {task['position']} is using "
                f"{len(controlled_arguments)} bounded positional "
                "argument fixture(s) for deterministic verification."
            ),
        )

    controlled_stdin = _controlled_workspace_stdin(
        task,
    )

    if controlled_stdin is not None:
        log_event(
            mission_id,
            "Workspace Executor",
            "stdin_fixture",
            (
                f"Task {task['position']} is using bounded "
                "controlled stdin for deterministic CLI verification."
            ),
        )

    try:
        evidence = execute_python_artifact(
            mission_id,
            artifact_path,
            stdin_text=controlled_stdin,
            arguments=controlled_arguments,
        )

        repair_result = None
        initial_evidence = None

        acceptance = _evaluate_execution_acceptance(
            task,
            evidence,
        )

        execution_failed = (
            not evidence.get("verified")
            or evidence.get("exit_code") != 0
        )

        acceptance_failed = (
            acceptance.get("applicable") is True
            and acceptance.get("verified") is not True
        )

        if execution_failed or acceptance_failed:
            initial_evidence = dict(evidence)
            initial_evidence["acceptance"] = acceptance

            failure_details = []

            if execution_failed:
                stderr_excerpt = str(
                    evidence.get("stderr", "")
                ).strip()

                if len(stderr_excerpt) > 400:
                    stderr_excerpt = (
                        stderr_excerpt[:400]
                        + "...[truncated]"
                    )

                failure_details.append(
                    "execution failed with exit code "
                    f"{evidence.get('exit_code')}; "
                    f"stderr={stderr_excerpt!r}"
                )

            if acceptance_failed:
                expected_excerpt = str(
                    acceptance.get("expected", "")
                )[:240]

                actual_excerpt = str(
                    acceptance.get("actual", "")
                )[:240]

                failure_details.append(
                    f"exact stdout expected "
                    f"{expected_excerpt!r} but received "
                    f"{actual_excerpt!r}"
                )

            log_event(
                mission_id,
                "Workspace Executor",
                "verification_failed",
                (
                    f"Task {task['position']} verification evidence: "
                    + " | ".join(failure_details)
                ),
            )

            log_event(
                mission_id,
                "Workspace Executor",
                "repairing",
                (
                    f"Task {task['position']} execution or "
                    "exact-output verification failed. Builder is "
                    "attempting one automatic repair."
                ),
            )

            repair_result = repair_artifact(
                mission_id=mission_id,
                mission_title=mission["title"],
                task_id=task["id"],
                task_position=task["position"],
                task_title=task["title"],
                task_instructions=task["instructions"],
                artifact_path=artifact_path,
                execution_evidence=initial_evidence,
            )

            log_event(
                mission_id,
                "Workspace Executor",
                "retesting",
                (
                    f"Task {task['position']} repaired artifact "
                    f"{artifact_path}; executing again"
                ),
            )

            evidence = execute_python_artifact(
                mission_id,
                artifact_path,
                stdin_text=controlled_stdin,
            arguments=controlled_arguments,
            )

        acceptance = _evaluate_execution_acceptance(
            task,
            evidence,
        )

        execution_failed = (
            not evidence.get("verified")
            or evidence.get("exit_code") != 0
        )

        acceptance_failed = (
            acceptance.get("applicable") is True
            and acceptance.get("verified") is not True
        )

        if execution_failed or acceptance_failed:
            rollback_result = None

            if repair_result:
                rollback_result = _rollback_failed_builder_repair(
                    mission_id,
                    int(task["position"]),
                    repair_result,
                )

                failed_confidence = _repair_confidence(
                    repair_result,
                    evidence,
                    acceptance,
                )

                failed_outcome = (
                    "failed_rolled_back"
                    if rollback_result.get("restored") is True
                    else "failed_rollback_incomplete"
                )

                failed_history = record_repair_history(
                    mission_id,
                    int(task["id"]),
                    int(task["position"]),
                    repair_result,
                    evidence,
                    failed_confidence,
                    outcome=failed_outcome,
                    rollback_evidence=rollback_result,
                    acceptance_evidence=acceptance,
                )

                if failed_history.get("created") is True:
                    log_event(
                        mission_id,
                        "Executor",
                        "repair_history",
                        (
                            f"Recorded Builder repair failure history "
                            f"{failed_history['id']} for task "
                            f"{task['position']} with outcome "
                            f"{failed_outcome}"
                        ),
                    )

            failure_message = (
                "Workspace artifact failed deterministic task "
                "acceptance."
                if acceptance_failed
                else (
                    "Workspace artifact failed execution after one "
                    "automatic Builder repair attempt."
                )
            )

            raise RuntimeError(
                failure_message
                + "\n\n"
                + json.dumps(
                    {
                        "initial_execution": initial_evidence,
                        "repair": _repair_result_for_evidence(
                            repair_result
                        ),
                        "final_execution": evidence,
                        "acceptance": acceptance,
                        "rollback": rollback_result,
                    },
                    indent=2,
                    sort_keys=True,
                )
            )

        manifest_result = write_project_manifest(
            workspace_name=evidence["workspace"],
            mission_id=mission_id,
            entrypoint=evidence["artifact"],
            runtime="python",
            run_command=[
                "python3",
                "-I",
                "-B",
                evidence["artifact"],
                *controlled_arguments,
            ],
            artifact_sha256=evidence["artifact_sha256"],
            artifact_size_bytes=evidence[
                "artifact_size_bytes"
            ],
            verified=True,
        )

        log_event(
            mission_id,
            "Workspace Executor",
            "manifest",
            (
                f"Verified project manifest written for "
                f"{evidence['artifact']}"
            ),
        )

        repair_confidence = _repair_confidence(
            repair_result,
            evidence,
            acceptance,
        )

        repair_history = None

        if (
            repair_result
            and repair_confidence
            and evidence.get("verified") is True
            and evidence.get("exit_code") == 0
        ):
            repair_history = record_repair_history(
                mission_id,
                int(task["id"]),
                int(task["position"]),
                repair_result,
                evidence,
                repair_confidence,
                acceptance_evidence=acceptance,
            )

            if repair_history.get("created") is True:
                log_event(
                    mission_id,
                    "Executor",
                    "repair_history",
                    (
                        f"Recorded verified Builder repair history "
                        f"{repair_history['id']} for task "
                        f"{task['position']} with confidence "
                        f"{repair_confidence['level']} "
                        f"{repair_confidence['score']}"
                    ),
                )

        result = (
            "WORKSPACE EXECUTION: VERIFIED\n\n"
            f"Artifact: {artifact_path}\n"
            f"Exit code: {evidence.get('exit_code')}\n"
            f"Stdout: {evidence.get('stdout', '').strip()}\n"
        )

        if repair_result:
            result += (
                "\nAUTO REPAIR: SUCCESS\n"
                f"{repair_result.get('summary', 'Artifact repaired.')}\n"
                "\nINITIAL EXECUTION EVIDENCE:\n"
                + json.dumps(
                    initial_evidence,
                    indent=2,
                    sort_keys=True,
                )
                + "\n\nREPAIR EVIDENCE:\n"
                + json.dumps(
                    repair_result.get("evidence", {}),
                    indent=2,
                    sort_keys=True,
                )
                + "\n\nREPAIR CONFIDENCE:\n"
                + json.dumps(
                    repair_confidence,
                    indent=2,
                    sort_keys=True,
                )
            )

        result += (
            "\n\nVERIFIED EXECUTION EVIDENCE:\n"
            + json.dumps(
                evidence,
                indent=2,
                sort_keys=True,
            )
            + "\n\nDETERMINISTIC ACCEPTANCE EVIDENCE:\n"
            + json.dumps(
                acceptance,
                indent=2,
                sort_keys=True,
            )
        )

    except Exception as error:
        conn = get_connection()

        try:
            conn.execute("BEGIN IMMEDIATE")

            _assert_terminal_worker_ownership(
                conn,
                mission_id,
                worker_owner_token,
            )

            cursor = conn.execute(
                """
                UPDATE mission_tasks
                SET
                    status='Error',
                    result=?,
                    execution_token=NULL
                WHERE id=?
                  AND status='Running'
                  AND execution_token=?
                """,
                (
                    str(error),
                    task["id"],
                    execution_token,
                ),
            )

            if cursor.rowcount != 1:
                conn.rollback()
                raise RuntimeError(
                    f'Task {task["id"]} execution failed after its '
                    "persisted Running state was lost."
                ) from error

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
        conn.execute("BEGIN IMMEDIATE")

        _assert_terminal_worker_ownership(
            conn,
            mission_id,
            worker_owner_token,
        )

        cursor = conn.execute(
            """
            UPDATE mission_tasks
            SET
                status='Completed',
                result=?,
                completed_at=CURRENT_TIMESTAMP,
                execution_token=NULL
            WHERE id=?
              AND status='Running'
              AND execution_token=?
            """,
            (
                result,
                task["id"],
                execution_token,
            ),
        )

        if cursor.rowcount != 1:
            conn.rollback()
            raise RuntimeError(
                f'Task {task["id"]} completion was rejected because '
                "its persisted Running state was lost."
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

        if completed == total:
            progress = 99
        else:
            progress = 20 + int(
                (completed / total) * 80
            )

        mission_status = "Running"

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
        "manifest": manifest_result,
        "repair": repair_result,
        "initial_evidence": initial_evidence,
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


def _future_project_task_state(
    mission_id: int,
    current_position: int,
) -> dict[str, bool]:
    """
    Inspect unfinished later tasks before automatically executing a
    Builder project.

    Auto-run is deferred when a later Builder task may still modify the
    project or when Planner already supplied an explicit execution task.
    """
    conn = get_connection()

    try:
        rows = conn.execute(
            """
            SELECT
                id,
                position,
                title,
                instructions,
                status
            FROM mission_tasks
            WHERE
                mission_id=?
                AND position>?
                AND status IN (
                    'Pending',
                    'Running',
                    'Blocked'
                )
            ORDER BY position ASC
            """,
            (
                mission_id,
                current_position,
            ),
        ).fetchall()
    finally:
        conn.close()

    later_builder = False
    later_execution = False

    for row in rows:
        if _is_workspace_execution_task(row):
            later_execution = True

        if _is_builder_task(row):
            later_builder = True

    return {
        "later_builder": later_builder,
        "later_execution": later_execution,
    }


def _complete_builder_task(
    mission: Any,
    task: Any,
    execution_token: str,
    worker_owner_token: str | None,
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

        entrypoint = builder_result.get("entrypoint")

        auto_execution = None
        auto_repair = None
        initial_auto_execution = None
        manifest_result = None
        auto_run_skipped_reason = ""

        if entrypoint:
            future_state = _future_project_task_state(
                mission_id,
                int(task["position"]),
            )

            if future_state["later_execution"]:
                auto_run_skipped_reason = (
                    "A later explicit Workspace Executor task "
                    "will verify the project."
                )

                log_event(
                    mission_id,
                    "Builder",
                    "auto_run_deferred",
                    (
                        f"Builder project entrypoint {entrypoint} "
                        "was not auto-run because a later explicit "
                        "execution task exists"
                    ),
                )

            elif future_state["later_builder"]:
                auto_run_skipped_reason = (
                    "A later Builder task may still modify "
                    "the project."
                )

                log_event(
                    mission_id,
                    "Builder",
                    "auto_run_deferred",
                    (
                        f"Builder project entrypoint {entrypoint} "
                        "was not auto-run because later Builder "
                        "work remains"
                    ),
                )

            else:
                log_event(
                    mission_id,
                    "Builder",
                    "auto_verifying",
                    (
                        f"Builder completed the final runnable project "
                        f"stage; automatically verifying {entrypoint}"
                    ),
                )

                auto_execution = execute_python_artifact(
                    mission_id,
                    entrypoint,
                )

                if (
                    not auto_execution.get("verified")
                    or auto_execution.get("exit_code") != 0
                ):
                    initial_auto_execution = auto_execution

                    log_event(
                        mission_id,
                        "Workspace Executor",
                        "auto_repairing",
                        (
                            "Automatic Builder project verification "
                            f"failed for {entrypoint}; Builder is "
                            "attempting one automatic repair"
                        ),
                    )

                    auto_repair = repair_artifact(
                        mission_id=mission_id,
                        mission_title=mission["title"],
                        task_id=task["id"],
                        task_position=task["position"],
                        task_title=task["title"],
                        task_instructions=task["instructions"],
                        artifact_path=entrypoint,
                        execution_evidence=auto_execution,
                    )

                    log_event(
                        mission_id,
                        "Workspace Executor",
                        "auto_retesting",
                        (
                            f"Builder repaired automatic project "
                            f"entrypoint {entrypoint}; executing again"
                        ),
                    )

                    auto_execution = execute_python_artifact(
                        mission_id,
                        entrypoint,
                    )

                auto_acceptance = _evaluate_execution_acceptance(
                    task,
                    auto_execution,
                )

                auto_execution_failed = (
                    not auto_execution.get("verified")
                    or auto_execution.get("exit_code") != 0
                )

                auto_acceptance_failed = (
                    auto_acceptance.get("applicable") is True
                    and auto_acceptance.get("verified") is not True
                )

                if (
                    auto_execution_failed
                    or auto_acceptance_failed
                ):
                    rollback_result = None

                    if auto_repair:
                        rollback_result = (
                            _rollback_failed_builder_repair(
                                mission_id,
                                int(task["position"]),
                                auto_repair,
                            )
                        )

                        failed_confidence = _repair_confidence(
                            auto_repair,
                            auto_execution,
                            auto_acceptance,
                        )

                        failed_outcome = (
                            "failed_rolled_back"
                            if rollback_result.get("restored") is True
                            else "failed_rollback_incomplete"
                        )

                        failed_history = record_repair_history(
                            mission_id,
                            int(task["id"]),
                            int(task["position"]),
                            auto_repair,
                            auto_execution,
                            failed_confidence,
                            outcome=failed_outcome,
                            rollback_evidence=rollback_result,
                            acceptance_evidence=auto_acceptance,
                        )

                        if failed_history.get("created") is True:
                            log_event(
                                mission_id,
                                "Executor",
                                "repair_history",
                                (
                                    "Recorded automatic Builder repair "
                                    f"failure history {failed_history['id']} "
                                    f"for task {task['position']} with "
                                    f"outcome {failed_outcome}"
                                ),
                            )

                    failure_message = (
                        "Automatic Builder project failed "
                        "deterministic task acceptance."
                        if auto_acceptance_failed
                        else (
                            "Automatic Builder project verification "
                            "failed after one automatic repair attempt."
                        )
                    )

                    raise RuntimeError(
                        failure_message
                        + "\n\n"
                        + json.dumps(
                            {
                                "initial_execution":
                                    initial_auto_execution,
                                "repair":
                                    _repair_result_for_evidence(
                                        auto_repair
                                    ),
                                "final_execution": auto_execution,
                                "acceptance": auto_acceptance,
                                "rollback": rollback_result,
                            },
                            indent=2,
                            sort_keys=True,
                        )
                    )

                manifest_result = write_project_manifest(
                    workspace_name=auto_execution["workspace"],
                    mission_id=mission_id,
                    entrypoint=auto_execution["artifact"],
                    runtime="python",
                    run_command=[
                        "python3",
                        "-I",
                        "-B",
                        auto_execution["artifact"],
                    ],
                    artifact_sha256=auto_execution[
                        "artifact_sha256"
                    ],
                    artifact_size_bytes=auto_execution[
                        "artifact_size_bytes"
                    ],
                    verified=True,
                )

                log_event(
                    mission_id,
                    "Workspace Executor",
                    "auto_verified",
                    (
                        f"Automatically verified Builder project "
                        f"{entrypoint}: exit code "
                        f"{auto_execution['exit_code']}"
                    ),
                )

                log_event(
                    mission_id,
                    "Workspace Executor",
                    "manifest",
                    (
                        "Verified project manifest automatically "
                        f"written for {entrypoint}"
                    ),
                )

        auto_repair_confidence = _repair_confidence(
            auto_repair,
            auto_execution or {},
            auto_acceptance if auto_execution else None,
        )

        auto_repair_history = None

        if (
            auto_repair
            and auto_repair_confidence
            and auto_execution
            and auto_execution.get("verified") is True
            and auto_execution.get("exit_code") == 0
        ):
            auto_repair_history = record_repair_history(
                mission_id,
                int(task["id"]),
                int(task["position"]),
                auto_repair,
                auto_execution,
                auto_repair_confidence,
                acceptance_evidence=auto_acceptance,
            )

            if auto_repair_history.get("created") is True:
                log_event(
                    mission_id,
                    "Executor",
                    "repair_history",
                    (
                        f"Recorded verified automatic Builder repair "
                        f"history {auto_repair_history['id']} for task "
                        f"{task['position']} with confidence "
                        f"{auto_repair_confidence['level']} "
                        f"{auto_repair_confidence['score']}"
                    ),
                )

        result = (
            "BUILDER AGENT: COMPLETED\n\n"
            f"{builder_result.get('summary', 'Builder task completed.')}\n\n"
            + (
                f"ENTRYPOINT: {builder_result['entrypoint']}\n\n"
                if builder_result.get("entrypoint")
                else ""
            )
            + "VERIFIED BUILDER EVIDENCE:\n"
            + json.dumps(
                evidence,
                indent=2,
                sort_keys=True,
            )
        )

        if auto_execution:
            result += (
                "\n\nAUTO PROJECT EXECUTION: VERIFIED\n\n"
                f"Entrypoint: {entrypoint}\n"
                f"Exit code: {auto_execution.get('exit_code')}\n"
                f"Stdout: "
                f"{auto_execution.get('stdout', '').strip()}\n"
            )

            if auto_repair:
                result += (
                    "\nAUTO REPAIR: SUCCESS\n"
                    f"{auto_repair.get('summary', 'Artifact repaired.')}\n"
                    "\nINITIAL AUTO EXECUTION EVIDENCE:\n"
                    + json.dumps(
                        initial_auto_execution,
                        indent=2,
                        sort_keys=True,
                    )
                    + "\n\nAUTO REPAIR EVIDENCE:\n"
                    + json.dumps(
                        auto_repair.get("evidence", {}),
                        indent=2,
                        sort_keys=True,
                    )
                    + "\n\nAUTO REPAIR CONFIDENCE:\n"
                    + json.dumps(
                        auto_repair_confidence,
                        indent=2,
                        sort_keys=True,
                    )
                    + "\n"
                )

            result += (
                "\nVERIFIED AUTO EXECUTION EVIDENCE:\n"
                + json.dumps(
                    auto_execution,
                    indent=2,
                    sort_keys=True,
                )
                + "\n\nDETERMINISTIC ACCEPTANCE EVIDENCE:\n"
                + json.dumps(
                    auto_acceptance,
                    indent=2,
                    sort_keys=True,
                )
                + "\n\nVERIFIED PROJECT MANIFEST:\n"
                + json.dumps(
                    manifest_result,
                    indent=2,
                    sort_keys=True,
                )
            )

        elif entrypoint and auto_run_skipped_reason:
            result += (
                "\n\nAUTO PROJECT EXECUTION: DEFERRED\n\n"
                f"Entrypoint: {entrypoint}\n"
                f"Reason: {auto_run_skipped_reason}"
            )

    except Exception as error:
        conn = get_connection()

        try:
            conn.execute("BEGIN IMMEDIATE")

            _assert_terminal_worker_ownership(
                conn,
                mission_id,
                worker_owner_token,
            )

            cursor = conn.execute(
                """
                UPDATE mission_tasks
                SET
                    status='Error',
                    result=?,
                    execution_token=NULL
                WHERE id=?
                  AND status='Running'
                  AND execution_token=?
                """,
                (
                    str(error),
                    task["id"],
                    execution_token,
                ),
            )

            if cursor.rowcount != 1:
                conn.rollback()
                raise RuntimeError(
                    f'Task {task["id"]} execution failed after its '
                    "execution ownership was lost."
                ) from error

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
        conn.execute("BEGIN IMMEDIATE")

        _assert_terminal_worker_ownership(
            conn,
            mission_id,
            worker_owner_token,
        )

        cursor = conn.execute(
            """
            UPDATE mission_tasks
            SET
                status='Completed',
                result=?,
                completed_at=CURRENT_TIMESTAMP,
                execution_token=NULL
            WHERE id=?
              AND status='Running'
              AND execution_token=?
            """,
            (
                result,
                task["id"],
                execution_token,
            ),
        )

        if cursor.rowcount != 1:
            conn.rollback()
            raise RuntimeError(
                f'Task {task["id"]} completion was rejected because '
                "its execution ownership was lost."
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

        if completed == total:
            progress = 99
        else:
            progress = 20 + int(
                (completed / total) * 80
            )

        mission_status = "Running"

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
        "auto_execution": auto_execution,
        "manifest": manifest_result,
        "auto_run": {
            "entrypoint": entrypoint,
            "attempted": auto_execution is not None,
            "verified": (
                bool(auto_execution)
                and auto_execution.get("verified") is True
            ),
            "skipped_reason": auto_run_skipped_reason,
        },
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


SAFE_TOOL_VERIFICATION_PATTERN = re.compile(
    r"""
    \b(?:ensure|confirm|verify|check)\b
    .{0,160}
    \b(?:installed|available|accessible|version)\b
    """,
    flags=re.IGNORECASE | re.DOTALL | re.VERBOSE,
)


EXPLICIT_MUTATION_PATTERN = re.compile(
    r"""
    \b(
        install|
        uninstall|
        configure|
        modify|
        delete|
        copy|
        move|
        deploy|
        restart|
        start|
        stop|
        execute|
        run|
        build|
        implement
    )\b
    """,
    flags=re.IGNORECASE | re.VERBOSE,
)


def _safe_tool_verification_only(
    task: Any,
) -> bool:
    """
    Permit an allowlisted diagnostic to verify existing software without
    treating a misleading task title as authorization to modify the host.
    """
    if not _task_safe_tool_names(task):
        return False

    instructions = str(
        task["instructions"]
    )

    if SAFE_TOOL_VERIFICATION_PATTERN.search(
        instructions
    ) is None:
        return False

    if EXPLICIT_MUTATION_PATTERN.search(
        instructions
    ) is not None:
        return False

    return True


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
    safe_tool_names = _task_safe_tool_names(task)

    if (
        safe_tool_names
        and _safe_tool_verification_only(task)
    ):
        return (
            "An allowlisted local diagnostic can verify the existing "
            "installation without changing the system.",
            True,
        )

    for reason, pattern in EVIDENCE_REQUIRED_RULES:
        if re.search(
            pattern,
            task_text,
            flags=re.IGNORECASE | re.DOTALL,
        ):
            return reason, False

    if safe_tool_names:
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
    execution_token: str,
    worker_owner_token: str | None,
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
        conn.execute("BEGIN IMMEDIATE")

        _assert_terminal_worker_ownership(
            conn,
            mission_id,
            worker_owner_token,
        )

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

        cursor = conn.execute(
            """
            UPDATE mission_tasks
            SET
                status='Blocked',
                result=?,
                completed_at=NULL,
                execution_token=NULL
            WHERE id=?
              AND status='Running'
              AND execution_token=?
            """,
            (
                result,
                task["id"],
                execution_token,
            ),
        )

        if cursor.rowcount != 1:
            conn.rollback()
            raise RuntimeError(
                f'Task {task["id"]} could not be blocked because '
                "its execution ownership was lost."
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
        conn.execute("BEGIN IMMEDIATE")

        _assert_terminal_worker_ownership(
            conn,
            mission_id,
            None,
        )

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
                completed_at=NULL,
                execution_token=NULL
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


def reset_error_task(
    mission_id: int,
) -> dict[str, Any]:
    """
    Explicitly reset the first Error task to Pending.

    Error recovery is operator-approved and refuses to modify a
    mission while an active durable worker lease owns it.
    """
    ensure_task_table()

    conn = get_connection()

    try:
        conn.execute("BEGIN IMMEDIATE")

        mission = conn.execute(
            """
            SELECT id, status, progress
            FROM missions
            WHERE id=?
            """,
            (mission_id,),
        ).fetchone()

        if mission is None:
            raise ValueError(
                f"Mission {mission_id} was not found."
            )

        lease = conn.execute(
            """
            SELECT expires_at
            FROM mission_worker_leases
            WHERE mission_id=?
            """,
            (mission_id,),
        ).fetchone()

        if lease is not None:
            try:
                expires_at = datetime.fromisoformat(
                    lease["expires_at"]
                )
                lease_active = expires_at > datetime.now(
                    timezone.utc
                )
            except (TypeError, ValueError) as error:
                conn.rollback()

                raise RuntimeError(
                    f"Mission {mission_id} worker lease expiry "
                    "is invalid and the Error task cannot "
                    "be safely reset."
                ) from error

            if lease_active:
                raise RuntimeError(
                    f"Mission {mission_id} has an active worker "
                    "lease and cannot reset an Error task."
                )

        error_task = conn.execute(
            """
            SELECT
                id,
                position,
                title,
                result
            FROM mission_tasks
            WHERE
                mission_id=?
                AND status='Error'
            ORDER BY position ASC
            LIMIT 1
            """,
            (mission_id,),
        ).fetchone()

        if error_task is None:
            raise RuntimeError(
                f"Mission {mission_id} has no Error task to reset."
            )

        cursor = conn.execute(
            """
            UPDATE mission_tasks
            SET
                status='Pending',
                started_at=NULL,
                completed_at=NULL,
                execution_token=NULL
            WHERE id=?
              AND status='Error'
            """,
            (error_task["id"],),
        )

        if cursor.rowcount != 1:
            raise RuntimeError(
                f"Error task {error_task['id']} changed "
                "before it could be reset."
            )

        cursor = conn.execute(
            """
            UPDATE missions
            SET
                status='Running',
                updated_at=CURRENT_TIMESTAMP
            WHERE id=?
              AND status='Error'
            """,
            (mission_id,),
        )

        if cursor.rowcount != 1:
            raise RuntimeError(
                f"Mission {mission_id} changed before its "
                "Error task could be reset."
            )

        conn.commit()

    except Exception:
        conn.rollback()
        raise

    finally:
        conn.close()

    log_event(
        mission_id,
        "Recovery",
        "reset",
        (
            f'Task {error_task["position"]} was explicitly reset '
            "from Error to Pending for operator-approved retry."
        ),
    )

    return {
        "mission_id": mission_id,
        "task_id": error_task["id"],
        "position": error_task["position"],
        "title": error_task["title"],
        "status": "Pending",
        "mission_status": "Running",
        "progress": int(mission["progress"] or 0),
        "previous_result_preserved": bool(
            error_task["result"]
        ),
    }


def reset_interrupted_task(
    mission_id: int,
) -> dict[str, Any]:
    """
    Explicitly reset the first Interrupted task to Pending.

    Interrupted work is never retried automatically. This function
    provides the deliberate recovery transition after an operator has
    chosen to resume the mission.
    """
    ensure_task_table()

    conn = get_connection()

    try:
        conn.execute("BEGIN IMMEDIATE")

        _assert_terminal_worker_ownership(
            conn,
            mission_id,
            None,
        )

        mission = conn.execute(
            """
            SELECT id, status, progress
            FROM missions
            WHERE id=?
            """,
            (mission_id,),
        ).fetchone()

        if mission is None:
            raise ValueError(
                f"Mission {mission_id} was not found."
            )

        interrupted_task = conn.execute(
            """
            SELECT
                id,
                position,
                title,
                result
            FROM mission_tasks
            WHERE
                mission_id=?
                AND status='Interrupted'
            ORDER BY position ASC
            LIMIT 1
            """,
            (mission_id,),
        ).fetchone()

        if interrupted_task is None:
            raise RuntimeError(
                f"Mission {mission_id} has no "
                "interrupted task to reset."
            )

        cursor = conn.execute(
            """
            UPDATE mission_tasks
            SET
                status='Pending',
                started_at=NULL,
                completed_at=NULL,
                execution_token=NULL
            WHERE id=?
              AND status='Interrupted'
            """,
            (interrupted_task["id"],),
        )

        if cursor.rowcount != 1:
            raise RuntimeError(
                f"Interrupted task "
                f"{interrupted_task['id']} changed "
                "before it could be reset."
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
        "Recovery",
        "reset",
        (
            f'Task {interrupted_task["position"]} '
            "was explicitly reset from Interrupted "
            "to Pending for operator-approved recovery."
        ),
    )

    return {
        "mission_id": mission_id,
        "task_id": interrupted_task["id"],
        "position": interrupted_task["position"],
        "title": interrupted_task["title"],
        "status": "Pending",
        "mission_status": "Running",
        "progress": int(mission["progress"] or 0),
        "previous_result_preserved": bool(
            interrupted_task["result"]
        ),
    }


def rollback_interrupted_task_reset(
    mission_id: int,
    task_id: int,
) -> dict[str, Any]:
    """
    Restore a just-reset recovery task from Pending to Interrupted.

    This rollback is intentionally narrow. It only succeeds while the
    exact task is still Pending, preventing recovery cleanup from
    overwriting a task that has already begun execution.
    """
    ensure_task_table()

    conn = get_connection()

    try:
        conn.execute("BEGIN IMMEDIATE")

        _assert_terminal_worker_ownership(
            conn,
            mission_id,
            None,
        )

        mission = conn.execute(
            """
            SELECT id, progress
            FROM missions
            WHERE id=?
            """,
            (mission_id,),
        ).fetchone()

        if mission is None:
            raise ValueError(
                f"Mission {mission_id} was not found."
            )

        task = conn.execute(
            """
            SELECT
                id,
                position,
                title,
                status
            FROM mission_tasks
            WHERE
                id=?
                AND mission_id=?
            """,
            (task_id, mission_id),
        ).fetchone()

        if task is None:
            raise ValueError(
                f"Task {task_id} was not found for "
                f"mission {mission_id}."
            )

        cursor = conn.execute(
            """
            UPDATE mission_tasks
            SET
                status='Interrupted',
                started_at=NULL,
                completed_at=NULL,
                execution_token=NULL
            WHERE
                id=?
                AND mission_id=?
                AND status='Pending'
            """,
            (task_id, mission_id),
        )

        if cursor.rowcount != 1:
            raise RuntimeError(
                f"Recovery rollback refused because task "
                f"{task_id} is no longer Pending."
            )

        conn.execute(
            """
            UPDATE missions
            SET
                status='Interrupted',
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
        "Recovery",
        "rollback",
        (
            f'Task {task["position"]} recovery reset was rolled '
            "back from Pending to Interrupted because worker "
            "startup did not complete."
        ),
    )

    return {
        "mission_id": mission_id,
        "task_id": task_id,
        "position": task["position"],
        "title": task["title"],
        "status": "Interrupted",
        "mission_status": "Interrupted",
        "progress": int(mission["progress"] or 0),
    }


def interrupt_orphaned_running_task(
    mission_id: int,
) -> dict[str, Any]:
    """
    Convert one orphaned persisted Running task to Interrupted.

    This function does not restart execution. It only performs the
    conservative recovery transition required before operator-approved
    guarded resume can occur.

    A mission with an active worker lease must never be interrupted by
    this recovery path.
    """
    ensure_task_table()

    conn = get_connection()

    try:
        conn.execute("BEGIN IMMEDIATE")

        mission = conn.execute(
            """
            SELECT id, status, progress
            FROM missions
            WHERE id=?
            """,
            (mission_id,),
        ).fetchone()

        if mission is None:
            conn.rollback()

            raise ValueError(
                f"Mission {mission_id} was not found."
            )

        task = conn.execute(
            """
            SELECT
                id,
                position,
                title,
                status,
                started_at
            FROM mission_tasks
            WHERE
                mission_id=?
                AND status='Running'
            ORDER BY position ASC
            LIMIT 1
            """,
            (mission_id,),
        ).fetchone()

        if task is None:
            conn.rollback()

            raise RuntimeError(
                f"Mission {mission_id} has no Running task "
                "eligible for orphan recovery."
            )

        lease_table_exists = conn.execute(
            """
            SELECT 1
            FROM sqlite_master
            WHERE
                type='table'
                AND name='mission_worker_leases'
            """
        ).fetchone() is not None

        if lease_table_exists:
            lease = conn.execute(
                """
                SELECT expires_at
                FROM mission_worker_leases
                WHERE mission_id=?
                """,
                (mission_id,),
            ).fetchone()

            if lease is not None:
                try:
                    expires_at = datetime.fromisoformat(
                        lease["expires_at"]
                    )
                except (TypeError, ValueError) as error:
                    conn.rollback()

                    raise RuntimeError(
                        f"Mission {mission_id} worker lease expiry "
                        "is invalid and orphan recovery cannot "
                        "safely determine ownership."
                    ) from error

                now = datetime.now(timezone.utc)

                if expires_at > now:
                    conn.rollback()

                    raise RuntimeError(
                        f"Mission {mission_id} still has an "
                        "active worker lease and cannot be "
                        "recovered as orphaned."
                    )

        cursor = conn.execute(
            """
            UPDATE mission_tasks
            SET
                status='Interrupted',
                execution_token=NULL
            WHERE
                id=?
                AND mission_id=?
                AND status='Running'
            """,
            (
                task["id"],
                mission_id,
            ),
        )

        if cursor.rowcount != 1:
            conn.rollback()

            raise RuntimeError(
                f"Task {task['id']} changed before orphan "
                "recovery could complete."
            )

        conn.execute(
            """
            UPDATE missions
            SET
                status='Interrupted',
                updated_at=CURRENT_TIMESTAMP
            WHERE id=?
            """,
            (mission_id,),
        )

        conn.commit()

    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass

        raise

    finally:
        conn.close()

    log_event(
        mission_id,
        "Recovery",
        "orphaned",
        (
            f'Task {task["position"]} was explicitly changed '
            "from orphaned Running state to Interrupted after "
            "operator-approved recovery."
        ),
    )

    return {
        "mission_id": mission_id,
        "task_id": task["id"],
        "position": task["position"],
        "title": task["title"],
        "status": "Interrupted",
        "mission_status": "Interrupted",
        "progress": int(mission["progress"] or 0),
        "started_at": task["started_at"],
    }


def recover_interrupted_tasks() -> dict[str, Any]:
    """
    Mark orphaned persisted Running tasks as Interrupted.

    A Running task can survive a backend crash because task execution
    state is persisted while the Autonomous Worker thread is not.

    An unexpired mission worker lease proves another worker process may
    still legitimately own the task, so startup recovery must leave that
    task untouched.

    Recovery is deliberately conservative. Interrupted tasks are not
    automatically retried because Builder or execution tasks may have
    produced side effects before the process stopped.
    """
    ensure_task_table()

    conn = get_connection()

    try:
        conn.execute("BEGIN IMMEDIATE")

        lease_table_exists = conn.execute(
            """
            SELECT 1
            FROM sqlite_master
            WHERE
                type='table'
                AND name='mission_worker_leases'
            """
        ).fetchone() is not None

        running_tasks = conn.execute(
            """
            SELECT
                id,
                mission_id,
                position,
                title,
                started_at
            FROM mission_tasks
            WHERE status='Running'
            ORDER BY mission_id, position
            """
        ).fetchall()

        recovered = []
        protected = []
        now = datetime.now(timezone.utc)

        for task in running_tasks:
            active_lease = None

            if lease_table_exists:
                active_lease = conn.execute(
                    """
                    SELECT
                        owner_token,
                        heartbeat_at,
                        expires_at
                    FROM mission_worker_leases
                    WHERE mission_id=?
                    """,
                    (task["mission_id"],),
                ).fetchone()

            if active_lease is not None:
                try:
                    expires_at = datetime.fromisoformat(
                        active_lease["expires_at"]
                    )
                except (TypeError, ValueError):
                    protected.append(
                        {
                            "task_id": task["id"],
                            "mission_id": task["mission_id"],
                            "position": task["position"],
                            "title": task["title"],
                            "started_at": task["started_at"],
                            "lease_expires_at": (
                                active_lease["expires_at"]
                            ),
                            "protection_reason": (
                                "invalid_worker_lease_expiry"
                            ),
                        }
                    )
                    continue

                if expires_at > now:
                    protected.append(
                        {
                            "task_id": task["id"],
                            "mission_id": task["mission_id"],
                            "position": task["position"],
                            "title": task["title"],
                            "started_at": task["started_at"],
                            "lease_expires_at": (
                                active_lease["expires_at"]
                            ),
                            "protection_reason": (
                                "active_worker_lease"
                            ),
                        }
                    )
                    continue

            cursor = conn.execute(
                """
                UPDATE mission_tasks
                SET
                    status='Interrupted',
                    execution_token=NULL
                WHERE id=?
                  AND status='Running'
                """,
                (task["id"],),
            )

            if cursor.rowcount != 1:
                continue

            conn.execute(
                """
                UPDATE missions
                SET
                    status='Interrupted',
                    updated_at=CURRENT_TIMESTAMP
                WHERE id=?
                  AND status='Running'
                """,
                (task["mission_id"],),
            )

            recovered.append(
                {
                    "task_id": task["id"],
                    "mission_id": task["mission_id"],
                    "position": task["position"],
                    "title": task["title"],
                    "started_at": task["started_at"],
                }
            )

        conn.commit()

    finally:
        conn.close()

    for task in recovered:
        log_event(
            task["mission_id"],
            "Recovery",
            "interrupted",
            (
                f'Task {task["position"]} was marked Interrupted '
                "after persisted Running state was found without "
                "an active worker lease."
            ),
        )

    return {
        "recovered_count": len(recovered),
        "protected_count": len(protected),
        "tasks": recovered,
        "protected_tasks": protected,
    }


def mark_mission_report_error(
    mission_id: int,
    worker_owner_token: str | None = None,
) -> dict[str, Any]:
    """Mark Reporter failure only while current worker ownership holds."""

    ensure_task_table()

    conn = get_connection()

    try:
        conn.execute("BEGIN IMMEDIATE")

        _assert_terminal_worker_ownership(
            conn,
            mission_id,
            worker_owner_token,
        )

        cursor = conn.execute(
            """
            UPDATE missions
            SET
                status='Report Error',
                progress=100,
                updated_at=CURRENT_TIMESTAMP
            WHERE id=?
            """,
            (mission_id,),
        )

        if cursor.rowcount != 1:
            conn.rollback()
            raise RuntimeError(
                f"Mission {mission_id} Report Error transition failed."
            )

        conn.commit()

    finally:
        conn.close()

    return {
        "mission_id": mission_id,
        "status": "Report Error",
        "progress": 100,
    }


def finalize_mission_completion(
    mission_id: int,
    required_status: str | None = None,
    worker_owner_token: str | None = None,
) -> dict[str, Any]:
    """
    Atomically complete a mission only if its current persisted task
    set still consists entirely of Completed tasks.

    Reporter generation can take a long time. Task synchronization
    may replace the task set while Reporter is running, so completion
    must revalidate the current task state immediately before the
    mission terminal transition.
    """
    ensure_task_table()

    conn = get_connection()

    try:
        conn.execute("BEGIN IMMEDIATE")

        _assert_terminal_worker_ownership(
            conn,
            mission_id,
            worker_owner_token,
        )

        mission = conn.execute(
            """
            SELECT
                id,
                status,
                progress
            FROM missions
            WHERE id=?
            """,
            (mission_id,),
        ).fetchone()

        if mission is None:
            conn.rollback()
            raise ValueError(
                f"Mission {mission_id} was not found."
            )

        if (
            required_status is not None
            and mission["status"] != required_status
        ):
            conn.rollback()
            raise RuntimeError(
                f"Mission {mission_id} cannot be finalized from "
                f"status {mission['status']!r}; expected "
                f"{required_status!r}."
            )

        counts = conn.execute(
            """
            SELECT
                COUNT(*) AS total_tasks,
                SUM(
                    CASE
                        WHEN status='Completed' THEN 1
                        ELSE 0
                    END
                ) AS completed_tasks
            FROM mission_tasks
            WHERE mission_id=?
            """,
            (mission_id,),
        ).fetchone()

        total_tasks = int(counts["total_tasks"] or 0)
        completed_tasks = int(
            counts["completed_tasks"] or 0
        )

        if total_tasks == 0:
            conn.rollback()
            raise RuntimeError(
                f"Mission {mission_id} cannot be completed because "
                "it has no execution tasks."
            )

        if completed_tasks != total_tasks:
            conn.rollback()
            raise RuntimeError(
                f"Mission {mission_id} completion was rejected "
                "because its current task set is no longer fully "
                f"completed ({completed_tasks}/{total_tasks})."
            )

        deliverable = conn.execute(
            """
            SELECT
                status,
                content
            FROM mission_deliverables
            WHERE mission_id=?
            """,
            (mission_id,),
        ).fetchone()

        if (
            deliverable is None
            or deliverable["status"] != "Ready"
            or not str(deliverable["content"] or "").strip()
        ):
            conn.rollback()
            raise RuntimeError(
                f"Mission {mission_id} completion was rejected "
                "because a Ready, non-empty final deliverable "
                "is required."
            )

        cursor = conn.execute(
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

        if cursor.rowcount != 1:
            conn.rollback()
            raise RuntimeError(
                f"Mission {mission_id} completion transition failed."
            )

        conn.commit()

    finally:
        conn.close()

    return {
        "mission_id": mission_id,
        "status": "Completed",
        "progress": 100,
        "total_tasks": total_tasks,
        "completed_tasks": completed_tasks,
    }

def execute_next_task(
    mission_id: int,
    worker_owner_token: str | None = None,
) -> dict[str, Any]:
    ensure_task_table()

    conn = get_connection()

    try:
        conn.execute("BEGIN IMMEDIATE")

        lease = conn.execute(
            """
            SELECT
                owner_token,
                expires_at
            FROM mission_worker_leases
            WHERE mission_id=?
            """,
            (mission_id,),
        ).fetchone()

        now = datetime.now(timezone.utc)
        active_lease = False

        if lease is not None:
            try:
                lease_expires_at = datetime.fromisoformat(
                    lease["expires_at"]
                )
                active_lease = lease_expires_at > now
            except (TypeError, ValueError) as error:
                conn.rollback()

                raise RuntimeError(
                    f"Mission {mission_id} worker lease expiry "
                    "is invalid and task execution cannot "
                    "be safely claimed."
                ) from error

        if worker_owner_token is None:
            if active_lease:
                conn.rollback()
                raise RuntimeError(
                    f"Mission {mission_id} has an active worker "
                    "lease. Manual task execution is not allowed."
                )
        else:
            if (
                not active_lease
                or lease is None
                or lease["owner_token"] != worker_owner_token
            ):
                conn.rollback()
                raise RuntimeError(
                    f"Mission {mission_id} worker ownership was "
                    "lost before task execution could be claimed."
                )

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
                status = "Tasks Completed"
                progress = int(mission["progress"] or 0)
                message = (
                    "All mission tasks are complete. "
                    "Final deliverable is still required."
                )
            else:
                status = "Incomplete"
                progress = int(mission["progress"] or 0)
                message = (
                    "No pending tasks remain, but not all mission "
                    "tasks are completed."
                )

            return {
                "mission_id": mission_id,
                "status": status,
                "message": message,
                "progress": progress,
                "total_tasks": total,
                "completed_tasks": completed,
            }

        execution_token = uuid.uuid4().hex

        cursor = conn.execute(
            """
            UPDATE mission_tasks
            SET
                status='Running',
                started_at=CURRENT_TIMESTAMP,
                execution_token=?
            WHERE id=?
              AND status='Pending'
              AND execution_token IS NULL
            """,
            (
                execution_token,
                task["id"],
            ),
        )

        if cursor.rowcount != 1:
            conn.rollback()
            raise RuntimeError(
                f'Task {task["id"]} could not be claimed because '
                "its persisted state changed before execution started."
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
            execution_token=execution_token,
            worker_owner_token=worker_owner_token,
        )

    if _is_workspace_execution_task(task):
        return _complete_workspace_execution_task(
            mission=mission,
            task=task,
            execution_token=execution_token,
            worker_owner_token=worker_owner_token,
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
            execution_token=execution_token,
            worker_owner_token=worker_owner_token,
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
            conn.execute("BEGIN IMMEDIATE")

            _assert_terminal_worker_ownership(
                conn,
                mission_id,
                worker_owner_token,
            )

            cursor = conn.execute(
                """
                UPDATE mission_tasks
                SET
                    status='Error',
                    result=?,
                    execution_token=NULL
                WHERE id=?
                  AND status='Running'
                  AND execution_token=?
                """,
                (
                    str(error),
                    task["id"],
                    execution_token,
                ),
            )

            if cursor.rowcount != 1:
                conn.rollback()
                raise RuntimeError(
                    f'Task {task["id"]} execution failed after its '
                    "execution ownership was lost."
                ) from error

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
        conn.execute("BEGIN IMMEDIATE")

        _assert_terminal_worker_ownership(
            conn,
            mission_id,
            worker_owner_token,
        )

        cursor = conn.execute(
            """
            UPDATE mission_tasks
            SET
                status='Completed',
                result=?,
                completed_at=CURRENT_TIMESTAMP,
                execution_token=NULL
            WHERE id=?
              AND status='Running'
              AND execution_token=?
            """,
            (
                result,
                task["id"],
                execution_token,
            ),
        )

        if cursor.rowcount != 1:
            conn.rollback()
            raise RuntimeError(
                f'Task {task["id"]} completion was rejected because '
                "its execution ownership was lost."
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

        if completed == total:
            progress = 99
        else:
            progress = 20 + int((completed / total) * 80)

        mission_status = "Running"

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
