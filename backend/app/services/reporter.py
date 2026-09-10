import json
from typing import Any

from app.database.database import get_connection
from app.services.events import log_event
from services.executor import _assert_terminal_worker_ownership
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
                claims_json TEXT NOT NULL DEFAULT '[]',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (mission_id) REFERENCES missions(id)
            )
            """
        )

        columns = {
            row["name"]
            for row in conn.execute(
                "PRAGMA table_info(mission_deliverables)"
            ).fetchall()
        }

        if "claims_json" not in columns:
            conn.execute(
                """
                ALTER TABLE mission_deliverables
                ADD COLUMN claims_json TEXT
                NOT NULL DEFAULT '[]'
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


def _parsed_verified_evidence(
    result: str,
    *,
    header: str,
    evidence_marker: str,
    require_service_stopped: bool = False,
) -> dict[str, Any] | None:
    if not result.startswith(header + "\n"):
        return None

    marker = evidence_marker + "\n"

    if marker not in result:
        return None

    evidence_text = result.split(marker, 1)[1]
    decoder = json.JSONDecoder()

    try:
        evidence, _ = decoder.raw_decode(
            evidence_text.lstrip()
        )
    except (json.JSONDecodeError, TypeError):
        return None

    if not isinstance(evidence, dict):
        return None

    if evidence.get("verified") is not True:
        return None

    if (
        require_service_stopped
        and evidence.get("service_stopped") is not True
    ):
        return None

    return evidence


def _is_non_bool_int(value: Any) -> bool:
    return (
        isinstance(value, int)
        and not isinstance(value, bool)
    )


def _is_sha256(value: Any) -> bool:
    if not isinstance(value, str):
        return False

    if len(value) != 64:
        return False

    return all(
        char in "0123456789abcdefABCDEF"
        for char in value
    )


def _valid_artifact_identity(
    artifact: Any,
    sha256: Any,
    size_bytes: Any,
) -> bool:
    return (
        isinstance(artifact, str)
        and bool(artifact.strip())
        and _is_sha256(sha256)
        and _is_non_bool_int(size_bytes)
        and size_bytes >= 0
    )


