import importlib
import sqlite3

import pytest
from fastapi import HTTPException

missions = importlib.import_module("app.routers.missions")


@pytest.fixture
def advisory_db(tmp_path, monkeypatch):
    db_path = tmp_path / "replan-advisory.db"

    def connect():
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        return conn

    conn = connect()
    try:
        conn.execute(
            "CREATE TABLE missions (id INTEGER PRIMARY KEY)"
        )
        conn.execute(
            "INSERT INTO missions (id) VALUES (9188)"
        )
        conn.commit()
    finally:
        conn.close()

    monkeypatch.setattr(
        missions,
        "get_connection",
        connect,
    )

    return connect


def _tasks(with_replan=True):
    evidence = (
        "VERIFIED EXECUTION EVIDENCE:\n"
        "Service uses a different persistence contract.\n"
        "REPLAN REQUIRED: remaining verification assumes "
        "the obsolete contract."
    )

    if not with_replan:
        evidence = (
            "VERIFIED EXECUTION EVIDENCE:\n"
            "Service passed deterministic verification."
        )

    return [
        {
            "id": 101,
            "mission_id": 9188,
            "position": 1,
            "title": "Inspect service",
            "instructions": "Inspect current behavior",
            "status": "Completed",
            "result": evidence,
        },
        {
            "id": 102,
            "mission_id": 9188,
            "position": 2,
            "title": "Verify service",
            "instructions": "Verify persistence",
            "status": "Pending",
            "result": "",
        },
    ]


def _database_snapshot(connect):
    conn = connect()
    try:
        return conn.execute(
            "SELECT id FROM missions ORDER BY id"
        ).fetchall()
    finally:
        conn.close()


def test_advisory_returns_trusted_persisted_evidence(
    monkeypatch,
    advisory_db,
):
    tasks = _tasks()

    monkeypatch.setattr(
        missions,
        "get_tasks",
        lambda mission_id: tasks,
    )

    before = _database_snapshot(advisory_db)

    result = missions.mission_replan_advisory(9188)

    after = _database_snapshot(advisory_db)

    assert result["success"] is True
    assert result["mission_id"] == 9188

    advisory = result["advisory"]

    assert advisory["recommended"] is True
    assert "obsolete contract" in advisory["reason"]
    assert "VERIFIED EXECUTION EVIDENCE" in (
        advisory["triggering_evidence"]
    )
    assert advisory["triggering_task"]["id"] == 101
    assert advisory["pending_tasks"][0]["id"] == 102

    assert before == after


def test_advisory_reports_no_replan_for_normal_evidence(
    monkeypatch,
    advisory_db,
):
    monkeypatch.setattr(
        missions,
        "get_tasks",
        lambda mission_id: _tasks(with_replan=False),
    )

    result = missions.mission_replan_advisory(9188)

    assert result["advisory"]["recommended"] is False
    assert result["advisory"]["reason"] == ""
    assert result["advisory"]["triggering_evidence"] == ""


def test_advisory_rejects_missing_mission(
    advisory_db,
):
    with pytest.raises(HTTPException) as caught:
        missions.mission_replan_advisory(999999)

    assert caught.value.status_code == 404


def test_advisory_maps_detector_failure_to_500(
    monkeypatch,
    advisory_db,
):
    monkeypatch.setattr(
        missions,
        "get_tasks",
        lambda mission_id: [],
    )

    def fail_detection(tasks):
        raise RuntimeError("simulated detector failure")

    monkeypatch.setattr(
        missions,
        "detect_pending_replan_need",
        fail_detection,
    )

    with pytest.raises(HTTPException) as caught:
        missions.mission_replan_advisory(9188)

    assert caught.value.status_code == 500
    assert "simulated detector failure" in (
        caught.value.detail
    )


def test_advisory_route_registered_in_openapi():
    from app.main import app

    path = "/api/missions/{mission_id}/replan/advisory"
    schema = app.openapi()

    assert path in schema["paths"]
    assert "get" in schema["paths"][path]


def test_advisory_reads_real_sqlite_evidence_without_mutation(
    tmp_path,
    monkeypatch,
):
    from services import executor
    from test_mission_replanning import _setup_database

    mission_id, connect = _setup_database(
        tmp_path,
        monkeypatch,
    )

    # Both the router and Executor must use the test database.
    monkeypatch.setattr(
        missions,
        "get_connection",
        connect,
    )

    evidence = (
        "VERIFIED EXECUTION EVIDENCE:\n"
        "Service uses a different persistence contract.\n"
        "REPLAN REQUIRED: remaining verification assumes "
        "the obsolete contract."
    )

    conn = connect()
    try:
        conn.execute(
            """
            UPDATE mission_tasks
            SET result=?
            WHERE mission_id=? AND position=1
            """,
            (evidence, mission_id),
        )
        conn.commit()
    finally:
        conn.close()

    def snapshot():
        connection = connect()
        try:
            tasks = [
                tuple(row)
                for row in connection.execute(
                    """
                    SELECT *
                    FROM mission_tasks
                    WHERE mission_id=?
                    ORDER BY position
                    """,
                    (mission_id,),
                ).fetchall()
            ]

            mission = tuple(
                connection.execute(
                    "SELECT * FROM missions WHERE id=?",
                    (mission_id,),
                ).fetchone()
            )

            plan = tuple(
                connection.execute(
                    "SELECT * FROM mission_plans WHERE mission_id=?",
                    (mission_id,),
                ).fetchone()
            )

            return tasks, mission, plan
        finally:
            connection.close()

    before = snapshot()

    result = missions.mission_replan_advisory(mission_id)

    after = snapshot()

    assert result["success"] is True
    assert result["mission_id"] == mission_id

    advisory = result["advisory"]

    assert advisory["recommended"] is True
    assert (
        advisory["reason"]
        == "remaining verification assumes the obsolete contract."
    )
    assert advisory["triggering_evidence"] == evidence
    assert advisory["triggering_task"]["position"] == 1
    assert len(advisory["pending_tasks"]) == 2

    assert before == after


