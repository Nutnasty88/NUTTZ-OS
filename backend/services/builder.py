import json
import re
from typing import Any

from app.services.events import log_event
from services.ollama_service import chat_with_ollama
from services.workspace_manager import (
    create_workspace,
    get_workspace,
    list_workspace_files,
    read_workspace_file,
    write_workspace_file,
)


BUILDER_MODEL = "qwen3:8b"

MAX_BUILDER_FILES = 20


def _workspace_name(mission_id: int) -> str:
    return f"mission-{mission_id}"


def _extract_content(response: dict[str, Any]) -> str:
    if response.get("status") == "error":
        raise RuntimeError(
            response.get(
                "error",
                "Unknown Ollama Builder error.",
            )
        )

    message = response.get("message")

    if not isinstance(message, dict):
        raise RuntimeError(
            "Builder Agent received no Ollama message."
        )

    content = message.get("content", "").strip()

    if not content:
        raise RuntimeError(
            "Builder Agent returned an empty response."
        )

    return content


def _strip_code_fence(value: str) -> str:
    value = value.strip()

    if value.startswith("```"):
        value = re.sub(
            r"^```(?:json)?\s*",
            "",
            value,
            flags=re.IGNORECASE,
        )

        value = re.sub(
            r"\s*```$",
            "",
            value,
        )

    return value.strip()


def _parse_builder_response(
    content: str,
) -> dict[str, Any]:
    cleaned = _strip_code_fence(content)

    try:
        payload = json.loads(cleaned)
    except json.JSONDecodeError as error:
        raise RuntimeError(
            "Builder Agent did not return valid JSON."
        ) from error

    if not isinstance(payload, dict):
        raise RuntimeError(
            "Builder Agent response must be a JSON object."
        )

    files = payload.get("files")

    if not isinstance(files, list):
        raise RuntimeError(
            'Builder Agent response requires a "files" list.'
        )

    if len(files) > MAX_BUILDER_FILES:
        raise RuntimeError(
            "Builder Agent requested too many file writes."
        )

    normalized_files = []

    for index, file_entry in enumerate(files, start=1):
        if not isinstance(file_entry, dict):
            raise RuntimeError(
                f"Builder file entry {index} is invalid."
            )

        path = file_entry.get("path")
        file_content = file_entry.get("content")

        if not isinstance(path, str) or not path.strip():
            raise RuntimeError(
                f"Builder file entry {index} has no valid path."
            )

        if not isinstance(file_content, str):
            raise RuntimeError(
                f"Builder file entry {index} has no text content."
            )

        normalized_files.append(
            {
                "path": path.strip(),
                "content": file_content,
            }
        )

    summary = payload.get("summary", "")

    if not isinstance(summary, str):
        summary = str(summary)

    return {
        "summary": summary.strip(),
        "files": normalized_files,
    }


def _workspace_context(
    workspace_name: str,
) -> str:
    listing = list_workspace_files(workspace_name)

    files = listing.get("files", [])

    if not files:
        return "Workspace currently contains no files."

    lines = [
        "Existing workspace files:",
    ]

    for item in files[:MAX_BUILDER_FILES]:
        if isinstance(item, dict):
            path = item.get("path", "")
            size = item.get("size_bytes", "")

            lines.append(
                f"- {path} ({size} bytes)"
            )

    return "\n".join(lines)


