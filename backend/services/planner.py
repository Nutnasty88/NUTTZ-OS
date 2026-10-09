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


def _is_http_service_mission(title: str) -> bool:
    normalized = title.lower()

    return any(
        marker in normalized
        for marker in (
            "fastapi",
            "http service",
            "api service",
            "web service",
        )
    )


def _validate_http_service_plan(
    mission_title: str,
    plan: str,
) -> None:
    """Reject HTTP plans that cannot use managed HTTP verification."""
    if not _is_http_service_mission(mission_title):
        return

    normalized = plan.lower()

    shell_markers = (
        "curl ",
        "grep ",
        "kill ",
        "pkill ",
        "nohup ",
        "uvicorn ",
        "python main.py &",
        "python3 main.py &",
        " | ",
        " && ",
        " ; ",
    )

    violations = [
        marker.strip()
        for marker in shell_markers
        if marker in normalized
    ]

    if violations:
        raise RuntimeError(
            "Planner HTTP contract violation: shell-style HTTP "
            "verification is not allowed: "
            + ", ".join(violations)
        )

    has_http_request = any(
        line.strip().upper().startswith(
            ("HTTP GET ", "HTTP POST ")
        )
        for line in plan.splitlines()
    )

    if not has_http_request:
        raise RuntimeError(
            "Planner HTTP contract violation: structured HTTP "
            "GET/POST verification is required."
        )

    has_http_status = any(
        line.strip().lower().startswith(
            "http status must equal "
        )
        for line in plan.splitlines()
    )

    if not has_http_status:
        raise RuntimeError(
            "Planner HTTP contract violation: each HTTP success "
            "contract requires an HTTP status requirement."
        )

    persistence_required = any(
        marker in mission_title.lower()
        for marker in (
            "restart",
            "persistent",
            "persistence",
            "still exists",
        )
    )

    if persistence_required:
        has_restart = any(
            line.strip().lower() == "restart service"
            for line in plan.splitlines()
        )

        if not has_restart:
            raise RuntimeError(
                "Planner HTTP contract violation: persistent HTTP "
                "missions require an explicit Restart service step."
            )


def create_plan(
    mission_id: int,
    revision_feedback: str | None = None,
) -> dict[str, Any]:
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

    revision_feedback = (
        revision_feedback or ""
    ).strip()

    current_plan = (
        get_plan(mission_id)
        if revision_feedback
        else None
    )

    if revision_feedback and current_plan is None:
        raise ValueError(
            f"Mission {mission_id} has no existing plan to revise."
        )

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
- When the mission specifies exact expected output, preserve that requirement
  explicitly in the success-check. Do not leave exact-output requirements only
  in the mission title or summarize them away.
- Express exact stdout deterministically using wording such as:
  stdout must equal "Buy milk"
  so the Executor can verify the expected output against execution evidence.
- For HTTP service missions, do not express API verification as shell commands
  such as curl, grep, pipes, background processes, kill commands, or other
  process-control syntax.
- For HTTP service success-checks, describe each request as structured HTTP
  evidence using the method and path, for example:
  HTTP POST /tasks
  JSON body must equal {"title":"Buy milk"}
  HTTP status must equal 200
- Describe expected HTTP response bodies as JSON response requirements, not
  stdout requirements.
- When an HTTP mission requires persistence across a service restart, explicitly
  require a service restart followed by the HTTP request that verifies the
  persisted state.
- For persistent SQLite HTTP applications, use Python's standard-library
  sqlite3 module unless the mission explicitly requires a third-party ORM or
  database library. Do not introduce SQLAlchemy merely to implement SQLite.
- Keep the plan focused and practical.
""".strip()

    if revision_feedback:
        user_prompt = f"""
Revise the existing execution plan for this NUTTZ-OS mission.

Mission ID: {mission["id"]}
Mission title: {mission["title"]}
Assigned agent: {mission["assigned_agent"]}
Priority: {mission["priority"]}
Current status: {mission["status"]}

Existing plan:
{current_plan["plan"]}

Operator revision request:
{revision_feedback}

Return a complete replacement plan, not a partial amendment.
Preserve all original mission requirements unless the operator
explicitly requests a change. Incorporate the revision request
while continuing to follow every Planner Agent rule.
""".strip()
    else:
        user_prompt = f"""
Create an execution plan for this NUTTZ-OS mission.

Mission ID: {mission["id"]}
Mission title: {mission["title"]}
Assigned agent: {mission["assigned_agent"]}
Priority: {mission["priority"]}
Current status: {mission["status"]}
""".strip()

    messages = [
        {
            "role": "system",
            "content": system_prompt,
        },
        {
            "role": "user",
            "content": user_prompt,
        },
    ]

    response = chat_with_ollama(
        model=PLANNER_MODEL,
        messages=messages,
        stream=False,
        timeout=300,
    )

    plan = extract_plan(response)

    try:
        _validate_http_service_plan(
            mission_title=mission["title"],
            plan=plan,
        )
    except RuntimeError as error:
        if not _is_http_service_mission(
            mission["title"]
        ):
            raise

        correction_prompt = f"""
Your previous plan violated the NUTTZ-OS HTTP Planner contract.

Violation:
{error}

Return a corrected complete execution plan.

For HTTP verification:
- Do not use curl, grep, pipes, uvicorn commands, background
  processes, kill commands, or shell process control.
