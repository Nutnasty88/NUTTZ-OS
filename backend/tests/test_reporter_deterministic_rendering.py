import pytest

from app.services import reporter


def test_render_verified_claims_is_deterministic():
    claims = [
        {
            "text": "The service restarted successfully.",
            "supported_by": [
                {
                    "fact_id": "task-6:service-restart",
                }
            ],
        },
        {
            "text": "The persisted task remained available.",
            "supported_by": [
                {
                    "fact_id": "task-6:http-check:1",
                }
            ],
        },
    ]

    rendered = reporter._render_verified_claims(claims)

    assert rendered == (
        "## Verified Results\n\n"
        "- The service restarted successfully.\n"
        "- The persisted task remained available."
    )


def test_render_verified_claims_preserves_claim_order():
    claims = [
        {
            "text": "First verified result.",
            "supported_by": [
                {"fact_id": "task-1:artifact"}
            ],
        },
        {
            "text": "Second verified result.",
            "supported_by": [
                {"fact_id": "task-2:execution"}
            ],
        },
        {
            "text": "Third verified result.",
            "supported_by": [
                {"fact_id": "task-3:http-check:1"}
            ],
        },
    ]

    rendered = reporter._render_verified_claims(claims)

    assert rendered.index(
        "First verified result."
    ) < rendered.index(
        "Second verified result."
    ) < rendered.index(
        "Third verified result."
    )


def test_render_verified_claims_does_not_render_fact_ids():
    claims = [
        {
            "text": "Execution completed successfully.",
            "supported_by": [
                {
                    "fact_id": "task-5:execution",
                }
            ],
        }
    ]

    rendered = reporter._render_verified_claims(claims)

    assert "Execution completed successfully." in rendered
    assert "task-5:execution" not in rendered


def test_render_verified_claims_rejects_empty_claims():
    with pytest.raises(
        ValueError,
        match="at least one verified claim",
    ):
        reporter._render_verified_claims([])


def test_render_verified_claims_rejects_invalid_claim_shape():
    claims = [
        {
            "text": "",
            "supported_by": [
                {
                    "fact_id": "task-1:artifact",
                }
            ],
        }
    ]

    with pytest.raises(
        ValueError,
        match="claim text",
    ):
        reporter._render_verified_claims(claims)


def test_model_deliverable_cannot_override_verified_claim_rendering(
    monkeypatch,
):
    claims = [
        {
            "text": "The verified task persisted after restart.",
            "supported_by": [
                {
                    "fact_id": "task-6:http-check:1",
                }
            ],
        }
    ]

    envelope = {
        "deliverable": (
            "# Model-authored report\n\n"
            "THIS TEXT MUST NOT BECOME THE STORED "
            "VERIFIED DELIVERABLE."
        ),
        "claims": claims,
    }

    validated_claims = claims

    rendered = reporter._render_verified_claims(
        validated_claims
    )

    assert rendered == (
        "## Verified Results\n\n"
        "- The verified task persisted after restart."
    )

    assert envelope["deliverable"] != rendered
    assert "THIS TEXT MUST NOT BECOME" not in rendered
