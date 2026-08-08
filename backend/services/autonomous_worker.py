from __future__ import annotations

import threading
from datetime import datetime
from typing import Any

from services.executor import execute_next_task, get_tasks


_state_lock = threading.RLock()
_stop_event = threading.Event()
_worker_thread: threading.Thread | None = None

_worker_state: dict[str, Any] = {
    "status": "Idle",
    "mission_id": None,
    "current_task_id": None,
    "total_tasks": 0,
    "completed_tasks": 0,
    "last_message": "Autonomous Worker is idle.",
    "last_error": "",
    "started_at": None,
    "updated_at": None,
}


def _timestamp() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _update_state(**changes: Any) -> None:
    with _state_lock:
        _worker_state.update(changes)
        _worker_state["updated_at"] = _timestamp()


def _snapshot() -> dict[str, Any]:
    with _state_lock:
        result = dict(_worker_state)
        result["thread_alive"] = bool(
            _worker_thread and _worker_thread.is_alive()
        )
        result["stop_requested"] = _stop_event.is_set()
        return result


def _count_tasks(tasks: list[dict[str, Any]]) -> tuple[int, int]:
    total = len(tasks)
    completed = sum(
        1 for task in tasks if task.get("status") == "Completed"
    )
    return total, completed


def _run_worker(mission_id: int, delay_seconds: float) -> None:
    global _worker_thread

    try:
        while not _stop_event.is_set():
            tasks = get_tasks(mission_id)
            total, completed = _count_tasks(tasks)

            pending_tasks = [
                task
                for task in tasks
                if task.get("status") == "Pending"
            ]

            _update_state(
                total_tasks=total,
                completed_tasks=completed,
            )

            if not tasks:
                _update_state(
                    status="Error",
                    last_error="No tasks exist for this mission.",
                    last_message="The worker stopped because no tasks exist.",
                )
                return

            if not pending_tasks:
                if completed == total:
                    _update_state(
                        status="Completed",
                        current_task_id=None,
                        completed_tasks=completed,
                        last_message="All mission tasks are complete.",
                    )
                else:
                    _update_state(
                        status="Error",
                        current_task_id=None,
                        last_error=(
                            "No pending tasks remain, but some tasks "
                            "are not completed."
                        ),
                        last_message="The worker could not continue.",
                    )
                return

            next_task = pending_tasks[0]

            _update_state(
                status="Running",
                current_task_id=next_task["id"],
                last_message=(
                    f'Executing task {next_task["position"]}: '
                    f'{next_task["title"]}'
                ),
                last_error="",
            )

            execute_next_task(mission_id)

            refreshed_tasks = get_tasks(mission_id)
            refreshed_total, refreshed_completed = _count_tasks(
                refreshed_tasks
            )

            _update_state(
                total_tasks=refreshed_total,
                completed_tasks=refreshed_completed,
                current_task_id=None,
                last_message=(
                    f'Task {next_task["position"]} completed: '
                    f'{next_task["title"]}'
                ),
            )

            if _stop_event.wait(delay_seconds):
                _update_state(
                    status="Paused",
                    current_task_id=None,
                    last_message=(
                        "Autonomous Worker paused after the current task."
                    ),
                )
                return

    except Exception as error:
        _update_state(
            status="Error",
            current_task_id=None,
            last_error=str(error),
            last_message="Autonomous Worker encountered an error.",
        )

    finally:
        with _state_lock:
            if _worker_state["status"] == "Stopping":
                _worker_state["status"] = "Paused"
                _worker_state["last_message"] = (
                    "Autonomous Worker is paused."
                )
                _worker_state["updated_at"] = _timestamp()

            _worker_thread = None


def start_worker(
    mission_id: int,
    delay_seconds: float = 2.0,
) -> dict[str, Any]:
    global _worker_thread

    delay_seconds = max(float(delay_seconds), 0.5)

    with _state_lock:
        if _worker_thread and _worker_thread.is_alive():
            raise RuntimeError(
                "An Autonomous Worker is already running."
            )

    tasks = get_tasks(mission_id)

    if not tasks:
        raise ValueError(
            "This mission has no tasks. Run or synchronize it first."
        )

    total, completed = _count_tasks(tasks)
    pending = [
        task for task in tasks if task.get("status") == "Pending"
    ]

    if not pending:
        _update_state(
            status="Completed" if completed == total else "Idle",
            mission_id=mission_id,
            current_task_id=None,
            total_tasks=total,
            completed_tasks=completed,
            last_message="No pending tasks remain.",
            last_error="",
        )
        return _snapshot()

    _stop_event.clear()

    _update_state(
        status="Running",
        mission_id=mission_id,
        current_task_id=None,
        total_tasks=total,
        completed_tasks=completed,
        last_message="Autonomous Worker is starting.",
        last_error="",
        started_at=_timestamp(),
    )

    _worker_thread = threading.Thread(
        target=_run_worker,
        args=(mission_id, delay_seconds),
        name=f"nuttz-worker-mission-{mission_id}",
        daemon=True,
    )
    _worker_thread.start()

    return _snapshot()


def pause_worker() -> dict[str, Any]:
    with _state_lock:
        if not (_worker_thread and _worker_thread.is_alive()):
            return _snapshot()

        _stop_event.set()
        _worker_state["status"] = "Stopping"
        _worker_state["last_message"] = (
            "Pause requested. The current task will finish first."
        )
        _worker_state["updated_at"] = _timestamp()

    return _snapshot()


def get_worker_status() -> dict[str, Any]:
    return _snapshot()
