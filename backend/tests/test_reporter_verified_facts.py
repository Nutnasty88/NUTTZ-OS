import json

from app.services import reporter


def task_result(header, marker, evidence, *, position=1):
    return {
        "position": position,
        "status": "Completed",
        "result": (
            header
            + "\n\n"
            + marker
            + "\n"
            + json.dumps(evidence, sort_keys=True)
        ),
    }


def fact_types(task):
    return [
        fact["type"]
        for fact in reporter._task_verified_facts(task)
    ]


def test_builder_emits_artifact_verified_fact():
    task = task_result(
        "BUILDER AGENT: COMPLETED",
        "VERIFIED BUILDER EVIDENCE:",
        {
            "verified": True,
            "workspace": "mission-1",
            "entrypoint": "main.py",
            "entrypoint_sha256": "a" * 64,
            "entrypoint_size_bytes": 321,
        },
    )

    facts = reporter._task_verified_facts(task)

    assert facts == [
        {
            "id": "task-1:artifact",
            "type": "artifact_verified",
            "task_position": 1,
            "evidence_type": "builder_verified",
            "artifact": "main.py",
            "sha256": "a" * 64,
            "size_bytes": 321,
        }
    ]


def test_workspace_emits_execution_verified_fact():
    task = task_result(
        "WORKSPACE EXECUTION: VERIFIED",
        "VERIFIED EXECUTION EVIDENCE:",
        {
            "type": "builder_workspace_execution",
                "verified": True,
            "artifact": "main.py",
            "artifact_sha256": "b" * 64,
            "artifact_size_bytes": 456,
            "status": "success",
            "exit_code": 0,
            "stdout": "Hello, Jason!\n",
            "stderr": "",
        },
    )

    facts = reporter._task_verified_facts(task)

    assert facts == [
        {
            "id": "task-1:execution",
            "type": "execution_verified",
            "task_position": 1,
            "evidence_type": "workspace_verified",
            "artifact": "main.py",
            "sha256": "b" * 64,
            "size_bytes": 456,
            "exit_code": 0,
            "stdout": "Hello, Jason!\n",
        }
    ]


def test_http_emits_one_fact_per_verified_check():
    task = task_result(
        "HTTP SERVICE EXECUTION: VERIFIED",
        "VERIFIED HTTP SERVICE EVIDENCE:",
        {
            "type": "builder_workspace_http_service",
            "verified": True,
            "artifact_sha256": "f" * 64,
            "artifact_size_bytes": 200,
            "requested_check_count": 2,
            "check_count": 2,
            "artifact": "main.py",
            "service_stopped": True,
            "restart_count": 0,
            "steps": [
                {
                    "index": 1,
                    "method": "POST",
                    "path": "/tasks",
                    "status_code": 200,
                    "expected_status": 200,
                    "response_json": {"title": "Buy milk"},
                    "expected_json": {"title": "Buy milk"},
                    "restart_before": False,
                    "verified": True,
                },
                {
                    "index": 2,
                    "method": "GET",
                    "path": "/tasks",
                    "status_code": 200,
                    "expected_status": 200,
                    "response_json": [
                        {"title": "Buy milk"}
                    ],
                    "expected_json": [
                        {"title": "Buy milk"}
                    ],
                    "restart_before": False,
                    "verified": True,
                },
            ],
        },
        position=5,
    )

    facts = reporter._task_verified_facts(task)

    http_facts = [
        fact
        for fact in facts
        if fact["type"] == "http_check_verified"
    ]

    assert len(http_facts) == 2

    assert http_facts[0] == {
        "id": "task-5:http-check:1",
        "type": "http_check_verified",
        "task_position": 5,
        "evidence_type": "http_service_verified",
        "check_index": 1,
        "method": "POST",
        "path": "/tasks",
        "status_code": 200,
        "expected_status": 200,
        "response_json": {"title": "Buy milk"},
        "expected_json": {"title": "Buy milk"},
        "restart_before": False,
    }

    assert http_facts[1]["method"] == "GET"
    assert http_facts[1]["path"] == "/tasks"