def test_advisory_does_not_repeat_evidence_after_applied_replan(
    tmp_path,
    monkeypatch,
):
    from services import executor
    from test_mission_replanning import _setup_database

    mission_id, connect = _setup_database(
        tmp_path,
        monkeypatch,
    )

    monkeypatch.setattr(
        missions,
        "get_connection",
        connect,
    )

    evidence = (
        "VERIFIED EXECUTION EVIDENCE:\n"
        "Persistence contract changed.\n"
        "REPLAN REQUIRED: update the remaining verification."
    )

    conn = connect()
    try:
        conn.execute(
            """
            UPDATE mission_tasks
            SET result=?
            WHERE mission_id=? AND position=1
            """,
            (evidence, mission_id),
        )
        conn.commit()
    finally:
        conn.close()

    before = missions.mission_replan_advisory(mission_id)
    assert before["advisory"]["recommended"] is True

    replacement_plan = (
        "1. Build application\n"
        "   Implement the application\n"
        "2. Updated verification\n"
        "   Verify the new persistence contract\n"
        "3. Updated final check\n"
        "   Confirm persisted results"
    )

    preview = executor.preview_pending_replan(
        mission_id,
        replacement_plan,
    )

    executor.replan_pending_tasks(
        mission_id,
        replacement_plan,
        expected_state_fingerprint=preview["state_fingerprint"],
        expected_proposal_fingerprint=preview["proposal_fingerprint"],
        reason=before["advisory"]["reason"],
        triggering_evidence=before["advisory"]["triggering_evidence"],
    )

    after = missions.mission_replan_advisory(mission_id)

    assert after["advisory"]["recommended"] is False
    assert after["advisory"]["reason"] == ""
    assert after["advisory"]["triggering_evidence"] == ""

    conn = connect()
    try:
        completed = conn.execute(
            """
            SELECT result
            FROM mission_tasks
            WHERE mission_id=? AND position=1
            """,
            (mission_id,),
        ).fetchone()

        history = conn.execute(
            """
            SELECT reason, triggering_evidence
            FROM mission_replan_history
            WHERE mission_id=?
            ORDER BY id DESC
            LIMIT 1
            """,
            (mission_id,),
        ).fetchone()

        assert completed["result"] == evidence
        assert history["triggering_evidence"] == (
            before["advisory"]["triggering_evidence"]
        )
        assert history["reason"] == before["advisory"]["reason"]
    finally:
        conn.close()


def test_executor_and_worker_ignore_acknowledged_replan_evidence(
    tmp_path,
    monkeypatch,
):
    from services import autonomous_worker, executor
    from test_mission_replanning import _setup_database

    mission_id, connect = _setup_database(
        tmp_path,
        monkeypatch,
    )

    evidence = (
        "VERIFIED EXECUTION EVIDENCE:\n"
        "Persistence contract changed.\n"
        "REPLAN REQUIRED: update remaining verification."
    )

    conn = connect()
    try:
        conn.execute(
            """
            UPDATE mission_tasks
            SET result=?
            WHERE mission_id=? AND position=1
            """,
            (evidence, mission_id),
        )
        conn.commit()
    finally:
        conn.close()

    tasks = executor.get_tasks(mission_id)
    advisory = executor.detect_pending_replan_need(tasks)

    assert advisory["recommended"] is True

    replacement_plan = (
        "1. Build application\n"
        "   Implement the application\n"
        "2. Updated verification\n"
        "   Verify the new persistence contract\n"
        "3. Updated final check\n"
        "   Confirm persisted results"
    )

    preview = executor.preview_pending_replan(
        mission_id,
        replacement_plan,
    )

    executor.replan_pending_tasks(
        mission_id,
        replacement_plan,
        expected_state_fingerprint=preview["state_fingerprint"],
        expected_proposal_fingerprint=preview["proposal_fingerprint"],
        reason=advisory["reason"],
        triggering_evidence=advisory["triggering_evidence"],
    )

    completed_before = executor.get_tasks(mission_id)[0]

    execution = {
        "mission_id": mission_id,
        "task_id": completed_before["id"],
        "status": "Completed",
        "result": completed_before["result"],
    }

    result = executor._attach_replan_advisory(
        mission_id,
        execution,
    )

    assert result["replan_advisory"]["recommended"] is False

    updates = []
    monkeypatch.setattr(
        autonomous_worker,
        "_update_state",
        lambda **changes: updates.append(changes),
    )

    stopped = autonomous_worker._stop_for_replan_advisory(
        result,
        total_tasks=3,
        completed_tasks=1,
    )

    assert stopped is False
    assert updates == []

    completed_after = executor.get_tasks(mission_id)[0]

    assert completed_after == completed_before
    assert completed_after["result"] == evidence
