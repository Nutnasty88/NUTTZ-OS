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

MAX_WORKSPACE_CONTEXT_BYTES = 24000


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

    entrypoint = payload.get("entrypoint")

    if entrypoint is not None:
        if (
            not isinstance(entrypoint, str)
            or not entrypoint.strip()
        ):
            raise RuntimeError(
                "Builder Agent entrypoint must be a non-empty "
                "relative path or null."
            )

        entrypoint = entrypoint.strip()

        if not entrypoint.lower().endswith(".py"):
            raise RuntimeError(
                "Builder Agent v1 currently requires a Python "
                "entrypoint."
            )

    return {
        "summary": summary.strip(),
        "entrypoint": entrypoint,
        "files": normalized_files,
    }


def _workspace_context(
    workspace_name: str,
) -> str:
    """
    Build bounded read-only project context for Builder.

    Builder can see existing UTF-8 project files so later tasks can
    extend a multi-file project coherently. NUTTZ-generated manifests
    are excluded because Builder must never author or modify them.
    """
    listing = list_workspace_files(workspace_name)

    files = listing.get("files", [])

    if not files:
        return "Workspace currently contains no files."

    sections = [
        "Existing workspace project files:",
    ]

    context_bytes = 0

    for item in files[:MAX_BUILDER_FILES]:
        if not isinstance(item, dict):
            continue

        relative_path = item.get("path", "")

        if not relative_path:
            continue

        if relative_path == "nuttz-project.json":
            continue

        try:
            file_data = read_workspace_file(
                workspace_name,
                relative_path,
            )
        except Exception:
            sections.append(
                f"\nFILE: {relative_path}\n"
                "[Unable to include file contents]"
            )
            continue

        content = file_data.get("content", "")

        if not isinstance(content, str):
            continue

        encoded = content.encode("utf-8")

        remaining = (
            MAX_WORKSPACE_CONTEXT_BYTES
            - context_bytes
        )

        if remaining <= 0:
            sections.append(
                "\n[Workspace context limit reached]"
            )
            break

        if len(encoded) > remaining:
            truncated = encoded[:remaining].decode(
                "utf-8",
                errors="ignore",
            )

            sections.append(
                f"\nFILE: {relative_path}\n"
                "--- BEGIN FILE ---\n"
                f"{truncated}\n"
                "--- END FILE (TRUNCATED) ---"
            )

            context_bytes += len(
                truncated.encode("utf-8")
            )

            sections.append(
                "\n[Workspace context limit reached]"
            )
            break

        sections.append(
            f"\nFILE: {relative_path}\n"
            "--- BEGIN FILE ---\n"
            f"{content}\n"
            "--- END FILE ---"
        )

        context_bytes += len(encoded)

    return "\n".join(sections)


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
  "entrypoint": "main.py or null",
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
- You may create multiple files when the task requires a project.
- For a runnable Python application or project, set "entrypoint" to the
  relative Python file that should be executed, normally main.py.
- For a task that does not define or maintain a runnable application,
  set "entrypoint" to null.
- The entrypoint may already exist in the workspace and does not need to
  be returned in "files" unless its contents must change.
- Prefer a clean multi-file structure when responsibilities should be
  separated into modules.
- Existing workspace file contents are read-only context. Return a file
  in the files list only when that file must be created or replaced.
- Preserve existing project behavior unless the task requires changing
  it.
- Keep imports consistent with the workspace file structure.
- The project entrypoint should import local modules normally when
  appropriate.
- Never create or modify nuttz-project.json. NUTTZ-OS owns the verified
  project manifest.
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

Create or update the files required to complete this task.

When the task represents a complete application or project, design the
workspace as a coherent multi-file project rather than forcing all
logic into one file.

Return only files that must be created or replaced.
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

        selected_entrypoint = build_plan.get(
            "entrypoint"
        )

        entrypoint_artifact = None

        if selected_entrypoint:
            try:
                entrypoint_artifact = read_workspace_file(
                    workspace_name,
                    selected_entrypoint,
                )
            except Exception as error:
                raise RuntimeError(
                    "Builder Agent selected an entrypoint that "
                    "does not exist in the workspace: "
                    f"{selected_entrypoint}"
                ) from error

            if not selected_entrypoint.lower().endswith(".py"):
                raise RuntimeError(
                    "Builder Agent selected a non-Python entrypoint."
                )

        final_workspace = get_workspace(workspace_name)

        evidence = {
            "type": "builder_workspace_artifact",
            "verified": True,
            "workspace": workspace_name,
            "workspace_path": final_workspace.get("path"),
            "file_count": len(written_files),
            "files": written_files,
            "entrypoint": selected_entrypoint,
        }

        if entrypoint_artifact:
            evidence["entrypoint_sha256"] = (
                entrypoint_artifact["sha256"]
            )
            evidence["entrypoint_size_bytes"] = (
                entrypoint_artifact["size_bytes"]
            )

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
            "entrypoint": selected_entrypoint,
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
    Repair a failed Builder project using verified execution evidence.

    Builder may replace up to three existing Python files when the
    failure crosses module boundaries. NUTTZ-OS validates every target,
    performs every write through Workspace Manager, and independently
    executes the original entrypoint afterward.
    """
    workspace_name = _workspace_name(mission_id)

    max_repair_files = 3

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

    listing = list_workspace_files(workspace_name)
    listed_files = listing.get("files", [])

    existing_python_paths = {
        item.get("path")
        for item in listed_files
        if (
            isinstance(item, dict)
            and isinstance(item.get("path"), str)
            and item["path"].lower().endswith(".py")
            and item["path"] != "nuttz-project.json"
        )
    }

    if artifact_path not in existing_python_paths:
        raise RuntimeError(
            "Builder repair entrypoint is not an existing Python "
            "workspace file."
        )

    workspace_context = _workspace_context(
        workspace_name,
    )

    log_event(
        mission_id,
        "Builder",
        "repairing",
        (
            f"Builder repairing task {task_position} project from "
            f"verified execution failure in {artifact_path}"
        ),
    )

    system_prompt = f"""
