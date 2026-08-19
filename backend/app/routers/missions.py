from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.database.database import get_connection
from services.executor import (
    execute_next_task,
    get_tasks,
    sync_tasks,
)
from services.planner import create_plan, get_plan
from app.services.researcher import get_research_report, research
from services.autonomous_worker import get_worker_status, pause_worker, start_worker


router = APIRouter(
    prefix="/api/missions",
    tags=["missions"],
)


class MissionCreate(BaseModel):
    title: str
    assigned_agent: str
    priority: str


@router.get("/")
def get_missions():
    conn = get_connection()

    try:
        rows = conn.execute(
            """
            SELECT
                id,
                title,
                status,
                progress,
                assigned_agent,
                priority
            FROM missions
            ORDER BY id DESC
            """
        ).fetchall()
    finally:
        conn.close()

    return [
        {
            "id": row["id"],
            "title": row["title"],
            "status": row["status"],
            "progress": row["progress"],
            "agent": row["assigned_agent"],
            "priority": row["priority"],
        }
        for row in rows
    ]


@router.post("/")
def create_mission(mission: MissionCreate):
    conn = get_connection()

    try:
        cursor = conn.execute(
            """
            INSERT INTO missions
                (title, status, assigned_agent, priority)
            VALUES
                (?, ?, ?, ?)
            """,
            (
                mission.title,
                "Pending",
                mission.assigned_agent,
                mission.priority,
            ),
        )

        mission_id = cursor.lastrowid
        conn.commit()
    finally:
        conn.close()

    return {
        "success": True,
        "mission_id": mission_id,
        "message": "Mission created",
    }


@router.post("/{mission_id}/run")
def run_mission(mission_id: int):
    conn = get_connection()

    try:
        mission = conn.execute(
            """
            SELECT id, title
            FROM missions
            WHERE id=?
            """,
            (mission_id,),
        ).fetchone()

        if mission is None:
            raise HTTPException(
                status_code=404,
                detail=f"Mission {mission_id} was not found.",
            )

        conn.execute(
            """
            UPDATE missions
            SET
                status='Running',
                progress=10,
                updated_at=CURRENT_TIMESTAMP
            WHERE id=?
            """,
            (mission_id,),
        )
        conn.commit()
    finally:
        conn.close()

    try:
        planner_result = create_plan(mission_id)

        research_result = research(
            mission_id,
            mission["title"],
        )

        tasks = sync_tasks(
            mission_id,
            planner_result["plan"],
        )
    except Exception as error:
        conn = get_connection()

        try:
            conn.execute(
                """
                UPDATE missions
                SET
                    status='Error',
                    progress=0,
                    updated_at=CURRENT_TIMESTAMP
                WHERE id=?
                """,
                (mission_id,),
            )
            conn.commit()
        finally:
            conn.close()

        raise HTTPException(
            status_code=500,
            detail=f"Planner Agent failed: {error}",
        ) from error

    return {
        "success": True,
        "message": f"Mission {mission_id} is now running.",
        "planner": planner_result,
        "research": research_result,
        "tasks": tasks,
    }


@router.get("/{mission_id}/research")
def get_mission_research(mission_id: int):
    research_report = get_research_report(mission_id)

    if research_report is None:
        raise HTTPException(
            status_code=404,
            detail=f"No research exists for mission {mission_id}.",
        )

    return research_report


@router.get("/{mission_id}/plan")
def get_mission_plan(mission_id: int):
    plan = get_plan(mission_id)

    if plan is None:
        raise HTTPException(
            status_code=404,
            detail=f"No plan exists for mission {mission_id}.",
        )

    return plan


@router.get("/{mission_id}/tasks")
def get_mission_tasks(mission_id: int):
    return {
        "mission_id": mission_id,
        "tasks": get_tasks(mission_id),
    }


@router.post("/{mission_id}/tasks/sync")
def sync_mission_tasks(mission_id: int):
    plan = get_plan(mission_id)

    if plan is None:
        raise HTTPException(
            status_code=404,
            detail=f"No plan exists for mission {mission_id}.",
        )

    try:
        tasks = sync_tasks(
            mission_id,
            plan["plan"],
        )
    except Exception as error:
        raise HTTPException(
            status_code=500,
            detail=f"Task synchronization failed: {error}",
        ) from error

    return {
        "success": True,
        "mission_id": mission_id,
        "tasks": tasks,
    }


@router.post("/{mission_id}/execute-next")
def execute_mission_task(mission_id: int):
    try:
        executor_result = execute_next_task(mission_id)
    except ValueError as error:
        raise HTTPException(
            status_code=404,
            detail=str(error),
        ) from error
    except Exception as error:
        raise HTTPException(
            status_code=500,
            detail=f"Executor Agent failed: {error}",
        ) from error

    return {
        "success": True,
        "executor": executor_result,
        "tasks": get_tasks(mission_id),
    }
@router.get("/{mission_id}/worker/status")
def get_mission_worker_status(mission_id: int):
    worker = get_worker_status()

    return {
        "success": True,
        "requested_mission_id": mission_id,
        "worker": worker,
    }


@router.post("/{mission_id}/worker/start")
def start_mission_worker(
    mission_id: int,
    delay_seconds: float = 2.0,
):
    try:
        worker = start_worker(
            mission_id=mission_id,
            delay_seconds=delay_seconds,
        )
    except ValueError as error:
        raise HTTPException(
            status_code=400,
            detail=str(error),
        ) from error
    except RuntimeError as error:
        raise HTTPException(
            status_code=409,
            detail=str(error),
        ) from error
    except Exception as error:
        raise HTTPException(
            status_code=500,
            detail=f"Autonomous Worker failed to start: {error}",
        ) from error

    return {
        "success": True,
        "message": f"Autonomous Worker started for mission {mission_id}.",
        "worker": worker,
    }


@router.post("/{mission_id}/worker/pause")
def pause_mission_worker(mission_id: int):
    current = get_worker_status()

    if (
        current["thread_alive"]
        and current["mission_id"] != mission_id
    ):
        raise HTTPException(
            status_code=409,
            detail=(
                "The active worker belongs to mission "
                f'{current["mission_id"]}.'
            ),
        )

    worker = pause_worker()

    return {
        "success": True,
        "message": (
            "Pause requested. The active task will finish before stopping."
        ),
        "worker": worker,
    }
