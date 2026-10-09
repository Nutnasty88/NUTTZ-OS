import sqlite3

import pytest

from services import executor


def _setup_database(tmp_path, monkeypatch):
    db_path = tmp_path / "mission-replanning.db"
    mission_id = 17001

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
                status TEXT NOT NULL DEFAULT 'Running',
                progress INTEGER NOT NULL DEFAULT 0,
                assigned_agent TEXT DEFAULT '',
                priority TEXT DEFAULT 'Normal',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE mission_plans (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                mission_id INTEGER NOT NULL UNIQUE,
                model TEXT NOT NULL,
                plan TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'Ready',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (mission_id)
                    REFERENCES missions(id)
                    ON DELETE CASCADE
            );

            CREATE TABLE mission_tasks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                mission_id INTEGER NOT NULL,
                position INTEGER NOT NULL,
                title TEXT NOT NULL,
                instructions TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL DEFAULT 'Pending',
                result TEXT NOT NULL DEFAULT '',
                execution_token TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                started_at TIMESTAMP,
                completed_at TIMESTAMP,
                FOREIGN KEY (mission_id)
                    REFERENCES missions(id)
                    ON DELETE CASCADE,
                UNIQUE (mission_id, position)
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
            VALUES (?, ?, 'Running', 50, 'Planner', 'Normal')
            """,
            (
                mission_id,
                "Replanning v2 acceptance mission",
            ),
        )

        conn.execute(
            """
            INSERT INTO mission_tasks (
                mission_id,
                position,
                title,
                instructions,
                status,
                result,
                completed_at
            )
            VALUES (?, 1, ?, ?, 'Completed', ?, CURRENT_TIMESTAMP)
            """,
            (
                mission_id,
                "Build application",
                "Implement the application",
                "VERIFIED BUILD EVIDENCE",
            ),
        )

        conn.execute(
            """
            INSERT INTO mission_tasks (
                mission_id,
                position,
                title,
                instructions,
                status,
                result
            )
            VALUES (?, 2, ?, ?, 'Pending', '')
            """,
            (
                mission_id,
                "Old verification",
                "Run the old verification",
            ),
        )

        conn.execute(
            """
            INSERT INTO mission_tasks (
                mission_id,
                position,
                title,
                instructions,
                status,
                result
            )
            VALUES (?, 3, ?, ?, 'Pending', '')
            """,
            (
                mission_id,
                "Old final check",
                "Run the old final check",
            ),
        )

        conn.execute(
            """
            INSERT INTO mission_plans (
                mission_id,
                model,
                plan,
                status
            )
            VALUES (?, ?, ?, 'Ready')
            """,
            (
                mission_id,
                "qwen3:8b",
                (
                    "1. Build application\n"
                    "   Implement the application\n"
                    "2. Old verification\n"
                    "   Run the old verification\n"
                    "3. Old final check\n"
                    "   Run the old final check"
                ),
            ),
        )

        conn.commit()

    finally:
        conn.close()

    monkeypatch.setattr(
        executor,
        "get_connection",
        get_connection,
    )

    executor.ensure_mission_approval_columns()

    return mission_id, get_connection


def test_replan_replaces_only_pending_tail_and_preserves_completed_work(
    tmp_path,
    monkeypatch,
):
    mission_id, get_connection = _setup_database(
        tmp_path,
        monkeypatch,
    )

    approval = executor.approve_mission_plan(mission_id)
    assert approval["approved"] is True

    conn = get_connection()

    try:
        before = conn.execute(
            """
            SELECT *
            FROM mission_tasks
            WHERE mission_id=?
            ORDER BY position
            """,
            (mission_id,),
        ).fetchall()

        completed_before = dict(before[0])
        old_pending_ids = {
            int(before[1]["id"]),
            int(before[2]["id"]),
        }

    finally:
        conn.close()

    replacement_plan = (
        "1. Build application\n"
        "   Implement the application\n"
        "2. Verify fresh process\n"
        "   Run deterministic verification in a fresh process\n"
        "3. Verify persisted state\n"
        "   Confirm the persisted state is still available\n"
        "4. Final acceptance\n"
        "   Run the final deterministic acceptance check"
    )

    replanned = executor.replan_pending_tasks(
        mission_id,
        replacement_plan,
    )

    assert [task["position"] for task in replanned] == [
        1,
        2,
        3,
        4,
    ]

    conn = get_connection()

    try:
        after = conn.execute(
            """
            SELECT *
            FROM mission_tasks
            WHERE mission_id=?
            ORDER BY position
            """,
            (mission_id,),
        ).fetchall()

        stored_plan = conn.execute(
            """
            SELECT plan
            FROM mission_plans
            WHERE mission_id=?
            """,
            (mission_id,),
        ).fetchone()["plan"]

        mission = conn.execute(
            """
            SELECT
                approved_plan_fingerprint,
                approved_at
            FROM missions
            WHERE id=?
            """,
            (mission_id,),
        ).fetchone()

    finally:
        conn.close()

    assert len(after) == 4

    completed_after = after[0]

    assert completed_after["id"] == completed_before["id"]
    assert completed_after["position"] == 1
    assert completed_after["title"] == completed_before["title"]
    assert (
        completed_after["instructions"]
        == completed_before["instructions"]
    )
    assert completed_after["status"] == "Completed"
    assert (
        completed_after["result"]
        == "VERIFIED BUILD EVIDENCE"
    )
    assert (
        completed_after["completed_at"]
        == completed_before["completed_at"]
    )

    new_pending = after[1:]

    assert all(
        task["status"] == "Pending"
        for task in new_pending
    )
    assert all(
        task["started_at"] is None
        for task in new_pending
    )
    assert all(
        task["completed_at"] is None
        for task in new_pending
    )
    assert all(
        task["execution_token"] is None
        for task in new_pending
    )

    assert {
        int(task["id"])
        for task in new_pending
    }.isdisjoint(old_pending_ids)

    assert [
        task["title"]
        for task in new_pending
    ] == [
        "Verify fresh process",
        "Verify persisted state",
        "Final acceptance",
    ]

    assert stored_plan == replacement_plan

    assert mission["approved_plan_fingerprint"] is None
    assert mission["approved_at"] is None

    approval_after = executor.get_mission_approval_status(
        mission_id
    )
    assert approval_after["approved"] is False


def test_replan_rejects_change_to_completed_prefix(
    tmp_path,
    monkeypatch,
):
    mission_id, get_connection = _setup_database(
        tmp_path,
        monkeypatch,
    )

    conn = get_connection()

    try:
        before_tasks = [
            dict(row)
            for row in conn.execute(
                """
                SELECT *
                FROM mission_tasks
                WHERE mission_id=?
                ORDER BY position
                """,
                (mission_id,),
            ).fetchall()
        ]

        before_plan = conn.execute(
            """
            SELECT plan
            FROM mission_plans
            WHERE mission_id=?
            """,
            (mission_id,),
        ).fetchone()["plan"]

    finally:
        conn.close()

    replacement_plan = (
        "1. Rewrite completed history\n"
        "   This must never replace completed work\n"
        "2. New verification\n"
        "   Run new verification"
    )

    import pytest

    with pytest.raises(
        RuntimeError,
        match="attempted to change preserved task 1",
    ):
        executor.replan_pending_tasks(
            mission_id,
            replacement_plan,
        )

    conn = get_connection()

    try:
        after_tasks = [
            dict(row)
            for row in conn.execute(
                """
                SELECT *
                FROM mission_tasks
                WHERE mission_id=?
                ORDER BY position
                """,
                (mission_id,),
            ).fetchall()
        ]

        after_plan = conn.execute(
            """
            SELECT plan
            FROM mission_plans
            WHERE mission_id=?
            """,
            (mission_id,),
        ).fetchone()["plan"]

    finally:
        conn.close()

    assert after_tasks == before_tasks
    assert after_plan == before_plan


def test_replan_rejects_non_pristine_pending_tail_without_mutation(
    tmp_path,
    monkeypatch,
):
    mission_id, get_connection = _setup_database(
        tmp_path,
        monkeypatch,
    )

    conn = get_connection()

    try:
        conn.execute(
            """
            UPDATE mission_tasks
            SET started_at='2026-10-07 00:00:00'
            WHERE mission_id=?
              AND position=3
            """,
            (mission_id,),
        )
        conn.commit()

        before_tasks = [
            dict(row)
            for row in conn.execute(
                """
                SELECT *
                FROM mission_tasks
                WHERE mission_id=?
                ORDER BY position
                """,
                (mission_id,),
            ).fetchall()
        ]

        before_plan = conn.execute(
            """
            SELECT plan
            FROM mission_plans
            WHERE mission_id=?
            """,
            (mission_id,),
        ).fetchone()["plan"]

    finally:
        conn.close()

    replacement_plan = (
        "1. Build application\n"
        "   Implement the application\n"
        "2. New verification\n"
        "   Run new verification\n"
        "3. Final acceptance\n"
        "   Run final acceptance"
    )

    import pytest

    with pytest.raises(
        RuntimeError,
        match="contiguous tail of unstarted Pending tasks",
    ):
        executor.replan_pending_tasks(
            mission_id,
            replacement_plan,
        )

    conn = get_connection()

    try:
        after_tasks = [
            dict(row)
            for row in conn.execute(
                """
                SELECT *
                FROM mission_tasks
                WHERE mission_id=?
                ORDER BY position
                """,
                (mission_id,),
            ).fetchall()
        ]

        after_plan = conn.execute(
            """
            SELECT plan
            FROM mission_plans
            WHERE mission_id=?
            """,
            (mission_id,),
        ).fetchone()["plan"]

    finally:
        conn.close()

    assert after_tasks == before_tasks
    assert after_plan == before_plan


def test_replan_rejects_noncontiguous_replacement_positions_without_mutation(
    tmp_path,
    monkeypatch,
):
    mission_id, get_connection = _setup_database(
        tmp_path,
        monkeypatch,
    )

    conn = get_connection()

    try:
        before_tasks = [
            dict(row)
            for row in conn.execute(
                """
                SELECT *
                FROM mission_tasks
                WHERE mission_id=?
                ORDER BY position
                """,
                (mission_id,),
            ).fetchall()
        ]

        before_plan = conn.execute(
            """
            SELECT plan
            FROM mission_plans
            WHERE mission_id=?
            """,
            (mission_id,),
        ).fetchone()["plan"]

    finally:
        conn.close()

    replacement_plan = (
        "1. Build application\n"
        "   Implement the application\n"
        "3. Skipped position\n"
        "   This plan skipped position two"
    )

    import pytest

    with pytest.raises(
        ValueError,
        match="contiguous and start at 1",
    ):
        executor.replan_pending_tasks(
            mission_id,
            replacement_plan,
        )

    conn = get_connection()

    try:
        after_tasks = [
            dict(row)
            for row in conn.execute(
                """
                SELECT *
                FROM mission_tasks
                WHERE mission_id=?
                ORDER BY position
                """,
                (mission_id,),
            ).fetchall()
        ]

        after_plan = conn.execute(
            """
            SELECT plan
            FROM mission_plans
            WHERE mission_id=?
            """,
            (mission_id,),
        ).fetchone()["plan"]

    finally:
        conn.close()

    assert after_tasks == before_tasks
    assert after_plan == before_plan


def test_replan_rolls_back_everything_if_replacement_insert_fails(
    tmp_path,
    monkeypatch,
):
    mission_id, get_connection = _setup_database(
        tmp_path,
        monkeypatch,
    )

    approval = executor.approve_mission_plan(mission_id)
    assert approval["approved"] is True

    conn = get_connection()

    try:
        conn.execute(
            """
            CREATE TRIGGER fail_replan_position_three
            BEFORE INSERT ON mission_tasks
            WHEN
                NEW.mission_id = 17001
                AND NEW.position = 3
            BEGIN
                SELECT RAISE(
                    ABORT,
                    'forced replanning insert failure'
                );
            END;
            """
        )
        conn.commit()

        before_tasks = [
            dict(row)
            for row in conn.execute(
                """
                SELECT *
                FROM mission_tasks
                WHERE mission_id=?
                ORDER BY position
                """,
                (mission_id,),
            ).fetchall()
        ]

        before_plan = conn.execute(
            """
            SELECT plan
            FROM mission_plans
            WHERE mission_id=?
            """,
            (mission_id,),
        ).fetchone()["plan"]

        before_mission = dict(
            conn.execute(
                """
                SELECT
                    approved_plan_fingerprint,
                    approved_at
                FROM missions
                WHERE id=?
                """,
                (mission_id,),
            ).fetchone()
        )

    finally:
        conn.close()

    assert before_mission["approved_plan_fingerprint"] is not None
    assert before_mission["approved_at"] is not None

    replacement_plan = (
        "1. Build application\n"
        "   Implement the application\n"
        "2. Replacement verification\n"
        "   Run replacement verification\n"
        "3. Replacement persistence check\n"
        "   Verify replacement persistence\n"
        "4. Replacement final acceptance\n"
        "   Run replacement final acceptance"
    )

    import pytest

    with pytest.raises(
        sqlite3.IntegrityError,
        match="forced replanning insert failure",
    ):
        executor.replan_pending_tasks(
            mission_id,
            replacement_plan,
        )

    conn = get_connection()

    try:
        after_tasks = [
            dict(row)
            for row in conn.execute(
                """
                SELECT *
                FROM mission_tasks
                WHERE mission_id=?
                ORDER BY position
                """,
                (mission_id,),
            ).fetchall()
        ]

        after_plan = conn.execute(
            """
            SELECT plan
            FROM mission_plans
            WHERE mission_id=?
            """,
            (mission_id,),
        ).fetchone()["plan"]

        after_mission = dict(
            conn.execute(
                """
                SELECT
                    approved_plan_fingerprint,
                    approved_at
                FROM missions
                WHERE id=?
                """,
                (mission_id,),
            ).fetchone()
        )

    finally:
        conn.close()

    assert after_tasks == before_tasks
    assert after_plan == before_plan

    assert (
        after_mission["approved_plan_fingerprint"]
        == before_mission["approved_plan_fingerprint"]
    )
    assert (
        after_mission["approved_at"]
        == before_mission["approved_at"]
    )

    approval_after = executor.get_mission_approval_status(
        mission_id
    )

    assert approval_after["approved"] is True


def test_replan_preview_does_not_mutate_mission(
    tmp_path,
    monkeypatch,
):
    mission_id, get_connection = _setup_database(
        tmp_path,
        monkeypatch,
    )

    approval = executor.approve_mission_plan(mission_id)
    assert approval["approved"] is True

    conn = get_connection()

    try:
        before_tasks = [
            dict(row)
            for row in conn.execute(
                """
                SELECT *
                FROM mission_tasks
                WHERE mission_id=?
                ORDER BY position
                """,
                (mission_id,),
            ).fetchall()
        ]

        before_plan = conn.execute(
            """
            SELECT plan
            FROM mission_plans
            WHERE mission_id=?
            """,
            (mission_id,),
        ).fetchone()["plan"]

        before_mission = dict(
            conn.execute(
                """
                SELECT
                    approved_plan_fingerprint,
                    approved_at
                FROM missions
                WHERE id=?
                """,
                (mission_id,),
            ).fetchone()
        )

    finally:
        conn.close()

    replacement_plan = (
        "1. Build application\n"
        "   Implement the application\n"
        "2. Fresh verification\n"
        "   Run fresh deterministic verification\n"
        "3. Persistence verification\n"
        "   Verify state across a fresh process\n"
        "4. Final acceptance\n"
        "   Run final acceptance"
    )

    preview = executor.preview_pending_replan(
        mission_id,
        replacement_plan,
    )

    assert preview["mission_id"] == mission_id
    assert preview["replacement_plan"] == replacement_plan

    assert isinstance(
        preview["state_fingerprint"],
        str,
    )
    assert len(preview["state_fingerprint"]) == 64

    assert [
        task["position"]
        for task in preview["preserved_tasks"]
    ] == [1]

    assert [
        task["position"]
        for task in preview["current_pending_tasks"]
    ] == [2, 3]

    assert [
        task["position"]
        for task in preview["replacement_pending_tasks"]
    ] == [2, 3, 4]

    assert [
        task["title"]
        for task in preview["replacement_pending_tasks"]
    ] == [
        "Fresh verification",
        "Persistence verification",
        "Final acceptance",
    ]

    conn = get_connection()

    try:
        after_tasks = [
            dict(row)
            for row in conn.execute(
                """
                SELECT *
                FROM mission_tasks
                WHERE mission_id=?
                ORDER BY position
                """,
                (mission_id,),
            ).fetchall()
        ]

        after_plan = conn.execute(
            """
            SELECT plan
            FROM mission_plans
            WHERE mission_id=?
            """,
            (mission_id,),
        ).fetchone()["plan"]

        after_mission = dict(
            conn.execute(
                """
                SELECT
                    approved_plan_fingerprint,
                    approved_at
                FROM missions
                WHERE id=?
                """,
                (mission_id,),
            ).fetchone()
        )

    finally:
        conn.close()

    assert after_tasks == before_tasks
    assert after_plan == before_plan
    assert after_mission == before_mission


def test_replan_preview_fingerprint_changes_when_task_state_changes(
    tmp_path,
    monkeypatch,
):
    mission_id, get_connection = _setup_database(
        tmp_path,
        monkeypatch,
    )

    replacement_plan = (
        "1. Build application\n"
        "   Implement the application\n"
        "2. Fresh verification\n"
        "   Run fresh verification"
    )

    first = executor.preview_pending_replan(
        mission_id,
        replacement_plan,
    )

    conn = get_connection()

    try:
        conn.execute(
            """
            UPDATE mission_tasks
            SET result='new external evidence'
            WHERE mission_id=?
              AND position=2
            """,
            (mission_id,),
        )
        conn.commit()

    finally:
        conn.close()

    second = executor.preview_pending_replan(
        mission_id,
        replacement_plan,
    )

    assert (
        first["state_fingerprint"]
        != second["state_fingerprint"]
    )


def test_replan_accepts_matching_preview_fingerprint(
    tmp_path,
    monkeypatch,
):
    mission_id, get_connection = _setup_database(
        tmp_path,
        monkeypatch,
    )

    replacement_plan = (
        "1. Build application\n"
        "   Implement the application\n"
        "2. Fresh verification\n"
        "   Run fresh verification\n"
        "3. Final acceptance\n"
        "   Run final acceptance"
    )

    preview = executor.preview_pending_replan(
        mission_id,
        replacement_plan,
    )

    replanned = executor.replan_pending_tasks(
        mission_id,
        replacement_plan,
        expected_state_fingerprint=(
            preview["state_fingerprint"]
        ),
    )

    assert [
        task["title"]
        for task in replanned
    ] == [
        "Build application",
        "Fresh verification",
        "Final acceptance",
    ]

    assert replanned[0]["status"] == "Completed"
    assert replanned[0]["result"] == "VERIFIED BUILD EVIDENCE"

    assert all(
        task["status"] == "Pending"
        for task in replanned[1:]
    )


def test_replan_rejects_stale_preview_without_mutation(
    tmp_path,
    monkeypatch,
):
    mission_id, get_connection = _setup_database(
        tmp_path,
        monkeypatch,
    )

    approval = executor.approve_mission_plan(mission_id)
    assert approval["approved"] is True

    replacement_plan = (
        "1. Build application\n"
        "   Implement the application\n"
        "2. Fresh verification\n"
        "   Run fresh verification\n"
        "3. Final acceptance\n"
        "   Run final acceptance"
    )

    preview = executor.preview_pending_replan(
        mission_id,
        replacement_plan,
    )

    conn = get_connection()

    try:
        conn.execute(
            """
            UPDATE mission_tasks
            SET result='evidence arrived after preview'
            WHERE mission_id=?
              AND position=2
            """,
            (mission_id,),
        )
        conn.commit()

        state_after_external_change = [
            dict(row)
            for row in conn.execute(
                """
                SELECT *
                FROM mission_tasks
                WHERE mission_id=?
                ORDER BY position
                """,
                (mission_id,),
            ).fetchall()
        ]

        plan_after_external_change = conn.execute(
            """
            SELECT plan
            FROM mission_plans
            WHERE mission_id=?
            """,
            (mission_id,),
        ).fetchone()["plan"]

        approval_after_external_change = dict(
            conn.execute(
                """
                SELECT
                    approved_plan_fingerprint,
                    approved_at
                FROM missions
                WHERE id=?
                """,
                (mission_id,),
            ).fetchone()
        )

    finally:
        conn.close()

    import pytest

    with pytest.raises(
        RuntimeError,
        match="preview is stale",
    ):
        executor.replan_pending_tasks(
            mission_id,
            replacement_plan,
            expected_state_fingerprint=(
                preview["state_fingerprint"]
            ),
        )

    conn = get_connection()

    try:
        final_tasks = [
            dict(row)
            for row in conn.execute(
                """
                SELECT *
                FROM mission_tasks
                WHERE mission_id=?
                ORDER BY position
                """,
                (mission_id,),
            ).fetchall()
        ]

        final_plan = conn.execute(
            """
            SELECT plan
            FROM mission_plans
            WHERE mission_id=?
            """,
            (mission_id,),
        ).fetchone()["plan"]

        final_approval = dict(
            conn.execute(
                """
                SELECT
                    approved_plan_fingerprint,
                    approved_at
                FROM missions
                WHERE id=?
                """,
                (mission_id,),
            ).fetchone()
        )

    finally:
        conn.close()

    assert final_tasks == state_after_external_change
    assert final_plan == plan_after_external_change

    assert (
        final_approval
        == approval_after_external_change
    )

    assert final_tasks[1]["result"] == (
        "evidence arrived after preview"
    )


def test_replan_rejects_preview_when_stored_plan_changes(
    tmp_path,
    monkeypatch,
):
    mission_id, get_connection = _setup_database(
        tmp_path,
        monkeypatch,
    )

    approval = executor.approve_mission_plan(mission_id)
    assert approval["approved"] is True

    replacement_plan = (
        "1. Build application\n"
        "   Implement the application\n"
        "2. Fresh verification\n"
        "   Run fresh verification\n"
        "3. Final acceptance\n"
        "   Run final acceptance"
    )

    preview = executor.preview_pending_replan(
        mission_id,
        replacement_plan,
    )

    conn = get_connection()

    try:
        changed_stored_plan = (
            "1. Build application\n"
            "   Implement the application\n"
            "2. Old verification\n"
            "   Run the old verification with changed plan text\n"
            "3. Old final check\n"
            "   Run the old final check"
        )

        conn.execute(
            """
            UPDATE mission_plans
            SET
                plan=?,
                updated_at=CURRENT_TIMESTAMP
            WHERE mission_id=?
            """,
            (
                changed_stored_plan,
                mission_id,
            ),
        )
        conn.commit()

        tasks_before_apply = [
            dict(row)
            for row in conn.execute(
                """
                SELECT *
                FROM mission_tasks
                WHERE mission_id=?
                ORDER BY position
                """,
                (mission_id,),
            ).fetchall()
        ]

        plan_before_apply = conn.execute(
            """
            SELECT plan
            FROM mission_plans
            WHERE mission_id=?
            """,
            (mission_id,),
        ).fetchone()["plan"]

        approval_before_apply = dict(
            conn.execute(
                """
                SELECT
                    approved_plan_fingerprint,
                    approved_at
                FROM missions
                WHERE id=?
                """,
                (mission_id,),
            ).fetchone()
        )

    finally:
        conn.close()

    import pytest

    with pytest.raises(
        RuntimeError,
        match="preview is stale",
    ):
        executor.replan_pending_tasks(
            mission_id,
            replacement_plan,
            expected_state_fingerprint=(
                preview["state_fingerprint"]
            ),
        )

    conn = get_connection()

    try:
        tasks_after_apply = [
            dict(row)
            for row in conn.execute(
                """
                SELECT *
                FROM mission_tasks
                WHERE mission_id=?
                ORDER BY position
                """,
                (mission_id,),
            ).fetchall()
        ]

        plan_after_apply = conn.execute(
            """
            SELECT plan
            FROM mission_plans
            WHERE mission_id=?
            """,
            (mission_id,),
        ).fetchone()["plan"]

        approval_after_apply = dict(
            conn.execute(
                """
                SELECT
                    approved_plan_fingerprint,
                    approved_at
                FROM missions
                WHERE id=?
                """,
                (mission_id,),
            ).fetchone()
        )

    finally:
        conn.close()

    assert tasks_after_apply == tasks_before_apply
    assert plan_after_apply == plan_before_apply

    assert (
        approval_after_apply
        == approval_before_apply
    )

    assert plan_after_apply == changed_stored_plan


def test_successful_replan_records_revision_history(
    tmp_path,
    monkeypatch,
):
    mission_id, get_connection = _setup_database(
        tmp_path,
        monkeypatch,
    )

    replacement_plan = (
        "1. Build application\n"
        "   Implement the application\n"
        "2. Fresh verification\n"
        "   Run fresh verification\n"
        "3. Final acceptance\n"
        "   Run final acceptance"
    )

    reason = (
        "New execution evidence made the old verification "
        "sequence obsolete."
    )

    triggering_evidence = (
        "Task 1 completed successfully, but verification "
        "requirements changed."
    )

    preview = executor.preview_pending_replan(
        mission_id,
        replacement_plan,
    )

    original_plan = preview["stored_plan"]
    state_fingerprint = preview["state_fingerprint"]

    executor.replan_pending_tasks(
        mission_id,
        replacement_plan,
        expected_state_fingerprint=state_fingerprint,
        reason=reason,
        triggering_evidence=triggering_evidence,
    )

    history = executor.get_mission_replan_history(
        mission_id
    )

    assert len(history) == 1

    entry = history[0]

    assert entry["mission_id"] == mission_id
    assert entry["original_plan"] == original_plan
    assert entry["replacement_plan"] == replacement_plan
    assert entry["reason"] == reason
    assert entry["triggering_evidence"] == triggering_evidence
    assert entry["state_fingerprint"] == state_fingerprint

    assert entry["created_at"] is not None

    conn = get_connection()

    try:
        row = conn.execute(
            """
            SELECT
                mission_id,
                original_plan,
                replacement_plan,
                reason,
                triggering_evidence,
                state_fingerprint,
                created_at
            FROM mission_replan_history
            WHERE mission_id=?
            """,
            (mission_id,),
        ).fetchone()

    finally:
        conn.close()

    assert row is not None
    assert row["mission_id"] == mission_id
    assert row["original_plan"] == original_plan
    assert row["replacement_plan"] == replacement_plan
    assert row["reason"] == reason
    assert row["triggering_evidence"] == triggering_evidence
    assert row["state_fingerprint"] == state_fingerprint
    assert row["created_at"] is not None


def test_failed_replan_does_not_record_revision_history(
    tmp_path,
    monkeypatch,
):
    mission_id, get_connection = _setup_database(
        tmp_path,
        monkeypatch,
    )

    replacement_plan = (
        "1. Build application\n"
        "   Implement the application\n"
        "2. Fresh verification\n"
        "   Run fresh verification\n"
        "3. Final acceptance\n"
        "   Run final acceptance"
    )

    preview = executor.preview_pending_replan(
        mission_id,
        replacement_plan,
    )

    conn = get_connection()

    try:
        conn.execute(
            """
            UPDATE mission_tasks
            SET result='state changed after preview'
            WHERE mission_id=?
              AND position=2
            """,
            (mission_id,),
        )
        conn.commit()

    finally:
        conn.close()

    import pytest

    with pytest.raises(
        RuntimeError,
        match="preview is stale",
    ):
        executor.replan_pending_tasks(
            mission_id,
            replacement_plan,
            expected_state_fingerprint=(
                preview["state_fingerprint"]
            ),
            reason="Should never be committed",
            triggering_evidence="Stale evidence",
        )

    history = executor.get_mission_replan_history(
        mission_id
    )

    assert history == []


def test_replan_rejects_different_plan_than_previewed(
    tmp_path,
    monkeypatch,
):
    mission_id, get_connection = _setup_database(
        tmp_path,
        monkeypatch,
    )

    previewed_plan = (
        "1. Build application\n"
        "   Implement the application\n"
        "2. New verification\n"
        "   Run the reviewed verification\n"
        "3. New final check\n"
        "   Run the reviewed final check"
    )

    different_plan = (
        "1. Build application\n"
        "   Implement the application\n"
        "2. Different verification\n"
        "   Run a different verification\n"
        "3. Different final check\n"
        "   Run a different final check"
    )

    preview = executor.preview_pending_replan(
        mission_id,
        previewed_plan,
    )

    assert "proposal_fingerprint" in preview

    conn = get_connection()

    try:
        before_tasks = [
            dict(row)
            for row in conn.execute(
                """
                SELECT *
                FROM mission_tasks
                WHERE mission_id=?
                ORDER BY position
                """,
                (mission_id,),
            ).fetchall()
        ]

        before_plan = conn.execute(
            """
            SELECT plan
            FROM mission_plans
            WHERE mission_id=?
            """,
            (mission_id,),
        ).fetchone()["plan"]

    finally:
        conn.close()

    with pytest.raises(
        RuntimeError,
        match="proposal",
    ):
        executor.replan_pending_tasks(
            mission_id,
            different_plan,
            expected_state_fingerprint=(
                preview["state_fingerprint"]
            ),
            expected_proposal_fingerprint=(
                preview["proposal_fingerprint"]
            ),
        )

    conn = get_connection()

    try:
        after_tasks = [
            dict(row)
            for row in conn.execute(
                """
                SELECT *
                FROM mission_tasks
                WHERE mission_id=?
                ORDER BY position
                """,
                (mission_id,),
            ).fetchall()
        ]

        after_plan = conn.execute(
            """
            SELECT plan
            FROM mission_plans
            WHERE mission_id=?
            """,
            (mission_id,),
        ).fetchone()["plan"]

    finally:
        conn.close()

    assert after_tasks == before_tasks
    assert after_plan == before_plan


def test_replan_detector_ignores_normal_completed_evidence():
    tasks = [
        {
            "id": 101,
            "mission_id": 17001,
            "position": 1,
            "title": "Build application",
            "instructions": "Implement the application",
            "status": "Completed",
            "result": (
                "VERIFIED EXECUTION EVIDENCE: "
                "application built successfully."
            ),
            "created_at": "2026-10-09 10:00:00",
            "started_at": "2026-10-09 10:01:00",
            "completed_at": "2026-10-09 10:02:00",
        },
        {
            "id": 102,
            "mission_id": 17001,
            "position": 2,
            "title": "Verify application",
            "instructions": "Run deterministic verification",
            "status": "Pending",
            "result": "",
            "created_at": "2026-10-09 10:00:00",
            "started_at": None,
            "completed_at": None,
        },
    ]

    detection = executor.detect_pending_replan_need(tasks)

    assert detection["recommended"] is False
    assert detection["reason"] == ""
    assert detection["triggering_evidence"] == ""
    assert detection["triggering_task"] is None
    assert detection["pending_tasks"] == [tasks[1]]


def test_replan_detector_detects_explicit_replan_evidence():
    tasks = [
        {
            "id": 101,
            "mission_id": 17001,
            "position": 1,
            "title": "Inspect service",
            "instructions": "Inspect current service behavior",
            "status": "Completed",
            "result": (
                "VERIFIED EXECUTION EVIDENCE:\n"
                "Service uses a different persistence contract.\n"
                "REPLAN REQUIRED: remaining verification assumes "
                "the obsolete contract."
            ),
            "created_at": "2026-10-09 10:00:00",
            "started_at": "2026-10-09 10:01:00",
            "completed_at": "2026-10-09 10:02:00",
        },
        {
            "id": 102,
            "mission_id": 17001,
            "position": 2,
            "title": "Old verification",
            "instructions": "Verify the obsolete contract",
            "status": "Pending",
            "result": "",
            "created_at": "2026-10-09 10:00:00",
            "started_at": None,
            "completed_at": None,
        },
        {
            "id": 103,
            "mission_id": 17001,
            "position": 3,
            "title": "Final check",
            "instructions": "Run final acceptance check",
            "status": "Pending",
            "result": "",
            "created_at": "2026-10-09 10:00:00",
            "started_at": None,
            "completed_at": None,
        },
    ]

    detection = executor.detect_pending_replan_need(tasks)

    assert detection["recommended"] is True
    assert (
        detection["reason"]
        == "remaining verification assumes the obsolete contract."
    )
    assert "VERIFIED EXECUTION EVIDENCE" in (
        detection["triggering_evidence"]
    )
    assert detection["triggering_task"] == tasks[0]
    assert detection["pending_tasks"] == tasks[1:]


def test_replan_detector_uses_latest_completed_trigger():
    tasks = [
        {
            "id": 101,
            "mission_id": 17001,
            "position": 1,
            "title": "Initial inspection",
            "instructions": "Inspect initial state",
            "status": "Completed",
            "result": (
                "REPLAN REQUIRED: first discovered incompatibility."
            ),
            "created_at": "2026-10-09 10:00:00",
            "started_at": "2026-10-09 10:01:00",
            "completed_at": "2026-10-09 10:02:00",
        },
        {
            "id": 102,
            "mission_id": 17001,
            "position": 2,
            "title": "Second inspection",
            "instructions": "Inspect updated state",
            "status": "Completed",
            "result": (
                "VERIFIED EXECUTION EVIDENCE:\n"
                "REPLAN REQUIRED: latest evidence supersedes "
                "the remaining verification."
            ),
            "created_at": "2026-10-09 10:03:00",
            "started_at": "2026-10-09 10:04:00",
            "completed_at": "2026-10-09 10:05:00",
        },
        {
            "id": 103,
            "mission_id": 17001,
            "position": 3,
            "title": "Pending verification",
            "instructions": "Run old verification",
            "status": "Pending",
            "result": "",
            "created_at": "2026-10-09 10:00:00",
            "started_at": None,
            "completed_at": None,
        },
    ]

    detection = executor.detect_pending_replan_need(tasks)

    assert detection["recommended"] is True
    assert detection["triggering_task"]["id"] == 102
    assert (
        detection["reason"]
        == "latest evidence supersedes the remaining verification."
    )
    assert detection["pending_tasks"] == [tasks[2]]


def test_replan_detector_does_not_recommend_without_pending_tail():
    tasks = [
        {
            "id": 101,
            "mission_id": 17001,
            "position": 1,
            "title": "Final verification",
            "instructions": "Run final verification",
            "status": "Completed",
            "result": (
                "VERIFIED EXECUTION EVIDENCE:\n"
                "REPLAN REQUIRED: discovered a changed assumption."
            ),
            "created_at": "2026-10-09 10:00:00",
            "started_at": "2026-10-09 10:01:00",
            "completed_at": "2026-10-09 10:02:00",
        },
    ]

    detection = executor.detect_pending_replan_need(tasks)

    assert detection["recommended"] is False
    assert detection["reason"] == ""
    assert detection["triggering_evidence"] == ""
    assert detection["triggering_task"] is None
    assert detection["pending_tasks"] == []


def test_replan_detector_rejects_unverified_model_marker():
    tasks = [
        {
            "id": 201,
            "mission_id": 17001,
            "position": 1,
            "title": "Analyze environment",
            "instructions": "Analyze the environment",
            "status": "Completed",
            "result": (
                "The model believes the plan should change.\n"
                "REPLAN REQUIRED: model-generated recommendation."
            ),
            "created_at": "2026-10-09 11:00:00",
            "started_at": "2026-10-09 11:01:00",
            "completed_at": "2026-10-09 11:02:00",
        },
        {
            "id": 202,
            "mission_id": 17001,
            "position": 2,
            "title": "Pending verification",
            "instructions": "Run verification",
            "status": "Pending",
            "result": "",
            "created_at": "2026-10-09 11:00:00",
            "started_at": None,
            "completed_at": None,
        },
    ]

    detection = executor.detect_pending_replan_need(tasks)

    assert detection["recommended"] is False
    assert detection["reason"] == ""
    assert detection["triggering_evidence"] == ""
    assert detection["triggering_task"] is None


def test_replan_detector_rejects_marker_before_verified_evidence():
    tasks = [
        {
            "id": 201,
            "mission_id": 17001,
            "position": 1,
            "title": "Inspect environment",
            "instructions": "Inspect environment",
            "status": "Completed",
            "result": (
                "REPLAN REQUIRED: untrusted model recommendation.\n\n"
                "SAFE TOOLS EVIDENCE (verified):\n"
                '[{"tool": "python.version", "ok": true}]'
            ),
            "created_at": "2026-10-09 11:00:00",
            "started_at": "2026-10-09 11:01:00",
            "completed_at": "2026-10-09 11:02:00",
        },
        {
            "id": 202,
            "mission_id": 17001,
            "position": 2,
            "title": "Pending verification",
            "instructions": "Run verification",
            "status": "Pending",
            "result": "",
            "created_at": "2026-10-09 11:00:00",
            "started_at": None,
            "completed_at": None,
        },
    ]

    detection = executor.detect_pending_replan_need(tasks)

    assert detection["recommended"] is False
    assert detection["reason"] == ""
    assert detection["triggering_task"] is None


def test_replan_detector_accepts_marker_after_safe_tools_boundary():
    tasks = [
        {
            "id": 201,
            "mission_id": 17001,
            "position": 1,
            "title": "Inspect environment",
            "instructions": "Inspect environment",
            "status": "Completed",
            "result": (
                "Inspection completed.\n\n"
                "SAFE TOOLS EVIDENCE (verified):\n"
                '{"verified": true}\n'
                "REPLAN REQUIRED: verified environment changed."
            ),
            "created_at": "2026-10-09 11:00:00",
            "started_at": "2026-10-09 11:01:00",
            "completed_at": "2026-10-09 11:02:00",
        },
        {
            "id": 202,
            "mission_id": 17001,
            "position": 2,
            "title": "Pending verification",
            "instructions": "Run old verification",
            "status": "Pending",
            "result": "",
            "created_at": "2026-10-09 11:00:00",
            "started_at": None,
            "completed_at": None,
        },
    ]

    detection = executor.detect_pending_replan_need(tasks)

    assert detection["recommended"] is True
    assert detection["reason"] == "verified environment changed."
    assert detection["triggering_task"] == tasks[0]


def test_replan_detector_accepts_http_verified_evidence_boundary():
    tasks = [
        {
            "id": 201,
            "mission_id": 17001,
            "position": 1,
            "title": "Verify HTTP service",
            "instructions": "Run structured HTTP verification",
            "status": "Completed",
            "result": (
                "HTTP SERVICE EXECUTION: VERIFIED\n\n"
                "VERIFIED HTTP SERVICE EVIDENCE:\n"
                '{"verified": true}\n'
                "REPLAN REQUIRED: verified API contract changed."
            ),
            "created_at": "2026-10-09 11:00:00",
            "started_at": "2026-10-09 11:01:00",
            "completed_at": "2026-10-09 11:02:00",
        },
        {
            "id": 202,
            "mission_id": 17001,
            "position": 2,
            "title": "Old API verification",
            "instructions": "Verify old API contract",
            "status": "Pending",
            "result": "",
            "created_at": "2026-10-09 11:00:00",
            "started_at": None,
            "completed_at": None,
        },
    ]

    detection = executor.detect_pending_replan_need(tasks)

    assert detection["recommended"] is True
    assert detection["reason"] == "verified API contract changed."
    assert detection["triggering_task"] == tasks[0]


def test_replan_detector_accepts_builder_verified_evidence_boundary():
    tasks = [
        {
            "id": 201,
            "mission_id": 17001,
            "position": 1,
            "title": "Build project",
            "instructions": "Build project artifacts",
            "status": "Completed",
            "result": (
                "BUILDER AGENT: COMPLETED\n\n"
                "VERIFIED BUILDER EVIDENCE:\n"
                '{"verified": true}\n'
                "REPLAN REQUIRED: verified artifact layout changed."
            ),
            "created_at": "2026-10-09 11:00:00",
            "started_at": "2026-10-09 11:01:00",
            "completed_at": "2026-10-09 11:02:00",
        },
        {
            "id": 202,
            "mission_id": 17001,
            "position": 2,
            "title": "Old artifact verification",
            "instructions": "Verify old artifact layout",
            "status": "Pending",
            "result": "",
            "created_at": "2026-10-09 11:00:00",
            "started_at": None,
            "completed_at": None,
        },
    ]

    detection = executor.detect_pending_replan_need(tasks)

    assert detection["recommended"] is True
    assert detection["reason"] == "verified artifact layout changed."
    assert detection["triggering_task"] == tasks[0]


def test_attach_replan_advisory_adds_negative_advisory(
    monkeypatch,
):
    execution = {
        "mission_id": 17001,
        "task_id": 101,
        "position": 1,
        "title": "Build application",
        "status": "Completed",
        "result": "completed",
    }

    tasks = [
        {
            "id": 101,
            "mission_id": 17001,
            "position": 1,
            "title": "Build application",
            "instructions": "Build application",
            "status": "Completed",
            "result": (
                "VERIFIED EXECUTION EVIDENCE:\n"
                "build succeeded"
            ),
            "created_at": "2026-10-09 12:00:00",
            "started_at": "2026-10-09 12:01:00",
            "completed_at": "2026-10-09 12:02:00",
        },
        {
            "id": 102,
            "mission_id": 17001,
            "position": 2,
            "title": "Verify application",
            "instructions": "Run verification",
            "status": "Pending",
            "result": "",
            "created_at": "2026-10-09 12:00:00",
            "started_at": None,
            "completed_at": None,
        },
    ]

    monkeypatch.setattr(
        executor,
        "get_tasks",
        lambda mission_id: tasks,
    )

    result = executor._attach_replan_advisory(
        17001,
        execution,
    )

    assert result is execution
    assert result["replan_advisory"]["recommended"] is False
    assert result["replan_advisory"]["reason"] == ""
    assert result["replan_advisory"]["triggering_task"] is None
    assert result["replan_advisory"]["pending_tasks"] == [tasks[1]]


def test_attach_replan_advisory_adds_verified_recommendation(
    monkeypatch,
):
    execution = {
        "mission_id": 17001,
        "task_id": 101,
        "position": 1,
        "title": "Inspect service",
        "status": "Completed",
        "result": "completed",
    }

    tasks = [
        {
            "id": 101,
            "mission_id": 17001,
            "position": 1,
            "title": "Inspect service",
            "instructions": "Inspect service",
            "status": "Completed",
            "result": (
                "VERIFIED EXECUTION EVIDENCE:\n"
                "service contract changed\n"
                "REPLAN REQUIRED: remaining checks use old contract."
            ),
            "created_at": "2026-10-09 12:00:00",
            "started_at": "2026-10-09 12:01:00",
            "completed_at": "2026-10-09 12:02:00",
        },
        {
            "id": 102,
            "mission_id": 17001,
            "position": 2,
            "title": "Old verification",
            "instructions": "Verify old contract",
            "status": "Pending",
            "result": "",
            "created_at": "2026-10-09 12:00:00",
            "started_at": None,
            "completed_at": None,
        },
    ]

    monkeypatch.setattr(
        executor,
        "get_tasks",
        lambda mission_id: tasks,
    )

    result = executor._attach_replan_advisory(
        17001,
        execution,
    )

    advisory = result["replan_advisory"]

    assert result is execution
    assert advisory["recommended"] is True
    assert (
        advisory["reason"]
        == "remaining checks use old contract."
    )
    assert advisory["triggering_task"] == tasks[0]
    assert advisory["pending_tasks"] == [tasks[1]]


def test_attach_replan_advisory_skips_noncompleted_execution(
    monkeypatch,
):
    execution = {
        "mission_id": 17001,
        "task_id": 101,
        "position": 1,
        "title": "Blocked verification",
        "status": "Blocked",
    }

    called = False

    def fake_get_tasks(mission_id):
        nonlocal called
        called = True
        raise AssertionError(
            "Blocked execution must not run replan detection."
        )

    monkeypatch.setattr(
        executor,
        "get_tasks",
        fake_get_tasks,
    )

    result = executor._attach_replan_advisory(
        17001,
        execution,
    )

    assert result is execution
    assert "replan_advisory" not in result
    assert called is False


def test_finalize_execution_result_routes_completed_result_through_advisory(
    monkeypatch,
):
    execution = {
        "mission_id": 17001,
        "task_id": 101,
        "status": "Completed",
        "result": "verified result",
    }

    calls = []

    def fake_attach(mission_id, result):
        calls.append((mission_id, result))
        result["replan_advisory"] = {
            "recommended": True,
            "reason": "verified state changed.",
        }
        return result

    monkeypatch.setattr(
        executor,
        "_attach_replan_advisory",
        fake_attach,
    )

    result = executor._finalize_execution_result(
        17001,
        execution,
    )

    assert result is execution
    assert calls == [(17001, execution)]
    assert result["replan_advisory"]["recommended"] is True


def test_finalize_execution_result_preserves_blocked_result(
    monkeypatch,
):
    execution = {
        "mission_id": 17001,
        "task_id": 101,
        "status": "Blocked",
    }

    calls = []

    def fake_attach(mission_id, result):
        calls.append((mission_id, result))
        return result

    monkeypatch.setattr(
        executor,
        "_attach_replan_advisory",
        fake_attach,
    )

    result = executor._finalize_execution_result(
        17001,
        execution,
    )

    assert result is execution
    assert calls == [(17001, execution)]
    assert "replan_advisory" not in result


def test_replan_detector_ignores_old_marker_after_newer_normal_completion():
    tasks = [
        {
            "id": 101,
            "mission_id": 17001,
            "position": 1,
            "title": "Inspect old contract",
            "instructions": "Inspect.",
            "status": "Completed",
            "result": (
                "VERIFIED EXECUTION EVIDENCE:\n"
                "contract changed\n"
                "REPLAN REQUIRED: remaining checks use old contract."
            ),
        },
        {
            "id": 102,
            "mission_id": 17001,
            "position": 2,
            "title": "Verify new contract",
            "instructions": "Verify.",
            "status": "Completed",
            "result": (
                "VERIFIED EXECUTION EVIDENCE:\n"
                "new contract verified successfully"
            ),
        },
        {
            "id": 103,
            "mission_id": 17001,
            "position": 3,
            "title": "Continue verification",
            "instructions": "Continue.",
            "status": "Pending",
            "result": "",
        },
    ]

    detection = executor.detect_pending_replan_need(tasks)

    assert detection["recommended"] is False
    assert detection["reason"] == ""
    assert detection["triggering_evidence"] == ""
    assert detection["triggering_task"] is None
    assert detection["pending_tasks"] == [tasks[2]]