You are Builder Agent v1 project repair mode inside NUTTZ-OS.

A Python project in an isolated Builder workspace was executed by the
NUTTZ-OS Workspace Executor and failed verification.

Repair the project using the supplied execution evidence and existing
workspace project context.

The failure may originate in the entrypoint or in another existing
Python module imported by the entrypoint.

You do NOT have direct filesystem or execution access.

NUTTZ-OS will validate every requested file replacement, write it
through Workspace Manager, and independently execute the ORIGINAL
entrypoint again afterward.

Return JSON only.

Required format:

{{
  "summary": "short description of the project repair",
  "files": [
    {{
      "path": "existing/relative/file.py",
      "content": "complete replacement file contents"
    }}
  ]
}}

Rules:
- Return valid JSON only.
- Do not use markdown code fences.
- Do not reveal internal reasoning.
- Do not include <think> tags.
- Return between 1 and {max_repair_files} files.
- Repair only existing Python files shown in the workspace context.
- Do not create new files.
- Do not return duplicate file paths.
- Never create, modify, or return nuttz-project.json.
- Return complete replacement contents, not patches.
- Change only files needed to correct the verified failure.
- Treat stdout, stderr, traceback, exit code and timeout data as
  factual evidence.
- Use traceback/module information to identify which existing project
  files require correction.
- Do not invent execution results.
- Do not claim the repair succeeded.
- NUTTZ-OS will rerun the original entrypoint after your repair.
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

Original project entrypoint:
{artifact_path}

Current entrypoint SHA256:
{current_artifact["sha256"]}

Verified Workspace Executor failure evidence:
{evidence_json}

Read-only existing project context:
--- BEGIN PROJECT CONTEXT ---
{workspace_context}
--- END PROJECT CONTEXT ---

Repair the existing project files necessary to correct this failure.
NUTTZ-OS will execute the original entrypoint again afterward.
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

        if not files:
            raise RuntimeError(
                "Builder project repair returned no files."
            )

        if len(files) > max_repair_files:
            raise RuntimeError(
                "Builder project repair may modify at most "
                f"{max_repair_files} files."
            )

        requested_paths = [
            file_entry["path"]
            for file_entry in files
        ]

        if len(set(requested_paths)) != len(requested_paths):
            raise RuntimeError(
                "Builder project repair returned duplicate file paths."
            )

        for relative_path in requested_paths:
            if relative_path == "nuttz-project.json":
                raise RuntimeError(
                    "Builder repair may not modify the NUTTZ project "
                    "manifest."
                )

            if not relative_path.lower().endswith(".py"):
                raise RuntimeError(
                    "Builder project repair may only modify Python files."
                )

            if relative_path not in existing_python_paths:
                raise RuntimeError(
                    "Builder project repair attempted to modify a file "
                    "that did not already exist in the workspace: "
                    f"{relative_path}"
                )

        before_files = {}

        for relative_path in requested_paths:
            before_files[relative_path] = read_workspace_file(
                workspace_name,
                relative_path,
            )

        changed_files = []

        for file_entry in files:
            relative_path = file_entry["path"]
            previous = before_files[relative_path]

            write_result = write_workspace_file(
                workspace_name,
                relative_path,
                file_entry["content"],
            )

            verification = read_workspace_file(
                workspace_name,
                relative_path,
            )

            if verification["sha256"] != write_result["sha256"]:
                raise RuntimeError(
                    "Builder project repair verification failed for "
                    f"{relative_path}."
                )

            changed = (
                verification["sha256"]
                != previous["sha256"]
            )

            changed_files.append(
                {
                    "path": relative_path,
                    "created": write_result["created"],
                    "previous_sha256": previous["sha256"],
                    "repaired_sha256": verification["sha256"],
                    "size_bytes": verification["size_bytes"],
                    "changed": changed,
                    "verified": True,
                }
            )

        if not any(
            item["changed"]
            for item in changed_files
        ):
            raise RuntimeError(
                "Builder project repair did not change any failed "
                "project file."
            )

        repair_evidence = {
            "type": "builder_workspace_project_repair",
            "verified": True,
            "workspace": workspace_name,
            "entrypoint": artifact_path,
            "file_count": len(changed_files),
            "files": changed_files,
        }

        log_event(
            mission_id,
            "Builder",
            "repaired",
            (
                f"Builder repaired task {task_position} project with "
                f"{len(changed_files)} verified file replacement(s)"
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
            "entrypoint": artifact_path,
            "artifact": {
                "path": artifact_path,
                "created": False,
                "size_bytes": read_workspace_file(
                    workspace_name,
                    artifact_path,
                )["size_bytes"],
                "sha256": read_workspace_file(
                    workspace_name,
                    artifact_path,
                )["sha256"],
                "verified": True,
            },
            "artifacts": [
                {
                    "path": item["path"],
                    "created": item["created"],
                    "size_bytes": item["size_bytes"],
                    "sha256": item["repaired_sha256"],
                    "changed": item["changed"],
                    "verified": item["verified"],
                }
                for item in changed_files
            ],
            "evidence": repair_evidence,
        }

    except Exception as error:
        log_event(
            mission_id,
            "Builder",
            "repair_error",
            (
                f"Builder failed project repair for task "
                f"{task_position} entrypoint {artifact_path}: {error}"
            ),
        )

        raise
