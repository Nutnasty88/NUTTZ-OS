import sqlite3

from services.executor import (
    _controlled_workspace_arguments,
    _controlled_workspace_stdin,
    _evaluate_execution_acceptance,
    _evidence_requirement,
    _exact_stdout_requirement,
    _is_workspace_execution_task,
    _is_builder_task,
    CONDITIONAL_INSTALL_PATTERN,
)


def test_environment_directory_setup_is_not_builder_task():
    task = {
        "title": "Set Up Environment",
        "instructions": (
            "Install Python 3.x if not already installed. "
            "Create a new directory for the project and "
            "navigate into it."
        ),
    }

    assert _is_builder_task(task) is False


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


def test_extracts_flagged_name_from_sample_command():
    task = {
        "title": "Test Script Functionality",
        "instructions": (
            "Run the script with a sample name "
            "(e.g., `python hello.py --name Alice`) "
            'to ensure it prints "Hello, Alice!".'
        ),
    }

    assert _controlled_workspace_arguments(
        task,
        "hello.py",
    ) == ["--name", "Alice"]


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


def test_conditional_install_pattern_handles_version_period():
    instructions = (
        "Install Python 3.x if not already installed. "
        "Create a new directory for the project."
    )

    match = CONDITIONAL_INSTALL_PATTERN.search(
        instructions
    )

    assert match is not None
    assert match.group(0) == (
        "Install Python 3.x if not already installed"
    )


def test_conditional_install_allows_existing_python_verification():
    task = {
        "title": "Set Up Environment",
        "instructions": (
            "Install Python 3.x if not already installed. "
            "Create a new directory for the project and "
            "navigate into it."
        ),
    }

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


def test_extracts_exact_stdout_when_exactly_follows_output_value():
    task = {
        "title": "Verify Execution Success",
        "instructions": (
            "Confirm the script runs without errors and "
            "produces the exact output. Check for correct "
            "argument parsing and formatting.\n\n"
            "Success-check: The script must output "
            '`"Hello, NAME!"` exactly when provided with '
            "a valid name argument."
        ),
    }

    assert _exact_stdout_requirement(task) == "Hello, NAME!"


def test_derives_flagged_name_from_symbolic_success_check():
    task = {
        "title": "Verify Execution Success",
        "instructions": (
            "Confirm the script runs without errors and "
            "produces the exact output. Check for correct "
            "argument parsing and formatting.\n\n"
            "Success-check: The script must output "
            '`"Hello, NAME!"` exactly when provided with '
            "a valid name argument."
        ),
    }

    assert _exact_stdout_requirement(task) == "Hello, NAME!"
    assert _controlled_workspace_arguments(
        task,
        "hello.py",
    ) == ["--name", "NAME"]


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


def test_explicit_python_filename_creation_is_builder_task():
    task = {
        "title": "Create calculator.py with add(a, b) function",
        "instructions": (
            "Write a function `add(a, b)` that returns "
            "the sum of two numbers."
        ),
    }

    assert _is_builder_task(task) is True


def test_explicit_python_entrypoint_creation_is_builder_task():
    task = {
        "title": "Create main.py to import and use add",
        "instructions": (
            "Import `add` from `calculator`, read two "
            "command-line arguments, convert them to "
            "integers, and print \"Result: 5\" when "
            "called with 2 and 3."
        ),
    }

    assert _is_builder_task(task) is True


def test_implementation_of_command_line_parsing_is_builder_task():
    task = {
        "title": "Implement command-line argument parsing",
        "instructions": (
            "Use `sys.argv` to retrieve arguments, validate numeric "
            "input, and compute the result."
        ),
    }

    assert _is_builder_task(task) is True


def test_generic_implementation_without_code_context_is_not_builder_task():
    task = {
        "title": "Implement the process",
        "instructions": (
            "Implement the requested operational process and document "
            "the outcome."
        ),
    }

    assert _is_builder_task(task) is False


def test_controlled_arguments_accept_bare_python_artifact_command():
    task = {
        "title": "Test the application",
        "instructions": (
            'Run `main.py 2 3` and confirm the output matches '
            '"Result: 5".'
        ),
    }

    assert _controlled_workspace_arguments(
        task,
        "main.py",
    ) == ["2", "3"]


def test_controlled_arguments_reject_bare_non_python_artifact():
    task = {
        "title": "Test the application",
        "instructions": (
            'Run `script.sh 2 3` and confirm the output.'
        ),
    }

    assert _controlled_workspace_arguments(
        task,
        "main.py",
    ) is None