def test_http_restart_emits_restart_verified_fact():
    task = task_result(
        "HTTP SERVICE EXECUTION: VERIFIED",
        "VERIFIED HTTP SERVICE EVIDENCE:",
        {
            "type": "builder_workspace_http_service",
            "verified": True,
            "artifact_sha256": "f" * 64,
            "artifact_size_bytes": 200,
            "requested_check_count": 1,
            "check_count": 1,
            "artifact": "main.py",
            "service_stopped": True,
            "restart_count": 1,
            "steps": [
                {
                    "index": 1,
                    "method": "GET",
                    "path": "/tasks",
                    "status_code": 200,
                    "expected_status": 200,
                    "response_json": [
                        {"title": "Buy milk"}
                    ],
                    "expected_json": [
                        {"title": "Buy milk"}
                    ],
                    "restart_before": True,
                    "verified": True,
                }
            ],
        },
        position=6,
    )

    facts = reporter._task_verified_facts(task)

    assert "service_restart_verified" in fact_types(task)

    restart_fact = next(
        fact
        for fact in facts
        if fact["type"] == "service_restart_verified"
    )

    assert restart_fact == {
        "id": "task-6:service-restart",
        "type": "service_restart_verified",
        "task_position": 6,
        "evidence_type": "http_service_verified",
        "restart_count": 1,
    }


def test_http_clean_shutdown_emits_service_stopped_fact():
    task = task_result(
        "HTTP SERVICE EXECUTION: VERIFIED",
        "VERIFIED HTTP SERVICE EVIDENCE:",
        {
            "type": "builder_workspace_http_service",
            "verified": True,
            "artifact_sha256": "f" * 64,
            "artifact_size_bytes": 200,
            "requested_check_count": 0,
            "check_count": 0,
            "artifact": "main.py",
            "service_stopped": True,
            "restart_count": 0,
            "steps": [],
        },
    )

    facts = reporter._task_verified_facts(task)

    assert {
        "id": "task-1:service-stopped",
        "type": "service_stopped_verified",
        "task_position": 1,
        "evidence_type": "http_service_verified",
    } in facts


def test_unverified_evidence_emits_no_facts():
    task = task_result(
        "WORKSPACE EXECUTION: VERIFIED",
        "VERIFIED EXECUTION EVIDENCE:",
        {
            "verified": False,
            "artifact": "main.py",
            "exit_code": 1,
        },
    )

    assert reporter._task_verified_facts(task) == []


def test_non_completed_task_emits_no_facts():
    task = task_result(
        "WORKSPACE EXECUTION: VERIFIED",
        "VERIFIED EXECUTION EVIDENCE:",
        {
            "verified": True,
            "artifact": "main.py",
            "exit_code": 0,
        },
    )
    task["status"] = "Error"

    assert reporter._task_verified_facts(task) == []


def test_http_unverified_step_emits_no_http_check_fact():
    task = task_result(
        "HTTP SERVICE EXECUTION: VERIFIED",
        "VERIFIED HTTP SERVICE EVIDENCE:",
        {
            "verified": True,
            "artifact": "main.py",
            "service_stopped": True,
            "restart_count": 0,
            "steps": [
                {
                    "index": 1,
                    "method": "GET",
                    "path": "/tasks",
                    "status_code": 500,
                    "expected_status": 200,
                    "response_json": None,
                    "expected_json": [],
                    "restart_before": False,
                    "verified": False,
                }
            ],
        },
    )

    facts = reporter._task_verified_facts(task)

    assert not any(
        fact["type"] == "http_check_verified"
        for fact in facts
    )


def test_builder_minimal_verified_payload_emits_no_fact():
    task = task_result(
        "BUILDER AGENT: COMPLETED",
        "VERIFIED BUILDER EVIDENCE:",
        {
            "verified": True,
        },
    )

    assert reporter._task_verified_facts(task) == []


def test_builder_invalid_sha_emits_no_fact():
    task = task_result(
        "BUILDER AGENT: COMPLETED",
        "VERIFIED BUILDER EVIDENCE:",
        {
            "verified": True,
            "entrypoint": "main.py",
            "entrypoint_sha256": "not-a-sha256",
            "entrypoint_size_bytes": 321,
        },
    )

    assert reporter._task_verified_facts(task) == []


def test_builder_boolean_size_emits_no_fact():
    task = task_result(
        "BUILDER AGENT: COMPLETED",
        "VERIFIED BUILDER EVIDENCE:",
        {
            "verified": True,
            "entrypoint": "main.py",
            "entrypoint_sha256": "a" * 64,
            "entrypoint_size_bytes": True,
        },
    )

    assert reporter._task_verified_facts(task) == []


def test_workspace_minimal_verified_payload_emits_no_fact():
    task = task_result(
        "WORKSPACE EXECUTION: VERIFIED",
        "VERIFIED EXECUTION EVIDENCE:",
        {
            "verified": True,
        },
    )

    assert reporter._task_verified_facts(task) == []


