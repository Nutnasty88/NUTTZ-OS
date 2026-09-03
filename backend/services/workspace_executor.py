from __future__ import annotations

import hashlib
import os
import re
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

STDIN_LIMIT_BYTES: Final = 4096

ARGUMENT_COUNT_LIMIT: Final = 8
ARGUMENT_BYTES_LIMIT: Final = 512

SAFE_ARGUMENT_PATTERN: Final = re.compile(
    r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$"
)

PYTHON_WORKSPACE_BOOTSTRAP: Final = (
    "import runpy, sys; "
    "entrypoint = sys.argv[1]; "
    "import_root = sys.argv[2]; "
    "artifact_arguments = sys.argv[3:]; "
    "sys.path.insert(0, import_root); "
    "sys.argv = [entrypoint, *artifact_arguments]; "
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


def _validate_controlled_arguments(
    arguments: list[str] | None,
) -> list[str]:
    """Validate bounded arguments accepted by Workspace Executor."""
    if arguments is None:
        return []

    if not isinstance(arguments, list):
        raise WorkspaceExecutionError(
            "Controlled arguments must be a list."
        )

    if len(arguments) > ARGUMENT_COUNT_LIMIT:
        raise WorkspaceExecutionError(
            "Controlled argument count exceeds the "
            "Workspace Executor limit."
        )

    controlled_name_pair = (
        len(arguments) == 2
        and arguments[0] == "--name"
        and isinstance(arguments[1], str)
        and SAFE_ARGUMENT_PATTERN.fullmatch(
            arguments[1]
        )
        is not None
    )

    if controlled_name_pair:
        safe_arguments = list(arguments)
    else:
        safe_arguments = []

        for argument in arguments:
            if (
                not isinstance(argument, str)
                or SAFE_ARGUMENT_PATTERN.fullmatch(
                    argument
                )
                is None
            ):
                raise WorkspaceExecutionError(
                    "Controlled arguments may contain only bounded "
                    "letters, numbers, dots, underscores, and hyphens."
                )

            safe_arguments.append(argument)

    argument_bytes = b"\x00".join(
        argument.encode("utf-8")
        for argument in safe_arguments
    )

    if len(argument_bytes) > ARGUMENT_BYTES_LIMIT:
        raise WorkspaceExecutionError(
            "Controlled arguments exceed the Workspace Executor "
            "byte limit."
        )

    return safe_arguments


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
    *,
    stdin_text: str | None = None,
    arguments: list[str] | None = None,
) -> dict[str, Any]:
    """
    Execute one verified Python artifact inside its Builder workspace.

    This executor deliberately does not accept arbitrary command strings,
    shell syntax, arguments, absolute paths, or non-Python executables.

    Optional stdin and positional arguments are bounded values supplied
    by trusted NUTTZ-OS execution policy. Their contents are not included
    directly in execution evidence.
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

    safe_arguments = _validate_controlled_arguments(
        arguments
    )

    argument_bytes = b"\x00".join(
        argument.encode("utf-8")
        for argument in safe_arguments
    )

    argument_evidence = {
        "arguments_supplied": bool(safe_arguments),
        "argument_count": len(safe_arguments),
        "arguments_sha256": (
            hashlib.sha256(argument_bytes).hexdigest()
            if safe_arguments
            else None
        ),
    }

    command = [
        python_executable,
        "-I",
        "-B",
        "-c",
        PYTHON_WORKSPACE_BOOTSTRAP,
        str(target),
        str(target.parent),
        *safe_arguments,
    ]

    evidence_command = list(command)

    if safe_arguments:
        evidence_command[-len(safe_arguments):] = [
            "<controlled-argument>"
            for _ in safe_arguments
        ]

    if stdin_text is not None:
        if not isinstance(stdin_text, str):
            raise WorkspaceExecutionError(
                "Controlled stdin must be text."
            )

        if "\x00" in stdin_text:
            raise WorkspaceExecutionError(
                "Controlled stdin contains an invalid character."
            )

        stdin_bytes = stdin_text.encode("utf-8")

        if len(stdin_bytes) > STDIN_LIMIT_BYTES:
            raise WorkspaceExecutionError(
                "Controlled stdin exceeds the Workspace Executor limit."
            )
    else:
        stdin_bytes = b""

    stdin_evidence = {
        "stdin_supplied": stdin_text is not None,
        "stdin_size_bytes": len(stdin_bytes),
        "stdin_sha256": (
            hashlib.sha256(stdin_bytes).hexdigest()
            if stdin_text is not None
            else None
        ),
    }

    started = time.monotonic()

    try:
        completed = subprocess.run(
            command,
            shell=False,
            cwd=str(workspace),
            env=environment,
            stdin=(
                subprocess.DEVNULL
                if stdin_text is None
                else None
            ),
            input=stdin_text,
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
            **stdin_evidence,
            **argument_evidence,
            "interpreter": python_executable,
            "command": evidence_command,
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
            **stdin_evidence,
            **argument_evidence,
            "interpreter": python_executable,
            "command": evidence_command,
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
            **stdin_evidence,
            **argument_evidence,
            "interpreter": python_executable,
            "command": evidence_command,
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

    if manifest.get("schema_version") != 2:
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

    project_files = manifest.get("files")

    if (
        not isinstance(project_files, list)
        or not project_files
    ):
        raise WorkspaceExecutionError(
            "Project manifest file set is invalid."
        )

    expected_files = {}

    for file_record in project_files:
        if not isinstance(file_record, dict):
            raise WorkspaceExecutionError(
                "Project manifest contains an invalid "
                "file record."
            )

        file_path = file_record.get("path")
        file_sha256 = file_record.get("sha256")
        file_size = file_record.get("size_bytes")

        if (
            not isinstance(file_path, str)
            or not file_path
            or file_path == PROJECT_MANIFEST_PATH
        ):
            raise WorkspaceExecutionError(
                "Project manifest contains an invalid "
                "file path."
            )

        if file_path in expected_files:
            raise WorkspaceExecutionError(
                "Project manifest contains duplicate "
                "file paths."
            )

        if (
            not isinstance(file_sha256, str)
            or len(file_sha256) != 64
        ):
            raise WorkspaceExecutionError(
                "Project manifest contains an invalid "
                "file SHA256."
            )

        if (
            not isinstance(file_size, int)
            or file_size < 0
        ):
            raise WorkspaceExecutionError(
                "Project manifest contains an invalid "
                "file size."
            )

        expected_files[file_path] = {
            "sha256": file_sha256,
            "size_bytes": file_size,
        }

    from services.workspace_manager import (
        list_workspace_files,
    )

    current_listing = list_workspace_files(
        workspace_name,
    )

    if current_listing["truncated"]:
        raise WorkspaceExecutionError(
            "Project launch denied: workspace file listing "
            "is truncated."
        )

    current_paths = {
        item["path"]
        for item in current_listing["files"]
        if item["path"] != PROJECT_MANIFEST_PATH
    }

    expected_paths = set(expected_files)

    if current_paths != expected_paths:
        added = sorted(
            current_paths - expected_paths
        )
        missing = sorted(
            expected_paths - current_paths
        )

        details = []

        if added:
            details.append(
                "unexpected files: " + ", ".join(added)
            )

        if missing:
            details.append(
                "missing files: " + ", ".join(missing)
            )

        raise WorkspaceExecutionError(
            "Project launch denied: workspace file set "
            "no longer matches the verified manifest"
            + (
                " (" + "; ".join(details) + ")"
                if details
                else ""
            )
            + "."
        )

    verified_files = []

    for file_path in sorted(expected_files):
        expected = expected_files[file_path]

        current_file = read_workspace_file(
            workspace_name,
            file_path,
        )

        if (
            current_file["sha256"]
            != expected["sha256"]
        ):
            raise WorkspaceExecutionError(
                "Project launch denied: file SHA256 "
                f"changed for {file_path}."
            )

        if (
            current_file["size_bytes"]
            != expected["size_bytes"]
        ):
            raise WorkspaceExecutionError(
                "Project launch denied: file size "
                f"changed for {file_path}."
            )

        verified_files.append(
            {
                "path": file_path,
                "sha256": current_file["sha256"],
                "size_bytes": current_file["size_bytes"],
            }
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

    run_command = manifest.get("run_command")

    approved_run_prefix = [
        "python3",
        "-I",
        "-B",
        entrypoint,
    ]

    if (
        not isinstance(run_command, list)
        or run_command[:4] != approved_run_prefix
    ):
        raise WorkspaceExecutionError(
            "Project manifest run command is not an approved "
            "Workspace Executor command."
        )

    try:
        manifest_arguments = _validate_controlled_arguments(
            run_command[4:]
        )
    except WorkspaceExecutionError as error:
        raise WorkspaceExecutionError(
            "Project manifest controlled arguments are invalid."
        ) from error

    execution = execute_python_artifact(
        mission_id,
        entrypoint,
        arguments=manifest_arguments,
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
        "verified_files": verified_files,
        "verified_file_count": len(verified_files),
        "execution": execution,
        "success": execution.get("verified") is True,
    }
