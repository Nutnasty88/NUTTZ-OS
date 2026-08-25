from __future__ import annotations

import os
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any, Final

from services.workspace_manager import (
    WORKSPACE_ROOT,
    read_workspace_file,
)


SAFE_PATH: Final = (
    "/usr/local/sbin:/usr/local/bin:"
    "/usr/sbin:/usr/bin:/sbin:/bin"
)

PYTHON_EXECUTABLE: Final = "python3"

EXECUTION_TIMEOUT_SECONDS: Final = 15

OUTPUT_LIMIT: Final = 6000

PYTHON_WORKSPACE_BOOTSTRAP: Final = (
    "import runpy, sys; "
    "entrypoint = sys.argv[1]; "
    "import_root = sys.argv[2]; "
    "sys.path.insert(0, import_root); "
    "runpy.run_path(entrypoint, run_name='__main__')"
)


class WorkspaceExecutionError(RuntimeError):
    """Raised when isolated Builder workspace execution is denied."""


def _workspace_name(mission_id: int) -> str:
    if not isinstance(mission_id, int):
        raise WorkspaceExecutionError(
            "Mission ID must be an integer."
        )

    if mission_id < 1:
        raise WorkspaceExecutionError(
            "Mission ID must be positive."
        )

    return f"mission-{mission_id}"


def _limit_output(
    value: str | bytes | None,
) -> tuple[str, bool]:
    if value is None:
        text = ""
    elif isinstance(value, bytes):
        text = value.decode(
            "utf-8",
            errors="replace",
        )
    else:
        text = value

    if len(text) <= OUTPUT_LIMIT:
        return text, False

    marker = "\n...[output truncated by NUTTZ-OS]"
    available = OUTPUT_LIMIT - len(marker)

    return (
        text[:available] + marker,
        True,
    )


def _resolve_workspace(
    mission_id: int,
) -> tuple[str, Path]:
    workspace_name = _workspace_name(mission_id)

    root = WORKSPACE_ROOT.resolve()
    workspace = (
        WORKSPACE_ROOT / workspace_name
    ).resolve()

    try:
        workspace.relative_to(root)
    except ValueError as error:
        raise WorkspaceExecutionError(
            "Workspace escaped the Builder workspace root."
        ) from error

    if not workspace.exists():
        raise WorkspaceExecutionError(
            f'Workspace "{workspace_name}" does not exist.'
        )

    if not workspace.is_dir():
        raise WorkspaceExecutionError(
            f'Workspace "{workspace_name}" is not a directory.'
        )

    return workspace_name, workspace


def _resolve_python_artifact(
    mission_id: int,
    relative_path: str,
) -> tuple[str, Path, dict[str, Any]]:
    if not isinstance(relative_path, str):
        raise WorkspaceExecutionError(
            "Artifact path must be text."
        )

    relative_path = relative_path.strip()

    if not relative_path:
        raise WorkspaceExecutionError(
            "Artifact path cannot be empty."
        )

    if not relative_path.lower().endswith(".py"):
        raise WorkspaceExecutionError(
            "Workspace Executor v1 only executes Python .py files."
        )

    workspace_name, workspace = (
        _resolve_workspace(mission_id)
    )

    # Reuse Workspace Manager validation and artifact verification.
    artifact = read_workspace_file(
        workspace_name,
        relative_path,
    )

    target = (
        workspace / artifact["path"]
    ).resolve()

    try:
        target.relative_to(workspace)
    except ValueError as error:
        raise WorkspaceExecutionError(
            "Artifact escaped its Builder workspace."
        ) from error

    if not target.exists() or not target.is_file():
        raise WorkspaceExecutionError(
            f'Artifact "{relative_path}" does not exist.'
        )

    if target.is_symlink():
        raise WorkspaceExecutionError(
            "Symlink execution is not allowed."
        )

    return workspace_name, target, artifact


