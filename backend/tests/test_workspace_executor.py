import pytest

from services import workspace_executor
from services import workspace_manager
from services.workspace_executor import (
    WorkspaceExecutionError,
    _validate_controlled_arguments,
    execute_python_artifact,
    launch_verified_project,
)


@pytest.fixture
def isolated_builder_root(tmp_path, monkeypatch):
    workspace_root = tmp_path / "builder-workspaces"
    workspace_root.mkdir()

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

    return workspace_root


def create_project(
    mission_id,
    source,
    arguments,
):
    workspace_name = f"mission-{mission_id}"

    workspace_manager.create_workspace(
        workspace_name
    )

    artifact = workspace_manager.write_workspace_file(
        workspace_name,
        "hello.py",
        source,
    )

    workspace_manager.write_project_manifest(
        workspace_name=workspace_name,
        mission_id=mission_id,
        entrypoint="hello.py",
        runtime="python",
        run_command=[
            "python3",
            "-I",
            "-B",
            "hello.py",
            *arguments,
        ],
        artifact_sha256=artifact["sha256"],
        artifact_size_bytes=artifact["size_bytes"],
        verified=True,
    )

    return workspace_name


def test_controlled_argument_validation():
    assert _validate_controlled_arguments(
        ["Alice", "sample-2"]
    ) == ["Alice", "sample-2"]

    with pytest.raises(
        WorkspaceExecutionError,
        match="only bounded",
    ):
        _validate_controlled_arguments(
            ["../../unsafe"]
        )

    with pytest.raises(
        WorkspaceExecutionError,
        match="count exceeds",
    ):
        _validate_controlled_arguments(
            ["A"] * 9
        )

    with pytest.raises(
        WorkspaceExecutionError,
        match="byte limit",
    ):
        _validate_controlled_arguments(
            ["A" * 64] * 8
        )


def test_controlled_name_flag_validation():
    assert _validate_controlled_arguments(
        ["--name", "Alice"]
    ) == ["--name", "Alice"]

    with pytest.raises(
        WorkspaceExecutionError,
        match="only bounded",
    ):
        _validate_controlled_arguments(
            ["--other", "Alice"]
        )

    with pytest.raises(
        WorkspaceExecutionError,
        match="only bounded",
    ):
        _validate_controlled_arguments(
            ["--name"]
        )

    with pytest.raises(
        WorkspaceExecutionError,
        match="only bounded",
    ):
        _validate_controlled_arguments(
            ["--name", "../../unsafe"]
        )


def test_executes_with_controlled_name_flag(
    isolated_builder_root,
):
    workspace_manager.create_workspace(
        "mission-12005"
    )

    workspace_manager.write_workspace_file(
        "mission-12005",
        "hello.py",
        (
            "import argparse\n"
            "parser = argparse.ArgumentParser()\n"
            'parser.add_argument("--name", required=True)\n'
            "args = parser.parse_args()\n"
            'print(f"Hello, {args.name}!")\n'
        ),
    )

    execution = execute_python_artifact(
        12005,
        "hello.py",
        arguments=["--name", "Alice"],
    )

    assert execution["verified"] is True
    assert execution["exit_code"] == 0
    assert execution["stdout"] == "Hello, Alice!\n"
    assert execution["arguments_supplied"] is True
    assert execution["argument_count"] == 2


def test_executes_with_controlled_stdin(
    isolated_builder_root,
):
    workspace_manager.create_workspace(
        "mission-12001"
    )

    workspace_manager.write_workspace_file(
        "mission-12001",
        "hello.py",
        (
            "name = input()\n"
            'print(f"Hello, {name}!")\n'
        ),
    )

    execution = execute_python_artifact(
        12001,
        "hello.py",
        stdin_text="NAME\n",
    )

    assert execution["verified"] is True
    assert execution["exit_code"] == 0
    assert execution["stdout"] == "Hello, NAME!\n"
    assert execution["stdin_supplied"] is True
    assert execution["stdin_size_bytes"] == 5
    assert len(execution["stdin_sha256"]) == 64


def test_manifest_launches_with_controlled_argument(
    isolated_builder_root,
):
    create_project(
        12002,
        (
            "import sys\n"
            "name = sys.argv[1]\n"
            'print(f"Hello, {name}!")\n'
        ),
        ["Alice"],
    )

    launch = launch_verified_project(12002)
    execution = launch["execution"]

    assert launch["success"] is True
    assert execution["verified"] is True
    assert execution["exit_code"] == 0
    assert execution["stdout"] == "Hello, Alice!\n"
    assert execution["arguments_supplied"] is True
    assert execution["argument_count"] == 1
    assert len(execution["arguments_sha256"]) == 64
    assert (
        execution["command"][-1]
        == "<controlled-argument>"
    )


