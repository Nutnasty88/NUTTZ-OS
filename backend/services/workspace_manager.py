from __future__ import annotations

import hashlib
import os
import re
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Final


PROJECT_ROOT: Final[Path] = Path(__file__).resolve().parents[2]
WORKSPACE_ROOT: Final[Path] = PROJECT_ROOT / "builder-workspaces"

MAX_WORKSPACES: Final = 100
MAX_FILE_BYTES: Final = 256_000
MAX_LISTED_FILES: Final = 1_000
MAX_RELATIVE_PATH_LENGTH: Final = 240

WORKSPACE_NAME_PATTERN: Final = re.compile(
    r"^[a-z0-9](?:[a-z0-9-]{0,62}[a-z0-9])?$"
)

PROTECTED_PATH_PARTS: Final = {
    ".git",
    ".ssh",
    ".venv",
    "venv",
    "node_modules",
    "__pycache__",
}


class WorkspaceError(RuntimeError):
    """Base error for Builder workspace operations."""


class WorkspaceNotFoundError(WorkspaceError):
    """Raised when a Builder workspace does not exist."""


class WorkspaceConflictError(WorkspaceError):
    """Raised when a workspace or file operation conflicts."""


class WorkspacePathError(WorkspaceError):
    """Raised when a path attempts to leave the workspace boundary."""


def _utc_timestamp(timestamp: float) -> str:
    return datetime.fromtimestamp(
        timestamp,
        tz=timezone.utc,
    ).isoformat()


def _ensure_within(candidate: Path, parent: Path) -> None:
    try:
        candidate.relative_to(parent)
    except ValueError as error:
        raise WorkspacePathError(
            "Path escapes the approved Builder workspace."
        ) from error


def _workspace_root() -> Path:
    WORKSPACE_ROOT.mkdir(
        mode=0o700,
        parents=True,
        exist_ok=True,
    )

    root = WORKSPACE_ROOT.resolve()
    project_root = PROJECT_ROOT.resolve()

    _ensure_within(root, project_root)
    root.chmod(0o700)

    return root


def _validate_workspace_name(name: str) -> str:
    if not isinstance(name, str):
        raise WorkspacePathError(
            "Workspace name must be a string."
        )

    normalized = name.strip().lower()

    if not WORKSPACE_NAME_PATTERN.fullmatch(normalized):
        raise WorkspacePathError(
            "Workspace names must contain only lowercase letters, "
            "numbers, and hyphens and must be 1-64 characters long."
        )

    return normalized


def _workspace_path(
    workspace_name: str,
    *,
    require_exists: bool = True,
) -> Path:
    normalized = _validate_workspace_name(workspace_name)
    root = _workspace_root()
    unresolved = root / normalized

    if unresolved.is_symlink():
        raise WorkspacePathError(
            "Workspace symlinks are not permitted."
        )

    resolved = unresolved.resolve(strict=False)
    _ensure_within(resolved, root)

    if require_exists and not resolved.is_dir():
        raise WorkspaceNotFoundError(
            f'Builder workspace "{normalized}" was not found.'
        )

    return resolved


def _is_protected_part(part: str) -> bool:
    lowered = part.lower()

    return (
        lowered in PROTECTED_PATH_PARTS
        or lowered == ".env"
        or lowered.startswith(".env.")
    )


def _validate_relative_path(relative_path: str) -> Path:
    if not isinstance(relative_path, str):
        raise WorkspacePathError(
            "Relative path must be a string."
        )

    value = relative_path.strip()

    if not value:
        raise WorkspacePathError(
            "Relative path cannot be empty."
        )

    if "\x00" in value:
        raise WorkspacePathError(
            "Relative path contains an invalid character."
        )

    if "\\" in value:
        raise WorkspacePathError(
            "Use forward slashes in Builder workspace paths."
        )

    if len(value) > MAX_RELATIVE_PATH_LENGTH:
        raise WorkspacePathError(
            "Relative path exceeds the workspace limit."
        )

    path = Path(value)

    if path.is_absolute():
        raise WorkspacePathError(
            "Absolute paths are not permitted."
        )

    if not path.parts:
        raise WorkspacePathError(
            "Relative path must identify a file."
        )

    for part in path.parts:
        if part in {"", ".", ".."}:
            raise WorkspacePathError(
                "Relative path traversal is not permitted."
            )

        if _is_protected_part(part):
            raise WorkspacePathError(
                f'Protected path component "{part}" is not permitted.'
            )

    return path


def _file_path(
    workspace_name: str,
    relative_path: str,
) -> tuple[Path, Path]:
    workspace = _workspace_path(workspace_name)
    relative = _validate_relative_path(relative_path)

    cursor = workspace

    for part in relative.parts:
        cursor = cursor / part

        if cursor.is_symlink():
            raise WorkspacePathError(
                "Symlink paths are not permitted."
            )

    resolved = cursor.resolve(strict=False)
    _ensure_within(resolved, workspace)

    return workspace, resolved


def _sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def create_workspace(workspace_name: str) -> dict[str, Any]:
    normalized = _validate_workspace_name(workspace_name)
    root = _workspace_root()
    workspace = _workspace_path(
        normalized,
        require_exists=False,
    )

    if workspace.exists():
        raise WorkspaceConflictError(
            f'Builder workspace "{normalized}" already exists.'
        )

    existing_workspaces = [
        entry
        for entry in root.iterdir()
        if entry.is_dir() and not entry.is_symlink()
    ]

    if len(existing_workspaces) >= MAX_WORKSPACES:
        raise WorkspaceConflictError(
            "Builder workspace limit has been reached."
        )

    workspace.mkdir(mode=0o700)
    workspace.chmod(0o700)

    return get_workspace(normalized)


