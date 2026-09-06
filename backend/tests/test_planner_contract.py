import sqlite3

from services import planner


def test_create_plan_requires_explicit_implementation_tasks(
    monkeypatch,
    tmp_path,
):
    db_path = tmp_path / "planner-contract.db"

    conn = sqlite3.connect(db_path)

    try:
        conn.execute(
            """
            CREATE TABLE missions (
                id INTEGER PRIMARY KEY,
                title TEXT NOT NULL,
                status TEXT NOT NULL,
                assigned_agent TEXT NOT NULL,
                priority TEXT NOT NULL,
                progress INTEGER NOT NULL DEFAULT 0,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
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
                1,
                "Build a persistent task application",
                "Running",
                "Planner",
                "Normal",
            ),
        )

        conn.commit()
    finally:
        conn.close()

    def fake_get_connection():
        connection = sqlite3.connect(db_path)
        connection.row_factory = sqlite3.Row
        return connection

    captured = {}

    def fake_chat_with_ollama(*, model, messages, stream):
        captured["model"] = model
        captured["messages"] = messages
        captured["stream"] = stream

        return {
            "message": {
                "content": (
                    "1. Implement task storage in task_manager.py\n"
                    "2. Verify persistence\n"
                    "3. Success-check"
                )
            }
        }

    monkeypatch.setattr(
        planner,
        "get_connection",
        fake_get_connection,
    )
    monkeypatch.setattr(
        planner,
        "chat_with_ollama",
        fake_chat_with_ollama,
    )
    monkeypatch.setattr(
        planner,
        "log_event",
        lambda *args, **kwargs: None,
    )

    result = planner.create_plan(1)

    assert result["status"] == "Ready"

    system_prompt = captured["messages"][0]["content"]
    normalized_prompt = " ".join(system_prompt.split())

    assert (
        "For any task that creates or changes application behavior"
        in normalized_prompt
    )
    assert (
        "describe it as an implementation task"
        in normalized_prompt
    )
    assert (
        "name that source file explicitly"
        in normalized_prompt
    )
    assert (
        "Do not describe required code changes only as desired "
        "runtime behavior."
        in normalized_prompt
    )
    assert (
        "Keep execution or verification tasks separate from "
        "implementation tasks."
        in normalized_prompt
    )

    assert (
        "Treat databases and other mutable runtime state as "
        "runtime artifacts, not Builder-created files."
        in normalized_prompt
    )

    assert (
        "For SQLite-backed applications, plan implementation work "
        "that creates or initializes the database from source code "
        "at runtime."
        in normalized_prompt
    )

    assert (
        "Do not create a task whose action is to synthesize a .db, "
        ".sqlite, or .sqlite3 file directly."
        in normalized_prompt
    )

    assert (
        "When the mission requires data to persist across program or "
        "process restarts, use a file-backed SQLite database created by "
        "application source code at runtime."
        in normalized_prompt
    )

    assert (
        "Do not plan an in-memory SQLite database for persistent state."
        in normalized_prompt
    )
