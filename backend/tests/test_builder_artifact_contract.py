import json

import pytest

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
