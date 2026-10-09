import sqlite3

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
