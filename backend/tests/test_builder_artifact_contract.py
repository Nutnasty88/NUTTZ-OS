import json

import pytest

from services import builder
from services.builder import _parse_builder_response


def builder_response(path: str, content: str) -> str:
    return json.dumps(
        {
            "summary": "test artifact",
            "entrypoint": None,
            "files": [
                {
                    "path": path,
                    "content": content,
                }
            ],
        }
    )


@pytest.mark.parametrize(
    "path",
    [
        "tasks.db",
        "data.sqlite",
        "state.sqlite3",
        "cache.bin",
        "archive.zip",
        "image.png",
        "document.pdf",
        "module.pyc",
    ],
)
def test_builder_rejects_runtime_or_binary_artifacts(path):
    with pytest.raises(
        RuntimeError,
        match="cannot create runtime or binary artifact",
    ):
        _parse_builder_response(
            builder_response(
                path,
                "model-generated text pretending to be binary data",
            )
        )


@pytest.mark.parametrize(
    "path",
    [
        "main.py",
        "task_manager.py",
        "schema.sql",
        "config.json",
        "README.md",
        "templates/index.html",
        "static/app.css",
        "requirements.txt",
    ],
)
def test_builder_accepts_text_artifacts(path):
    parsed = _parse_builder_response(
        builder_response(
            path,
            "valid UTF-8 text",
        )
    )

    assert parsed["files"] == [
        {
            "path": path,
            "content": "valid UTF-8 text",
        }
    ]


def test_builder_binary_suffix_check_is_case_insensitive():
    with pytest.raises(
        RuntimeError,
        match="cannot create runtime or binary artifact",
    ):
        _parse_builder_response(
            builder_response(
                "DATA.DB",
                "not a database",
            )
        )


def test_builder_prompt_requires_runtime_database_creation(
    monkeypatch,
    tmp_path,
):
    workspace_path = tmp_path / "mission-1"

    def fake_get_workspace(workspace_name):
        return {
            "name": workspace_name,
            "path": str(workspace_path),
        }

    captured = {}

    def fake_chat_with_ollama(
        *,
        model,
        messages,
        stream,
        think,
    ):
        captured["model"] = model
        captured["messages"] = messages
        captured["stream"] = stream
        captured["think"] = think

        return {
            "message": {
                "content": json.dumps(
                    {
                        "summary": "prompt contract test",
                        "entrypoint": None,
                        "files": [],
                    }
                )
            }
        }

    monkeypatch.setattr(
        builder,
        "get_workspace",
        fake_get_workspace,
    )
    monkeypatch.setattr(
        builder,
        "create_workspace",
        fake_get_workspace,
    )
    monkeypatch.setattr(
        builder,
        "_workspace_context",
        lambda workspace_name: "[Workspace is empty]",
    )
    monkeypatch.setattr(
        builder,
        "chat_with_ollama",
        fake_chat_with_ollama,
    )
    monkeypatch.setattr(
        builder,
        "log_event",
        lambda *args, **kwargs: None,
    )

    result = builder.build_task(
        mission_id=1,
        mission_title="Build a SQLite task application",
        task_id=10,
        task_position=1,
        task_title="Implement SQLite initialization",
        task_instructions=(
            "Implement SQLite initialization in task_manager.py."
        ),
    )

    assert result["status"] == "Completed"

    system_prompt = captured["messages"][0]["content"]
    normalized_prompt = " ".join(system_prompt.split())

    assert (
        "Create or modify UTF-8 source, configuration, "
        "documentation, and other text artifacts only."
        in normalized_prompt
    )

    assert (
        'Never return databases or other mutable runtime/binary '
        'state in "files".'
        in normalized_prompt
    )

    assert (
        "For SQLite applications, implement database creation and "
        "initialization in source code so execution creates .db, "
        ".sqlite, or .sqlite3 files at runtime."
        in normalized_prompt
    )

    assert captured["think"] is False
