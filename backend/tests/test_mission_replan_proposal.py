import sqlite3

import pytest

from services import planner


MISSION_ID = 18001


def _setup_database(tmp_path, monkeypatch):
    db_path = tmp_path / "mission-replan-proposal.db"

    def get_connection():
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        return conn

    conn = get_connection()

    try:
        conn.executescript(
            """
            CREATE TABLE missions (
                id INTEGER PRIMARY KEY,
                title TEXT NOT NULL,
                status TEXT NOT NULL,
                assigned_agent TEXT NOT NULL,
                priority TEXT NOT NULL
            );

            CREATE TABLE mission_plans (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                mission_id INTEGER NOT NULL UNIQUE,
                model TEXT NOT NULL,
                plan TEXT NOT NULL,
                status TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            """
        )

        conn.execute(
            """
            INSERT INTO missions (
                id,
                title,
                status,
                assigned_agent,
                priority
            )
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                MISSION_ID,
                "Build and verify a local task application",
                "Running",
                "Executor",
                "High",
            ),
        )

        conn.execute(
            """
            INSERT INTO mission_plans (
                mission_id,
                model,
                plan,
                status
            )
            VALUES (?, ?, ?, ?)
            """,
            (
                MISSION_ID,
                "qwen3:8b",
                (
                    "1. Build application\n"
                    "   Implement the application\n"
                    "2. Old verification\n"
                    "   Run the old verification"
                ),
                "ready",
            ),
        )

        conn.commit()

    finally:
        conn.close()

    monkeypatch.setattr(
        planner,
        "get_connection",
        get_connection,
    )

    monkeypatch.setattr(
        planner,
        "ensure_plan_table",
        lambda: None,
    )

    monkeypatch.setattr(
        planner,
        "log_event",
        lambda *args, **kwargs: None,
    )

    return get_connection


def test_propose_replan_uses_evidence_without_mutating_stored_plan(
    tmp_path,
    monkeypatch,
):
    get_connection = _setup_database(
        tmp_path,
        monkeypatch,
    )

    captured = {}

    proposed_plan = (
        "1. Build application\n"
        "   Implement the application\n"
        "2. Fresh verification\n"
        "   Run deterministic verification"
    )

    def fake_chat_with_ollama(
        *,
        model,
        messages,
        stream,
        timeout,
    ):
        captured["model"] = model
        captured["messages"] = messages
        captured["stream"] = stream
        captured["timeout"] = timeout

        return {
            "message": {
                "content": proposed_plan,
            }
        }

    monkeypatch.setattr(
        planner,
        "chat_with_ollama",
        fake_chat_with_ollama,
    )

    proposal = planner.propose_replan(
        MISSION_ID,
        completed_work=(
            "Task 1 completed with verified build evidence."
        ),
        triggering_evidence=(
            "VERIFIED EXECUTION EVIDENCE: old verification "
            "is no longer appropriate."
        ),
        reason=(
            "New execution evidence requires a different "
            "Pending verification tail."
        ),
    )

    assert proposal["mission_id"] == MISSION_ID
    assert proposal["replacement_plan"] == proposed_plan

    prompt = "\n".join(
        message["content"]
        for message in captured["messages"]
    )

    assert "Old verification" in prompt
    assert "verified build evidence" in prompt
    assert "VERIFIED EXECUTION EVIDENCE" in prompt
    assert "different Pending verification tail" in prompt

    conn = get_connection()

    try:
        stored_plan = conn.execute(
            """
            SELECT plan
            FROM mission_plans
            WHERE mission_id=?
            """,
            (MISSION_ID,),
        ).fetchone()["plan"]

    finally:
        conn.close()

    assert stored_plan == (
        "1. Build application\n"
        "   Implement the application\n"
        "2. Old verification\n"
        "   Run the old verification"
    )


def test_propose_replan_requires_existing_mission(
    tmp_path,
    monkeypatch,
):
    _setup_database(
        tmp_path,
        monkeypatch,
    )

    with pytest.raises(
        ValueError,
        match="was not found",
    ):
        planner.propose_replan(
            99999,
            completed_work="Completed work.",
            triggering_evidence="Evidence.",
            reason="Reason.",
        )


def test_planner_proposal_must_pass_executor_preview(
    tmp_path,
    monkeypatch,
):
    get_connection = _setup_database(
        tmp_path,
        monkeypatch,
    )

    # Executor and Planner must observe the same test database.
    from services import executor

    monkeypatch.setattr(
        executor,
        "get_connection",
        get_connection,
    )

    monkeypatch.setattr(
        executor,
        "ensure_task_table",
        lambda: None,
    )

    conn = get_connection()

    try:
        conn.execute(
            """
            CREATE TABLE mission_tasks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                mission_id INTEGER NOT NULL,
                position INTEGER NOT NULL,
                title TEXT NOT NULL,
                instructions TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'Pending',
                result TEXT NOT NULL DEFAULT '',
                execution_token TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                started_at TIMESTAMP,
                completed_at TIMESTAMP,
                UNIQUE(mission_id, position)
            )
            """
        )

        conn.execute(
            """
            INSERT INTO mission_tasks (
                mission_id,
                position,
                title,
                instructions,
                status,
                result,
                started_at,
                completed_at
            )
            VALUES (?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
            """,
            (
                MISSION_ID,
                1,
                "Build application",
                "Implement the application",
                "Completed",
                "VERIFIED BUILD EVIDENCE",
            ),
        )

        conn.execute(
            """
            INSERT INTO mission_tasks (
                mission_id,
                position,
                title,
                instructions,
                status
            )
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                MISSION_ID,
                2,
                "Old verification",
                "Run the old verification",
                "Pending",
            ),
        )

        conn.commit()

    finally:
        conn.close()

    # Simulate an LLM proposal that illegally rewrites completed task 1.
    invalid_proposal = (
        "1. Rewrite completed application\n"
        "   Replace previously completed work\n"
        "2. Fresh verification\n"
        "   Run deterministic verification"
    )

    monkeypatch.setattr(
        planner,
        "chat_with_ollama",
        lambda **kwargs: {
            "message": {
                "content": invalid_proposal,
            }
        },
    )

    proposal = planner.propose_replan(
        MISSION_ID,
        completed_work=(
            "Task 1 completed with VERIFIED BUILD EVIDENCE."
        ),
        triggering_evidence=(
            "New evidence requires different verification."
        ),
        reason=(
            "Replace only the remaining Pending verification."
        ),
    )

    assert proposal["replacement_plan"] == invalid_proposal

    with pytest.raises(
        RuntimeError,
        match="preserved|completed|immutable|match",
    ):
        executor.preview_pending_replan(
            MISSION_ID,
            proposal["replacement_plan"],
        )