def _task_verified_facts(
    task: dict[str, Any],
) -> list[dict[str, Any]]:
    if task.get("status") != "Completed":
        return []

    result = task.get("result", "") or ""
    position = task.get("position")
    facts: list[dict[str, Any]] = []

    builder = _parsed_verified_evidence(
        result,
        header="BUILDER AGENT: COMPLETED",
        evidence_marker="VERIFIED BUILDER EVIDENCE:",
    )

    if builder is not None:
        artifact = builder.get("entrypoint")
        sha256 = builder.get("entrypoint_sha256")
        size_bytes = builder.get(
            "entrypoint_size_bytes"
        )

        if _valid_artifact_identity(
            artifact,
            sha256,
            size_bytes,
        ):
            facts.append(
                {
                    "id": f"task-{position}:artifact",
                    "type": "artifact_verified",
                    "task_position": position,
                    "evidence_type": "builder_verified",
                    "artifact": artifact,
                    "sha256": sha256,
                    "size_bytes": size_bytes,
                }
            )

    workspace = _parsed_verified_evidence(
        result,
        header="WORKSPACE EXECUTION: VERIFIED",
        evidence_marker="VERIFIED EXECUTION EVIDENCE:",
    )

    if workspace is not None:
        artifact = workspace.get("artifact")
        sha256 = workspace.get("artifact_sha256")
        size_bytes = workspace.get(
            "artifact_size_bytes"
        )
        exit_code = workspace.get("exit_code")
        status = workspace.get("status")

        single_execution_verified = (
            workspace.get("type")
            == "builder_workspace_execution"
            and _valid_artifact_identity(
                artifact,
                sha256,
                size_bytes,
            )
            and status == "success"
            and _is_non_bool_int(exit_code)
            and exit_code == 0
        )

        sequence_execution_verified = False

        steps = workspace.get("steps")
        step_count = workspace.get("step_count")
        requested_step_count = workspace.get(
            "requested_step_count"
        )

        if (
            isinstance(steps, list)
            and steps
            and _is_non_bool_int(step_count)
            and _is_non_bool_int(requested_step_count)
            and step_count == len(steps)
            and requested_step_count == len(steps)
            and _valid_artifact_identity(
                artifact,
                sha256,
                size_bytes,
            )
            and _is_non_bool_int(exit_code)
            and exit_code == 0
        ):
            valid_sequence_steps = True

            for step in steps:
                if not isinstance(step, dict):
                    valid_sequence_steps = False
                    break

                if (
                    step.get("type")
                    != "builder_workspace_execution"
                    or step.get("verified") is not True
                    or step.get("status") != "success"
                    or not _is_non_bool_int(
                        step.get("exit_code")
                    )
                    or step.get("exit_code") != 0
                    or not _valid_artifact_identity(
                        step.get("artifact"),
                        step.get("artifact_sha256"),
                        step.get(
                            "artifact_size_bytes"
                        ),
                    )
                    or step.get("artifact") != artifact
                    or step.get(
                        "artifact_sha256"
                    ) != sha256
                    or step.get(
                        "artifact_size_bytes"
                    ) != size_bytes
                ):
                    valid_sequence_steps = False
                    break

            sequence_execution_verified = (
                valid_sequence_steps
            )

        if (
            single_execution_verified
            or sequence_execution_verified
        ):
            facts.append(
                {
                    "id": f"task-{position}:execution",
                    "type": "execution_verified",
                    "task_position": position,
                    "evidence_type": "workspace_verified",
                    "artifact": artifact,
                    "sha256": sha256,
                    "size_bytes": size_bytes,
                    "exit_code": exit_code,
                    "stdout": workspace.get(
                        "stdout",
                        "",
                    ),
                }
            )

    http = _parsed_verified_evidence(
        result,
        header="HTTP SERVICE EXECUTION: VERIFIED",
        evidence_marker=(
            "VERIFIED HTTP SERVICE EVIDENCE:"
        ),
        require_service_stopped=True,
    )

    if http is not None:
        artifact = http.get("artifact")
        sha256 = http.get("artifact_sha256")
        size_bytes = http.get(
            "artifact_size_bytes"
        )
        requested_check_count = http.get(
            "requested_check_count"
        )
        check_count = http.get("check_count")
        restart_count = http.get("restart_count")
        steps = http.get("steps")

        http_structure_verified = (
            http.get("type")
            == "builder_workspace_http_service"
            and _valid_artifact_identity(
                artifact,
                sha256,
                size_bytes,
            )
            and isinstance(steps, list)
            and _is_non_bool_int(
                requested_check_count
            )
            and requested_check_count >= 0
            and _is_non_bool_int(check_count)
            and check_count >= 0
            and requested_check_count
            == check_count
            == len(steps)
            and _is_non_bool_int(restart_count)
            and restart_count >= 0
        )

        valid_steps: list[dict[str, Any]] = []
        indexes: set[int] = set()

        if http_structure_verified:
            for step in steps:
                if not isinstance(step, dict):
                    http_structure_verified = False
                    break

                index = step.get("index")
                method = step.get("method")
                request_path = step.get("path")
                status_code = step.get(
                    "status_code"
                )
                expected_status = step.get(
                    "expected_status"
                )
                restart_before = step.get(
                    "restart_before"
                )

                if step.get("verified") is not True:
                    http_structure_verified = False
                    break

                if (
                    not _is_non_bool_int(index)
                    or index <= 0
                    or index in indexes
                ):
                    http_structure_verified = False
                    break

                if method not in {"GET", "POST"}:
                    http_structure_verified = False
                    break

                if (
                    not isinstance(
                        request_path,
                        str,
                    )
                    or not request_path.startswith("/")
                ):
                    http_structure_verified = False
                    break

                if (
                    not _is_non_bool_int(
                        status_code
                    )
                    or not _is_non_bool_int(
                        expected_status
                    )
                    or status_code
                    != expected_status
                ):
                    http_structure_verified = False
                    break

                if not isinstance(
                    restart_before,
                    bool,
                ):
                    http_structure_verified = False
                    break

                expected_json = step.get(
                    "expected_json"
                )

                if (
                    expected_json is not None
                    and step.get("response_json")
                    != expected_json
                ):
                    http_structure_verified = False
                    break

                indexes.add(index)
                valid_steps.append(step)

        if (
            http_structure_verified
            and indexes
            != set(
                range(
                    1,
                    len(steps) + 1,
                )
            )
        ):
            http_structure_verified = False

        actual_restart_count = sum(
            1
            for step in valid_steps
            if step.get("restart_before") is True
        )

        if (
            http_structure_verified
            and restart_count
            != actual_restart_count
        ):
            http_structure_verified = False

        if http_structure_verified:
            for step in valid_steps:
                facts.append(
                    {
                        "id": (
                            f"task-{position}:"
                            f"http-check:{step['index']}"
                        ),
                        "type": (
                            "http_check_verified"
                        ),
                        "task_position": position,
                        "evidence_type": (
                            "http_service_verified"
                        ),
                        "check_index": step["index"],
                        "method": step["method"],
                        "path": step["path"],
                        "status_code": (
                            step["status_code"]
                        ),
                        "expected_status": (
                            step["expected_status"]
                        ),
                        "response_json": step.get(
                            "response_json"
                        ),
                        "expected_json": step.get(
                            "expected_json"
                        ),
                        "restart_before": (
                            step["restart_before"]
                        ),
                    }
                )

            if restart_count > 0:
                facts.append(
                    {
                        "id": (
                            f"task-{position}:"
                            "service-restart"
                        ),
                        "type": (
                            "service_restart_verified"
                        ),
                        "task_position": position,
                        "evidence_type": (
                            "http_service_verified"
                        ),
                        "restart_count": (
                            restart_count
                        ),
                    }
                )

            facts.append(
                {
                    "id": (
                        f"task-{position}:"
                        "service-stopped"
                    ),
                    "type": (
                        "service_stopped_verified"
                    ),
                    "task_position": position,
                    "evidence_type": (
                        "http_service_verified"
                    ),
                }
            )

    return facts




