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


def test_claim_accepts_exact_verified_reference():
    claims = [
        {
            "text": (
                "The service preserved its task "
                "across restart."
            ),
            "supported_by": [
                {
                    "task_position": 6,
                    "evidence_type": (
                        "http_service_verified"
                    ),
                }
            ],
        }
    ]

    task_provenance = [
        provenance(
            6,
            ["http_service_verified"],
        )
    ]

    validated = reporter._validate_claim_provenance(
        claims,
        task_provenance,
    )

    assert validated == claims


def test_claim_rejects_unknown_task_position():
    claims = [
        {
            "text": "Verified claim.",
            "supported_by": [
                {
                    "task_position": 99,
                    "evidence_type": (
                        "workspace_verified"
                    ),
                }
            ],
        }
    ]

    with pytest.raises(
        ValueError,
        match="unknown task position",
    ):
        reporter._validate_claim_provenance(
            claims,
            [
                provenance(
                    1,
                    ["workspace_verified"],
                )
            ],
        )


def test_claim_rejects_wrong_evidence_type():
    claims = [
        {
            "text": "Verified claim.",
            "supported_by": [
                {
                    "task_position": 1,
                    "evidence_type": (
                        "http_service_verified"
                    ),
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
        )


def test_claim_rejects_unverified_task():
    claims = [
        {
            "text": "Verified claim.",
            "supported_by": [
                {
                    "task_position": 1,
                    "evidence_type": (
                        "builder_verified"
                    ),
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
        )


def test_claim_rejects_non_completed_task():
    claims = [
        {
            "text": "Verified claim.",
            "supported_by": [
                {
                    "task_position": 1,
                    "evidence_type": (
                        "builder_verified"
                    ),
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
        )


def test_claim_rejects_empty_text():
    claims = [
        {
            "text": "   ",
            "supported_by": [
                {
                    "task_position": 1,
                    "evidence_type": (
                        "builder_verified"
                    ),
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
            [
                provenance(
                    1,
                    ["builder_verified"],
                )
            ],
        )


def test_claim_rejects_non_list_claims():
    with pytest.raises(
        ValueError,
        match="list",
    ):
        reporter._validate_claim_provenance(
            {"text": "bad"},
            [],
        )


def test_claim_rejects_missing_task_position():
    claims = [
        {
            "text": "Verified claim.",
            "supported_by": [
                {
                    "evidence_type": "builder_verified",
                }
            ],
        }
    ]

    with pytest.raises(
        ValueError,
        match="task_position",
    ):
        reporter._validate_claim_provenance(
            claims,
            [
                provenance(
                    1,
                    ["builder_verified"],
                )
            ],
        )


def test_claim_rejects_non_integer_task_position():
    claims = [
        {
            "text": "Verified claim.",
            "supported_by": [
                {
                    "task_position": "1",
                    "evidence_type": "builder_verified",
                }
            ],
        }
    ]

    with pytest.raises(
        ValueError,
        match="task_position",
    ):
        reporter._validate_claim_provenance(
            claims,
            [
                provenance(
                    1,
                    ["builder_verified"],
                )
            ],
        )


def test_claim_rejects_missing_evidence_type():
    claims = [
        {
            "text": "Verified claim.",
            "supported_by": [
                {
                    "task_position": 1,
                }
            ],
        }
    ]

    with pytest.raises(
        ValueError,
        match="evidence_type",
    ):
        reporter._validate_claim_provenance(
            claims,
            [
                provenance(
                    1,
                    ["builder_verified"],
                )
            ],
        )


def test_claim_rejects_empty_evidence_type():
    claims = [
        {
            "text": "Verified claim.",
            "supported_by": [
                {
                    "task_position": 1,
                    "evidence_type": "   ",
                }
            ],
        }
    ]

    with pytest.raises(
        ValueError,
        match="evidence_type",
    ):
        reporter._validate_claim_provenance(
            claims,
            [
                provenance(
                    1,
                    ["builder_verified"],
                )
            ],
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
          "task_position": 6,
          "evidence_type": "http_service_verified"
        }
      ]
    }
  ]
}
""".strip()

    envelope = reporter._parse_reporter_envelope(content)

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
        reporter._parse_reporter_envelope(content)


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
        reporter._parse_reporter_envelope(content)