def test_argument_free_manifest_remains_supported(
    isolated_builder_root,
):
    create_project(
        12003,
        'print("READY")\n',
        [],
    )

    launch = launch_verified_project(12003)
    execution = launch["execution"]

    assert launch["success"] is True
    assert execution["stdout"] == "READY\n"
    assert execution["arguments_supplied"] is False
    assert execution["argument_count"] == 0


def test_manifest_denies_unsafe_argument(
    isolated_builder_root,
):
    create_project(
        12004,
        'print("SHOULD NOT RUN")\n',
        ["../../unsafe"],
    )

    with pytest.raises(
        WorkspaceExecutionError,
        match="controlled arguments are invalid",
    ):
        launch_verified_project(12004)


def test_manifest_allows_sqlite_runtime_state_created_after_verification(
    isolated_builder_root,
):
    import sqlite3

    mission_id = 12008
    workspace_name = create_project(
        mission_id,
        'print("READY")\n',
        [],
    )

    database_path = (
        isolated_builder_root
        / workspace_name
        / "tasks.db"
    )

    connection = sqlite3.connect(database_path)

    try:
        connection.execute(
            """
            CREATE TABLE tasks (
                id INTEGER PRIMARY KEY,
                task TEXT NOT NULL
            )
            """
        )
        connection.execute(
            "INSERT INTO tasks (task) VALUES (?)",
            ("Buy milk",),
        )
        connection.commit()
    finally:
        connection.close()

    launch = launch_verified_project(mission_id)

    assert launch["success"] is True
    assert launch["execution"]["verified"] is True
    assert launch["execution"]["stdout"] == "READY\n"

    manifest = workspace_manager.read_workspace_file(
        workspace_name,
        workspace_manager.PROJECT_MANIFEST_PATH,
    )

    assert '"tasks.db"' not in manifest["content"]

    connection = sqlite3.connect(
        f"file:{database_path}?mode=ro",
        uri=True,
    )

    try:
        rows = connection.execute(
            "SELECT id, task FROM tasks ORDER BY id"
        ).fetchall()
    finally:
        connection.close()

    assert rows == [(1, "Buy milk")]


def test_manifest_still_denies_unexpected_source_file(
    isolated_builder_root,
):
    mission_id = 12009
    workspace_name = create_project(
        mission_id,
        'print("READY")\n',
        [],
    )

    workspace_manager.write_workspace_file(
        workspace_name,
        "unexpected.py",
        'print("UNVERIFIED")\n',
    )

    with pytest.raises(
        WorkspaceExecutionError,
        match="unexpected files: unexpected.py",
    ):
        launch_verified_project(mission_id)


def test_controlled_argument_validation_accepts_bounded_spaces():
    assert _validate_controlled_arguments(
        ["add", "Buy milk"]
    ) == ["add", "Buy milk"]


def test_controlled_argument_validation_rejects_shell_metacharacters():
    unsafe_values = [
        "Buy;rm",
        "Buy|cat",
        "Buy&rm",
        "Buy>file",
        "Buy<file",
        "$(whoami)",
        "`whoami`",
    ]

    for value in unsafe_values:
        with pytest.raises(
            WorkspaceExecutionError,
            match="only bounded",
        ):
            _validate_controlled_arguments(
                ["add", value]
            )


def test_controlled_argument_validation_rejects_control_characters():
    unsafe_values = [
        "Buy\nmilk",
        "Buy\rmilk",
        "Buy\x00milk",
    ]

    for value in unsafe_values:
        with pytest.raises(
            WorkspaceExecutionError,
            match="only bounded",
        ):
            _validate_controlled_arguments(
                ["add", value]
            )


