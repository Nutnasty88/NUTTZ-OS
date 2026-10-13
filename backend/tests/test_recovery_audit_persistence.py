import importlib
import sqlite3

import pytest
from fastapi import HTTPException
from app.services import events

missions = importlib.import_module("app.routers.missions")


def test_recovery_events_persist_in_sqlite(
    tmp_path,
    monkeypatch,
):
    path = tmp_path / "recovery-audit.db"
    mission_id = 17501

    def connect():
        conn = sqlite3.connect(path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    conn = connect()
    try:
        conn.executescript("""
            CREATE TABLE missions (
                id INTEGER PRIMARY KEY
            );

            CREATE TABLE mission_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                mission_id INTEGER,
                agent TEXT NOT NULL,
                event_type TEXT NOT NULL,
                message TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (mission_id)
                    REFERENCES missions(id)
            );
        """)
        conn.execute(
            "INSERT INTO missions (id) VALUES (?)",
            (mission_id,),
        )
        conn.commit()
    finally:
        conn.close()

    monkeypatch.setattr(
        events,
        "get_connection",
        connect,
    )

    event_types = [
        "recovery_requested",
        "recovery_authorized",
        "recovery_started",
    ]

    for event_type in event_types:
        events.log_event(
            mission_id,
            "Recovery",
            event_type,
            f"Test event: {event_type}",
        )

    saved = events.get_events(mission_id)

    assert len(saved) == 3
    assert [row["event_type"] for row in saved] == event_types
    assert all(row["agent"] == "Recovery" for row in saved)
    assert all(row["mission_id"] == mission_id for row in saved)
    assert all(row["created_at"] for row in saved)
    assert [row["id"] for row in saved] == sorted(
        row["id"] for row in saved
    )


@pytest.fixture
def route_db(tmp_path, monkeypatch):
    path = tmp_path / "route-audit.db"
    mission_id = 17501

    def connect():
        conn = sqlite3.connect(path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    conn = connect()
    try:
        conn.executescript("""
            CREATE TABLE missions (
                id INTEGER PRIMARY KEY
            );

            CREATE TABLE mission_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                mission_id INTEGER,
                agent TEXT NOT NULL,
                event_type TEXT NOT NULL,
                message TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (mission_id)
                    REFERENCES missions(id)
            );
        """)
        conn.execute(
            "INSERT INTO missions (id) VALUES (?)",
            (mission_id,),
        )
        conn.commit()
    finally:
        conn.close()

    monkeypatch.setattr(events, "get_connection", connect)
    monkeypatch.setattr(missions, "get_connection", connect)
    monkeypatch.setattr(missions, "log_event", events.log_event)

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

    return mission_id


def test_successful_route_audit_persists(route_db, monkeypatch):
    mission_id = route_db

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

    result = missions.resume_mission_recovery(mission_id)

    assert result["success"] is True

    saved = events.get_events(mission_id)
    assert [row["event_type"] for row in saved] == [
        "recovery_requested",
        "recovery_authorized",
        "recovery_started",
    ]
    assert all(row["agent"] == "Recovery" for row in saved)


def test_denied_route_audit_persists(route_db, monkeypatch):
    mission_id = route_db

    monkeypatch.setattr(
        missions,
        "get_worker_status",
        lambda: {
            "thread_alive": True,
            "mission_id": mission_id,
        },
    )

    def forbidden_start(**kwargs):
        pytest.fail("Denied recovery must not start a worker")

    monkeypatch.setattr(
        missions,
        "start_worker",
        forbidden_start,
    )

    with pytest.raises(HTTPException) as caught:
        missions.resume_mission_recovery(mission_id)

    assert caught.value.status_code == 409

    saved = events.get_events(mission_id)
    assert [row["event_type"] for row in saved] == [
        "recovery_requested",
        "recovery_denied",
    ]