def get_workspace(workspace_name: str) -> dict[str, Any]:
    normalized = _validate_workspace_name(workspace_name)
    workspace = _workspace_path(normalized)
    files = list_workspace_files(normalized)

    stat = workspace.stat()

    return {
        "name": normalized,
        "path": str(workspace),
        "created_at": _utc_timestamp(stat.st_ctime),
        "modified_at": _utc_timestamp(stat.st_mtime),
        "file_count": files["count"],
        "total_bytes": sum(
            item["size_bytes"]
            for item in files["files"]
        ),
        "file_listing_truncated": files["truncated"],
    }


def list_workspaces() -> list[dict[str, Any]]:
    root = _workspace_root()
    workspaces = []

    for entry in sorted(
        root.iterdir(),
        key=lambda item: item.name.lower(),
    ):
        if (
            not entry.is_dir()
            or entry.is_symlink()
            or not WORKSPACE_NAME_PATTERN.fullmatch(entry.name)
        ):
            continue

        workspaces.append(get_workspace(entry.name))

    return workspaces


def list_workspace_files(
    workspace_name: str,
) -> dict[str, Any]:
    normalized = _validate_workspace_name(workspace_name)
    workspace = _workspace_path(normalized)
    files: list[dict[str, Any]] = []
    truncated = False

    for current, directory_names, file_names in os.walk(
        workspace,
        followlinks=False,
    ):
        current_path = Path(current)

        safe_directories = []

        for directory_name in directory_names:
            child = current_path / directory_name

            if (
                _is_protected_part(directory_name)
                or child.is_symlink()
            ):
                continue

            safe_directories.append(directory_name)

        directory_names[:] = safe_directories

        for file_name in file_names:
            if _is_protected_part(file_name):
                continue

            file_path = current_path / file_name

            if file_path.is_symlink() or not file_path.is_file():
                continue

            resolved = file_path.resolve()
            _ensure_within(resolved, workspace)

            stat = resolved.stat()

            files.append(
                {
                    "path": resolved.relative_to(
                        workspace
                    ).as_posix(),
                    "size_bytes": stat.st_size,
                    "modified_at": _utc_timestamp(
                        stat.st_mtime
                    ),
                }
            )

            if len(files) >= MAX_LISTED_FILES:
                truncated = True
                break

        if truncated:
            break

    files.sort(key=lambda item: item["path"].lower())

    return {
        "workspace": normalized,
        "files": files,
        "count": len(files),
        "truncated": truncated,
    }


def read_workspace_file(
    workspace_name: str,
    relative_path: str,
) -> dict[str, Any]:
    normalized = _validate_workspace_name(workspace_name)
    _, target = _file_path(normalized, relative_path)

    if not target.exists() or not target.is_file():
        raise WorkspaceNotFoundError(
            f'File "{relative_path}" was not found.'
        )

    size = target.stat().st_size

    if size > MAX_FILE_BYTES:
        raise WorkspaceConflictError(
            "File exceeds the Builder read limit."
        )

    content_bytes = target.read_bytes()

    if len(content_bytes) > MAX_FILE_BYTES:
        raise WorkspaceConflictError(
            "File exceeds the Builder read limit."
        )

    try:
        content = content_bytes.decode("utf-8")
    except UnicodeDecodeError as error:
        raise WorkspaceConflictError(
            "Builder Agent v1 can only read UTF-8 text files."
        ) from error

    return {
        "workspace": normalized,
        "path": target.relative_to(
            _workspace_path(normalized)
        ).as_posix(),
        "size_bytes": len(content_bytes),
        "sha256": _sha256(content_bytes),
        "content": content,
    }


def write_workspace_file(
    workspace_name: str,
    relative_path: str,
    content: str,
) -> dict[str, Any]:
    normalized = _validate_workspace_name(workspace_name)

    if not isinstance(content, str):
        raise WorkspaceConflictError(
            "Builder Agent v1 can only write text content."
        )

    content_bytes = content.encode("utf-8")

    if len(content_bytes) > MAX_FILE_BYTES:
        raise WorkspaceConflictError(
            "Content exceeds the Builder write limit."
        )

    workspace, target = _file_path(
        normalized,
        relative_path,
    )

    if target.exists() and target.is_dir():
        raise WorkspaceConflictError(
            f'Path "{relative_path}" is a directory.'
        )

    created = not target.exists()

    if created:
        file_mode = 0o600
    else:
        file_mode = target.stat().st_mode & 0o777

    target.parent.mkdir(
        mode=0o700,
        parents=True,
        exist_ok=True,
    )

    resolved_parent = target.parent.resolve()
    _ensure_within(resolved_parent, workspace)

    descriptor, temporary_name = tempfile.mkstemp(
        prefix=".nuttz-write-",
        dir=str(resolved_parent),
    )

    temporary_path = Path(temporary_name)

    try:
        with os.fdopen(descriptor, "wb") as temporary_file:
            temporary_file.write(content_bytes)
            temporary_file.flush()
            os.fsync(temporary_file.fileno())

        temporary_path.chmod(file_mode)
        os.replace(temporary_path, target)
    finally:
        if temporary_path.exists():
            temporary_path.unlink()

    stat = target.stat()

    return {
        "workspace": normalized,
        "path": target.relative_to(workspace).as_posix(),
        "created": created,
        "size_bytes": len(content_bytes),
        "sha256": _sha256(content_bytes),
        "modified_at": _utc_timestamp(stat.st_mtime),
    }