def test_python_artifact_sequence_persists_state_between_processes(
    isolated_builder_root,
):
    workspace_name = "mission-12005"

    workspace_manager.create_workspace(
        workspace_name
    )

    workspace_manager.write_workspace_file(
        workspace_name,
        "main.py",
        (
            "import sys\n"
            "from pathlib import Path\n"
            "\n"
            'state = Path("state.txt")\n'
            "\n"
            'if sys.argv[1] == "add":\n'
            "    state.write_text(sys.argv[2])\n"
            'elif sys.argv[1] == "list":\n'
            "    print(state.read_text())\n"
        ),
    )

    runner = getattr(
        workspace_executor,
        "execute_python_artifact_sequence",
        None,
    )

    assert runner is not None

    evidence = runner(
        12005,
        "main.py",
        [
            ["add", "Buy milk"],
            ["list"],
        ],
    )

    assert evidence["verified"] is True
    assert evidence["step_count"] == 2
    assert len(evidence["steps"]) == 2

    first = evidence["steps"][0]
    second = evidence["steps"][1]

    assert first["verified"] is True
    assert first["exit_code"] == 0
    assert first["stdout"] == ""

    assert second["verified"] is True
    assert second["exit_code"] == 0
    assert second["stdout"] == "Buy milk\n"

    assert (
        second["command"][-1]
        == "<controlled-argument>"
    )


def test_python_artifact_sequence_stops_after_failed_step(
    isolated_builder_root,
):
    workspace_name = "mission-12006"

    workspace_manager.create_workspace(
        workspace_name
    )

    workspace_manager.write_workspace_file(
        workspace_name,
        "main.py",
        (
            "import sys\n"
            "\n"
            'if sys.argv[1] == "fail":\n'
            "    raise SystemExit(7)\n"
            "\n"
            'print("SHOULD NOT RUN")\n'
        ),
    )

    runner = getattr(
        workspace_executor,
        "execute_python_artifact_sequence",
        None,
    )

    assert runner is not None

    evidence = runner(
        12006,
        "main.py",
        [
            ["fail"],
            ["list"],
        ],
    )

    assert evidence["verified"] is False
    assert evidence["step_count"] == 1
    assert len(evidence["steps"]) == 1
    assert evidence["steps"][0]["exit_code"] == 7


def test_python_artifact_sequence_rejects_too_many_steps(
    isolated_builder_root,
):
    workspace_name = "mission-12007"

    workspace_manager.create_workspace(
        workspace_name
    )

    workspace_manager.write_workspace_file(
        workspace_name,
        "main.py",
        'print("OK")\n',
    )

    runner = getattr(
        workspace_executor,
        "execute_python_artifact_sequence",
        None,
    )

    assert runner is not None

    with pytest.raises(
        WorkspaceExecutionError,
        match="sequence",
    ):
        runner(
            12007,
            "main.py",
            [
                ["one"],
                ["two"],
                ["three"],
                ["four"],
                ["five"],
            ],
        )


def test_python_artifact_sequence_rejects_empty_sequence(
    isolated_builder_root,
):
    workspace_name = "mission-12008"

    workspace_manager.create_workspace(
        workspace_name
    )

    workspace_manager.write_workspace_file(
        workspace_name,
        "main.py",
        'print("OK")\n',
    )

    runner = getattr(
        workspace_executor,
        "execute_python_artifact_sequence",
        None,
    )

    assert runner is not None

    with pytest.raises(
        WorkspaceExecutionError,
        match="sequence",
    ):
        runner(
            12008,
            "main.py",
            [],
        )


def test_python_artifact_sequence_promotes_artifact_metadata(
    isolated_builder_root,
):
    workspace_name = "mission-12009"

    workspace_manager.create_workspace(
        workspace_name
    )

    artifact = workspace_manager.write_workspace_file(
        workspace_name,
        "main.py",
        (
            "import sys\n"
            "\n"
            'if sys.argv[1] == "write":\n'
            '    open("state.txt", "w").write("READY")\n'
            'elif sys.argv[1] == "read":\n'
            '    print(open("state.txt").read())\n'
        ),
    )

    evidence = workspace_executor.execute_python_artifact_sequence(
        12009,
        "main.py",
        [
            ["write"],
            ["read"],
        ],
    )

    assert evidence["verified"] is True
    assert evidence["workspace"] == workspace_name
    assert evidence["artifact"] == "main.py"
    assert evidence["artifact_sha256"] == artifact["sha256"]
    assert (
        evidence["artifact_size_bytes"]
        == artifact["size_bytes"]
    )
    assert evidence["stdout"] == "READY\n"


