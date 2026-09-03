import sqlite3

from services.executor import (
    _controlled_workspace_arguments,
    _controlled_workspace_stdin,
    _evaluate_execution_acceptance,
    _evidence_requirement,
    _exact_stdout_requirement,
    _is_workspace_execution_task,
)


def test_extracts_exact_stdout_and_controlled_stdin():
    task = {
        "title": "Test Script Execution",
        "instructions": (
            'Input a name and verify output matches '
            '"Hello, NAME!".'
        ),
    }

    assert _exact_stdout_requirement(task) == "Hello, NAME!"
    assert _controlled_workspace_stdin(task) == "NAME\n"


def test_extracts_arguments_from_sample_command():
    task = {
        "title": "Test Execution",
        "instructions": (
            "Run the script with a sample name, e.g., "
            "`python hello.py Alice`."
        ),
    }

    assert _controlled_workspace_arguments(
        task,
        "hello.py",
    ) == ["Alice"]


def test_derives_argument_from_exact_greeting():
    task = {
        "title": "Verify Output",
        "instructions": (
            "Confirm the output is exactly "
            "`Hello, Alice!` with no extra text when "
            "provided with a name."
        ),
    }

    assert _exact_stdout_requirement(task) == "Hello, Alice!"
    assert _controlled_workspace_arguments(
        task,
        "hello.py",
    ) == ["Alice"]


def test_exact_stdout_acceptance_matches_execution():
    task = {
        "title": "Verify Output",
        "instructions": (
            "Confirm the output is exactly "
            "`Hello, Alice!` with no extra text."
        ),
    }

    acceptance = _evaluate_execution_acceptance(
        task,
        {
            "stdout": "Hello, Alice!\n",
        },
    )

    assert acceptance == {
        "applicable": True,
        "verified": True,
        "type": "exact_stdout",
        "expected": "Hello, Alice!",
        "actual": "Hello, Alice!",
        "reason": "Exact stdout matched the task requirement.",
    }


def test_sqlite_row_allows_safe_installation_verification():
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row

    try:
        task = connection.execute(
            """
            SELECT
                'Install Python' AS title,
                ': Ensure Python is installed on the system.'
                    AS instructions
            """
        ).fetchone()
    finally:
        connection.close()

    requirement, allowed = _evidence_requirement(task)

    assert allowed is True
    assert "allowlisted local diagnostic" in requirement


def test_explicit_installation_remains_blocked():
    task = {
        "title": "Install Python",
        "instructions": (
            "Install Python using the system package manager."
        ),
    }

    requirement, allowed = _evidence_requirement(task)

    assert allowed is False
    assert requirement == (
        "System-changing work requires an approved "
        "execution tool result."
    )

def test_python_installation_check_is_not_workspace_execution():
    task = {
        "title": "Check Python Installation",
        "instructions": (
            "Verify Python is installed and accessible via command line."
        ),
    }

    assert _is_workspace_execution_task(task) is False


def test_explicit_python_artifact_run_is_workspace_execution():
    task = {
        "title": "Run the Program",
        "instructions": (
            "Execute `python hello.py` in the terminal, "
            'then input a name (e.g., "Alice").'
        ),
    }

    assert _is_workspace_execution_task(task) is True


def test_exact_stdout_verification_is_workspace_execution():
    task = {
        "title": "Verify Output",
        "instructions": (
            "Confirm the terminal displays "
            "`Hello, Alice!` exactly."
        ),
    }

    assert _is_workspace_execution_task(task) is True


def test_explicit_python_program_execution_is_workspace_execution():
    task = {
        "title": "Execute Python Program",
        "instructions": "Run the Python program and verify its output.",
    }

    assert _is_workspace_execution_task(task) is True


def test_derives_stdin_from_explicit_example_name():
    task = {
        "title": "Run the Program",
        "instructions": (
            "Execute `python hello.py` in the terminal, "
            'then input a name (e.g., "Alice").'
        ),
    }

    assert _controlled_workspace_stdin(task) == "Alice\n"
