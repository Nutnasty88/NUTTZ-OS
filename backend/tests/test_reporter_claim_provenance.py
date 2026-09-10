import pytest

from app.services import reporter


def provenance(
    position,
    evidence_types,
    *,
    status="Completed",
    verified=True,
):
    return {
        "position": position,
        "status": status,
        "evidence_types": evidence_types,
        "verified": verified,
    }


def fact(
    fact_id,
    *,
    position=1,
    evidence_type="workspace_verified",
):
    return {
        "id": fact_id,
        "type": "execution_verified",
        "task_position": position,
        "evidence_type": evidence_type,
    }


def test_claim_accepts_exact_verified_fact_reference():
    claims = [
        {
            "kind": "execution_verified",
            "supported_by": [
                {
                    "fact_id": "task-1:execution",
                }
            ],
        }
    ]

    validated = reporter._validate_claim_provenance(
        claims,
        [
            provenance(
                1,
                ["workspace_verified"],
            )
        ],
        [
            fact("task-1:execution"),
        ],
    )

    assert validated == claims


def test_claim_rejects_unknown_fact_id():
    claims = [
        {
            "kind": "execution_verified",
            "supported_by": [
                {
                    "fact_id": "task-99:missing",
                }
            ],
        }
    ]

    with pytest.raises(
        ValueError,
        match="unknown verified fact",
    ):
        reporter._validate_claim_provenance(
            claims,
            [
                provenance(
                    1,
                    ["workspace_verified"],
                )
            ],
            [
                fact("task-1:execution"),
            ],
        )


def test_claim_rejects_missing_fact_id():
    claims = [
        {
            "kind": "execution_verified",
            "supported_by": [
                {}
            ],
        }
    ]

    with pytest.raises(
        ValueError,
        match="fact_id",
    ):
        reporter._validate_claim_provenance(
            claims,
            [],
            [],
        )


def test_claim_rejects_empty_fact_id():
    claims = [
        {
            "kind": "execution_verified",
            "supported_by": [
                {
                    "fact_id": "   ",
                }
            ],
        }
    ]

    with pytest.raises(
        ValueError,
        match="fact_id",
    ):
        reporter._validate_claim_provenance(
            claims,
            [],
            [],
        )


def test_claim_rejects_unverified_task():
    claims = [
        {
            "kind": "execution_verified",
            "supported_by": [
                {
                    "fact_id": "task-1:artifact",
                }
            ],
        }
    ]

    with pytest.raises(
        ValueError,
        match="not verified",
    ):
        reporter._validate_claim_provenance(
            claims,
            [
                provenance(
                    1,
                    ["builder_verified"],
                    verified=False,
                )
            ],
            [
                fact(
                    "task-1:artifact",
                    evidence_type="builder_verified",
                )
            ],
        )


def test_claim_rejects_non_completed_task():
    claims = [
        {
            "kind": "execution_verified",
            "supported_by": [
                {
                    "fact_id": "task-1:artifact",
                }
            ],
        }
    ]

    with pytest.raises(
        ValueError,
        match="not completed",
    ):
        reporter._validate_claim_provenance(
            claims,
            [
                provenance(
                    1,
                    ["builder_verified"],
                    status="Error",
                )
            ],
            [
                fact(
                    "task-1:artifact",
                    evidence_type="builder_verified",
                )
            ],
        )


def test_claim_rejects_fact_with_wrong_evidence_type():
    claims = [
        {
            "kind": "execution_verified",
            "supported_by": [
                {
                    "fact_id": "task-1:execution",
                }
            ],
        }
    ]

    with pytest.raises(
        ValueError,
        match="evidence type",
    ):
        reporter._validate_claim_provenance(
            claims,
            [
                provenance(
                    1,
                    ["builder_verified"],
                )
            ],
            [
                fact(
                    "task-1:execution",
                    evidence_type="workspace_verified",
                )
            ],
        )


def test_claim_rejects_empty_support():
    claims = [
        {
            "kind": "execution_verified",
            "supported_by": [],
        }
    ]

    with pytest.raises(
        ValueError,
        match="at least one",
    ):
        reporter._validate_claim_provenance(
            claims,
            [],
            [],
        )




def test_claim_rejects_non_list_claims():
    with pytest.raises(
        ValueError,
        match="list",
    ):
        reporter._validate_claim_provenance(
            {},
            [],
            [],
        )


def test_claim_rejects_non_object_reference():
    claims = [
        {
            "kind": "execution_verified",
            "supported_by": [
                "task-1:execution"
            ],
        }
    ]

    with pytest.raises(
        ValueError,
        match="reference must",
    ):
        reporter._validate_claim_provenance(
            claims,
            [],
            [],
        )