def build_task(
    mission_id: int,
    mission_title: str,
    task_id: int,
    task_position: int,
    task_title: str,
    task_instructions: str,
) -> dict[str, Any]:
    workspace_name = _workspace_name(mission_id)

    log_event(
        mission_id,
        "Builder",
        "started",
        (
            f"Builder started task {task_position}: "
            f"{task_title}"
        ),
    )

    try:
        try:
            workspace = get_workspace(workspace_name)
        except Exception:
            workspace = create_workspace(workspace_name)

        context = _workspace_context(workspace_name)

        system_prompt = """
You are Builder Agent v1 inside NUTTZ-OS.

You create or modify UTF-8 text files inside an isolated Builder
workspace.

You do NOT have direct filesystem access.

NUTTZ-OS will validate and write every file you request through its
Workspace Manager.

Return JSON only.

Required format:

{
  "summary": "short description of what was built",
  "files": [
    {
      "path": "relative/path/to/file",
      "content": "complete file contents"
    }
  ]
}

Rules:
- Return valid JSON only.
- Do not use markdown code fences.
- Do not reveal internal reasoning.
- Do not include <think> tags.
- Every path must be relative to the workspace.
- Never request absolute paths.
- Never request ../ path traversal.
- Never write .git or .env files.
- Return complete file contents, not patches.
- Keep the implementation focused on the assigned task.
- Do not claim files were written. NUTTZ-OS performs the writes after
  your response is validated.
""".strip()

        user_prompt = f"""
Mission ID: {mission_id}
Mission title: {mission_title}

Task ID: {task_id}
Task number: {task_position}
Task title: {task_title}

Task instructions:
{task_instructions}

Builder workspace:
{workspace_name}

{context}

Create the files required to complete this task.
""".strip()

        response = chat_with_ollama(
            model=BUILDER_MODEL,
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
        )

        content = _extract_content(response)
        build_plan = _parse_builder_response(content)

        written_files = []

        for file_entry in build_plan["files"]:
            write_result = write_workspace_file(
                workspace_name,
                file_entry["path"],
                file_entry["content"],
            )

            verification = read_workspace_file(
                workspace_name,
                file_entry["path"],
            )

            if (
                verification["sha256"]
                != write_result["sha256"]
            ):
                raise RuntimeError(
                    "Builder artifact verification failed for "
                    f'{file_entry["path"]}.'
                )

            written_files.append(
                {
                    "path": write_result["path"],
                    "created": write_result["created"],
                    "size_bytes": write_result["size_bytes"],
                    "sha256": write_result["sha256"],
                    "verified": True,
                }
            )

        final_workspace = get_workspace(workspace_name)

        evidence = {
            "type": "builder_workspace_artifact",
            "verified": True,
            "workspace": workspace_name,
            "workspace_path": final_workspace.get("path"),
            "file_count": len(written_files),
            "files": written_files,
        }

        log_event(
            mission_id,
            "Builder",
            "completed",
            (
                f"Builder completed task {task_position} "
                f"with {len(written_files)} verified artifact(s)"
            ),
        )

        return {
            "mission_id": mission_id,
            "task_id": task_id,
            "position": task_position,
            "title": task_title,
            "status": "Completed",
            "model": BUILDER_MODEL,
            "summary": build_plan["summary"],
            "workspace": workspace_name,
            "artifacts": written_files,
            "evidence": evidence,
        }

    except Exception as error:
        log_event(
            mission_id,
            "Builder",
            "error",
            (
                f"Builder failed task {task_position}: "
                f"{error}"
            ),
        )

        raise


