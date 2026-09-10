import pytest

from app.services import reporter


def artifact_fact(
    fact_id="task-1:artifact",
    artifact="main.py",
    size_bytes=321,
):
    return {
        "id": fact_id,
        "type": "artifact_verified",
        "task_position": 1,
        "evidence_type": "builder_verified",
        "artifact": artifact,
        "sha256": "a" * 64,
        "size_bytes": size_bytes,
    }


def execution_fact(
    fact_id="task-2:execution",
    artifact="main.py",
):
    return {
        "id": fact_id,
        "type": "execution_verified",
        "task_position": 2,
        "evidence_type": "workspace_verified",
        "artifact": artifact,
        "sha256": "b" * 64,
        "size_bytes": 456,
        "exit_code": 0,
        "stdout": "Buy milk\n",
    }


def http_fact(
    fact_id="task-3:http-check:1",
):
    return {
        "id": fact_id,
        "type": "http_check_verified",
        "task_position": 3,
        "evidence_type": "http_service_verified",
        "check_index": 1,
        "method": "GET",
        "path": "/tasks",
        "status_code": 200,
        "expected_status": 200,
        "response_json": [
            {"title": "Buy milk"},
        ],
        "expected_json": [
            {"title": "Buy milk"},
        ],
        "restart_before": False,
    }


def restart_fact(
    fact_id="task-6:service-restart",
):
    return {
        "id": fact_id,
        "type": "service_restart_verified",
        "task_position": 6,
        "evidence_type": "http_service_verified",
        "restart_count": 1,
    }


def stopped_fact(
    fact_id="task-6:service-stopped",
):
    return {
        "id": fact_id,
        "type": "service_stopped_verified",
        "task_position": 6,
        "evidence_type": "http_service_verified",
    }


def claim(kind, fact_id, text=None):
    item = {
        "kind": kind,
        "supported_by": [
            {
                "fact_id": fact_id,
            }
        ],
    }

    if text is not None:
        item["text"] = text

    return item


def test_render_verified_claims_is_deterministic():
    claims = [
        claim(
            "service_restart_verified",
            "task-6:service-restart",
        ),
        claim(
            "http_check_verified",
            "task-3:http-check:1",
        ),
    ]

    verified_facts = [
        restart_fact(),
        http_fact(),
    ]

    rendered = reporter._render_verified_claims(
        claims,
        verified_facts,
    )

    assert rendered == (
        "## Verified Results\n\n"
        "- The service restart was verified "
        "(restart count: 1).\n"
        "- Verified HTTP GET /tasks returned status 200 "
        "with the expected JSON response."
    )


def test_render_verified_claims_preserves_claim_order():
    claims = [
        claim(
            "artifact_verified",
            "task-1:artifact",
        ),
        claim(
            "execution_verified",
            "task-2:execution",
        ),
        claim(
            "http_check_verified",
            "task-3:http-check:1",
        ),
    ]

    verified_facts = [
        artifact_fact(),
        execution_fact(),
        http_fact(),
    ]

    rendered = reporter._render_verified_claims(
        claims,
        verified_facts,
    )

    assert rendered.index(
        "Verified artifact main.py"
    ) < rendered.index(
        "Verified execution of main.py"
    ) < rendered.index(
        "Verified HTTP GET /tasks"
    )


def test_render_verified_claims_does_not_render_fact_ids():
    claims = [
        claim(
            "execution_verified",
            "task-2:execution",
        )
    ]

    rendered = reporter._render_verified_claims(
        claims,
        [execution_fact()],
    )

    assert (
        "Verified execution of main.py completed "
        "successfully with exit code 0."
        in rendered
    )
    assert "task-2:execution" not in rendered


def test_render_verified_claims_rejects_empty_claims():
    with pytest.raises(
        ValueError,
        match="at least one verified claim",
    ):
        reporter._render_verified_claims(
            [],
            [],
        )


def test_render_verified_claims_rejects_invalid_claim_shape():
    claims = [
        {
            "supported_by": [
                {
                    "fact_id": "task-1:artifact",
                }
            ],
        }
    ]

    with pytest.raises(
        ValueError,
        match="requires a kind",
    ):
        reporter._render_verified_claims(
            claims,
            [artifact_fact()],
        )