def _verified_evidence_block(
    result: str,
    *,
    header: str,
    evidence_marker: str,
    require_service_stopped: bool = False,
) -> bool:
    if not result.startswith(header + "\n"):
        return False

    marker = evidence_marker + "\n"

    if marker not in result:
        return False

    evidence_text = result.split(marker, 1)[1]

    decoder = json.JSONDecoder()

    try:
        evidence, _ = decoder.raw_decode(
            evidence_text.lstrip()
        )
    except (json.JSONDecodeError, TypeError):
        return False

    if not isinstance(evidence, dict):
        return False

    if evidence.get("verified") is not True:
        return False

    if (
        require_service_stopped
        and evidence.get("service_stopped") is not True
    ):
        return False

    return True


def _task_provenance(
    task: dict[str, Any],
) -> dict[str, Any]:
    result = task.get("result", "") or ""

    evidence_types: list[str] = []

    if task.get("status") != "Completed":
        return {
            "position": task.get("position"),
            "status": task.get("status"),
            "evidence_types": [],
            "verified": False,
        }

    if _verified_evidence_block(
        result,
        header="BUILDER AGENT: COMPLETED",
        evidence_marker="VERIFIED BUILDER EVIDENCE:",
    ):
        evidence_types.append("builder_verified")

    if _verified_evidence_block(
        result,
        header="WORKSPACE EXECUTION: VERIFIED",
        evidence_marker="VERIFIED EXECUTION EVIDENCE:",
    ):
        evidence_types.append("workspace_verified")

    if _verified_evidence_block(
        result,
        header="HTTP SERVICE EXECUTION: VERIFIED",
        evidence_marker="VERIFIED HTTP SERVICE EVIDENCE:",
        require_service_stopped=True,
    ):
        evidence_types.append("http_service_verified")

    return {
        "position": task.get("position"),
        "status": task.get("status"),
        "evidence_types": evidence_types,
        "verified": bool(evidence_types),
    }


