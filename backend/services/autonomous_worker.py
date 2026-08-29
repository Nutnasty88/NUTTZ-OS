from __future__ import annotations

import threading
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from app.database.database import get_connection
from app.services.events import log_event
from app.services.reporter import (
    create_deliverable,
    get_deliverable,
)
from services.executor import (
    mark_mission_report_error,
    execute_next_task,
    finalize_mission_completion,
    get_tasks,
)


WORKER_LEASE_SECONDS = 30


def ensure_worker_lease_table() -> None:
    conn = get_connection()

    try:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS mission_worker_leases (
                mission_id INTEGER PRIMARY KEY,
                owner_token TEXT NOT NULL,
                acquired_at TEXT NOT NULL,
                heartbeat_at TEXT NOT NULL,
                expires_at TEXT NOT NULL,
                FOREIGN KEY (mission_id)
                    REFERENCES missions(id)
                    ON DELETE CASCADE
            )
            """
        )

        conn.commit()

    finally:
        conn.close()


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _lease_timestamp(value: datetime) -> str:
    return value.isoformat(timespec="microseconds")


def acquire_worker_lease(
    mission_id: int,
    lease_seconds: int = WORKER_LEASE_SECONDS,
) -> dict[str, Any]:
    ensure_worker_lease_table()

    owner_token = uuid.uuid4().hex
    now = _utc_now()
    expires_at = now + timedelta(seconds=lease_seconds)

    conn = get_connection()

    try:
        conn.execute("BEGIN IMMEDIATE")

        existing = conn.execute(
            """
            SELECT
                mission_id,
                owner_token,
                acquired_at,
                heartbeat_at,
                expires_at
            FROM mission_worker_leases
            WHERE mission_id=?
            """,
            (mission_id,),
        ).fetchone()

        if existing is not None:
            existing_expiry = datetime.fromisoformat(
                existing["expires_at"]
            )

            if existing_expiry > now:
                conn.rollback()

                raise RuntimeError(
                    f"Mission {mission_id} already has an "
                    "active worker lease."
                )

            conn.execute(
                """
                DELETE FROM mission_worker_leases
                WHERE mission_id=?
                """,
                (mission_id,),
            )

        running_task = conn.execute(
            """
            SELECT
                id,
                position,
                title,
                execution_token
            FROM mission_tasks
            WHERE
                mission_id=?
                AND status='Running'
            ORDER BY position ASC
            LIMIT 1
            """,
            (mission_id,),
        ).fetchone()

        if running_task is not None:
            conn.rollback()

            raise RuntimeError(
                f"Mission {mission_id} already has Running task "
                f"{running_task['position']} and cannot acquire "
                "Autonomous Worker ownership."
            )

        conn.execute(
            """
            INSERT INTO mission_worker_leases (
                mission_id,
                owner_token,
                acquired_at,
                heartbeat_at,
                expires_at
            )
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                mission_id,
                owner_token,
                _lease_timestamp(now),
                _lease_timestamp(now),
                _lease_timestamp(expires_at),
            ),
        )

        conn.commit()

    finally:
        conn.close()

    return {
        "mission_id": mission_id,
        "owner_token": owner_token,
        "acquired_at": _lease_timestamp(now),
        "heartbeat_at": _lease_timestamp(now),
        "expires_at": _lease_timestamp(expires_at),
    }


