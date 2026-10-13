import importlib

import pytest
from fastapi import HTTPException

missions = importlib.import_module("app.routers.missions")
MISSION_ID = 17501


class FakeMissionConnection:
    def __init__(self, exists=True):
        self.exists = exists

    def execute(self, query, params):
        assert "SELECT 1 FROM missions" in query
        assert len(params) == 1
        return self

    def fetchone(self):
        return (1,) if self.exists else None

    def close(self):
        pass


@pytest.fixture
def audit_setup(monkeypatch):
    events = []

    monkeypatch.setattr(
        missions,
        "get_connection",
        lambda: FakeMissionConnection(exists=True),
    )

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
    monkeypatch.setattr(
        missions,
        "log_event",
        lambda mission_id, agent, event_type, message:
            events.append((mission_id, agent, event_type, message)),
        raising=False,
    )

    return events


def audit_types(events):
    return [
        event_type
        for _, agent, event_type, _ in events
        if agent == "Recovery"
    ]


def test_active_worker_recovery_denial_is_audited(
    monkeypatch,
    audit_setup,
):
    monkeypatch.setattr(
        missions,
        "get_worker_status",
        lambda: {
            "thread_alive": True,
            "mission_id": MISSION_ID,
        },
    )

    with pytest.raises(HTTPException) as caught:
        missions.resume_mission_recovery(MISSION_ID)

    assert caught.value.status_code == 409
    assert audit_types(audit_setup) == [
        "recovery_requested",
        "recovery_denied",
    ]


def test_approval_denial_is_audited(
    monkeypatch,
    audit_setup,
):
    def reject_reset(mission_id):
        raise RuntimeError(
            "Explicit approval required"
        )

    monkeypatch.setattr(
        missions,
        "reset_interrupted_task",
        reject_reset,
    )

    with pytest.raises(HTTPException) as caught:
        missions.resume_mission_recovery(MISSION_ID)

    assert caught.value.status_code == 409
    assert audit_types(audit_setup) == [
        "recovery_requested",
        "recovery_denied",
    ]


def test_successful_recovery_is_audited(
    monkeypatch,
    audit_setup,
):
    monkeypatch.setattr(
        missions,
        "reset_interrupted_task",
        lambda mission_id: {
            "task_id": 102,
            "status": "Pending",
        },
    )

    monkeypatch.setattr(
        missions,
        "start_worker",
        lambda **kwargs: {"started": True},
    )

    monkeypatch.setattr(
        missions,
        "get_tasks",
        lambda mission_id: [],
    )

    result = missions.resume_mission_recovery(MISSION_ID)

    assert result["success"] is True
    assert audit_types(audit_setup) == [
        "recovery_requested",
        "recovery_authorized",
        "recovery_started",
    ]


def test_worker_start_failure_is_audited(
    monkeypatch,
    audit_setup,
):
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
        lambda **kwargs: {"status": "Interrupted"},
    )

    with pytest.raises(HTTPException) as caught:
        missions.resume_mission_recovery(MISSION_ID)

    assert caught.value.status_code == 409
    assert audit_types(audit_setup) == [
        "recovery_requested",
        "recovery_authorized",
        "recovery_failed",
    ]


@pytest.mark.parametrize(
    "lease",
    [
        {"valid": False, "active": False},
        {"valid": True, "active": True},
    ],
)
def test_lease_denial_is_audited(
    monkeypatch,
    audit_setup,
    lease,
):
    monkeypatch.setattr(
        missions,
        "get_worker_lease",
        lambda mission_id: lease,
    )

    def forbidden_reset(mission_id):
        pytest.fail(
            "Recovery must not reset a task with an unsafe lease."
        )

    monkeypatch.setattr(
        missions,
        "reset_interrupted_task",
        forbidden_reset,
    )

    with pytest.raises(HTTPException) as caught:
        missions.resume_mission_recovery(MISSION_ID)

    assert caught.value.status_code == 409
    assert audit_types(audit_setup) == [
        "recovery_requested",
        "recovery_denied",
    ]