def test_workspace_nonzero_exit_emits_no_fact():
    task = task_result(
        "WORKSPACE EXECUTION: VERIFIED",
        "VERIFIED EXECUTION EVIDENCE:",
        {
            "verified": True,
            "artifact": "main.py",
            "artifact_sha256": "b" * 64,
            "artifact_size_bytes": 456,
            "status": "success",
            "exit_code": 1,
            "stdout": "",
            "stderr": "",
        },
    )

    assert reporter._task_verified_facts(task) == []


def test_http_status_mismatch_emits_no_http_check_fact():
    task = task_result(
        "HTTP SERVICE EXECUTION: VERIFIED",
        "VERIFIED HTTP SERVICE EVIDENCE:",
        {
            "verified": True,
            "artifact": "main.py",
            "service_stopped": True,
            "restart_count": 0,
            "steps": [
                {
                    "index": 1,
                    "method": "GET",
                    "path": "/tasks",
                    "status_code": 500,
                    "expected_status": 200,
                    "response_json": [],
                    "expected_json": [],
                    "restart_before": False,
                    "verified": True,
                }
            ],
        },
    )

    facts = reporter._task_verified_facts(task)

    assert not any(
        fact["type"] == "http_check_verified"
        for fact in facts
    )


def test_http_json_mismatch_emits_no_http_check_fact():
    task = task_result(
        "HTTP SERVICE EXECUTION: VERIFIED",
        "VERIFIED HTTP SERVICE EVIDENCE:",
        {
            "verified": True,
            "artifact": "main.py",
            "service_stopped": True,
            "restart_count": 0,
            "steps": [
                {
                    "index": 1,
                    "method": "GET",
                    "path": "/tasks",
                    "status_code": 200,
                    "expected_status": 200,
                    "response_json": [],
                    "expected_json": [
                        {"title": "Buy milk"}
                    ],
                    "restart_before": False,
                    "verified": True,
                }
            ],
        },
    )

    facts = reporter._task_verified_facts(task)

    assert not any(
        fact["type"] == "http_check_verified"
        for fact in facts
    )


def test_http_invalid_step_index_emits_no_http_check_fact():
    task = task_result(
        "HTTP SERVICE EXECUTION: VERIFIED",
        "VERIFIED HTTP SERVICE EVIDENCE:",
        {
            "verified": True,
            "artifact": "main.py",
            "service_stopped": True,
            "restart_count": 0,
            "steps": [
                {
                    "index": None,
                    "method": "GET",
                    "path": "/tasks",
                    "status_code": 200,
                    "expected_status": 200,
                    "response_json": [],
                    "expected_json": [],
                    "restart_before": False,
                    "verified": True,
                }
            ],
        },
    )

    facts = reporter._task_verified_facts(task)

    assert not any(
        fact["type"] == "http_check_verified"
        for fact in facts
    )


def test_http_restart_count_without_restart_step_emits_no_restart_fact():
    task = task_result(
        "HTTP SERVICE EXECUTION: VERIFIED",
        "VERIFIED HTTP SERVICE EVIDENCE:",
        {
            "verified": True,
            "artifact": "main.py",
            "service_stopped": True,
            "restart_count": 1,
            "steps": [
                {
                    "index": 1,
                    "method": "GET",
                    "path": "/tasks",
                    "status_code": 200,
                    "expected_status": 200,
                    "response_json": [],
                    "expected_json": [],
                    "restart_before": False,
                    "verified": True,
                }
            ],
        },
    )

    facts = reporter._task_verified_facts(task)

    assert not any(
        fact["type"] == "service_restart_verified"
        for fact in facts
    )


def test_workspace_sequence_rejects_mismatched_counts():
    task = task_result(
        "WORKSPACE EXECUTION: VERIFIED",
        "VERIFIED EXECUTION EVIDENCE:",
        {
            "verified": True,
            "step_count": 1,
            "requested_step_count": 2,
            "steps": [
                {
                    "type": "builder_workspace_execution",
                    "verified": True,
                    "artifact": "main.py",
                    "artifact_sha256": "c" * 64,
                    "artifact_size_bytes": 100,
                    "status": "success",
                    "exit_code": 0,
                    "stdout": "ok",
                }
            ],
            "artifact": "main.py",
            "artifact_sha256": "c" * 64,
            "artifact_size_bytes": 100,
            "exit_code": 0,
            "stdout": "ok",
        },
    )

    assert reporter._task_verified_facts(task) == []