def renew_worker_lease(
    mission_id: int,
    owner_token: str,
    lease_seconds: int = WORKER_LEASE_SECONDS,
) -> dict[str, Any]:
    """
    Renew an active worker lease owned by owner_token.

    Renewal fails if the lease is missing, expired, or belongs to
    another worker. This prevents a stale worker from resurrecting
    ownership after another process has taken over the mission.
    """
    ensure_worker_lease_table()

    now = _utc_now()
    expires_at = now + timedelta(seconds=lease_seconds)

    conn = get_connection()

    try:
        conn.execute("BEGIN IMMEDIATE")

        lease = conn.execute(
            """
            SELECT
                mission_id,
                owner_token,
                acquired_at,
                heartbeat_at,
                expires_at
            FROM mission_worker_leases
            WHERE mission_id=?
            """,
            (mission_id,),
        ).fetchone()

        if lease is None:
            conn.rollback()

            raise RuntimeError(
                f"Mission {mission_id} has no worker lease to renew."
            )

        if lease["owner_token"] != owner_token:
            conn.rollback()

            raise RuntimeError(
                f"Mission {mission_id} worker lease is owned "
                "by another worker."
            )

        current_expiry = datetime.fromisoformat(
            lease["expires_at"]
        )

        if current_expiry <= now:
            conn.rollback()

            raise RuntimeError(
                f"Mission {mission_id} worker lease has expired."
            )

        cursor = conn.execute(
            """
            UPDATE mission_worker_leases
            SET
                heartbeat_at=?,
                expires_at=?
            WHERE mission_id=?
              AND owner_token=?
            """,
            (
                _lease_timestamp(now),
                _lease_timestamp(expires_at),
                mission_id,
                owner_token,
            ),
        )

        if cursor.rowcount != 1:
            conn.rollback()

            raise RuntimeError(
                f"Mission {mission_id} worker lease changed "
                "before heartbeat renewal completed."
            )

        conn.commit()

    finally:
        conn.close()

    return {
        "mission_id": mission_id,
        "owner_token": owner_token,
        "acquired_at": lease["acquired_at"],
        "heartbeat_at": _lease_timestamp(now),
        "expires_at": _lease_timestamp(expires_at),
    }


def release_worker_lease(
    mission_id: int,
    owner_token: str,
) -> bool:
    ensure_worker_lease_table()

    conn = get_connection()

    try:
        cursor = conn.execute(
            """
            DELETE FROM mission_worker_leases
            WHERE mission_id=?
              AND owner_token=?
            """,
            (
                mission_id,
                owner_token,
            ),
        )

        conn.commit()

        return cursor.rowcount == 1

    finally:
        conn.close()


def get_worker_lease(
    mission_id: int,
) -> dict[str, Any] | None:
    """
    Return the persisted worker lease for a mission.

    This is read-only observability state. The owner token is not
    exposed because callers only need ownership/liveness metadata.
    """
    ensure_worker_lease_table()

    conn = get_connection()

    try:
        lease = conn.execute(
            """
            SELECT
                mission_id,
                acquired_at,
                heartbeat_at,
                expires_at
            FROM mission_worker_leases
            WHERE mission_id=?
            """,
            (mission_id,),
        ).fetchone()

    finally:
        conn.close()

    if lease is None:
        return None

    now = _utc_now()

    try:
        expires_at = datetime.fromisoformat(
            lease["expires_at"]
        )
        active = expires_at > now
    except (TypeError, ValueError):
        active = False

    return {
        "mission_id": lease["mission_id"],
        "active": active,
        "acquired_at": lease["acquired_at"],
        "heartbeat_at": lease["heartbeat_at"],
        "expires_at": lease["expires_at"],
    }


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


def _complete_mission(
    mission_id: int,
    worker_owner_token: str | None = None,
) -> None:
    """Finalize a verified mission and generate its deliverable."""

    log_event(
        mission_id,
        "Autonomous Worker",
        "reporting",
        "All verified tasks completed. Reporter is generating the final deliverable.",
    )

    deliverable = get_deliverable(mission_id)

    if not (
        deliverable
        and deliverable.get("status") == "Ready"
        and deliverable.get("content", "").strip()
    ):
        try:
            deliverable = create_deliverable(
                mission_id,
                worker_owner_token=worker_owner_token,
            )
        except Exception as error:
            mark_mission_report_error(
                mission_id,
                worker_owner_token=worker_owner_token,
            )

            log_event(
                mission_id,
                "Autonomous Worker",
                "error",
                (
                    "All mission tasks completed, but final deliverable "
                    f"generation failed: {error}"
                ),
            )

            raise RuntimeError(
                "Mission tasks completed successfully, but Reporter "
                f"failed to create the final deliverable: {error}"
            ) from error

    if not deliverable:
        raise RuntimeError(
            "Reporter returned no final deliverable."
        )

    finalize_mission_completion(
        mission_id,
        worker_owner_token=worker_owner_token,
    )

    log_event(
        mission_id,
        "Autonomous Worker",
        "completed",
        (
            "All verified mission tasks completed and the final "
            "Reporter deliverable was created."
        ),
    )