def test_model_text_cannot_override_verified_fact_rendering():
    claims = [
        claim(
            "service_restart_verified",
            "task-6:service-restart",
            text=(
                "INVENTED MODEL TEXT THAT MUST NEVER "
                "REACH THE DELIVERABLE"
            ),
        )
    ]

    rendered = reporter._render_verified_claims(
        claims,
        [restart_fact()],
    )

    assert "INVENTED MODEL TEXT" not in rendered
    assert rendered == (
        "## Verified Results\n\n"
        "- The service restart was verified "
        "(restart count: 1)."
    )


def test_render_typed_claim_from_verified_fact():
    rendered = reporter._render_verified_claims(
        [
            claim(
                "service_restart_verified",
                "task-6:service-restart",
            )
        ],
        [restart_fact()],
    )

    assert rendered == (
        "## Verified Results\n\n"
        "- The service restart was verified "
        "(restart count: 1)."
    )


def test_render_typed_http_check_from_verified_fact():
    rendered = reporter._render_verified_claims(
        [
            claim(
                "http_check_verified",
                "task-3:http-check:1",
            )
        ],
        [http_fact()],
    )

    assert rendered == (
        "## Verified Results\n\n"
        "- Verified HTTP GET /tasks returned status 200 "
        "with the expected JSON response."
    )


def test_render_typed_execution_from_verified_fact():
    rendered = reporter._render_verified_claims(
        [
            claim(
                "execution_verified",
                "task-2:execution",
            )
        ],
        [execution_fact()],
    )

    assert rendered == (
        "## Verified Results\n\n"
        "- Verified execution of main.py completed "
        "successfully with exit code 0."
    )


def test_render_typed_artifact_from_verified_fact():
    rendered = reporter._render_verified_claims(
        [
            claim(
                "artifact_verified",
                "task-1:artifact",
            )
        ],
        [artifact_fact()],
    )

    assert rendered == (
        "## Verified Results\n\n"
        "- Verified artifact main.py (321 bytes)."
    )


def test_render_typed_service_stopped_from_verified_fact():
    rendered = reporter._render_verified_claims(
        [
            claim(
                "service_stopped_verified",
                "task-6:service-stopped",
            )
        ],
        [stopped_fact()],
    )

    assert rendered == (
        "## Verified Results\n\n"
        "- The managed service was verified stopped "
        "after execution."
    )


def test_render_rejects_kind_without_matching_fact():
    claims = [
        claim(
            "service_restart_verified",
            "task-3:http-check:1",
        )
    ]

    with pytest.raises(
        ValueError,
        match="no matching verified fact",
    ):
        reporter._render_verified_claims(
            claims,
            [http_fact()],
        )


def test_render_http_status_only_does_not_claim_json_verification():
    fact = http_fact(
        fact_id="task-3:http-check:2",
    )
    fact["path"] = "/health"
    fact["response_json"] = {
        "status": "healthy",
    }
    fact["expected_json"] = None

    rendered = reporter._render_verified_claims(
        [
            claim(
                "http_check_verified",
                "task-3:http-check:2",
            )
        ],
        [fact],
    )

    assert rendered == (
        "## Verified Results\n\n"
        "- Verified HTTP GET /health returned status 200."
    )

    assert "expected JSON" not in rendered


def test_render_http_expected_json_keeps_json_verification_claim():
    fact = http_fact()

    rendered = reporter._render_verified_claims(
        [
            claim(
                "http_check_verified",
                "task-3:http-check:1",
            )
        ],
        [fact],
    )

    assert rendered == (
        "## Verified Results\n\n"
        "- Verified HTTP GET /tasks returned status 200 "
        "with the expected JSON response."
    )


def test_render_service_stopped_does_not_claim_clean_shutdown():
    rendered = reporter._render_verified_claims(
        [
            claim(
                "service_stopped_verified",
                "task-6:service-stopped",
            )
        ],
        [stopped_fact()],
    )

    assert rendered == (
        "## Verified Results\n\n"
        "- The managed service was verified stopped after execution."
    )

    assert "cleanly" not in rendered
