from __future__ import annotations

import shutil
import subprocess
import time
from dataclasses import dataclass
from typing import Any, Final


SAFE_PATH: Final = (
    "/usr/local/sbin:/usr/local/bin:"
    "/usr/sbin:/usr/bin:/sbin:/bin"
)

DEFAULT_TIMEOUT_SECONDS: Final = 15
OUTPUT_LIMIT: Final = 6000


@dataclass(frozen=True)
class ToolDefinition:
    name: str
    description: str
    executable: str
    arguments: tuple[str, ...]
    timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS


TOOLS: Final[dict[str, ToolDefinition]] = {
    "system.kernel": ToolDefinition(
        name="system.kernel",
        description="Display the operating-system kernel information.",
        executable="uname",
        arguments=("-a",),
    ),
    "system.uptime": ToolDefinition(
        name="system.uptime",
        description="Display system uptime and load averages.",
        executable="uptime",
        arguments=(),
    ),
    "system.disk": ToolDefinition(
        name="system.disk",
        description="Display mounted filesystem usage.",
        executable="df",
        arguments=(
            "-h",
            "--output=source,fstype,size,used,avail,pcent,target",
        ),
    ),
    "system.memory": ToolDefinition(
        name="system.memory",
        description="Display system memory usage.",
        executable="free",
        arguments=("-h",),
    ),
    "python.version": ToolDefinition(
        name="python.version",
        description="Display the installed Python version.",
        executable="python3",
        arguments=("--version",),
    ),
    "docker.version": ToolDefinition(
        name="docker.version",
        description="Display the installed Docker client version.",
        executable="docker",
        arguments=("--version",),
    ),
    "virtualbox.version": ToolDefinition(
        name="virtualbox.version",
        description="Display the installed VirtualBox version.",
        executable="VBoxManage",
        arguments=("--version",),
    ),
    "pytest.version": ToolDefinition(
        name="pytest.version",
        description="Display the installed pytest version.",
        executable="pytest",
        arguments=("--version",),
    ),
}


def _resolve_executable(executable: str) -> str | None:
    return shutil.which(executable, path=SAFE_PATH)


def _normalize_output(value: str | bytes | None) -> str:
    if value is None:
        return ""

    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")

    return value


def _limit_output(value: str) -> tuple[str, bool]:
    if len(value) <= OUTPUT_LIMIT:
        return value, False

    marker = "\n...[output truncated by NUTTZ-OS]"
    available = OUTPUT_LIMIT - len(marker)
    return value[:available] + marker, True


def list_tools() -> list[dict[str, Any]]:
    return [
        {
            "name": tool.name,
            "description": tool.description,
            "available": _resolve_executable(tool.executable) is not None,
        }
        for tool in TOOLS.values()
    ]


def run_tool(tool_name: str) -> dict[str, Any]:
    tool = TOOLS.get(tool_name)

    if tool is None:
        return {
            "tool": tool_name,
            "status": "denied",
            "error": "Tool is not in the NUTTZ-OS allowlist.",
        }

    executable = _resolve_executable(tool.executable)
    command = [
        executable or tool.executable,
        *tool.arguments,
    ]

    if executable is None:
        return {
            "tool": tool.name,
            "description": tool.description,
            "status": "unavailable",
            "command": command,
            "exit_code": None,
            "duration_ms": 0,
            "timeout_seconds": tool.timeout_seconds,
            "stdout": "",
            "stderr": "",
            "truncated": False,
            "error": f"{tool.executable} was not found in the safe PATH.",
        }

    environment = {
        "PATH": SAFE_PATH,
        "LANG": "C",
        "LC_ALL": "C",
        "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1",
    }

    started = time.monotonic()

    try:
        completed = subprocess.run(
            [executable, *tool.arguments],
            shell=False,
            cwd="/tmp",
            env=environment,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=tool.timeout_seconds,
            check=False,
            close_fds=True,
            start_new_session=True,
        )

        duration_ms = int((time.monotonic() - started) * 1000)

        stdout, stdout_truncated = _limit_output(
            _normalize_output(completed.stdout),
        )
        stderr, stderr_truncated = _limit_output(
            _normalize_output(completed.stderr),
        )

        return {
            "tool": tool.name,
            "description": tool.description,
            "status": (
                "success"
                if completed.returncode == 0
                else "error"
            ),
            "command": command,
            "exit_code": completed.returncode,
            "duration_ms": duration_ms,
            "timeout_seconds": tool.timeout_seconds,
            "stdout": stdout,
            "stderr": stderr,
            "truncated": stdout_truncated or stderr_truncated,
        }

    except subprocess.TimeoutExpired as error:
        duration_ms = int((time.monotonic() - started) * 1000)

        stdout, stdout_truncated = _limit_output(
            _normalize_output(error.stdout),
        )
        stderr, stderr_truncated = _limit_output(
            _normalize_output(error.stderr),
        )

        return {
            "tool": tool.name,
            "description": tool.description,
            "status": "timeout",
            "command": command,
            "exit_code": None,
            "duration_ms": duration_ms,
            "timeout_seconds": tool.timeout_seconds,
            "stdout": stdout,
            "stderr": stderr,
            "truncated": stdout_truncated or stderr_truncated,
            "error": "Tool execution exceeded its timeout.",
        }

    except OSError as error:
        duration_ms = int((time.monotonic() - started) * 1000)

        return {
            "tool": tool.name,
            "description": tool.description,
            "status": "error",
            "command": command,
            "exit_code": None,
            "duration_ms": duration_ms,
            "timeout_seconds": tool.timeout_seconds,
            "stdout": "",
            "stderr": "",
            "truncated": False,
            "error": str(error),
        }