def _run_worker(
    mission_id: int,
    delay_seconds: float,
    owner_token: str,
) -> None:
    global _worker_thread

    heartbeat_stop = threading.Event()
    lease_lost = threading.Event()

    def heartbeat_worker_lease() -> None:
        interval = max(
            1.0,
            WORKER_LEASE_SECONDS / 3,
        )

        while not heartbeat_stop.wait(interval):
            try:
                renewed = renew_worker_lease(
                    mission_id,
                    owner_token,
                )
            except Exception:
                renewed = False

            if not renewed:
                lease_lost.set()
                return

    heartbeat_thread = threading.Thread(
        target=heartbeat_worker_lease,
        name=f"nuttz-worker-lease-{mission_id}",
        daemon=True,
    )
    heartbeat_thread.start()

    try:
        while not _stop_event.is_set():
            if lease_lost.is_set():
                raise RuntimeError(
                    "Autonomous Worker lost its mission lease."
                )
            tasks = get_tasks(mission_id)
            total, completed = _count_tasks(tasks)

            pending_tasks = [
                task
                for task in tasks
                if task.get("status") == "Pending"
            ]
            blocked_tasks = [
                task
                for task in tasks
                if task.get("status") == "Blocked"
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

            if blocked_tasks:
                blocked_task = blocked_tasks[0]

                _update_state(
                    status="Blocked",
                    current_task_id=blocked_task["id"],
                    last_error="",
                    last_message=(
                        f'Task {blocked_task["position"]} requires '
                        "verified evidence before the mission can continue."
                    ),
                )
                return

            if not pending_tasks:
                if completed == total:
                    _complete_mission(
                        mission_id,
                        worker_owner_token=owner_token,
                    )

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

            execution = execute_next_task(
                mission_id,
                worker_owner_token=owner_token,
            )

            if lease_lost.is_set():
                raise RuntimeError(
                    "Autonomous Worker lost its mission lease "
                    "while executing the current task."
                )

            if execution.get("status") == "Blocked":
                refreshed_tasks = get_tasks(mission_id)
                refreshed_total, refreshed_completed = _count_tasks(
                    refreshed_tasks
                )

                _update_state(
                    status="Blocked",
                    total_tasks=refreshed_total,
                    completed_tasks=refreshed_completed,
                    current_task_id=execution.get("task_id"),
                    last_error="",
                    last_message=(
                        f'Task {execution.get("position")} requires '
                        "verified evidence before the mission can continue."
                    ),
                )
                return

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
        heartbeat_stop.set()
        heartbeat_thread.join(timeout=2.0)

        try:
            release_worker_lease(
                mission_id,
                owner_token,
            )
        except Exception:
            pass

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
    blocked = [
        task for task in tasks if task.get("status") == "Blocked"
    ]

    if blocked:
        blocked_task = blocked[0]

        _update_state(
            status="Blocked",
            mission_id=mission_id,
            current_task_id=blocked_task["id"],
            total_tasks=total,
            completed_tasks=completed,
            last_message=(
                f'Task {blocked_task["position"]} requires verified '
                "evidence before the mission can continue."
            ),
            last_error="",
        )
        return _snapshot()

    if not pending:
        if completed == total:
            try:
                _complete_mission(mission_id)
            except Exception as error:
                _update_state(
                    status="Report Error",
                    mission_id=mission_id,
                    current_task_id=None,
                    total_tasks=total,
                    completed_tasks=completed,
                    last_message=(
                        "All mission tasks are complete, but final "
                        "deliverable generation failed."
                    ),
                    last_error=str(error),
                )
                return _snapshot()

            _update_state(
                status="Completed",
                mission_id=mission_id,
                current_task_id=None,
                total_tasks=total,
                completed_tasks=completed,
                last_message=(
                    "All mission tasks and the final deliverable "
                    "are complete."
                ),
                last_error="",
            )
        else:
            _update_state(
                status="Idle",
                mission_id=mission_id,
                current_task_id=None,
                total_tasks=total,
                completed_tasks=completed,
                last_message="No pending tasks remain.",
                last_error="",
            )

        return _snapshot()

    lease = acquire_worker_lease(mission_id)
    owner_token = lease["owner_token"]

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

    try:
        _worker_thread = threading.Thread(
            target=_run_worker,
            args=(
                mission_id,
                delay_seconds,
                owner_token,
            ),
            name=f"nuttz-worker-mission-{mission_id}",
            daemon=True,
        )
        _worker_thread.start()

    except Exception:
        release_worker_lease(
            mission_id,
            owner_token,
        )
        raise

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
