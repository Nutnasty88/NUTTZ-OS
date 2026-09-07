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
    assert captured["timeout"] == 300

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
        "Every implementation task must name the source file it creates "
        "or changes explicitly in the task action."
        in normalized_prompt
    )

    assert (
        "repeat its filename rather than relying on context."
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

    assert (
        "When success depends on a sequence of command-line executions, "
        "write every required command explicitly and in execution order "
        "in the success-check."
        in normalized_prompt
    )

    assert (
        "Do not summarize a required command as prose such as "
        "\"after adding a task\"."
        in normalized_prompt
    )

    assert (
        "When a success-check verifies persistence across a restart or "
        "fresh process, explicitly include the command that creates the "
        "state"
        in normalized_prompt
    )

    assert (
        'main.py add "Buy milk" followed by main.py list'
        in normalized_prompt
    )

    assert (
        "When the mission specifies exact expected output, preserve that "
        "requirement explicitly in the success-check."
        in normalized_prompt
    )

    assert (
        'stdout must equal "Buy milk"'
        in normalized_prompt
    )


def test_create_plan_defines_structured_http_service_contract(
    monkeypatch,
    tmp_path,
):
    db_path = tmp_path / "planner-http-contract.db"

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
                2,
                "Build a persistent FastAPI task service",
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

    def fake_chat_with_ollama(
        *,
        model,
        messages,
        stream,
        timeout,
    ):
        captured["messages"] = messages

        return {
            "message": {
                "content": (
                    "1. Create FastAPI service in main.py\n"
                    "2. Verify HTTP persistence\n"
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

    result = planner.create_plan(2)

    assert result["status"] == "Ready"

    system_prompt = captured["messages"][0]["content"]
    normalized_prompt = " ".join(system_prompt.split())

    assert (
        "For HTTP service missions, do not express API verification "
        "as shell commands"
        in normalized_prompt
    )
    assert (
        "curl, grep, pipes, background processes, kill commands"
        in normalized_prompt
    )
    assert "HTTP POST /tasks" in normalized_prompt
    assert (
        'JSON body must equal {"title":"Buy milk"}'
        in normalized_prompt
    )
    assert "HTTP status must equal 200" in normalized_prompt
    assert (
        "expected HTTP response bodies as JSON response requirements, "
        "not stdout requirements."
        in normalized_prompt
    )
    assert (
        "service restart followed by the HTTP request that verifies "
        "the persisted state."
        in normalized_prompt
    )
    assert (
        "use Python's standard-library sqlite3 module unless the mission "
        "explicitly requires a third-party ORM or database library."
        in normalized_prompt
    )
    assert (
        "Do not introduce SQLAlchemy merely to implement SQLite."
        in normalized_prompt
    )