def repair_artifact(
    mission_id: int,
    mission_title: str,
    task_id: int,
    task_position: int,
    task_title: str,
    task_instructions: str,
    artifact_path: str,
    execution_evidence: dict[str, Any],
) -> dict[str, Any]:
    """
    Repair one failed Builder artifact using verified execution evidence.

    Builder proposes complete replacement contents.
    Workspace Manager performs and verifies the actual write.
    """
    workspace_name = _workspace_name(mission_id)

    if not isinstance(execution_evidence, dict):
        raise RuntimeError(
            "Builder repair requires structured execution evidence."
        )

    if execution_evidence.get("verified"):
        raise RuntimeError(
            "Builder repair was requested for an already verified artifact."
        )

    current_artifact = read_workspace_file(
        workspace_name,
        artifact_path,
    )

    current_content = current_artifact["content"]

    log_event(
        mission_id,
        "Builder",
        "repairing",
        (
            f"Builder repairing task {task_position} artifact "
            f"{artifact_path} from verified execution failure"
        ),
    )

    system_prompt = """
You are Builder Agent v1 repair mode inside NUTTZ-OS.

A Python artifact in an isolated Builder workspace was executed by the
NUTTZ-OS Workspace Executor and failed verification.

Repair the artifact using the supplied execution evidence.

You do NOT have direct filesystem or execution access.

NUTTZ-OS will validate and write the replacement through Workspace
Manager and independently execute it again afterward.

Return JSON only.

Required format:

{
  "summary": "short description of the repair",
  "files": [
    {
      "path": "relative/path/to/file.py",
      "content": "complete replacement file contents"
    }
  ]
}

Rules:
- Return valid JSON only.
- Do not use markdown code fences.
- Do not reveal internal reasoning.
- Do not include <think> tags.
- Repair only the supplied artifact.
- Return exactly one file.
- The path must exactly match the supplied artifact path.
- Return complete replacement contents, not a patch.
- Treat stdout, stderr, exit code and timeout data as factual evidence.
- Do not invent execution results.
- Do not claim the repair succeeded.
""".strip()

    evidence_json = json.dumps(
        execution_evidence,
        indent=2,
        sort_keys=True,
    )

    user_prompt = f"""
Mission ID: {mission_id}
Mission title: {mission_title}

Task ID: {task_id}
Task number: {task_position}
Task title: {task_title}

Original task instructions:
{task_instructions}

Artifact requiring repair:
{artifact_path}

Current artifact SHA256:
{current_artifact["sha256"]}

Current artifact contents:
--- BEGIN CURRENT ARTIFACT ---
{current_content}
--- END CURRENT ARTIFACT ---

Verified Workspace Executor failure evidence:
{evidence_json}

Repair this artifact so NUTTZ-OS can execute it again.
""".strip()

    try:
        response = chat_with_ollama(
            model=BUILDER_MODEL,
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
        )

        content = _extract_content(response)
        repair_plan = _parse_builder_response(content)

        files = repair_plan["files"]

        if len(files) != 1:
            raise RuntimeError(
                "Builder repair must return exactly one file."
            )

        file_entry = files[0]

        if file_entry["path"] != artifact_path:
            raise RuntimeError(
                "Builder repair attempted to modify a different artifact."
            )

        write_result = write_workspace_file(
            workspace_name,
            artifact_path,
            file_entry["content"],
        )

        verification = read_workspace_file(
            workspace_name,
            artifact_path,
        )

        if verification["sha256"] != write_result["sha256"]:
            raise RuntimeError(
                "Builder repair artifact verification failed."
            )

        if verification["sha256"] == current_artifact["sha256"]:
            raise RuntimeError(
                "Builder repair did not change the failed artifact."
            )

        repair_evidence = {
            "type": "builder_workspace_repair",
            "verified": True,
            "workspace": workspace_name,
            "artifact": artifact_path,
            "previous_sha256": current_artifact["sha256"],
            "repaired_sha256": verification["sha256"],
            "size_bytes": verification["size_bytes"],
        }

        log_event(
            mission_id,
            "Builder",
            "repaired",
            (
                f"Builder repaired task {task_position} artifact "
                f"{artifact_path}"
            ),
        )

        return {
            "mission_id": mission_id,
            "task_id": task_id,
            "position": task_position,
            "title": task_title,
            "status": "Repaired",
            "model": BUILDER_MODEL,
            "summary": repair_plan["summary"],
            "workspace": workspace_name,
            "artifact": {
                "path": artifact_path,
                "created": write_result["created"],
                "size_bytes": verification["size_bytes"],
                "sha256": verification["sha256"],
                "verified": True,
            },
            "evidence": repair_evidence,
        }

    except Exception as error:
        log_event(
            mission_id,
            "Builder",
            "repair_error",
            (
                f"Builder failed repair for task {task_position} "
                f"artifact {artifact_path}: {error}"
            ),
        )

        raise
