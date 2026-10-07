import importlib

import pytest
from fastapi import HTTPException

missions = importlib.import_module(
    "app.routers.missions"
)


def _configure(
    monkeypatch,
    *,
    worker=None,
    lease=None,
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

    def revise(
        mission_id,
        task_id,
        *,
        title,
        instructions,
    ):
        calls.append(
            (
                "revise",
                mission_id,
                task_id,
                title,
                instructions,
            )
        )

        return {
            "id": task_id,
            "mission_id": mission_id,
            "position": 8,
            "title": title,
            "instructions": instructions,
            "status": "Pending",
        }

    monkeypatch.setattr(
        missions,
        "revise_pending_task",
        revise,
    )

    monkeypatch.setattr(
        missions,
        "get_mission_approval_status",
        lambda mission_id: {
            "mission_id": mission_id,
            "approved": False,
            "approved_at": None,
            "approved_plan_fingerprint": None,
            "current_plan_fingerprint": "new-fingerprint",
        },
    )

    return calls


def test_route_revises_pending_task_and_requires_reapproval(
    monkeypatch,
):
    calls = _configure(monkeypatch)

    result = missions.revise_mission_pending_task(
        9188,
        9237,
        missions.PendingTaskRevision(
            title="Verify persistence",
            instructions=(
                'Run `python main.py add "Buy milk"`. '
                "Then run `python main.py list` in a separate "
                "process and verify stdout is exactly `Buy milk`."
            ),
        ),
    )

    assert result["success"] is True
    assert result["task"]["id"] == 9237
    assert result["approval"]["approved"] is False
    assert "Fresh mission-plan approval" in result["message"]

    assert calls == [
        (
            "revise",
            9188,
            9237,
            "Verify persistence",
            (
                'Run `python main.py add "Buy milk"`. '
                "Then run `python main.py list` in a separate "
                "process and verify stdout is exactly `Buy milk`."
            ),
        )
    ]


def test_route_rejects_active_worker(
    monkeypatch,
):
    calls = _configure(
        monkeypatch,
        worker={
            "thread_alive": True,
            "mission_id": 9188,
        },
    )

    with pytest.raises(HTTPException) as caught:
        missions.revise_mission_pending_task(
            9188,
            9237,
            missions.PendingTaskRevision(
                title="Verify persistence",
                instructions="Run controlled verification.",
            ),
        )

    assert caught.value.status_code == 409
    assert "Worker is active" in caught.value.detail
    assert calls == []


def test_route_rejects_active_lease(
    monkeypatch,
):
    calls = _configure(
        monkeypatch,
        lease={
            "active": True,
            "valid": True,
        },
    )

    with pytest.raises(HTTPException) as caught:
        missions.revise_mission_pending_task(
            9188,
            9237,
            missions.PendingTaskRevision(
                title="Verify persistence",
                instructions="Run controlled verification.",
            ),
        )

    assert caught.value.status_code == 409
    assert "active worker lease" in caught.value.detail
    assert calls == []


def test_route_maps_missing_task_to_404(
    monkeypatch,
):
    _configure(monkeypatch)

    def missing(*args, **kwargs):
        raise ValueError(
            "Task 9237 was not found in mission 9188."
        )

    monkeypatch.setattr(
        missions,
        "revise_pending_task",
        missing,
    )

    with pytest.raises(HTTPException) as caught:
        missions.revise_mission_pending_task(
            9188,
            9237,
            missions.PendingTaskRevision(
                title="Verify persistence",
                instructions="Run controlled verification.",
            ),
        )

    assert caught.value.status_code == 404


def test_route_maps_started_task_to_409(
    monkeypatch,
):
    _configure(monkeypatch)

    def started(*args, **kwargs):
        raise RuntimeError(
            "Only an unstarted Pending task may be revised."
        )

    monkeypatch.setattr(
        missions,
        "revise_pending_task",
        started,
    )

    with pytest.raises(HTTPException) as caught:
        missions.revise_mission_pending_task(
            9188,
            9237,
            missions.PendingTaskRevision(
                title="Verify persistence",
                instructions="Run controlled verification.",
            ),
        )

    assert caught.value.status_code == 409