def execute_python_artifact(
    mission_id: int,
    relative_path: str,
) -> dict[str, Any]:
    """
    Execute one verified Python artifact inside its Builder workspace.

    This executor deliberately does not accept arbitrary command strings,
    shell syntax, arguments, absolute paths, or non-Python executables.
    """
    workspace_name, target, artifact = (
        _resolve_python_artifact(
            mission_id,
            relative_path,
        )
    )

    python_executable = shutil.which(
        PYTHON_EXECUTABLE,
        path=SAFE_PATH,
    )

    if python_executable is None:
        raise WorkspaceExecutionError(
            "python3 was not found in the safe PATH."
        )

    workspace = target.parent

    # Execute from the artifact directory so simple relative imports/files
    # behave predictably without granting access through a shell.
    environment = {
        "PATH": SAFE_PATH,
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        "PYTHONIOENCODING": "utf-8",
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONNOUSERSITE": "1",
        "PYTHONUNBUFFERED": "1",
    }

    command = [
        python_executable,
        "-I",
        "-B",
        "-c",
        PYTHON_WORKSPACE_BOOTSTRAP,
        str(target),
        str(target.parent),
    ]

    started = time.monotonic()

    try:
        completed = subprocess.run(
            command,
            shell=False,
            cwd=str(workspace),
            env=environment,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=EXECUTION_TIMEOUT_SECONDS,
            check=False,
            close_fds=True,
            start_new_session=True,
        )

        duration_ms = int(
            (time.monotonic() - started) * 1000
        )

        stdout, stdout_truncated = _limit_output(
            completed.stdout,
        )

        stderr, stderr_truncated = _limit_output(
            completed.stderr,
        )

        verified = (
            completed.returncode == 0
        )

        return {
            "type": "builder_workspace_execution",
            "verified": verified,
            "mission_id": mission_id,
            "workspace": workspace_name,
            "artifact": artifact["path"],
            "artifact_sha256": artifact["sha256"],
            "artifact_size_bytes": artifact["size_bytes"],
            "interpreter": python_executable,
            "command": command,
            "status": (
                "success"
                if verified
                else "error"
            ),
            "exit_code": completed.returncode,
            "duration_ms": duration_ms,
            "timeout_seconds": EXECUTION_TIMEOUT_SECONDS,
            "stdout": stdout,
            "stderr": stderr,
            "truncated": (
                stdout_truncated
                or stderr_truncated
            ),
        }

    except subprocess.TimeoutExpired as error:
        duration_ms = int(
            (time.monotonic() - started) * 1000
        )

        stdout, stdout_truncated = _limit_output(
            error.stdout,
        )

        stderr, stderr_truncated = _limit_output(
            error.stderr,
        )

        return {
            "type": "builder_workspace_execution",
            "verified": False,
            "mission_id": mission_id,
            "workspace": workspace_name,
            "artifact": artifact["path"],
            "artifact_sha256": artifact["sha256"],
            "artifact_size_bytes": artifact["size_bytes"],
            "interpreter": python_executable,
            "command": command,
            "status": "timeout",
            "exit_code": None,
            "duration_ms": duration_ms,
            "timeout_seconds": EXECUTION_TIMEOUT_SECONDS,
            "stdout": stdout,
            "stderr": stderr,
            "truncated": (
                stdout_truncated
                or stderr_truncated
            ),
            "error": "Artifact execution exceeded its timeout.",
        }

    except OSError as error:
        duration_ms = int(
            (time.monotonic() - started) * 1000
        )

        return {
            "type": "builder_workspace_execution",
            "verified": False,
            "mission_id": mission_id,
            "workspace": workspace_name,
            "artifact": artifact["path"],
            "artifact_sha256": artifact["sha256"],
            "artifact_size_bytes": artifact["size_bytes"],
            "interpreter": python_executable,
            "command": command,
            "status": "error",
            "exit_code": None,
            "duration_ms": duration_ms,
            "timeout_seconds": EXECUTION_TIMEOUT_SECONDS,
            "stdout": "",
            "stderr": "",
            "truncated": False,
            "error": str(error),
        }


