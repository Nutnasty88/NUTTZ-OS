import json
import sqlite3

from app.services import events
from app.services import reporter
from services import autonomous_worker
from services import builder
from services import executor
from services import workspace_executor
from services import workspace_manager


MAIN_SOURCE = '''\
import argparse

from db_init import get_connection


def add_task(title):
    connection = get_connection()

    try:
        connection.execute(
            "INSERT INTO tasks (title) VALUES (?)",
            (title,),
        )
        connection.commit()
    finally:
        connection.close()


def list_tasks():
    connection = get_connection()

    try:
        rows = connection.execute(
            "SELECT title FROM tasks ORDER BY id"
        ).fetchall()
    finally:
        connection.close()

    for row in rows:
        print(row[0])


def main():
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(
        dest="command"
    )

    add_parser = subparsers.add_parser("add")
    add_parser.add_argument("title")

    subparsers.add_parser("list")

    args = parser.parse_args()

    if args.command == "add":
        add_task(args.title)
        return

    if args.command == "list":
        list_tasks()
        return

    parser.print_help()


if __name__ == "__main__":
    main()
'''


DB_INIT_SOURCE = '''\
import sqlite3
from pathlib import Path


DB_PATH = Path(__file__).with_name("tasks.db")


def get_connection():
    connection = sqlite3.connect(DB_PATH)

    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS tasks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL
        )
        """
    )

    connection.commit()

    return connection
'''


