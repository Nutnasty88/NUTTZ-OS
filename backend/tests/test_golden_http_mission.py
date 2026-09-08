import json
import sqlite3

from app.services import events
from app.services import reporter
from services import autonomous_worker
from services import builder
from services import executor
from services import workspace_executor
from services import workspace_manager


MAIN_SOURCE = """\
from pathlib import Path
import sqlite3

from fastapi import FastAPI
from pydantic import BaseModel


app = FastAPI()
database_path = Path(__file__).with_name("tasks.db")


def initialize_database():
    connection = sqlite3.connect(database_path)

    try:
        connection.execute(
            "CREATE TABLE IF NOT EXISTS tasks "
            "(id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "title TEXT NOT NULL)"
        )
        connection.commit()
    finally:
        connection.close()


initialize_database()


class TaskInput(BaseModel):
    title: str


@app.post("/tasks")
def create_task(task: TaskInput):
    connection = sqlite3.connect(database_path)

    try:
        connection.execute(
            "INSERT INTO tasks (title) VALUES (?)",
            (task.title,),
        )
        connection.commit()
    finally:
        connection.close()

    return {"title": task.title}


@app.get("/tasks")
def list_tasks():
    connection = sqlite3.connect(database_path)

    try:
        rows = connection.execute(
            "SELECT title FROM tasks ORDER BY id"
        ).fetchall()
    finally:
        connection.close()

    return [
        {"title": row[0]}
        for row in rows
    ]
"""


