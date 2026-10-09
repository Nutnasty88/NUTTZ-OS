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

    def preview(
        mission_id,
        replacement_plan,
    ):
        calls.append(
            (
                "preview",
                mission_id,
                replacement_plan,
            )
        )

        return {
            "mission_id": mission_id,
            "stored_plan": (
                "1. Build application\n"
                "2. Old verification"
            ),
            "replacement_plan": replacement_plan,
            "state_fingerprint": "preview-fingerprint",
            "preserved_tasks": [
                {
                    "id": 101,
                    "position": 1,
                    "title": "Build application",
                    "status": "Completed",
                }
            ],
            "current_pending_tasks": [
                {
                    "id": 102,
                    "position": 2,
                    "title": "Old verification",
                    "status": "Pending",
                }
            ],
            "replacement_pending_tasks": [
                {
                    "position": 2,
                    "title": "Fresh verification",
                    "instructions": "Run fresh verification.",
                }
            ],
        }

    monkeypatch.setattr(
        missions,
        "preview_pending_replan",
        preview,
        raising=False,
    )

    return calls


def test_replan_preview_route_returns_non_mutating_preview(
    monkeypatch,
):
    calls = _configure(monkeypatch)

    replacement_plan = (
        "1. Build application\n"
        "2. Fresh verification"
    )

    result = missions.preview_mission_replan(
        9188,
        missions.MissionReplanPreview(
            replacement_plan=replacement_plan,
        ),
    )

    assert result["success"] is True
    assert result["preview"]["mission_id"] == 9188

    assert (
        result["preview"]["state_fingerprint"]
        == "preview-fingerprint"
    )

    assert calls == [
        (
            "preview",
            9188,
            replacement_plan,
        )
    ]