def test_golden_9911_workspace_mission(
    tmp_path,
    monkeypatch,
):
    db_path = tmp_path / "golden-9911.db"
    workspace_root = tmp_path / "builder-workspaces"
    workspace_root.mkdir()

    mission_id = 13002

    def get_connection():
        connection = sqlite3.connect(
            db_path,
            timeout=5.0,
        )
        connection.row_factory = sqlite3.Row
        connection.execute(
            "PRAGMA foreign_keys = ON"
        )
        connection.execute(
            "PRAGMA busy_timeout = 5000"
        )
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
                    "Build a Python command-line task app "
                    "that persists tasks in SQLite."
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
                (
                    "Create main.py command-line "
                    "application"
                ),
                (
                    "Implement the command-line task "
                    "application in main.py."
                ),
            ),
            (
                mission_id,
                2,
                (
                    "Implement SQLite initialization "
                    "in db_init.py"
                ),
                (
                    "Implement file-backed SQLite "
                    "database initialization in "
                    "db_init.py."
                ),
            ),
            (
                mission_id,
                3,
                (
                    "Implement task insertion logic "
                    "in main.py"
                ),
                (
                    "Implement the add command logic "
                    "in main.py."
                ),
            ),
            (
                mission_id,
                4,
                (
                    "Implement task listing logic "
                    "in main.py"
                ),
                (
                    "Implement the list command logic "
                    "in main.py."
                ),
            ),
            (
                mission_id,
                5,
                (
                    "Verify task persistence across "
                    "restarts"
                ),
                (
                    "Verify task persistence across "
                    "restarts.\n\n"
                    "Success-check:\n"
                    'main.py add "Buy milk"\n'
                    "main.py list\n"
                    "python main.py\n"
                    "main.py list\n"
                    'stdout must equal "Buy milk"'
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
            "summary": "Created CLI application.",
            "entrypoint": "main.py",
            "files": [
                {
                    "path": "main.py",
                    "content": MAIN_SOURCE,
                }
            ],
        },
        {
            "summary": (
                "Added runtime SQLite initialization."
            ),
            "entrypoint": "main.py",
            "files": [
                {
                    "path": "db_init.py",
                    "content": DB_INIT_SOURCE,
                }
            ],
        },
        {
            "summary": "Implemented task insertion.",
            "entrypoint": "main.py",
            "files": [
                {
                    "path": "main.py",
                    "content": MAIN_SOURCE,
                }
            ],
        },
        {
            "summary": "Implemented task listing.",
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
        assert options == {
            "num_predict": 500,
        }
        assert timeout == 300

        assert len(messages) == 2
        assert messages[0]["role"] == "system"
        assert messages[1]["role"] == "user"

        user_prompt = messages[1]["content"]

        assert "MISSION EVIDENCE:" in user_prompt

        evidence = json.loads(
            user_prompt.split(
                "MISSION EVIDENCE:\n",
                1,
            )[1]
        )

        provenance = evidence["task_provenance"]
        verified_facts = evidence["verified_facts"]

        assert any(
            fact["type"] == "execution_verified"
            and fact["task_position"] == 5
            and fact["evidence_type"] == "workspace_verified"
            and fact["exit_code"] == 0
            for fact in verified_facts
        )

        assert [
            item["evidence_types"]
            for item in provenance
        ] == [
            ["builder_verified"],
            ["builder_verified"],
            ["builder_verified"],
            ["builder_verified"],
            ["workspace_verified"],
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
            "do not emit a claim about it"
            in messages[0]["content"]
        )
        assert "Buy milk" in user_prompt
        assert "WORKSPACE EXECUTION: VERIFIED" in user_prompt

        return {
            "message": {
                "content": json.dumps(
                    {
                        "claims": [
                            {
                                "kind": "execution_verified",
                                "supported_by": [
                                    {
                                        "fact_id": (
                                            "task-5:execution"
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

    # This golden fixture represents an explicitly reviewed,
    # deterministic task plan. Approve that exact plan before
    # crossing the execution boundary.
    approval = executor.approve_mission_plan(mission_id)
    assert approval["approved"] is True

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

    runtime_connection = sqlite3.connect(
        runtime_db
    )

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
            SELECT
                position,
                status,
                result
            FROM mission_tasks
            WHERE mission_id=?
            ORDER BY position
            """,
            (mission_id,),
        ).fetchall()

        deliverable = conn.execute(
            """
            SELECT
                model,
                status,
                content
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

        routing_events = conn.execute(
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
    ] == [
        "Completed",
        "Completed",
        "Completed",
        "Completed",
        "Completed",
    ]

    assert all(
        row["result"]
        for row in completed_tasks
    )

    final_result = completed_tasks[-1]["result"]

    assert (
        "WORKSPACE EXECUTION: VERIFIED"
        in final_result
    )
    assert (
        '"requested_step_count": 3'
        in final_result
    )
    assert (
        '"step_count": 3'
        in final_result
    )
    assert (
        '"expected": "Buy milk"'
        in final_result
    )
    assert (
        '"actual": "Buy milk"'
        in final_result
    )
    assert (
        '"verified": true'
        in final_result
    )

    manifest_path = (
        workspace_path / "nuttz-project.json"
    )

    assert manifest_path.exists()

    manifest = json.loads(
        manifest_path.read_text()
    )

    manifest_paths = {
        item["path"]
        for item in manifest["files"]
    }

    assert "main.py" in manifest_paths
    assert "db_init.py" in manifest_paths
    assert "tasks.db" not in manifest_paths

    assert deliverable["model"] == reporter.REPORTER_MODEL
    assert deliverable["status"] == "Ready"
    assert deliverable["content"] == (
        "## Verified Results\n\n"
        "- Verified execution of main.py completed "
        "successfully with exit code 0."
    )

    assert lease_row is None

    messages = [
        row["message"]
        for row in routing_events
    ]

    assert any(
        "routed to Builder Agent"
        in message
        for message in messages
    )

    assert any(
        "routed to Workspace Executor"
        in message
        for message in messages
    )

    assert any(
        "3 controlled process invocations"
        in message
        for message in messages
    )

    state = autonomous_worker.get_worker_status()

    assert state["status"] == "Completed"
    assert state["completed_tasks"] == 5
    assert state["total_tasks"] == 5
    assert state["last_error"] == ""