def test_claim_rejects_non_string_fact_id():
    claims = [
        {
            "kind": "execution_verified",
            "supported_by": [
                {
                    "fact_id": 1,
                }
            ],
        }
    ]

    with pytest.raises(
        ValueError,
        match="fact_id",
    ):
        reporter._validate_claim_provenance(
            claims,
            [],
            [],
        )


def test_parse_reporter_envelope_accepts_valid_json():
    content = """
{
  "claims": [
    {
      "text": "The service was verified.",
      "supported_by": [
        {
          "fact_id": "task-6:http-check:1"
        }
      ]
    }
  ]
}
""".strip()

    envelope = reporter._parse_reporter_envelope(
        content
    )

    assert len(envelope["claims"]) == 1


def test_parse_reporter_envelope_rejects_invalid_json():
    with pytest.raises(
        ValueError,
        match="valid JSON",
    ):
        reporter._parse_reporter_envelope(
            '{"deliverable": "broken"'
        )


def test_parse_reporter_envelope_requires_object():
    with pytest.raises(
        ValueError,
        match="JSON object",
    ):
        reporter._parse_reporter_envelope(
            '["not", "an", "object"]'
        )


def test_parse_reporter_envelope_requires_claims():
    with pytest.raises(
        ValueError,
        match="claims",
    ):
        reporter._parse_reporter_envelope(
            '{}'
        )


def test_parse_reporter_envelope_rejects_deliverable_field():
    with pytest.raises(
        ValueError,
        match="unexpected fields",
    ):
        reporter._parse_reporter_envelope(
            '{"deliverable": "Report", "claims": []}'
        )


def test_parse_reporter_envelope_requires_claims_list():
    with pytest.raises(
        ValueError,
        match="claims",
    ):
        reporter._parse_reporter_envelope(
            '{"claims": {}}'
        )


def test_parse_reporter_envelope_rejects_markdown_fence():
    content = """```json
{"claims": []}
```"""

    with pytest.raises(
        ValueError,
        match="valid JSON",
    ):
        reporter._parse_reporter_envelope(
            content
        )


def test_parse_reporter_envelope_rejects_extra_fields():
    content = """
{
  "deliverable": "Report",
  "claims": [],
  "reasoning": "hidden"
}
""".strip()

    with pytest.raises(
        ValueError,
        match="unexpected",
    ):
        reporter._parse_reporter_envelope(
            content
        )


def test_claim_rejects_duplicate_verified_fact_ids():
    claims = [
        {
            "kind": "execution_verified",
            "supported_by": [
                {
                    "fact_id": "task-1:execution",
                }
            ],
        }
    ]

    duplicate_facts = [
        fact(
            "task-1:execution",
            position=1,
            evidence_type="workspace_verified",
        ),
        fact(
            "task-1:execution",
            position=1,
            evidence_type="workspace_verified",
        ),
    ]

    with pytest.raises(
        ValueError,
        match="duplicate verified fact id",
    ):
        reporter._validate_claim_provenance(
            claims,
            [
                provenance(
                    1,
                    evidence_types=[
                        "workspace_verified",
                    ],
                )
            ],
            duplicate_facts,
        )


def test_reporter_envelope_accepts_claims_only():
    envelope = reporter._parse_reporter_envelope(
        '{"claims": []}'
    )

    assert envelope == {
        "claims": [],
    }


def test_reporter_envelope_rejects_legacy_deliverable_field():
    with pytest.raises(
        ValueError,
        match="unexpected fields",
    ):
        reporter._parse_reporter_envelope(
            (
                '{"deliverable":"legacy model markdown",'
                '"claims":[]}'
            )
        )




def test_claim_accepts_restart_claim_with_restart_fact():
    claims = [
        {
            "kind": "service_restart_verified",
            "supported_by": [
                {
                    "fact_id": "task-6:service-restart",
                },
                {
                    "fact_id": "task-6:http-check:1",
                },
            ],
        }
    ]

    task_provenance = [
        {
            "position": 6,
            "status": "Completed",
            "evidence_types": [
                "http_service_verified",
            ],
            "verified": True,
        }
    ]

    verified_facts = [
        {
            "id": "task-6:service-restart",
            "type": "service_restart_verified",
            "task_position": 6,
            "evidence_type": "http_service_verified",
            "restart_count": 1,
        },
        {
            "id": "task-6:http-check:1",
            "type": "http_check_verified",
            "task_position": 6,
            "evidence_type": "http_service_verified",
            "check_index": 1,
            "method": "GET",
            "path": "/tasks",
            "status_code": 200,
            "expected_status": 200,
            "response_json": [
                {
                    "title": "Buy milk",
                }
            ],
            "expected_json": [
                {
                    "title": "Buy milk",
                }
            ],
            "restart_before": True,
        },
    ]

    validated = reporter._validate_claim_provenance(
        claims,
        task_provenance,
        verified_facts,
    )

    assert validated == claims