def test_loopback_http_service_checks_persist_sqlite_across_restart(
    isolated_builder_root,
):
    import sqlite3

    mission_id = 12010
    workspace_name = f"mission-{mission_id}"

    workspace_manager.create_workspace(
        workspace_name
    )

    source = """
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
            "(id INTEGER PRIMARY KEY, title TEXT NOT NULL)"
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
""".lstrip()

    artifact = workspace_manager.write_workspace_file(
        workspace_name,
        "main.py",
        source,
    )

    evidence = (
        workspace_executor.execute_loopback_http_service_checks(
            mission_id,
            "main.py",
            [
                {
                    "method": "POST",
                    "path": "/tasks",
                    "json_body": {
                        "title": "Buy milk",
                    },
                    "expected_status": 200,
                    "expected_json": {
                        "title": "Buy milk",
                    },
                },
                {
                    "method": "GET",
                    "path": "/tasks",
                    "expected_status": 200,
                    "expected_json": [
                        {
                            "title": "Buy milk",
                        }
                    ],
                },
                {
                    "method": "GET",
                    "path": "/tasks",
                    "expected_status": 200,
                    "expected_json": [
                        {
                            "title": "Buy milk",
                        }
                    ],
                    "restart_before": True,
                },
            ],
        )
    )

    assert evidence["verified"] is True
    assert evidence["artifact"] == artifact["path"]
    assert evidence["requested_check_count"] == 3
    assert evidence["check_count"] == 3
    assert evidence["restart_count"] == 1
    assert evidence["host"] == "127.0.0.1"
    assert evidence["ephemeral_port"] is True
    assert evidence["service_stopped"] is True

    assert all(
        step["verified"] is True
        for step in evidence["steps"]
    )

    database_path = (
        isolated_builder_root
        / workspace_name
        / "tasks.db"
    )

    connection = sqlite3.connect(
        f"file:{database_path}?mode=ro",
        uri=True,
    )

    try:
        rows = connection.execute(
            "SELECT id, title FROM tasks ORDER BY id"
        ).fetchall()
    finally:
        connection.close()

    assert rows == [(1, "Buy milk")]


def test_loopback_http_service_checks_reject_external_url_path(
    isolated_builder_root,
):
    mission_id = 12011
    workspace_name = f"mission-{mission_id}"

    workspace_manager.create_workspace(
        workspace_name
    )

    workspace_manager.write_workspace_file(
        workspace_name,
        "main.py",
        "app = None\n",
    )

    with pytest.raises(
        WorkspaceExecutionError,
        match="path is not allowed",
    ):
        workspace_executor.execute_loopback_http_service_checks(
            mission_id,
            "main.py",
            [
                {
                    "method": "GET",
                    "path": "http://example.com/",
                    "expected_status": 200,
                }
            ],
        )


def test_verify_project_manifest_does_not_execute_entrypoint(
    isolated_builder_root,
):
    mission_id = 12012
    workspace_name = create_project(
        mission_id,
        (
            "from pathlib import Path\n"
            'Path("executed.txt").write_text("RUN")\n'
            'print("READY")\n'
        ),
        [],
    )

    verification = (
        workspace_executor.verify_project_manifest(
            mission_id
        )
    )

    assert verification["verified_manifest"] is True
    assert verification["entrypoint"] == "hello.py"
    assert verification["arguments"] == []
    assert verification["verified_file_count"] == 1

    executed_path = (
        isolated_builder_root
        / workspace_name
        / "executed.txt"
    )

    assert executed_path.exists() is False


def test_verify_project_manifest_denies_tampered_entrypoint(
    isolated_builder_root,
):
    mission_id = 12013
    workspace_name = create_project(
        mission_id,
        'print("ORIGINAL")\n',
        [],
    )

    workspace_manager.write_workspace_file(
        workspace_name,
        "hello.py",
        'print("TAMPERED")\n',
    )

    with pytest.raises(
        WorkspaceExecutionError,
        match="SHA256",
    ):
        workspace_executor.verify_project_manifest(
            mission_id
        )


def test_launch_verified_project_still_executes_after_refactor(
    isolated_builder_root,
):
    mission_id = 12014

    create_project(
        mission_id,
        (
            "import sys\n"
            'print(f"VALUE={sys.argv[1]}")\n'
        ),
        ["preserved"],
    )

    launch = launch_verified_project(
        mission_id
    )

    assert launch["verified_manifest"] is True
    assert launch["success"] is True
    assert launch["entrypoint"] == "hello.py"
    assert launch["execution"]["verified"] is True
    assert (
        launch["execution"]["stdout"]
        == "VALUE=preserved\n"
    )
    assert (
        launch["execution"]["argument_count"]
        == 1
    )