def test_golden_9916_http_service_mission(
    tmp_path,
    monkeypatch,
):
    db_path = tmp_path / "golden-9916.db"
    workspace_root = tmp_path / "builder-workspaces"
    workspace_root.mkdir()

    mission_id = 13003

    def get_connection():
        connection = sqlite3.connect(
            db_path,
            timeout=5.0,
        )
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 5000")
        return connection

    conn = get_connection()

    try:
        conn.executescript(
            """
            CREATE TABLE missions (
                id INTEGER PRIMARY KEY,
                title TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'Waiting',
                progress INTEGER NOT NULL DEFAULT 0,
                assigned_agent TEXT DEFAULT '',
                priority TEXT DEFAULT 'Normal',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE mission_tasks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                mission_id INTEGER NOT NULL,
                position INTEGER NOT NULL,
                title TEXT NOT NULL,
                instructions TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL DEFAULT 'Pending',
                result TEXT NOT NULL DEFAULT '',
                started_at TIMESTAMP,
                completed_at TIMESTAMP,
                execution_token TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (mission_id)
                    REFERENCES missions(id)
                    ON DELETE CASCADE,
                UNIQUE (mission_id, position)
            );

            CREATE TABLE mission_plans (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                mission_id INTEGER NOT NULL UNIQUE,
                plan TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (mission_id)
                    REFERENCES missions(id)
                    ON DELETE CASCADE
            );

            CREATE TABLE mission_research (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                mission_id INTEGER NOT NULL UNIQUE,
                report_json TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (mission_id)
                    REFERENCES missions(id)
                    ON DELETE CASCADE
            );

            CREATE TABLE mission_deliverables (
                mission_id INTEGER PRIMARY KEY,
                model TEXT NOT NULL,
                status TEXT NOT NULL,
                content TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (mission_id)
                    REFERENCES missions(id)
                    ON DELETE CASCADE
            );

            CREATE TABLE mission_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                mission_id INTEGER,
                agent TEXT NOT NULL,
                event_type TEXT NOT NULL,
                message TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (mission_id)
                    REFERENCES missions(id)
                    ON DELETE CASCADE
            );
            """
        )

        conn.execute(
            """
            INSERT INTO missions (
                id,
                title,
                status,
                progress,
                assigned_agent,
                priority
            )
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                mission_id,
                (
                    "Build a FastAPI task service in main.py "
                    "using persistent SQLite storage."
                ),
                "Running",
                0,
                "Planner",
                "Normal",
            ),
        )

        tasks = [
            (
                mission_id,
                1,
                "Create main.py with FastAPI setup",
                (
                    "Implement FastAPI application with "
                    "SQLite integration in main.py."
                ),
            ),
            (
                mission_id,
                2,
                "Implement SQLite database schema creation",
                (
                    "Create tasks table in tasks.db using "
                    "sqlite3 in main.py."
                ),
            ),
            (
                mission_id,
                3,
                "Implement POST /tasks endpoint",
                (
                    "Modify main.py to insert the JSON title "
                    "into tasks.db."
                ),
            ),
            (
                mission_id,
                4,
                "Implement GET /tasks endpoint",
                (
                    "Modify main.py to return persisted tasks "
                    "as a JSON list."
                ),
            ),
            (
                mission_id,
                5,
                "Verify initial task persistence",
                (
                    "HTTP POST /tasks\n"
                    'JSON body must equal {"title":"Buy milk"}\n'
                    "HTTP status must equal 200\n"
                    'JSON response must equal {"title":"Buy milk"}\n'
                    "HTTP GET /tasks\n"
                    "HTTP status must equal 200\n"
                    'JSON response must equal '
                    '[{"title":"Buy milk"}]'
                ),
            ),
            (
                mission_id,
                6,
                "Restart service",
                (
                    "Restart service\n"
                    "HTTP GET /tasks\n"
                    "HTTP status must equal 200\n"
                    'JSON response must equal '
                    '[{"title":"Buy milk"}]'
                ),
            ),
        ]

        conn.executemany(
            """
            INSERT INTO mission_tasks (
                mission_id,
                position,
                title,
                instructions,
                status
            )
            VALUES (?, ?, ?, ?, 'Pending')
            """,
            tasks,
        )

        conn.commit()
    finally:
        conn.close()

    for module in (
        autonomous_worker,
        executor,
        reporter,
        events,
    ):
        monkeypatch.setattr(
            module,
            "get_connection",
            get_connection,
        )

    monkeypatch.setattr(
        workspace_manager,
        "PROJECT_ROOT",
        tmp_path,
    )
    monkeypatch.setattr(
        workspace_manager,
        "WORKSPACE_ROOT",
        workspace_root,
    )
    monkeypatch.setattr(
        workspace_executor,
        "WORKSPACE_ROOT",
        workspace_root,
    )

    builder_responses = [
        {
            "summary": "Created FastAPI application.",
            "entrypoint": "main.py",
            "files": [
                {
                    "path": "main.py",
                    "content": MAIN_SOURCE,
                }
            ],
        },
        {
            "summary": "Implemented SQLite schema.",
            "entrypoint": "main.py",
            "files": [
                {
                    "path": "main.py",
                    "content": MAIN_SOURCE,
                }
            ],
        },
        {
            "summary": "Implemented POST /tasks.",
            "entrypoint": "main.py",
            "files": [
                {
                    "path": "main.py",
                    "content": MAIN_SOURCE,
                }
            ],
        },
        {
            "summary": "Implemented GET /tasks.",
            "entrypoint": "main.py",
            "files": [
                {
                    "path": "main.py",
                    "content": MAIN_SOURCE,
                }
            ],
        },
    ]

    def fake_builder_chat(
        *,
        model,
        messages,
        stream,
        think,
    ):
        assert stream is False
        assert think is False
        assert builder_responses

        payload = builder_responses.pop(0)

        return {
            "message": {
                "content": json.dumps(payload)
            }
        }

    monkeypatch.setattr(
        builder,
        "chat_with_ollama",
        fake_builder_chat,
    )

    def fake_reporter_chat(
        *,
        model,
        messages,
        stream,
        think,
        options,
        timeout,
    ):
        assert model == reporter.REPORTER_MODEL
        assert stream is False
        assert think is False
        assert options == {"num_predict": 500}
        assert timeout == 300

        user_prompt = messages[1]["content"]

        assert "MISSION EVIDENCE:" in user_prompt

        evidence = json.loads(
            user_prompt.split(
                "MISSION EVIDENCE:\n",
                1,
            )[1]
        )

        provenance = evidence["task_provenance"]

        assert [
            item["evidence_types"]
            for item in provenance
        ] == [
            ["builder_verified"],
            ["builder_verified"],
            ["builder_verified"],
            ["builder_verified"],
            ["http_service_verified"],
            ["http_service_verified"],
        ]

        assert all(
            item["status"] == "Completed"
            and item["verified"] is True
            for item in provenance
        )

        assert (
            "machine-derived verification metadata"
            in messages[0]["content"]
        )

        assert (
            "only when they are explicitly supported"
            in messages[0]["content"]
        )
        assert (
            "Do not contradict verified mission evidence"
            in messages[0]["content"]
        )
        assert (
            "omit a limitations section entirely"
            in messages[0]["content"]
        )
        assert "Buy milk" in user_prompt
        assert "HTTP SERVICE EXECUTION: VERIFIED" in user_prompt

        return {
            "message": {
                "content": json.dumps(
                    {
                        "deliverable": (
                            "# Golden 9916 HTTP Deliverable\n\n"
                            "The FastAPI SQLite service was "
                            "verified across restart.\n\n"
                            "Mission outcome: Completed."
                        ),
                        "claims": [
                            {
                                "text": (
                                    "The FastAPI SQLite service "
                                    "was verified across restart."
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
                        ],
                    }
                )
            }
        }

    monkeypatch.setattr(
        reporter,
        "chat_with_ollama",
        fake_reporter_chat,
    )

    autonomous_worker._stop_event.clear()

    lease = autonomous_worker.acquire_worker_lease(
        mission_id
    )

    autonomous_worker._run_worker(
        mission_id,
        0,
        lease["owner_token"],
    )

    assert builder_responses == []

    workspace_path = (
        workspace_root / f"mission-{mission_id}"
    )

    runtime_db = workspace_path / "tasks.db"

    assert runtime_db.exists()
    assert runtime_db.is_file()

    runtime_connection = sqlite3.connect(runtime_db)

    try:
        rows = runtime_connection.execute(
            """
            SELECT id, title
            FROM tasks
            ORDER BY id
            """
        ).fetchall()
    finally:
        runtime_connection.close()

    assert rows == [(1, "Buy milk")]

    manifest_path = workspace_path / "nuttz-project.json"

    assert manifest_path.exists()

    manifest = json.loads(
        manifest_path.read_text()
    )

    assert manifest["schema_version"] == 3
    assert manifest["runtime"] == "python"
    assert manifest["launch_type"] == "python-asgi"
    assert manifest["entrypoint"] == "main.py"
    assert manifest["run_command"] == [
        "python-asgi",
        "main.py",
    ]

    manifest_paths = {
        item["path"]
        for item in manifest["files"]
    }

    assert "main.py" in manifest_paths
    assert "tasks.db" not in manifest_paths

    conn = get_connection()

    try:
        mission = conn.execute(
            """
            SELECT status, progress
            FROM missions
            WHERE id=?
            """,
            (mission_id,),
        ).fetchone()

        completed_tasks = conn.execute(
            """
            SELECT position, status, result
            FROM mission_tasks
            WHERE mission_id=?
            ORDER BY position
            """,
            (mission_id,),
        ).fetchall()

        deliverable = conn.execute(
            """
            SELECT model, status, content
            FROM mission_deliverables
            WHERE mission_id=?
            """,
            (mission_id,),
        ).fetchone()

        lease_row = conn.execute(
            """
            SELECT mission_id
            FROM mission_worker_leases
            WHERE mission_id=?
            """,
            (mission_id,),
        ).fetchone()

        event_rows = conn.execute(
            """
            SELECT agent, event_type, message
            FROM mission_events
            WHERE mission_id=?
            ORDER BY id
            """,
            (mission_id,),
        ).fetchall()
    finally:
        conn.close()

    assert mission["status"] == "Completed"
    assert mission["progress"] == 100

    assert [
        row["status"]
        for row in completed_tasks
    ] == ["Completed"] * 6

    assert all(
        row["result"]
        for row in completed_tasks
    )

    for row in completed_tasks[:4]:
        assert "BUILDER AGENT: COMPLETED" in row["result"]
        assert "AUTO PROJECT EXECUTION: DEFERRED" in row["result"]

    for row in completed_tasks[4:]:
        assert (
            "HTTP SERVICE EXECUTION: VERIFIED"
            in row["result"]
        )
        assert '"service_stopped": true' in row["result"]
        assert '"verified": true' in row["result"]

    assert '"restart_count": 0' in completed_tasks[4]["result"]
    assert '"restart_count": 1' in completed_tasks[5]["result"]

    assert deliverable["model"] == reporter.REPORTER_MODEL
    assert deliverable["status"] == "Ready"
    assert (
        deliverable["content"]
        == (
            "# Golden 9916 HTTP Deliverable\n\n"
            "The FastAPI SQLite service was "
            "verified across restart.\n\n"
            "Mission outcome: Completed."
        )
    )

    assert lease_row is None

    messages = [
        row["message"]
        for row in event_rows
    ]

    assert sum(
        "routed to Builder Agent" in message
        for message in messages
    ) == 4

    assert sum(
        "routed to managed HTTP service verification"
        in message
        for message in messages
    ) == 2

    assert sum(
        "Verified ASGI project manifest written"
        in message
        for message in messages
    ) == 2

    assert sum(
        "verified HTTP service main.py"
        in message
        for message in messages
    ) == 2

    state = autonomous_worker.get_worker_status()

    assert state["status"] == "Completed"
    assert state["completed_tasks"] == 6
    assert state["total_tasks"] == 6
    assert state["last_error"] == ""