def launch_verified_project(
    mission_id: int,
) -> dict[str, Any]:
    """
    Launch a project strictly from its NUTTZ-generated manifest.

    The caller supplies only a mission ID. No command, executable,
    arguments, or artifact path can be supplied through the API.
    """
    import json

    from services.workspace_manager import (
        PROJECT_MANIFEST_PATH,
    )

    workspace_name = _workspace_name(mission_id)

    try:
        manifest_file = read_workspace_file(
            workspace_name,
            PROJECT_MANIFEST_PATH,
        )
    except Exception as error:
        raise WorkspaceExecutionError(
            "Verified project manifest could not be read."
        ) from error

    try:
        manifest = json.loads(
            manifest_file["content"]
        )
    except (
        json.JSONDecodeError,
        TypeError,
    ) as error:
        raise WorkspaceExecutionError(
            "Project manifest is not valid JSON."
        ) from error

    if not isinstance(manifest, dict):
        raise WorkspaceExecutionError(
            "Project manifest must contain an object."
        )

    if manifest.get("schema_version") != 1:
        raise WorkspaceExecutionError(
            "Unsupported project manifest schema."
        )

    if manifest.get("mission_id") != mission_id:
        raise WorkspaceExecutionError(
            "Project manifest mission ID does not match."
        )

    if manifest.get("name") != workspace_name:
        raise WorkspaceExecutionError(
            "Project manifest workspace does not match."
        )

    if manifest.get("runtime") != "python":
        raise WorkspaceExecutionError(
            "Workspace Executor v1 only launches Python projects."
        )

    verification = manifest.get("verification")

    if (
        not isinstance(verification, dict)
        or verification.get("verified") is not True
        or verification.get("source")
        != "Workspace Executor"
    ):
        raise WorkspaceExecutionError(
            "Project manifest is not verified."
        )

    entrypoint = manifest.get("entrypoint")

    if not isinstance(entrypoint, str):
        raise WorkspaceExecutionError(
            "Project manifest entrypoint is invalid."
        )

    artifact_record = manifest.get("artifact")

    if not isinstance(artifact_record, dict):
        raise WorkspaceExecutionError(
            "Project manifest artifact record is invalid."
        )

    if artifact_record.get("path") != entrypoint:
        raise WorkspaceExecutionError(
            "Project manifest artifact path does not match "
            "the entrypoint."
        )

    expected_sha256 = artifact_record.get(
        "sha256"
    )

    expected_size = artifact_record.get(
        "size_bytes"
    )

    if (
        not isinstance(expected_sha256, str)
        or len(expected_sha256) != 64
    ):
        raise WorkspaceExecutionError(
            "Project manifest artifact SHA256 is invalid."
        )

    if (
        not isinstance(expected_size, int)
        or expected_size < 0
    ):
        raise WorkspaceExecutionError(
            "Project manifest artifact size is invalid."
        )

    artifact = read_workspace_file(
        workspace_name,
        entrypoint,
    )

    if artifact["sha256"] != expected_sha256:
        raise WorkspaceExecutionError(
            "Project launch denied: entrypoint SHA256 "
            "no longer matches the verified manifest."
        )

    if artifact["size_bytes"] != expected_size:
        raise WorkspaceExecutionError(
            "Project launch denied: entrypoint size "
            "no longer matches the verified manifest."
        )

    expected_run_command = [
        "python3",
        "-I",
        "-B",
        entrypoint,
    ]

    if manifest.get("run_command") != expected_run_command:
        raise WorkspaceExecutionError(
            "Project manifest run command is not an approved "
            "Workspace Executor command."
        )

    execution = execute_python_artifact(
        mission_id,
        entrypoint,
    )

    return {
        "type": "builder_project_launch",
        "verified_manifest": True,
        "mission_id": mission_id,
        "workspace": workspace_name,
        "manifest": {
            "path": PROJECT_MANIFEST_PATH,
            "sha256": manifest_file["sha256"],
        },
        "entrypoint": entrypoint,
        "artifact_sha256": artifact["sha256"],
        "execution": execution,
        "success": execution.get("verified") is True,
    }
