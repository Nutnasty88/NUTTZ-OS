import pytest

from services import autonomous_worker


MISSION_ID = 17051


def _tasks():
    return [
        {
            "id": 101,
            "mission_id": MISSION_ID,
            "position": 1,
            "title": "Inspect service",
            "status": "Completed",
            "result": (
                "VERIFIED EXECUTION EVIDENCE:\n"
                "Service contract changed.\n"
                "REPLAN REQUIRED: remaining checks use old contract."
            ),
        },
        {
            "id": 102,
            "mission_id": MISSION_ID,
            "position": 2,
            "title": "Run remaining checks",
            "status": "Pending",
            "result": "",
        },
    ]


def test_startup_rejects_unresolved_replan_before_lease(monkeypatch):
    from services import executor

    lease_calls = []

    monkeypatch.setattr(
        autonomous_worker,
        "get_tasks",
        lambda mission_id: _tasks(),
    )
    monkeypatch.setattr(
        autonomous_worker,
        "require_mission_plan_approval",
        lambda mission_id: {"approved": True},
    )
    monkeypatch.setattr(
        executor,
        "get_mission_replan_history",
        lambda mission_id: [],
    )
    monkeypatch.setattr(
        autonomous_worker,
        "acquire_worker_lease",
        lambda mission_id: lease_calls.append(mission_id),
    )

    with pytest.raises(
        RuntimeError,
        match="requires replan review",
    ):
        autonomous_worker.start_worker(MISSION_ID)

    assert lease_calls == []


def test_worker_loop_stops_before_pending_task(monkeypatch):
    from services import executor

    executed = []
    released = []

    monkeypatch.setattr(
        autonomous_worker,
        "get_tasks",
        lambda mission_id: _tasks(),
    )
    monkeypatch.setattr(
        executor,
        "get_mission_replan_history",
        lambda mission_id: [],
    )
    monkeypatch.setattr(
        autonomous_worker,
        "execute_next_task",
        lambda *args, **kwargs: executed.append(args),
    )
    monkeypatch.setattr(
        autonomous_worker,
        "release_worker_lease",
        lambda mission_id, token: released.append(
            (mission_id, token)
        ) or True,
    )

    autonomous_worker._stop_event.clear()

    autonomous_worker._run_worker(
        MISSION_ID,
        0,
        "restart-test-owner",
    )

    assert executed == []
    assert released == [(MISSION_ID, "restart-test-owner")]

    state = autonomous_worker.get_worker_status()
    assert state["status"] == "Replan Required"
    assert state["completed_tasks"] == 1
    assert state["total_tasks"] == 2


def test_acknowledged_replan_allows_worker_startup(monkeypatch):
    from services import executor

    tasks = _tasks()
    evidence = tasks[0]["result"]

    monkeypatch.setattr(
        autonomous_worker,
        "get_tasks",
        lambda mission_id: tasks,
    )
    monkeypatch.setattr(
        autonomous_worker,
        "require_mission_plan_approval",
        lambda mission_id: {"approved": True},
    )
    monkeypatch.setattr(
        executor,
        "get_mission_replan_history",
        lambda mission_id: [
            {"triggering_evidence": evidence}
        ],
    )

    class StartupReached(Exception):
        pass

    def fake_acquire(mission_id):
        raise StartupReached()

    monkeypatch.setattr(
        autonomous_worker,
        "acquire_worker_lease",
        fake_acquire,
    )

    with pytest.raises(StartupReached):
        autonomous_worker.start_worker(MISSION_ID)
