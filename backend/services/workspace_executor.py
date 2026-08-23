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
        str(target),
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
            "command": [
                python_executable,
                "-I",
                "-B",
                artifact["path"],
            ],
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
            "command": [
                python_executable,
                "-I",
                "-B",
                artifact["path"],
            ],
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
            "command": [
                python_executable,
                "-I",
                "-B",
                artifact["path"],
            ],
            "status": "error",
            "exit_code": None,
            "duration_ms": duration_ms,
            "timeout_seconds": EXECUTION_TIMEOUT_SECONDS,
            "stdout": "",
            "stderr": "",
            "truncated": False,
            "error": str(error),
        }