def test_controlled_arguments_reject_bare_python_artifact_shell_syntax():
    task = {
        "title": "Test the application",
        "instructions": (
            'Run `main.py 2 3 && rm file` and confirm the output.'
        ),
    }

    assert _controlled_workspace_arguments(
        task,
        "main.py",
    ) is None


def test_persistence_verification_is_not_builder_task():
    task = {
        "title": "Verify Persistence",
        "instructions": (
            '- Run `main.py add "Buy milk"` to save the task.\n'
            '- Run `main.py list` to ensure "Buy milk" is printed.\n'
            '- Restart the program and re-run `list` to confirm '
            'the task persists across executions.\n\n'
            'Success-check: The task "Buy milk" must be stored '
            'in the SQLite database and remain visible after '
            'closing and reopening the program.'
        ),
    }

    assert _is_builder_task(task) is False


def test_explicit_source_creation_remains_builder_task():
    task = {
        "title": "Create main.py",
        "instructions": (
            "Create main.py that imports database and implements "
            "the add and list command-line operations."
        ),
    }

    assert _is_builder_task(task) is True


def test_extracts_bounded_python_command_sequence():
    from services import executor

    task = {
        "title": "Verify Persistence",
        "instructions": (
            '- Run `main.py add "Buy milk"` to save the task.\n'
            '- Run `main.py list` to ensure "Buy milk" is printed.\n'
            '- Restart the program and re-run `main.py list` to '
            'confirm persistence.'
        ),
    }

    parser = getattr(
        executor,
        "_controlled_workspace_command_sequence",
        None,
    )

    assert parser is not None
    assert parser(
        task,
        "main.py",
    ) == [
        ["add", "Buy milk"],
        ["list"],
        ["list"],
    ]


def test_command_sequence_accepts_python_prefixes():
    from services import executor

    task = {
        "title": "Verify CLI",
        "instructions": (
            '- Run `python main.py add "Buy milk"`.\n'
            '- Run `python3 main.py list`.'
        ),
    }

    parser = getattr(
        executor,
        "_controlled_workspace_command_sequence",
        None,
    )

    assert parser is not None
    assert parser(
        task,
        "main.py",
    ) == [
        ["add", "Buy milk"],
        ["list"],
    ]


def test_command_sequence_rejects_shell_syntax():
    from services import executor

    task = {
        "title": "Verify CLI",
        "instructions": (
            '- Run `main.py add "Buy milk" && rm data.db`.\n'
            '- Run `main.py list`.'
        ),
    }

    parser = getattr(
        executor,
        "_controlled_workspace_command_sequence",
        None,
    )

    assert parser is not None
    assert parser(
        task,
        "main.py",
    ) == [
        ["list"],
    ]


def test_command_sequence_rejects_other_artifacts():
    from services import executor

    task = {
        "title": "Verify CLI",
        "instructions": (
            '- Run `other.py add "Buy milk"`.\n'
            '- Run `main.py list`.'
        ),
    }

    parser = getattr(
        executor,
        "_controlled_workspace_command_sequence",
        None,
    )

    assert parser is not None
    assert parser(
        task,
        "main.py",
    ) == [
        ["list"],
    ]


def test_workspace_execution_selector_uses_sequence_for_multiple_commands(
    monkeypatch,
):
    from services import executor

    calls = []

    def fake_single(
        mission_id,
        artifact_path,
        *,
        stdin_text=None,
        arguments=None,
    ):
        calls.append(
            (
                "single",
                mission_id,
                artifact_path,
                stdin_text,
                arguments,
            )
        )
        return {"verified": True, "exit_code": 0}

    def fake_sequence(
        mission_id,
        artifact_path,
        argument_steps,
    ):
        calls.append(
            (
                "sequence",
                mission_id,
                artifact_path,
                argument_steps,
            )
        )
        return {
            "verified": True,
            "exit_code": 0,
            "stdout": "Buy milk\n",
        }

    monkeypatch.setattr(
        executor,
        "execute_python_artifact",
        fake_single,
    )

    monkeypatch.setattr(
        executor,
        "execute_python_artifact_sequence",
        fake_sequence,
        raising=False,
    )

    selector = getattr(
        executor,
        "_execute_controlled_workspace_artifact",
        None,
    )

    assert selector is not None

    evidence = selector(
        9186,
        "main.py",
        controlled_stdin=None,
        controlled_arguments=[
            "add",
            "Buy milk",
        ],
        command_sequence=[
            ["add", "Buy milk"],
            ["list"],
        ],
    )

    assert evidence["stdout"] == "Buy milk\n"

    assert calls == [
        (
            "sequence",
            9186,
            "main.py",
            [
                ["add", "Buy milk"],
                ["list"],
            ],
        )
    ]


