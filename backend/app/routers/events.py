from fastapi import APIRouter, Query

from app.services.events import get_events


router = APIRouter(
    prefix="/api/events",
    tags=["events"],
)


@router.get("")
def list_events(
    mission_id: int | None = None,
    limit: int = Query(default=50, ge=1, le=200),
):
    events = get_events(
        mission_id=mission_id,
        limit=limit,
    )

    return {
        "events": events,
        "count": len(events),
    }
