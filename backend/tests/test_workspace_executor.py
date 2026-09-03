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