def test_workspace_execution_selector_preserves_single_command_path(
    monkeypatch,
):
    from services import executor

    calls = []

    def fake_single(
        mission_id,
        artifact_path,
        *,
        stdin_text=None,
        arguments=None,
    ):
        calls.append(
            (
                "single",
                mission_id,
                artifact_path,
                stdin_text,
                arguments,
            )
        )
        return {
            "verified": True,
            "exit_code": 0,
            "stdout": "Result: 5\n",
        }

    def fake_sequence(
        mission_id,
        artifact_path,
        argument_steps,
    ):
        calls.append(
            (
                "sequence",
                mission_id,
                artifact_path,
                argument_steps,
            )
        )
        return {"verified": True, "exit_code": 0}

    monkeypatch.setattr(
        executor,
        "execute_python_artifact",
        fake_single,
    )

    monkeypatch.setattr(
        executor,
        "execute_python_artifact_sequence",
        fake_sequence,
        raising=False,
    )

    selector = getattr(
        executor,
        "_execute_controlled_workspace_artifact",
        None,
    )

    assert selector is not None

    evidence = selector(
        9185,
        "main.py",
        controlled_stdin=None,
        controlled_arguments=["2", "3"],
        command_sequence=[
            ["2", "3"],
        ],
    )

    assert evidence["stdout"] == "Result: 5\n"

    assert calls == [
        (
            "single",
            9185,
            "main.py",
            None,
            ["2", "3"],
        )
    ]


def test_workspace_execution_selector_preserves_stdin_path(
    monkeypatch,
):
    from services import executor

    calls = []

    def fake_single(
        mission_id,
        artifact_path,
        *,
        stdin_text=None,
        arguments=None,
    ):
        calls.append(
            (
                mission_id,
                artifact_path,
                stdin_text,
                arguments,
            )
        )
        return {
            "verified": True,
            "exit_code": 0,
            "stdout": "Hello, Alice!\n",
        }

    monkeypatch.setattr(
        executor,
        "execute_python_artifact",
        fake_single,
    )

    selector = getattr(
        executor,
        "_execute_controlled_workspace_artifact",
        None,
    )

    assert selector is not None

    selector(
        9184,
        "hello.py",
        controlled_stdin="Alice\n",
        controlled_arguments=[],
        command_sequence=[],
    )

    assert calls == [
        (
            9184,
            "hello.py",
            "Alice\n",
            [],
        )
    ]


def test_extracts_stdout_requirement_from_ensure_printed_wording():
    task = {
        "title": "Verify Persistence",
        "instructions": (
            '- Run `main.py add "Buy milk"` to save the task.\n'
            '- Run `main.py list` to ensure "Buy milk" is printed.\n'
            "- Restart the program and re-run `list` to confirm "
            "the task persists across executions.\n\n"
            'Success-check: The task "Buy milk" must be stored '
            "in the SQLite database and remain visible after "
            "closing and reopening the program."
        ),
    }

    assert _exact_stdout_requirement(task) == "Buy milk"


def test_persistence_acceptance_rejects_wrong_stdout():
    task = {
        "title": "Verify Persistence",
        "instructions": (
            '- Run `main.py add "Buy milk"` to save the task.\n'
            '- Run `main.py list` to ensure "Buy milk" is printed.\n'
            "- Restart the program and re-run `list` to confirm "
            "the task persists across executions."
        ),
    }

    acceptance = _evaluate_execution_acceptance(
        task,
        {
            "stdout": "no such table: tasks\nNo tasks found.\n",
        },
    )

    assert acceptance["applicable"] is True
    assert acceptance["verified"] is False
    assert acceptance["expected"] == "Buy milk"
    assert acceptance["actual"] == (
        "no such table: tasks\nNo tasks found."
    )


def test_ensure_printed_rule_requires_quoted_expected_value():
    task = {
        "title": "Verify Output",
        "instructions": (
            "Run the program and ensure the task is printed."
        ),
    }

    assert _exact_stdout_requirement(task) is None


def test_ensure_printed_rule_does_not_match_other_verbs():
    task = {
        "title": "Verify Output",
        "instructions": (
            'Run the program and ensure "Buy milk" is logged.'
        ),
    }

    assert _exact_stdout_requirement(task) is None