def test_replan_preview_route_rejects_active_worker(
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
        missions.preview_mission_replan(
            9188,
            missions.MissionReplanPreview(
                replacement_plan=(
                    "1. Build application\n"
                    "2. Fresh verification"
                ),
            ),
        )

    assert caught.value.status_code == 409
    assert "Worker is active" in caught.value.detail
    assert calls == []


def test_replan_preview_route_rejects_active_lease(
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
        missions.preview_mission_replan(
            9188,
            missions.MissionReplanPreview(
                replacement_plan=(
                    "1. Build application\n"
                    "2. Fresh verification"
                ),
            ),
        )

    assert caught.value.status_code == 409
    assert "active worker lease" in caught.value.detail
    assert calls == []


def test_replan_apply_route_uses_reviewed_proposal_and_requires_reapproval(
    monkeypatch,
):
    calls = _configure(monkeypatch)

    replacement_plan = (
        "1. Build application\n"
        "2. Fresh verification"
    )

    def apply_replan(
        mission_id,
        replacement_plan,
        *,
        expected_state_fingerprint=None,
        expected_proposal_fingerprint=None,
        reason="",
        triggering_evidence="",
    ):
        calls.append(
            (
                "apply",
                mission_id,
                replacement_plan,
                expected_state_fingerprint,
                expected_proposal_fingerprint,
                reason,
                triggering_evidence,
            )
        )

        return [
            {
                "id": 101,
                "mission_id": mission_id,
                "position": 1,
                "title": "Build application",
                "status": "Completed",
            },
            {
                "id": 103,
                "mission_id": mission_id,
                "position": 2,
                "title": "Fresh verification",
                "status": "Pending",
            },
        ]

    monkeypatch.setattr(
        missions,
        "replan_pending_tasks",
        apply_replan,
        raising=False,
    )

    monkeypatch.setattr(
        missions,
        "get_mission_approval_status",
        lambda mission_id: {
            "mission_id": mission_id,
            "approved": False,
            "approved_at": None,
            "approved_plan_fingerprint": None,
            "current_plan_fingerprint": "replacement-fingerprint",
        },
    )

    result = missions.apply_mission_replan(
        9188,
        missions.MissionReplanApply(
            replacement_plan=replacement_plan,
            state_fingerprint="state-fingerprint",
            proposal_fingerprint="proposal-fingerprint",
            reason="Execution evidence changed the remaining work.",
            triggering_evidence="VERIFIED EXECUTION EVIDENCE",
        ),
    )

    assert result["success"] is True
    assert result["approval"]["approved"] is False
    assert len(result["tasks"]) == 2

    assert "approval" in result["message"].lower()

    assert calls == [
        (
            "apply",
            9188,
            replacement_plan,
            "state-fingerprint",
            "proposal-fingerprint",
            "Execution evidence changed the remaining work.",
            "VERIFIED EXECUTION EVIDENCE",
        )
    ]


def test_replan_apply_route_rejects_active_worker(
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
        missions.apply_mission_replan(
            9188,
            missions.MissionReplanApply(
                replacement_plan=(
                    "1. Build application\n"
                    "2. Fresh verification"
                ),
                state_fingerprint="state-fingerprint",
                proposal_fingerprint="proposal-fingerprint",
            ),
        )

    assert caught.value.status_code == 409
    assert "Worker is active" in caught.value.detail
    assert calls == []


def test_replan_apply_route_rejects_active_lease(
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
        missions.apply_mission_replan(
            9188,
            missions.MissionReplanApply(
                replacement_plan=(
                    "1. Build application\n"
                    "2. Fresh verification"
                ),
                state_fingerprint="state-fingerprint",
                proposal_fingerprint="proposal-fingerprint",
            ),
        )

    assert caught.value.status_code == 409
    assert "active worker lease" in caught.value.detail
    assert calls == []


def test_replan_apply_route_requires_state_fingerprint(
    monkeypatch,
):
    calls = _configure(monkeypatch)

    with pytest.raises(HTTPException) as caught:
        missions.apply_mission_replan(
            9188,
            missions.MissionReplanApply(
                replacement_plan=(
                    "1. Build application\n"
                    "2. Fresh verification"
                ),
                state_fingerprint="   ",
                proposal_fingerprint="proposal-fingerprint",
            ),
        )

    assert caught.value.status_code == 400
    assert "state fingerprint" in caught.value.detail
    assert calls == []


def test_replan_apply_route_requires_proposal_fingerprint(
    monkeypatch,
):
    calls = _configure(monkeypatch)

    with pytest.raises(HTTPException) as caught:
        missions.apply_mission_replan(
            9188,
            missions.MissionReplanApply(
                replacement_plan=(
                    "1. Build application\n"
                    "2. Fresh verification"
                ),
                state_fingerprint="state-fingerprint",
                proposal_fingerprint="   ",
            ),
        )

    assert caught.value.status_code == 400
    assert "proposal fingerprint" in caught.value.detail
    assert calls == []


def test_replan_apply_route_maps_stale_preview_to_conflict(
    monkeypatch,
):
    _configure(monkeypatch)

    def stale(*args, **kwargs):
        raise RuntimeError(
            "Replan preview is stale because mission "
            "state changed after preview."
        )

    monkeypatch.setattr(
        missions,
        "replan_pending_tasks",
        stale,
        raising=False,
    )

    with pytest.raises(HTTPException) as caught:
        missions.apply_mission_replan(
            9188,
            missions.MissionReplanApply(
                replacement_plan=(
                    "1. Build application\n"
                    "2. Fresh verification"
                ),
                state_fingerprint="stale-state",
                proposal_fingerprint="proposal-fingerprint",
            ),
        )

    assert caught.value.status_code == 409
    assert "stale" in caught.value.detail


def test_replan_apply_route_maps_proposal_mismatch_to_conflict(
    monkeypatch,
):
    _configure(monkeypatch)

    def mismatch(*args, **kwargs):
        raise RuntimeError(
            "Replan proposal does not match the "
            "replacement plan that was previewed."
        )

    monkeypatch.setattr(
        missions,
        "replan_pending_tasks",
        mismatch,
        raising=False,
    )

    with pytest.raises(HTTPException) as caught:
        missions.apply_mission_replan(
            9188,
            missions.MissionReplanApply(
                replacement_plan=(
                    "1. Build application\n"
                    "2. Substituted verification"
                ),
                state_fingerprint="state-fingerprint",
                proposal_fingerprint="reviewed-proposal",
            ),
        )

    assert caught.value.status_code == 409
    assert "proposal" in caught.value.detail


def test_replan_history_route_returns_chronological_history(
    monkeypatch,
):
    calls = []

    expected_history = [
        {
            "id": 1,
            "mission_id": 9188,
            "original_plan": (
                "1. Build application\n"
                "2. Old verification"
            ),
            "replacement_plan": (
                "1. Build application\n"
                "2. Fresh verification"
            ),
            "reason": "Execution evidence changed the remaining work.",
            "triggering_evidence": "VERIFIED EXECUTION EVIDENCE",
            "state_fingerprint": "state-fingerprint",
            "created_at": "2026-10-09 01:00:00",
        },
        {
            "id": 2,
            "mission_id": 9188,
            "original_plan": (
                "1. Build application\n"
                "2. Fresh verification"
            ),
            "replacement_plan": (
                "1. Build application\n"
                "2. Final verification"
            ),
            "reason": "New verification evidence required another revision.",
            "triggering_evidence": "SECOND VERIFIED EVIDENCE",
            "state_fingerprint": "second-state-fingerprint",
            "created_at": "2026-10-09 02:00:00",
        },
    ]

    def history(mission_id):
        calls.append(mission_id)
        return expected_history

    monkeypatch.setattr(
        missions,
        "get_mission_replan_history",
        history,
        raising=False,
    )

    result = missions.mission_replan_history(9188)

    assert result["success"] is True
    assert result["mission_id"] == 9188
    assert result["history"] == expected_history
    assert calls == [9188]


def test_replan_history_route_returns_empty_history(
    monkeypatch,
):
    calls = []

    def history(mission_id):
        calls.append(mission_id)
        return []

    monkeypatch.setattr(
        missions,
        "get_mission_replan_history",
        history,
        raising=False,
    )

    result = missions.mission_replan_history(9188)

    assert result == {
        "success": True,
        "mission_id": 9188,
        "history": [],
    }

    assert calls == [9188]