- Use structured HTTP request evidence.
- Write HTTP GET or HTTP POST followed by the relative path.
- Write HTTP status must equal N for every verified request.
- Use JSON body must equal for request bodies.
- Use JSON response must equal for expected response bodies.
- When persistence across restart must be verified, the exact
  standalone line below is mandatory:

Restart service

- The words above must appear exactly as `Restart service` on a
  line by themselves. Do not replace them with phrases such as
  "restart the server", "restart the application", or shell
  commands.
- Put `Restart service` immediately before the HTTP request that
  verifies state after the restart.

Return only the corrected complete plan.
""".strip()

        repair_messages = [
            *messages,
            {
                "role": "assistant",
                "content": plan,
            },
            {
                "role": "user",
                "content": correction_prompt,
            },
        ]

        repair_response = chat_with_ollama(
            model=PLANNER_MODEL,
            messages=repair_messages,
            stream=False,
            timeout=300,
        )

        plan = extract_plan(repair_response)

        _validate_http_service_plan(
            mission_title=mission["title"],
            plan=plan,
        )

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
        (
            "Mission plan revised"
            if revision_feedback
            else "Mission plan created"
        ),
    )

    return {
        "mission_id": mission_id,
        "model": PLANNER_MODEL,
        "status": "Ready",
        "plan": plan,
    }


def propose_replan(
    mission_id: int,
    *,
    completed_work: str,
    triggering_evidence: str,
    reason: str,
) -> dict[str, Any]:
    """
    Generate a read-only Mission Replanning v2 proposal.

    This function does not mutate mission_plans or mission_tasks.
    The returned replacement plan must still pass the Executor
    replan preview before it can be applied.
    """
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

        if mission is None:
            raise ValueError(
                f"Mission {mission_id} was not found."
            )

        current_plan_row = conn.execute(
            """
            SELECT plan
            FROM mission_plans
            WHERE mission_id=?
            """,
            (mission_id,),
        ).fetchone()

    finally:
        conn.close()

    if current_plan_row is None:
        raise ValueError(
            f"Mission {mission_id} has no existing plan to replan."
        )

    completed_work = (completed_work or "").strip()
    triggering_evidence = (
        triggering_evidence or ""
    ).strip()
    reason = (reason or "").strip()

    current_plan = str(current_plan_row["plan"])

    system_prompt = """
You are Planner Agent v1 inside NUTTZ-OS.

You are generating a Mission Replanning v2 proposal after execution
has already begun.

Rules:
- Return only the finished replacement plan.
- Do not reveal internal reasoning.
- Do not include <think> tags.
- Return a complete numbered execution plan, not a partial amendment.
- Completed work is immutable history.
- Preserve every completed task in the existing plan exactly.
- Do not rewrite, renumber, remove, or reinterpret completed work.
- Replace only the remaining unstarted Pending portion when evidence
  shows that the old remaining plan is no longer appropriate.
- Keep numbered task positions contiguous and in execution order.
- Give each new Pending task a short title and clear action.
- For any task that creates or changes application behavior, explicitly
  describe it as an implementation task using a verb such as Implement,
  Modify, Update, or Create.
- Every implementation task must explicitly name the source file it
  creates or changes.
- Keep execution or verification tasks separate from implementation
  tasks.
- Treat databases and other mutable runtime state as runtime artifacts,
  not Builder-created files.
- For SQLite-backed applications, implementation source code must create
  or initialize the database at runtime. Do not plan direct synthesis of
  a .db, .sqlite, or .sqlite3 file.
- When persistent state is required, use file-backed SQLite rather than
  an in-memory database unless the mission explicitly requires otherwise.
- Preserve concrete mission arguments and exact-output requirements.
- Express exact stdout deterministically using wording such as:
  stdout must equal "Buy milk"
- For HTTP verification, use structured HTTP evidence rather than curl,
  grep, pipes, background processes, kill commands, or shell process
  control.
- For HTTP requests, specify the HTTP method, relative path, expected
  status, request JSON when applicable, and expected response JSON.
- If HTTP persistence across restart must be verified, use the exact
  standalone line:
  Restart service
- Include a final success-check section when required by the mission.
- Keep the proposal focused and practical.
""".strip()

    user_prompt = f"""
Generate a proposed replacement execution plan for this NUTTZ-OS
mission.

Mission ID: {mission["id"]}
Mission title: {mission["title"]}
Assigned agent: {mission["assigned_agent"]}
Priority: {mission["priority"]}
Current status: {mission["status"]}

Existing stored plan:
{current_plan}

Completed work that must remain immutable:
{completed_work or "(none supplied)"}

Reason replanning is being considered:
{reason or "(none supplied)"}

Triggering execution evidence:
{triggering_evidence or "(none supplied)"}

Return a complete replacement plan.

The completed prefix must remain exactly compatible with the existing
stored plan. Revise only the remaining unstarted Pending work.

This is a proposal only. Do not claim that it has been applied,
approved, executed, or persisted.
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

    replacement_plan = extract_plan(response)

    _validate_http_service_plan(
        mission_title=mission["title"],
        plan=replacement_plan,
    )

    return {
        "mission_id": mission_id,
        "replacement_plan": replacement_plan,
        "reason": reason,
        "triggering_evidence": triggering_evidence,
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
