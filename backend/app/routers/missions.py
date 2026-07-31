from fastapi import APIRouter
from pydantic import BaseModel

from app.database.database import get_connection

router = APIRouter(
    prefix="/api/missions",
    tags=["missions"],
)


class MissionCreate(BaseModel):
    title: str
    assigned_agent: str
    priority: str
    assigned_agent: str


@router.get("/")
def get_missions():
    conn = get_connection()

    rows = conn.execute(
        """
        SELECT
            id,
            title,
            status,
            assigned_agent
        FROM missions
        ORDER BY id DESC
        """
    ).fetchall()

    conn.close()

    return [
        {
            "id": row["id"],
            "title": row["title"],
            "status": row["status"],
            "agent": row["assigned_agent"],
        }
        for row in rows
    ]


@router.post("/")
def create_mission(mission: MissionCreate):
    conn = get_connection()

    conn.execute(
        """
        INSERT INTO missions
        (title, status, assigned_agent, priority)
        VALUES (?, ?, ?, ?)
        """,
        (
            mission.title,
            "Pending",
            mission.assigned_agent,
            mission.priority,
        ),
    )

    conn.commit()
    conn.close()

    return {
        "success": True,
        "message": "Mission created",
    }


@router.post("/{mission_id}/run")
def run_mission(mission_id: int):
    conn = get_connection()

    conn.execute(
        """
        UPDATE missions
        SET status='Running'
        WHERE id=?
        """,
        (mission_id,),
    )

    conn.commit()
    conn.close()

    return {
        "success": True,
        "message": f"Mission {mission_id} is now running."
    }
