from app.services import reporter


def task(result, position=1, status="Completed"):
    return {
        "position": position,
        "title": "Test task",
        "instructions": "",
        "status": status,
        "result": result,
    }


def test_builder_provenance_requires_both_exact_markers():
    result = (
        "BUILDER AGENT: COMPLETED\n\n"
        "Built main.py\n\n"
        "VERIFIED BUILDER EVIDENCE:\n"
        '{"verified": true}'
    )

    provenance = reporter._task_provenance(
        task(result)
    )

    assert provenance == {
        "position": 1,
        "status": "Completed",
        "evidence_types": ["builder_verified"],
        "verified": True,
    }


def test_workspace_provenance_requires_both_exact_markers():
    result = (
        "WORKSPACE EXECUTION: VERIFIED\n\n"
        "Artifact: main.py\n\n"
        "VERIFIED EXECUTION EVIDENCE:\n"
        '{"verified": true}'
    )

    provenance = reporter._task_provenance(
        task(result)
    )

    assert provenance["evidence_types"] == [
        "workspace_verified"
    ]
    assert provenance["verified"] is True


def test_http_provenance_requires_both_exact_markers():
    result = (
        "HTTP SERVICE EXECUTION: VERIFIED\n\n"
        "VERIFIED HTTP SERVICE EVIDENCE:\n"
        '{"verified": true, "service_stopped": true}'
    )

    provenance = reporter._task_provenance(
        task(result)
    )

    assert provenance["evidence_types"] == [
        "http_service_verified"
    ]
    assert provenance["verified"] is True


def test_provenance_rejects_marker_without_evidence_block():
    provenance = reporter._task_provenance(
        task("HTTP SERVICE EXECUTION: VERIFIED\n")
    )

    assert provenance["evidence_types"] == []
    assert provenance["verified"] is False


def test_provenance_rejects_evidence_block_without_verified_header():
    provenance = reporter._task_provenance(
        task(
            "Something else\n\n"
            "VERIFIED HTTP SERVICE EVIDENCE:\n"
            '{"verified": true}'
        )
    )

    assert provenance["evidence_types"] == []
    assert provenance["verified"] is False


def test_provenance_map_preserves_task_order():
    tasks = [
        task(
            (
                "BUILDER AGENT: COMPLETED\n\n"
                'VERIFIED BUILDER EVIDENCE:\n{"verified": true}'
            ),
            position=1,
        ),
        task(
            (
                "HTTP SERVICE EXECUTION: VERIFIED\n\n"
                "VERIFIED HTTP SERVICE EVIDENCE:\n"
                '{"verified": true, "service_stopped": true}'
            ),
            position=2,
        ),
    ]

    provenance = reporter._task_provenance_map(tasks)

    assert [item["position"] for item in provenance] == [
        1,
        2,
    ]
    assert provenance[0]["evidence_types"] == [
        "builder_verified"
    ]
    assert provenance[1]["evidence_types"] == [
        "http_service_verified"
    ]



def test_provenance_rejects_verified_false():
    result = (
        "BUILDER AGENT: COMPLETED\n\n"
        "VERIFIED BUILDER EVIDENCE:\n"
        '{"verified": false}'
    )

    provenance = reporter._task_provenance(
        task(result)
    )

    assert provenance["verified"] is False
    assert provenance["evidence_types"] == []


def test_provenance_rejects_invalid_evidence_json():
    result = (
        "WORKSPACE EXECUTION: VERIFIED\n\n"
        "VERIFIED EXECUTION EVIDENCE:\n"
        '{"verified": true'
    )

    provenance = reporter._task_provenance(
        task(result)
    )

    assert provenance["verified"] is False
    assert provenance["evidence_types"] == []


def test_http_provenance_requires_clean_service_stop():
    result = (
        "HTTP SERVICE EXECUTION: VERIFIED\n\n"
        "VERIFIED HTTP SERVICE EVIDENCE:\n"
        '{"verified": true, "service_stopped": false}'
    )

    provenance = reporter._task_provenance(
        task(result)
    )

    assert provenance["verified"] is False
    assert provenance["evidence_types"] == []


def test_http_provenance_accepts_verified_clean_stop():
    result = (
        "HTTP SERVICE EXECUTION: VERIFIED\n\n"
        "VERIFIED HTTP SERVICE EVIDENCE:\n"
        '{"verified": true, "service_stopped": true}'
    )

    provenance = reporter._task_provenance(
        task(result)
    )

    assert provenance["verified"] is True
    assert provenance["evidence_types"] == [
        "http_service_verified"
    ]



def test_provenance_requires_completed_task_status():
    result = (
        "BUILDER AGENT: COMPLETED\n\n"
        "VERIFIED BUILDER EVIDENCE:\n"
        '{"verified": true}'
    )

    provenance = reporter._task_provenance(
        {
            "position": 1,
            "status": "Error",
            "result": result,
        }
    )

    assert provenance == {
        "position": 1,
        "status": "Error",
        "evidence_types": [],
        "verified": False,
    }
