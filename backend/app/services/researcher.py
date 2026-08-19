import json
from typing import Any

import requests

from app.database.database import get_connection
from app.services.events import log_event


OLLAMA_URL = "http://127.0.0.1:11434/api/generate"
MODEL = "qwen3:8b"


def ensure_research_table() -> None:
    conn = get_connection()

    try:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS mission_research (
                mission_id INTEGER PRIMARY KEY,
                model TEXT NOT NULL,
                report_json TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (mission_id) REFERENCES missions(id)
            )
            """
        )
        conn.commit()
    finally:
        conn.close()


def save_research_report(
    mission_id: int,
    report: dict[str, Any],
) -> None:
    ensure_research_table()

    conn = get_connection()

    try:
        conn.execute(
            """
            INSERT INTO mission_research
                (
                    mission_id,
                    model,
                    report_json,
                    created_at,
                    updated_at
                )
            VALUES
                (?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
            ON CONFLICT(mission_id) DO UPDATE SET
                model=excluded.model,
                report_json=excluded.report_json,
                updated_at=CURRENT_TIMESTAMP
            """,
            (
                mission_id,
                MODEL,
                json.dumps(report),
            ),
        )
        conn.commit()
    finally:
        conn.close()


def get_research_report(
    mission_id: int,
) -> dict[str, Any] | None:
    ensure_research_table()

    conn = get_connection()

    try:
        row = conn.execute(
            """
            SELECT
                mission_id,
                model,
                report_json,
                created_at,
                updated_at
            FROM mission_research
            WHERE mission_id=?
            """,
            (mission_id,),
        ).fetchone()
    finally:
        conn.close()

    if row is None:
        return None

    try:
        report = json.loads(row["report_json"])
    except json.JSONDecodeError:
        report = {
            "summary": row["report_json"],
            "technologies": [],
            "steps": [],
            "risks": [],
        }

    return {
        "mission_id": row["mission_id"],
        "model": row["model"],
        "report": report,
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def research(
    mission_id: int,
    mission_title: str,
) -> dict[str, Any]:
    log_event(
        mission_id,
        "Researcher",
        "started",
        "Research started",
    )

    prompt = f"""
You are the Research Agent inside NUTTZ OS.

Mission:
{mission_title}

Produce a concise technical research report.

Return JSON only.

Format:

{{
  "summary": "...",
  "technologies": ["..."],
  "steps": ["..."],
  "risks": ["..."]
}}
"""

    response = requests.post(
        OLLAMA_URL,
        json={
            "model": MODEL,
            "prompt": prompt,
            "stream": False,
        },
        timeout=180,
    )

    response.raise_for_status()

    result = response.json()["response"]

    try:
        report = json.loads(result)
    except json.JSONDecodeError:
        report = {
            "summary": result,
            "technologies": [],
            "steps": [],
            "risks": [],
        }

    save_research_report(mission_id, report)

    log_event(
        mission_id,
        "Researcher",
        "completed",
        "Research completed",
    )

    return report