def _task_provenance_map(
    tasks: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    return [
        _task_provenance(task)
        for task in tasks
    ]


def _verified_facts_map(
    tasks: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    facts: list[dict[str, Any]] = []

    for task in tasks:
        facts.extend(
            _task_verified_facts(task)
        )

    return facts




def _parse_reporter_envelope(
    content: str,
) -> dict[str, Any]:
    try:
        envelope = json.loads(content)
    except (json.JSONDecodeError, TypeError) as exc:
        raise ValueError(
            "Reporter response must be valid JSON."
        ) from exc

    if not isinstance(envelope, dict):
        raise ValueError(
            "Reporter response must be a JSON object."
        )

    allowed_fields = {
        "claims",
    }
    unexpected = set(envelope) - allowed_fields

    if unexpected:
        raise ValueError(
            "Reporter response contains unexpected fields: "
            + ", ".join(sorted(unexpected))
        )

    claims = envelope.get("claims")

    if not isinstance(claims, list):
        raise ValueError(
            "Reporter response requires a claims list."
        )

    return {
        "claims": claims,
    }


def _validate_claim_provenance(
    claims: Any,
    task_provenance: list[dict[str, Any]],
    verified_facts: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    if not isinstance(claims, list):
        raise ValueError(
            "Reporter claims must be a list."
        )

    provenance_by_position = {
        item.get("position"): item
        for item in task_provenance
    }

    facts_by_id: dict[str, dict[str, Any]] = {}

    for fact in verified_facts:
        if not isinstance(fact, dict):
            continue

        fact_id = fact.get("id")

        if (
            not isinstance(fact_id, str)
            or not fact_id.strip()
        ):
            continue

        if fact_id in facts_by_id:
            raise ValueError(
                "Reporter verified facts contain duplicate "
                f"verified fact id {fact_id!r}."
            )

        facts_by_id[fact_id] = fact

    for claim in claims:
        if not isinstance(claim, dict):
            raise ValueError(
                "Each Reporter claim must be an object."
            )

        claim_text = claim.get("text")

        if (
            not isinstance(claim_text, str)
            or not claim_text.strip()
        ):
            raise ValueError(
                "Each Reporter claim requires text."
            )

        supported_by = claim.get("supported_by")

        if (
            not isinstance(supported_by, list)
            or not supported_by
        ):
            raise ValueError(
                "Each Reporter claim requires at least one "
                "provenance reference."
            )

        for reference in supported_by:
            if not isinstance(reference, dict):
                raise ValueError(
                    "Claim provenance reference must "
                    "be an object."
                )

            fact_id = reference.get("fact_id")

            if (
                not isinstance(fact_id, str)
                or not fact_id.strip()
            ):
                raise ValueError(
                    "Claim provenance fact_id must be "
                    "a non-empty string."
                )

            fact = facts_by_id.get(fact_id)

            if fact is None:
                raise ValueError(
                    "Claim references unknown verified fact "
                    f"{fact_id!r}."
                )

            position = fact.get("task_position")
            evidence_type = fact.get("evidence_type")

            task = provenance_by_position.get(position)

            if task is None:
                raise ValueError(
                    "Claim fact references unknown task position "
                    f"{position!r}."
                )

            if task.get("status") != "Completed":
                raise ValueError(
                    "Claim fact references task that is "
                    "not completed."
                )

            if task.get("verified") is not True:
                raise ValueError(
                    "Claim fact references task that is "
                    "not verified."
                )

            evidence_types = task.get(
                "evidence_types",
                [],
            )

            if evidence_type not in evidence_types:
                raise ValueError(
                    "Claim fact references evidence type "
                    "not verified for task."
                )

    return claims


def _render_verified_claims(
    claims: list[dict[str, Any]],
) -> str:
    if not isinstance(claims, list) or not claims:
        raise ValueError(
            "Reporter requires at least one verified claim."
        )

    lines = [
        "## Verified Results",
        "",
    ]

    for claim in claims:
        if not isinstance(claim, dict):
            raise ValueError(
                "Reporter claim must be an object."
            )

        claim_text = claim.get("text")

        if (
            not isinstance(claim_text, str)
            or not claim_text.strip()
        ):
            raise ValueError(
                "Reporter claim text must be non-empty."
            )

        lines.append(
            f"- {claim_text.strip()}"
        )

    return "\n".join(lines)



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


def create_deliverable(
    mission_id: int,
    worker_owner_token: str | None = None,
) -> dict[str, Any]:
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

Your job is to synthesize the completed mission into concise
evidence-backed claims for deterministic final rendering.

Rules:
- Return only one valid JSON object with exactly one field:
  claims.
- Do not wrap the JSON in Markdown fences.
- claims must be a JSON list of factual claims with verified fact references.
- Each claim must contain text and supported_by.
- Each supported_by entry must contain fact_id referencing an exact verified_facts entry.
- Do not reveal internal reasoning.
- Do not include <think> tags.
- Use the supplied mission evidence.
- Do not invent actions, tests, files, commands, sources, or results.
- Return only concise factual claims supported by verified_facts.
- Include only the most important verified findings or completed work.
- Do not include headings, summaries, conclusions, or free-form report text.
- State limitations, risks, failures, missing capabilities, or unresolved
  items only when they are explicitly supported by the supplied mission
  evidence.
- Never infer a limitation merely because a feature, safeguard, test, or
  implementation detail is not mentioned in the evidence.
- Do not contradict verified mission evidence with speculative caveats.
- Treat task_provenance as machine-derived verification metadata. Describe a task as verified only when its provenance entry has verified=true, and limit that verification claim to the listed evidence_types.
- If the evidence contains no supported limitations or unresolved items,
  omit a limitations section entirely.
- Keep claim text concise.
- Prefer a small set of high-value verified claims over repeating every task.
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
        "task_provenance": _task_provenance_map(tasks),
        "verified_facts": _verified_facts_map(tasks),
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
            timeout=300,
        )

        content = _extract_content(response)

        envelope = _parse_reporter_envelope(
            content
        )
        validated_claims = _validate_claim_provenance(
            envelope["claims"],
            evidence["task_provenance"],
            evidence["verified_facts"],
        )

        deliverable_content = _render_verified_claims(
            validated_claims
        )

        claims_json = json.dumps(
            validated_claims,
            ensure_ascii=False,
            separators=(",", ":"),
        )

        conn = get_connection()

        try:
            conn.execute("BEGIN IMMEDIATE")

            _assert_terminal_worker_ownership(
                conn,
                mission_id,
                worker_owner_token,
            )

            conn.execute(
                """
                INSERT INTO mission_deliverables
                    (
                        mission_id,
                        model,
                        status,
                        content,
                        claims_json
                    )
                VALUES
                    (?, ?, 'Ready', ?, ?)
                ON CONFLICT(mission_id)
                DO UPDATE SET
                    model=excluded.model,
                    status='Ready',
                    content=excluded.content,
                    claims_json=excluded.claims_json,
                    updated_at=CURRENT_TIMESTAMP
                """,
                (
                    mission_id,
                    REPORTER_MODEL,
                    deliverable_content,
                    claims_json,
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
                claims_json,
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
        "claims": json.loads(
            row["claims_json"] or "[]"
        ),
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }
