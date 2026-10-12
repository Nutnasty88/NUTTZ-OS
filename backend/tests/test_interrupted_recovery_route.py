import importlib

import pytest
from fastapi import HTTPException


missions = importlib.import_module(
    "app.routers.missions"
)

MISSION_ID = 17501


@pytest.fixture
def safe_recovery(monkeypatch):
    monkeypatch.setattr(
        missions,
        "get_worker_status",
        lambda: {"thread_alive": False},
    )
    monkeypatch.setattr(
        missions,
        "get_worker_lease",
        lambda mission_id: None,
    )


@pytest.mark.parametrize(
    "message",
    [
        "Mission plan review and explicit approval "
        "are required before interrupted recovery.",
        "Interrupted recovery requires replan review "
        "before the task can be reset.",
    ],
)
def test_recovery_guard_returns_409_without_starting_worker(
    monkeypatch,
    safe_recovery,
    message,
):
    started = []

    def reject_reset(mission_id):
        raise RuntimeError(message)

    monkeypatch.setattr(
        missions,
        "reset_interrupted_task",
        reject_reset,
    )
    monkeypatch.setattr(
        missions,
        "start_worker",
        lambda **kwargs: started.append(kwargs),
    )

    with pytest.raises(HTTPException) as caught:
        missions.resume_mission_recovery(MISSION_ID)

    assert caught.value.status_code == 409
    assert message in caught.value.detail
    assert started == []


def test_active_worker_blocks_recovery_before_reset(
    monkeypatch,
):
    reset_calls = []

    monkeypatch.setattr(
        missions,
        "get_worker_status",
        lambda: {
            "thread_alive": True,
            "mission_id": MISSION_ID,
        },
    )
    monkeypatch.setattr(
        missions,
        "reset_interrupted_task",
        lambda mission_id: reset_calls.append(mission_id),
    )

    with pytest.raises(HTTPException) as caught:
        missions.resume_mission_recovery(MISSION_ID)

    assert caught.value.status_code == 409
    assert reset_calls == []


def test_active_lease_blocks_recovery_before_reset(
    monkeypatch,
    safe_recovery,
):
    reset_calls = []

    monkeypatch.setattr(
        missions,
        "get_worker_lease",
        lambda mission_id: {
            "valid": True,
            "active": True,
        },
    )
    monkeypatch.setattr(
        missions,
        "reset_interrupted_task",
        lambda mission_id: reset_calls.append(mission_id),
    )

    with pytest.raises(HTTPException) as caught:
        missions.resume_mission_recovery(MISSION_ID)

    assert caught.value.status_code == 409
    assert reset_calls == []


def test_worker_start_failure_rolls_back_recovery(
    monkeypatch,
    safe_recovery,
):
    rollbacks = []

    monkeypatch.setattr(
        missions,
        "reset_interrupted_task",
        lambda mission_id: {
            "task_id": 102,
            "status": "Pending",
        },
    )

    def fail_start(**kwargs):
        raise RuntimeError("Lease unavailable")

    monkeypatch.setattr(
        missions,
        "start_worker",
        fail_start,
    )
    monkeypatch.setattr(
        missions,
        "rollback_interrupted_task_reset",
        lambda **kwargs: (
            rollbacks.append(kwargs)
            or {"status": "Interrupted"}
        ),
    )

    with pytest.raises(HTTPException) as caught:
        missions.resume_mission_recovery(MISSION_ID)

    assert caught.value.status_code == 409
    assert "safely rolled back" in caught.value.detail
    assert rollbacks == [
        {"mission_id": MISSION_ID, "task_id": 102}
    ]
