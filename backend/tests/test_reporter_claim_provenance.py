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
            "text": "Execution was verified.",
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
            "text": "Verified claim.",
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
            "text": "Verified claim.",
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
            "text": "Verified claim.",
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
            "text": "Verified claim.",
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
            "text": "Verified claim.",
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
            "text": "Verified claim.",
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
            "text": "Unsupported claim.",
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


def test_claim_rejects_empty_text():
    claims = [
        {
            "text": "   ",
            "supported_by": [
                {
                    "fact_id": "task-1:execution",
                }
            ],
        }
    ]

    with pytest.raises(
        ValueError,
        match="text",
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
            {"text": "bad"},
            [],
            [],
        )


def test_claim_rejects_non_object_reference():
    claims = [
        {
            "text": "Verified claim.",
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
            "text": "Verified claim.",
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
  "deliverable": "# Mission Report\\n\\nCompleted.",
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

    assert envelope["deliverable"] == (
        "# Mission Report\n\nCompleted."
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


def test_parse_reporter_envelope_requires_deliverable():
    with pytest.raises(
        ValueError,
        match="deliverable",
    ):
        reporter._parse_reporter_envelope(
            '{"claims": []}'
        )


def test_parse_reporter_envelope_rejects_empty_deliverable():
    with pytest.raises(
        ValueError,
        match="deliverable",
    ):
        reporter._parse_reporter_envelope(
            '{"deliverable": "   ", "claims": []}'
        )


def test_parse_reporter_envelope_requires_claims_list():
    with pytest.raises(
        ValueError,
        match="claims",
    ):
        reporter._parse_reporter_envelope(
            '{"deliverable": "Report", "claims": {}}'
        )


def test_parse_reporter_envelope_rejects_markdown_fence():
    content = """```json
{"deliverable": "Report", "claims": []}
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
            "text": "Execution was verified.",
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