def test_typed_claim_requires_kind():
    claims = [
        {
            "supported_by": [
                {
                    "fact_id": "task-6:service-restart",
                }
            ],
        }
    ]

    task_provenance = [
        {
            "position": 6,
            "status": "Completed",
            "evidence_types": [
                "http_service_verified",
            ],
            "verified": True,
        }
    ]

    verified_facts = [
        {
            "id": "task-6:service-restart",
            "type": "service_restart_verified",
            "task_position": 6,
            "evidence_type": "http_service_verified",
            "restart_count": 1,
        }
    ]

    with pytest.raises(
        ValueError,
        match="kind",
    ):
        reporter._validate_claim_provenance(
            claims,
            task_provenance,
            verified_facts,
        )


def test_typed_claim_rejects_kind_fact_mismatch():
    claims = [
        {
            "kind": "service_restart_verified",
            "supported_by": [
                {
                    "fact_id": "task-6:http-check:1",
                }
            ],
        }
    ]

    task_provenance = [
        {
            "position": 6,
            "status": "Completed",
            "evidence_types": [
                "http_service_verified",
            ],
            "verified": True,
        }
    ]

    verified_facts = [
        {
            "id": "task-6:http-check:1",
            "type": "http_check_verified",
            "task_position": 6,
            "evidence_type": "http_service_verified",
            "check_index": 1,
            "method": "GET",
            "path": "/tasks",
            "status_code": 200,
            "expected_status": 200,
            "response_json": [],
            "expected_json": [],
            "restart_before": False,
        }
    ]

    with pytest.raises(
        ValueError,
        match="kind",
    ):
        reporter._validate_claim_provenance(
            claims,
            task_provenance,
            verified_facts,
        )


def test_typed_restart_claim_accepts_matching_fact():
    claims = [
        {
            "kind": "service_restart_verified",
            "supported_by": [
                {
                    "fact_id": "task-6:service-restart",
                }
            ],
        }
    ]

    task_provenance = [
        {
            "position": 6,
            "status": "Completed",
            "evidence_types": [
                "http_service_verified",
            ],
            "verified": True,
        }
    ]

    verified_facts = [
        {
            "id": "task-6:service-restart",
            "type": "service_restart_verified",
            "task_position": 6,
            "evidence_type": "http_service_verified",
            "restart_count": 1,
        }
    ]

    validated = reporter._validate_claim_provenance(
        claims,
        task_provenance,
        verified_facts,
    )

    assert validated == claims


def test_selector_claim_accepts_kind_and_support_only():
    claims = [
        {
            "kind": "execution_verified",
            "supported_by": [
                {
                    "fact_id": "task-1:execution",
                }
            ],
        }
    ]

    task_provenance = [
        {
            "position": 1,
            "status": "Completed",
            "verified": True,
            "evidence_types": [
                "workspace_verified",
            ],
        }
    ]

    verified_facts = [
        {
            "id": "task-1:execution",
            "type": "execution_verified",
            "task_position": 1,
            "evidence_type": "workspace_verified",
            "artifact": "main.py",
            "exit_code": 0,
        }
    ]

    validated = reporter._validate_claim_provenance(
        claims,
        task_provenance,
        verified_facts,
    )

    assert validated == claims


def test_selector_claim_rejects_model_authored_text():
    claims = [
        {
            "kind": "execution_verified",
            "text": "The model must not author deliverable prose.",
            "supported_by": [
                {
                    "fact_id": "task-1:execution",
                }
            ],
        }
    ]

    task_provenance = [
        {
            "position": 1,
            "status": "Completed",
            "verified": True,
            "evidence_types": [
                "workspace_verified",
            ],
        }
    ]

    verified_facts = [
        {
            "id": "task-1:execution",
            "type": "execution_verified",
            "task_position": 1,
            "evidence_type": "workspace_verified",
            "artifact": "main.py",
            "exit_code": 0,
        }
    ]

    with pytest.raises(
        ValueError,
        match="unexpected",
    ):
        reporter._validate_claim_provenance(
            claims,
            task_provenance,
            verified_facts,
        )


def test_selector_claim_rejects_unknown_semantic_field():
    claims = [
        {
            "kind": "execution_verified",
            "summary": "Model-authored semantic escape hatch.",
            "supported_by": [
                {
                    "fact_id": "task-1:execution",
                }
            ],
        }
    ]

    task_provenance = [
        {
            "position": 1,
            "status": "Completed",
            "verified": True,
            "evidence_types": [
                "workspace_verified",
            ],
        }
    ]

    verified_facts = [
        {
            "id": "task-1:execution",
            "type": "execution_verified",
            "task_position": 1,
            "evidence_type": "workspace_verified",
            "artifact": "main.py",
            "exit_code": 0,
        }
    ]

    with pytest.raises(
        ValueError,
        match="unexpected",
    ):
        reporter._validate_claim_provenance(
            claims,
            task_provenance,
            verified_facts,
        )
