from services import autonomous_worker


def test_worker_stops_for_replan_recommendation(
    monkeypatch,
):
    updates = []

    monkeypatch.setattr(
        autonomous_worker,
        "_update_state",
        lambda **changes: updates.append(changes),
    )

    execution = {
        "mission_id": 17001,
        "task_id": 101,
        "position": 1,
        "title": "Inspect service",
        "status": "Completed",
        "replan_advisory": {
            "recommended": True,
            "reason": "remaining checks use old contract.",
            "triggering_evidence": (
                "VERIFIED EXECUTION EVIDENCE:\n"
                "REPLAN REQUIRED: remaining checks use old contract."
            ),
            "triggering_task": {
                "id": 101,
                "position": 1,
                "status": "Completed",
            },
            "pending_tasks": [
                {
                    "id": 102,
                    "position": 2,
                    "status": "Pending",
                },
            ],
        },
    }

    stopped = autonomous_worker._stop_for_replan_advisory(
        execution,
        total_tasks=2,
        completed_tasks=1,
    )

    assert stopped is True
    assert len(updates) == 1

    update = updates[0]

    assert update["status"] == "Replan Required"
    assert update["current_task_id"] is None
    assert update["total_tasks"] == 2
    assert update["completed_tasks"] == 1
    assert update["last_error"] == ""
    assert "remaining checks use old contract." in update["last_message"]


def test_worker_continues_without_replan_recommendation(
    monkeypatch,
):
    updates = []

    monkeypatch.setattr(
        autonomous_worker,
        "_update_state",
        lambda **changes: updates.append(changes),
    )

    execution = {
        "mission_id": 17001,
        "task_id": 101,
        "position": 1,
        "title": "Inspect service",
        "status": "Completed",
        "replan_advisory": {
            "recommended": False,
            "reason": "",
            "triggering_evidence": "",
            "triggering_task": None,
            "pending_tasks": [
                {
                    "id": 102,
                    "position": 2,
                    "status": "Pending",
                },
            ],
        },
    }

    stopped = autonomous_worker._stop_for_replan_advisory(
        execution,
        total_tasks=2,
        completed_tasks=1,
    )

    assert stopped is False
    assert updates == []


def test_worker_continues_when_execution_has_no_replan_advisory(
    monkeypatch,
):
    updates = []

    monkeypatch.setattr(
        autonomous_worker,
        "_update_state",
        lambda **changes: updates.append(changes),
    )

    execution = {
        "mission_id": 17001,
        "task_id": 101,
        "position": 1,
        "title": "Inspect service",
        "status": "Completed",
    }

    stopped = autonomous_worker._stop_for_replan_advisory(
        execution,
        total_tasks=2,
        completed_tasks=1,
    )

    assert stopped is False
    assert updates == []


def test_run_worker_stops_before_claiming_second_task_on_replan(
    monkeypatch,
):
    task_one_pending = {
        "id": 101,
        "mission_id": 17001,
        "position": 1,
        "title": "Inspect service",
        "instructions": "Inspect the service.",
        "status": "Pending",
        "result": "",
    }

    task_one_completed = {
        **task_one_pending,
        "status": "Completed",
        "result": (
            "VERIFIED EXECUTION EVIDENCE:\n"
            "service contract changed\n"
            "REPLAN REQUIRED: remaining checks use old contract."
        ),
    }

    task_two_pending = {
        "id": 102,
        "mission_id": 17001,
        "position": 2,
        "title": "Run old checks",
        "instructions": "Run the old checks.",
        "status": "Pending",
        "result": "",
    }

    task_reads = [
        [
            task_one_pending,
            task_two_pending,
        ],
        [
            task_one_completed,
            task_two_pending,
        ],
    ]

    execute_calls = []
    released = []

    def fake_get_tasks(mission_id):
        assert mission_id == 17001

        if task_reads:
            return task_reads.pop(0)

        return [
            task_one_completed,
            task_two_pending,
        ]

    def fake_execute_next_task(
        mission_id,
        *,
        worker_owner_token=None,
    ):
        execute_calls.append(
            (mission_id, worker_owner_token)
        )

        if len(execute_calls) > 1:
            raise AssertionError(
                "Worker claimed a second task after replan advisory."
            )

        return {
            "mission_id": mission_id,
            "task_id": 101,
            "position": 1,
            "title": "Inspect service",
            "status": "Completed",
            "result": task_one_completed["result"],
            "replan_advisory": {
                "recommended": True,
                "reason": "remaining checks use old contract.",
                "triggering_evidence": task_one_completed["result"],
                "triggering_task": task_one_completed,
                "pending_tasks": [task_two_pending],
            },
        }

    monkeypatch.setattr(
        autonomous_worker,
        "get_tasks",
        fake_get_tasks,
    )
    monkeypatch.setattr(
        autonomous_worker,
        "execute_next_task",
        fake_execute_next_task,
    )
    monkeypatch.setattr(
        autonomous_worker,
        "renew_worker_lease",
        lambda mission_id, owner_token: {
            "mission_id": mission_id,
            "owner_token": owner_token,
        },
    )
    monkeypatch.setattr(
        autonomous_worker,
        "release_worker_lease",
        lambda mission_id, owner_token: released.append(
            (mission_id, owner_token)
        ) or True,
    )

    autonomous_worker._stop_event.clear()

    autonomous_worker._run_worker(
        17001,
        0,
        "replan-test-owner",
    )

    assert execute_calls == [
        (17001, "replan-test-owner"),
    ]

    assert released == [
        (17001, "replan-test-owner"),
    ]

    state = autonomous_worker.get_worker_status()

    assert state["status"] == "Replan Required"
    assert state["current_task_id"] is None
    assert state["total_tasks"] == 2
    assert state["completed_tasks"] == 1
    assert state["last_error"] == ""
    assert "remaining checks use old contract." in state["last_message"]

    assert task_reads == []
