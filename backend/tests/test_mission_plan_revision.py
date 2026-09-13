import importlib
import sqlite3

import pytest
from fastapi import HTTPException

from services import planner


missions = importlib.import_module(
    "app.routers.missions"
)


def _configure_router(
    monkeypatch,
    *,
    tasks=None,
    worker=None,
    lease=None,
    repair_history=None,
):
    calls = []

    monkeypatch.setattr(
        missions,
        "get_worker_status",
        lambda: worker or {
            "thread_alive": False,
            "mission_id": None,
        },
    )
    monkeypatch.setattr(
        missions,
        "get_worker_lease",
        lambda mission_id: lease,
    )
    monkeypatch.setattr(
        missions,
        "get_tasks",
        lambda mission_id: tasks
        if tasks is not None
        else [
            {
                "id": 1,
                "position": 1,
                "title": "Build",
                "status": "Pending",
            }
        ],
    )
    monkeypatch.setattr(
        missions,
        "get_repair_history",
        lambda mission_id: repair_history or [],
    )

    def invalidate(mission_id):
        calls.append(("invalidate", mission_id))
        return {"approved": False}

    def create(mission_id, revision_feedback=None):
        calls.append(
            (
                "create",
                mission_id,
                revision_feedback,
            )
        )
        return {
            "mission_id": mission_id,
            "plan": "1. **Revised task**\nDo revised work",
            "status": "Ready",
        }

    def synchronize(mission_id, plan):
        calls.append(("sync", mission_id, plan))
        return [
            {
                "id": 2,
                "position": 1,
                "title": "Revised task",
                "status": "Pending",
            }
        ]

    monkeypatch.setattr(
        missions,
        "invalidate_mission_plan_approval",
        invalidate,
    )
    monkeypatch.setattr(
        missions,
        "create_plan",
        create,
    )
    monkeypatch.setattr(
        missions,
        "sync_tasks",
        synchronize,
    )
    monkeypatch.setattr(
        missions,
        "get_mission_approval_status",
        lambda mission_id: {
            "mission_id": mission_id,
            "approved": False,
            "task_count": 1,
        },
    )

    return calls


def test_revision_invalidates_before_replanning(
    monkeypatch,
):
    calls = _configure_router(monkeypatch)

    result = missions.revise_mission_plan(
        42,
        missions.MissionPlanRevision(
            feedback="Add a browser test"
        ),
    )

    assert result["success"] is True
    assert result["approval"]["approved"] is False
    assert "Fresh approval is required" in result["message"]

    assert calls[0] == ("invalidate", 42)
    assert calls[1] == (
        "create",
        42,
        "Add a browser test",
    )
    assert calls[2][0] == "sync"


def test_revision_rejects_empty_feedback(
    monkeypatch,
):
    _configure_router(monkeypatch)

    with pytest.raises(HTTPException) as caught:
        missions.revise_mission_plan(
            42,
            missions.MissionPlanRevision(
                feedback="   "
            ),
        )

    assert caught.value.status_code == 400


def test_revision_rejects_started_tasks(
    monkeypatch,
):
    calls = _configure_router(
        monkeypatch,
        tasks=[
            {
                "id": 1,
                "position": 1,
                "title": "Build",
                "status": "Completed",
            }
        ],
    )

    with pytest.raises(HTTPException) as caught:
        missions.revise_mission_plan(
            42,
            missions.MissionPlanRevision(
                feedback="Change the test"
            ),
        )

    assert caught.value.status_code == 409
    assert "only before execution" in caught.value.detail
    assert calls == []


def test_revision_rejects_active_worker(
    monkeypatch,
):
    calls = _configure_router(
        monkeypatch,
        worker={
            "thread_alive": True,
            "mission_id": 42,
        },
    )

    with pytest.raises(HTTPException) as caught:
        missions.revise_mission_plan(
            42,
            missions.MissionPlanRevision(
                feedback="Change the test"
            ),
        )

    assert caught.value.status_code == 409
    assert "Worker is active" in caught.value.detail
    assert calls == []


def test_revision_rejects_active_lease(
    monkeypatch,
):
    calls = _configure_router(
        monkeypatch,
        lease={
            "active": True,
            "valid": True,
        },
    )

    with pytest.raises(HTTPException) as caught:
        missions.revise_mission_plan(
            42,
            missions.MissionPlanRevision(
                feedback="Change the test"
            ),
        )

    assert caught.value.status_code == 409
    assert "active worker lease" in caught.value.detail
    assert calls == []


def test_revision_rejects_repair_history(
    monkeypatch,
):
    calls = _configure_router(
        monkeypatch,
        repair_history=[
            {
                "id": 7,
                "task_position": 1,
            }
        ],
    )

    with pytest.raises(HTTPException) as caught:
        missions.revise_mission_plan(
            42,
            missions.MissionPlanRevision(
                feedback="Change the test"
            ),
        )

    assert caught.value.status_code == 409
    assert "repair history" in caught.value.detail
    assert calls == []


def test_planner_receives_existing_plan_and_feedback(
    monkeypatch,
    tmp_path,
):
    database_path = tmp_path / "revision-planner.db"

    def get_connection():
        connection = sqlite3.connect(database_path)
        connection.row_factory = sqlite3.Row
        return connection

    connection = get_connection()

    try:
        connection.executescript(
            """
            CREATE TABLE missions (
                id INTEGER PRIMARY KEY,
                title TEXT NOT NULL,
                status TEXT NOT NULL,
                assigned_agent TEXT NOT NULL,
                priority TEXT NOT NULL,
                progress INTEGER NOT NULL DEFAULT 0,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE mission_plans (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                mission_id INTEGER NOT NULL UNIQUE,
                model TEXT NOT NULL,
                plan TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'Ready',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            """
        )

        connection.execute(
            """
            INSERT INTO missions (
                id,
                title,
                status,
                assigned_agent,
                priority
            )
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                61,
                "Build a Python CLI",
                "Running",
                "Planner",
                "Normal",
            ),
        )

        connection.execute(
            """
            INSERT INTO mission_plans (
                mission_id,
                model,
                plan,
                status
            )
            VALUES (?, ?, ?, ?)
            """,
            (
                61,
                "qwen3:8b",
                "1. **Old task**\nCreate old.py",
                "Ready",
            ),
        )

        connection.commit()
    finally:
        connection.close()

    captured = {}

    def fake_chat_with_ollama(**kwargs):
        captured["messages"] = kwargs["messages"]

        return {
            "message": {
                "content": (
                    "1. **Revised task**\n"
                    "Create main.py with a browser test\n\n"
                    "Success-check:\n"
                    "python main.py"
                )
            }
        }

    monkeypatch.setattr(
        planner,
        "get_connection",
        get_connection,
    )
    monkeypatch.setattr(
        planner,
        "chat_with_ollama",
        fake_chat_with_ollama,
    )
    monkeypatch.setattr(
        planner,
        "log_event",
        lambda *args, **kwargs: None,
    )

    result = planner.create_plan(
        61,
        revision_feedback="Add a browser test",
    )

    prompt = captured["messages"][-1]["content"]

    assert "Existing plan:" in prompt
    assert "Create old.py" in prompt
    assert "Operator revision request:" in prompt
    assert "Add a browser test" in prompt
    assert "complete replacement plan" in prompt
    assert "browser test" in result["plan"]
