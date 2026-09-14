import importlib

import pytest
from fastapi import HTTPException


missions = importlib.import_module(
    "app.routers.missions"
)


def ready_deliverable(content="Old report"):
    return {
        "mission_id": 42,
        "status": "Ready",
        "model": "qwen3:8b",
        "content": content,
    }


def test_regenerate_replaces_existing_ready_deliverable(
    monkeypatch,
):
    calls = []

    monkeypatch.setattr(
        missions,
        "get_worker_lease",
        lambda mission_id: None,
    )
    monkeypatch.setattr(
        missions,
        "get_deliverable",
        lambda mission_id: ready_deliverable(),
    )

    def create(mission_id):
        calls.append(mission_id)
        return ready_deliverable(
            "New verified report"
        )

    monkeypatch.setattr(
        missions,
        "create_deliverable",
        create,
    )

    result = missions.regenerate_mission_deliverable(42)

    assert calls == [42]
    assert result["success"] is True
    assert "regenerated" in result["message"]
    assert (
        result["deliverable"]["content"]
        == "New verified report"
    )


def test_regenerate_rejects_active_worker_lease(
    monkeypatch,
):
    monkeypatch.setattr(
        missions,
        "get_worker_lease",
        lambda mission_id: {
            "valid": True,
            "active": True,
        },
    )

    with pytest.raises(HTTPException) as caught:
        missions.regenerate_mission_deliverable(42)

    assert caught.value.status_code == 409
    assert "active Autonomous Worker lease" in (
        caught.value.detail
    )


def test_regenerate_rejects_invalid_lease_metadata(
    monkeypatch,
):
    monkeypatch.setattr(
        missions,
        "get_worker_lease",
        lambda mission_id: {
            "valid": False,
            "active": False,
        },
    )

    with pytest.raises(HTTPException) as caught:
        missions.regenerate_mission_deliverable(42)

    assert caught.value.status_code == 409
    assert "invalid worker lease metadata" in (
        caught.value.detail
    )


def test_regenerate_requires_existing_ready_deliverable(
    monkeypatch,
):
    calls = []

    monkeypatch.setattr(
        missions,
        "get_worker_lease",
        lambda mission_id: None,
    )
    monkeypatch.setattr(
        missions,
        "get_deliverable",
        lambda mission_id: None,
    )
    monkeypatch.setattr(
        missions,
        "create_deliverable",
        lambda mission_id: calls.append(mission_id),
    )

    with pytest.raises(HTTPException) as caught:
        missions.regenerate_mission_deliverable(42)

    assert caught.value.status_code == 409
    assert "existing Ready final deliverable" in (
        caught.value.detail
    )
    assert calls == []


def test_regenerate_maps_terminal_ownership_race_to_conflict(
    monkeypatch,
):
    monkeypatch.setattr(
        missions,
        "get_worker_lease",
        lambda mission_id: None,
    )
    monkeypatch.setattr(
        missions,
        "get_deliverable",
        lambda mission_id: ready_deliverable(),
    )
    monkeypatch.setattr(
        missions,
        "create_deliverable",
        lambda mission_id: (_ for _ in ()).throw(
            RuntimeError("Worker lease changed.")
        ),
    )

    with pytest.raises(HTTPException) as caught:
        missions.regenerate_mission_deliverable(42)

    assert caught.value.status_code == 409
    assert "Worker lease changed" in caught.value.detail