def test_missing_mission_does_not_start_worker(
    monkeypatch,
):
    monkeypatch.setattr(
        missions,
        "get_connection",
        lambda: FakeMissionConnection(exists=False),
    )

    def forbidden_audit(*args, **kwargs):
        pytest.fail(
            "Missing mission must not produce audit events."
        )

    def forbidden_start(**kwargs):
        pytest.fail(
            "Missing mission must never start a worker."
        )

    monkeypatch.setattr(
        missions,
        "log_event",
        forbidden_audit,
    )
    monkeypatch.setattr(
        missions,
        "start_worker",
        forbidden_start,
    )

    with pytest.raises(HTTPException) as caught:
        missions.resume_mission_recovery(99999999)

    assert caught.value.status_code == 404
    assert "not found" in str(caught.value.detail).lower()


def test_final_audit_failure_does_not_hide_started_worker(
    monkeypatch,
    audit_setup,
):
    started = []

    def start(mission_id, delay_seconds):
        started.append(mission_id)
        return {"started": True}

    def log(mission_id, agent, event_type, message):
        if event_type == "recovery_started":
            raise OSError("Simulated audit write failure")

    monkeypatch.setattr(
        missions, "start_worker", start
    )
    monkeypatch.setattr(
        missions, "log_event", log
    )
    monkeypatch.setattr(
        missions, "reset_interrupted_task",
        lambda mission_id: {
            "task_id": 102,
            "status": "Pending",
        },
    )
    monkeypatch.setattr(
        missions, "get_tasks",
        lambda mission_id: [],
    )

    result = missions.resume_mission_recovery(MISSION_ID)

    assert started == [MISSION_ID]
    assert result["success"] is True
    assert "audit event could not be saved" in (
        result["audit_warning"]
    )


def test_failed_audit_preserves_worker_start_error(
    monkeypatch,
    audit_setup,
):
    rollbacks = []

    def start_failure(**kwargs):
        raise RuntimeError("Lease unavailable")

    def rollback(mission_id, task_id):
        rollbacks.append((mission_id, task_id))
        return {"status": "Interrupted"}

    def audit(mission_id, agent, event_type, message):
        if event_type == "recovery_failed":
            raise OSError("Audit database unavailable")

    monkeypatch.setattr(
        missions, "start_worker", start_failure
    )
    monkeypatch.setattr(
        missions, "rollback_interrupted_task_reset", rollback
    )
    monkeypatch.setattr(
        missions, "log_event", audit
    )
    monkeypatch.setattr(
        missions, "reset_interrupted_task",
        lambda mission_id: {
            "task_id": 102,
            "status": "Pending",
        },
    )

    with pytest.raises(HTTPException) as caught:
        missions.resume_mission_recovery(MISSION_ID)

    assert caught.value.status_code == 409
    assert "Lease unavailable" in str(caught.value.detail)
    assert rollbacks == [(MISSION_ID, 102)]


def test_authorization_audit_failure_prevents_worker_start(
    monkeypatch,
    audit_setup,
):
    started = []
    rollbacks = []

    def audit(mission_id, agent, event_type, message):
        if event_type == "recovery_authorized":
            raise OSError("Authorization audit unavailable")

    def start(**kwargs):
        started.append(kwargs)
        pytest.fail("Worker must not start")

    def rollback(mission_id, task_id):
        rollbacks.append((mission_id, task_id))
        return {"status": "Interrupted"}

    monkeypatch.setattr(
        missions, "log_event", audit
    )
    monkeypatch.setattr(
        missions, "start_worker", start
    )
    monkeypatch.setattr(
        missions,
        "rollback_interrupted_task_reset",
        rollback,
    )
    monkeypatch.setattr(
        missions,
        "reset_interrupted_task",
        lambda mission_id: {
            "task_id": 102,
            "status": "Pending",
        },
    )

    with pytest.raises(HTTPException) as caught:
        missions.resume_mission_recovery(MISSION_ID)

    assert caught.value.status_code == 500
    assert started == []
    assert rollbacks == [(MISSION_ID, 102)]
    assert "authorization audit failed" in (
        str(caught.value.detail).lower()
    )
    assert "Authorization audit unavailable" in (
        str(caught.value.detail)
    )
