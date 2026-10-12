"""Regression tests for read-only mission recovery observability."""

import importlib

import pytest

from services import executor
from test_interrupted_recovery_guard import (
    EVIDENCE,
    MISSION_ID,
    recovery_db,
    snapshot,
)

missions = importlib.import_module("app.routers.missions")


@pytest.fixture
def status_db(recovery_db, monkeypatch):
    """Use the existing isolated recovery database."""
    monkeypatch.setattr(
        missions,
        "get_connection",
        recovery_db,
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
    conn = recovery_db()
    try:
        conn.execute(
            "ALTER TABLE missions ADD COLUMN title TEXT"
        )
        conn.execute(
            "UPDATE missions SET title=? WHERE id=?",
            ("Recovery status regression", MISSION_ID),
        )
        conn.commit()
    finally:
        conn.close()

    return recovery_db


def acknowledge_evidence(connect):
    conn = connect()
    try:
        conn.execute(
            """
            INSERT INTO mission_replan_history (
                mission_id,
                original_plan,
                replacement_plan,
                reason,
                triggering_evidence,
                state_fingerprint
            )
            VALUES (?, 'old', 'new', 'Reviewed', ?, 'test')
            """,
            (MISSION_ID, EVIDENCE),
        )
        conn.commit()
    finally:
        conn.close()


def test_unapproved_interrupted_task_cannot_resume(status_db):
    before = snapshot(status_db)

    result = missions.get_mission_recovery_status(MISSION_ID)

    assert result["recovery"]["can_resume"] is False
    assert "approval" in (
        result["recovery"]["message"].lower()
    )
    assert snapshot(status_db) == before


def test_unresolved_replan_evidence_blocks_resume(status_db):
    assert executor.approve_mission_plan(
        MISSION_ID
    )["approved"]

    before = snapshot(status_db)

    result = missions.get_mission_recovery_status(MISSION_ID)

    assert result["recovery"]["can_resume"] is False
    assert "replan" in (
        result["recovery"]["message"].lower()
    )
    assert snapshot(status_db) == before


def test_acknowledged_evidence_allows_resume(status_db):
    assert executor.approve_mission_plan(
        MISSION_ID
    )["approved"]

    acknowledge_evidence(status_db)

    before = snapshot(status_db)

    result = missions.get_mission_recovery_status(MISSION_ID)

    assert result["recovery"]["can_resume"] is True
    assert result["recovery"]["state"] == "interrupted"
    assert snapshot(status_db) == before


def test_active_lease_blocks_resume(status_db, monkeypatch):
    assert executor.approve_mission_plan(
        MISSION_ID
    )["approved"]

    acknowledge_evidence(status_db)

    monkeypatch.setattr(
        missions,
        "get_worker_lease",
        lambda mission_id: {
            "valid": True,
            "active": True,
        },
    )

    before = snapshot(status_db)

    result = missions.get_mission_recovery_status(MISSION_ID)

    assert result["recovery"]["can_resume"] is False
    assert result["recovery"]["state"] == "worker_owned"
    assert snapshot(status_db) == before