def test_workspace_sequence_rejects_invalid_step_type():
    task = task_result(
        "WORKSPACE EXECUTION: VERIFIED",
        "VERIFIED EXECUTION EVIDENCE:",
        {
            "verified": True,
            "step_count": 1,
            "requested_step_count": 1,
            "steps": [
                {
                    "type": "wrong_type",
                    "verified": True,
                    "artifact": "main.py",
                    "artifact_sha256": "d" * 64,
                    "artifact_size_bytes": 100,
                    "status": "success",
                    "exit_code": 0,
                    "stdout": "ok",
                }
            ],
            "artifact": "main.py",
            "artifact_sha256": "d" * 64,
            "artifact_size_bytes": 100,
            "exit_code": 0,
            "stdout": "ok",
        },
    )

    assert reporter._task_verified_facts(task) == []


def test_workspace_sequence_rejects_artifact_identity_mismatch():
    task = task_result(
        "WORKSPACE EXECUTION: VERIFIED",
        "VERIFIED EXECUTION EVIDENCE:",
        {
            "verified": True,
            "step_count": 1,
            "requested_step_count": 1,
            "steps": [
                {
                    "type": "builder_workspace_execution",
                    "verified": True,
                    "artifact": "other.py",
                    "artifact_sha256": "e" * 64,
                    "artifact_size_bytes": 100,
                    "status": "success",
                    "exit_code": 0,
                    "stdout": "ok",
                }
            ],
            "artifact": "main.py",
            "artifact_sha256": "e" * 64,
            "artifact_size_bytes": 100,
            "exit_code": 0,
            "stdout": "ok",
        },
    )

    assert reporter._task_verified_facts(task) == []


def _strict_http_payload():
    return {
        "type": "builder_workspace_http_service",
        "verified": True,
        "artifact": "main.py",
        "artifact_sha256": "f" * 64,
        "artifact_size_bytes": 200,
        "requested_check_count": 1,
        "check_count": 1,
        "restart_count": 0,
        "steps": [
            {
                "index": 1,
                "method": "GET",
                "path": "/tasks",
                "status_code": 200,
                "expected_status": 200,
                "response_json": [],
                "expected_json": [],
                "restart_before": False,
                "verified": True,
            }
        ],
        "service_stopped": True,
    }


def test_http_rejects_wrong_top_level_type():
    payload = _strict_http_payload()
    payload["type"] = "wrong_type"

    task = task_result(
        "HTTP SERVICE EXECUTION: VERIFIED",
        "VERIFIED HTTP SERVICE EVIDENCE:",
        payload,
    )

    assert reporter._task_verified_facts(task) == []


def test_http_rejects_invalid_artifact_identity():
    payload = _strict_http_payload()
    payload["artifact_sha256"] = "not-a-sha256"

    task = task_result(
        "HTTP SERVICE EXECUTION: VERIFIED",
        "VERIFIED HTTP SERVICE EVIDENCE:",
        payload,
    )

    assert reporter._task_verified_facts(task) == []


def test_http_rejects_check_count_mismatch():
    payload = _strict_http_payload()
    payload["check_count"] = 2

    task = task_result(
        "HTTP SERVICE EXECUTION: VERIFIED",
        "VERIFIED HTTP SERVICE EVIDENCE:",
        payload,
    )

    assert reporter._task_verified_facts(task) == []


def test_http_rejects_requested_check_count_mismatch():
    payload = _strict_http_payload()
    payload["requested_check_count"] = 2

    task = task_result(
        "HTTP SERVICE EXECUTION: VERIFIED",
        "VERIFIED HTTP SERVICE EVIDENCE:",
        payload,
    )

    assert reporter._task_verified_facts(task) == []


def test_http_rejects_duplicate_step_indexes():
    payload = _strict_http_payload()

    duplicate = dict(payload["steps"][0])
    payload["steps"].append(duplicate)
    payload["check_count"] = 2
    payload["requested_check_count"] = 2

    task = task_result(
        "HTTP SERVICE EXECUTION: VERIFIED",
        "VERIFIED HTTP SERVICE EVIDENCE:",
        payload,
    )

    assert reporter._task_verified_facts(task) == []


def test_http_rejects_boolean_restart_count():
    payload = _strict_http_payload()
    payload["restart_count"] = True

    task = task_result(
        "HTTP SERVICE EXECUTION: VERIFIED",
        "VERIFIED HTTP SERVICE EVIDENCE:",
        payload,
    )

    assert reporter._task_verified_facts(task) == []


def test_http_malformed_step_emits_no_service_stopped_fact():
    payload = _strict_http_payload()
    payload["steps"][0]["status_code"] = 500

    task = task_result(
        "HTTP SERVICE EXECUTION: VERIFIED",
        "VERIFIED HTTP SERVICE EVIDENCE:",
        payload,
    )

    assert reporter._task_verified_facts(task) == []
