from services.executor import (
    _controlled_workspace_command_sequence,
    parse_plan_tasks,
)


def test_success_check_numbering_does_not_create_duplicate_tasks():
    plan = """\
1. Create main.py with command-line argument parsing and SQLite database initialization
   Action: Create main.py with code to parse `add`/`list` commands and initialize SQLite database connection

2. Implement SQLite database creation and table setup in main.py
   Action: Implement database creation (tasks.db) and table setup in main.py

3. Implement task insertion for add command in main.py
   Action: Implement add command logic in main.py

4. Implement task listing for list command in main.py
   Action: Implement list command logic in main.py

5. Verify task persistence across restarts
   Action: Run `main.py add "Buy milk"` then `main.py list`

Success-check:
1. python main.py add "Buy milk"
2. python main.py list
3. python main.py
4. python main.py list
"""

    tasks = parse_plan_tasks(plan)

    assert len(tasks) == 5
    assert [task["position"] for task in tasks] == [1, 2, 3, 4, 5]

    assert "Success-check:" in tasks[-1]["instructions"]
    assert 'python main.py add "Buy milk"' in tasks[-1]["instructions"]
    assert "python main.py list" in tasks[-1]["instructions"]


def test_success_check_commands_remain_available_to_workspace_executor():
    plan = """\
1. Create main.py
   Action: Create main.py

2. Verify persistence
   Action: Verify persistence across a fresh process

Success-check:
1. python main.py add "Buy milk"
2. python main.py list
3. python main.py
4. python main.py list
"""

    tasks = parse_plan_tasks(plan)

    sequence = _controlled_workspace_command_sequence(
        tasks[-1],
        "main.py",
    )

    assert sequence == [
        ["add", "Buy milk"],
        ["list"],
        ["list"],
    ]


def test_exact_9908_plan_uses_success_check_as_execution_sequence():
    plan = """\
1. Create main.py with command-line argument parsing and SQLite database initialization
   Action: Create main.py with code to parse `add`/`list` commands and initialize SQLite database connection

2. Implement SQLite database creation and table setup in main.py
   Action: Implement database creation (tasks.db) and table setup (tasks table with id INTEGER PRIMARY KEY and task TEXT) in main.py

3. Implement task insertion for add command in main.py
   Action: Implement add command logic to insert task into tasks table using sqlite3 in main.py

4. Implement task listing for list command in main.py
   Action: Implement list command logic to query and print all tasks from tasks table in main.py

5. Verify task persistence across restarts
   Action: Run `main.py add "Buy milk"` then `main.py list` to confirm output; restart application via `python main.py` and verify "Buy milk" remains in list

Success-check:
1. python main.py add "Buy milk"
2. python main.py list
3. python main.py
4. python main.py list
"""

    tasks = parse_plan_tasks(plan)

    assert len(tasks) == 5
    assert [task["position"] for task in tasks] == [
        1,
        2,
        3,
        4,
        5,
    ]

    assert _controlled_workspace_command_sequence(
        tasks[-1],
        "main.py",
    ) == [
        ["add", "Buy milk"],
        ["list"],
        ["list"],
    ]

