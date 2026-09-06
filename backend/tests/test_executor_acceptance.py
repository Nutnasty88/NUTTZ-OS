import sqlite3

from services.executor import (
    _controlled_workspace_arguments,
    _controlled_workspace_command_sequence,
    _execute_controlled_workspace_artifact,
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


def test_implementation_of_add_command_logic_is_builder_task():
    task = {
        "title": "Implement 'add' command logic to insert a task",
        "instructions": (
            "Implement 'add' command logic to insert a task into "
            "the 'tasks' table."
        ),
    }

    assert _is_builder_task(task) is True


def test_implementation_of_list_command_logic_is_builder_task():
    task = {
        "title": "Implement 'list' command logic to select tasks",
        "instructions": (
            "Implement 'list' command logic to select and print "
            "all tasks from the 'tasks' table."
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


def test_extracts_stdout_requirement_from_displayed_exactly_as_wording():
    task = {
        "title": (
            "Test 'main.py list' to confirm task is displayed "
            'exactly as "Buy milk"'
        ),
        "instructions": (
            "Test 'main.py list' to confirm task is displayed "
            'exactly as "Buy milk"'
        ),
    }

    assert _exact_stdout_requirement(task) == "Buy milk"


def test_displayed_exactly_as_acceptance_rejects_wrong_stdout():
    task = {
        "title": (
            "Test 'main.py list' to confirm task is displayed "
            'exactly as "Buy milk"'
        ),
        "instructions": (
            "Test 'main.py list' to confirm task is displayed "
            'exactly as "Buy milk"'
        ),
    }

    acceptance = _evaluate_execution_acceptance(
        task,
        {
            "stdout": "No tasks found.\n",
        },
    )

    assert acceptance["applicable"] is True
    assert acceptance["verified"] is False
    assert acceptance["expected"] == "Buy milk"
    assert acceptance["actual"] == "No tasks found."


def test_extracts_arguments_from_single_quoted_python_command():
    task = {
        "title": (
            'Test \'main.py add "Buy milk"\' '
            "to verify task is saved to database"
        ),
        "instructions": (
            'Test \'main.py add "Buy milk"\' '
            "to verify task is saved to database"
        ),
    }

    assert _controlled_workspace_arguments(
        task,
        "main.py",
    ) == ["add", "Buy milk"]


def test_extracts_sequence_from_single_quoted_python_commands():
    task = {
        "title": "Verify persistence",
        "instructions": (
            'Run \'main.py add "Buy milk"\', then '
            "run 'main.py list'."
        ),
    }

    assert _controlled_workspace_command_sequence(
        task,
        "main.py",
    ) == [
        ["add", "Buy milk"],
        ["list"],
    ]


def test_single_quoted_command_rejects_shell_control_syntax():
    task = {
        "title": "Test application",
        "instructions": (
            "Run 'main.py list; whoami' and verify it works."
        ),
    }

    assert _controlled_workspace_arguments(
        task,
        "main.py",
    ) == []

    assert _controlled_workspace_command_sequence(
        task,
        "main.py",
    ) == []


def test_command_sequence_deduplicates_identical_title_and_instructions():
    task = {
        "title": (
            'Test \'main.py add "Buy milk"\' '
            "to verify task is saved to database"
        ),
        "instructions": (
            'Test \'main.py add "Buy milk"\' '
            "to verify task is saved to database"
        ),
    }

    assert _controlled_workspace_command_sequence(
        task,
        "main.py",
    ) == [
        ["add", "Buy milk"],
    ]


def test_workspace_execution_selector_uses_single_sequence_arguments(
    monkeypatch,
):
    calls = []

    def fake_execute(
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
        return {"verified": True, "exit_code": 0}

    monkeypatch.setattr(
        "services.executor.execute_python_artifact",
        fake_execute,
    )

    result = _execute_controlled_workspace_artifact(
        9188,
        "main.py",
        controlled_stdin=None,
        controlled_arguments=[],
        command_sequence=[["add", "Buy milk"]],
    )

    assert result["verified"] is True
    assert calls == [
        (
            9188,
            "main.py",
            None,
            ["add", "Buy milk"],
        )
    ]


def test_workspace_execution_selector_single_sequence_overrides_empty_legacy_arguments(
    monkeypatch,
):
    calls = []

    def fake_execute(
        mission_id,
        artifact_path,
        *,
        stdin_text=None,
        arguments=None,
    ):
        calls.append(arguments)
        return {"verified": True, "exit_code": 0}

    monkeypatch.setattr(
        "services.executor.execute_python_artifact",
        fake_execute,
    )

    _execute_controlled_workspace_artifact(
        9188,
        "main.py",
        controlled_stdin=None,
        controlled_arguments=[],
        command_sequence=[["list"]],
    )

    assert calls == [["list"]]


def test_real_task_app_success_check_routes_to_workspace_executor():
    task = {
        "title": "Success-Check",
        "instructions": (
            "Confirm that:  \n"
            '   - `main.py add "Buy milk"` stores the task exactly.  \n'
            "   - `main.py list` outputs `Buy milk` immediately after adding.  \n"
            "   - The task is still visible after restarting the program."
        ),
    }

    assert _controlled_workspace_command_sequence(
        task,
        "main.py",
    ) == [
        ["add", "Buy milk"],
        ["list"],
    ]

    assert _is_workspace_execution_task(task) is True
    assert _is_builder_task(task) is False


def test_real_task_app_persistence_check_routes_to_workspace_executor():
    task = {
        "title": "Verify Persistence Across Restarts",
        "instructions": (
            "Restart the program after adding a task and re-run "
            "`main.py list` to confirm the task remains in the database."
        ),
    }

    assert _controlled_workspace_command_sequence(
        task,
        "main.py",
    ) == [["list"]]

    assert _is_workspace_execution_task(task) is True
    assert _is_builder_task(task) is False


def test_bare_entrypoint_reference_does_not_force_workspace_execution():
    task = {
        "title": "Implement Command-Line Parsing",
        "instructions": (
            "Use argparse in `main.py` to support the add and list "
            "commands."
        ),
    }

    assert _controlled_workspace_command_sequence(
        task,
        "main.py",
    ) == [[]]

    assert _is_builder_task(task) is True
    assert _is_workspace_execution_task(task) is False


def test_command_sequence_extracts_plain_commands_from_success_check():
    task = {
        "title": (
            'Verify task persistence by restarting the application '
            'and confirming "Buy milk" remains in list'
        ),
        "instructions": (
            'Success-check: After executing main.py add "Buy milk" '
            'and restarting the application, main.py list should '
            'display exactly "Buy milk" in the task list.'
        ),
    }

    assert _controlled_workspace_command_sequence(
        task,
        "main.py",
    ) == [
        ["add", "Buy milk"],
        ["list"],
    ]


def test_plain_command_sequence_accepts_python_prefix():
    task = {
        "title": "Verify persistence",
        "instructions": (
            'Execute python3 main.py add "Buy milk" and then '
            'python3 main.py list should display the task.'
        ),
    }

    assert _controlled_workspace_command_sequence(
        task,
        "main.py",
    ) == [
        ["add", "Buy milk"],
        ["list"],
    ]


def test_plain_command_sequence_rejects_shell_control_syntax():
    task = {
        "title": "Verify application",
        "instructions": (
            "Execute main.py list; whoami and verify the output."
        ),
    }

    assert _controlled_workspace_command_sequence(
        task,
        "main.py",
    ) == []


def test_plain_command_sequence_is_line_bounded_and_ignores_shell_restart():
    task = {
        "title": (
            "Verify persistence by restarting the application "
            "and rechecking task list"
        ),
        "instructions": (
            'Success-check:\n'
            'main.py add "Buy milk"\n'
            'main.py list\n'
            'killall python && sleep 2\n'
            'main.py list'
        ),
    }

    assert _controlled_workspace_command_sequence(
        task,
        "main.py",
    ) == [
        ["add", "Buy milk"],
        ["list"],
        ["list"],
    ]


def test_plain_sequence_splits_commands_separated_by_followed_by():
    task = {
        "title": "Verify task addition and listing",
        "instructions": (
            'Action: Execute main.py add "Buy milk" followed by '
            'main.py list to confirm task is displayed'
        ),
    }

    assert _controlled_workspace_command_sequence(
        task,
        "main.py",
    ) == [
        ["add", "Buy milk"],
        ["list"],
    ]
