import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).parent / "nuttz.db"


def get_connection():
    conn = sqlite3.connect(
        DB_PATH,
        timeout=5.0,
    )

    conn.row_factory = sqlite3.Row

    conn.execute(
        "PRAGMA foreign_keys = ON"
    )

    conn.execute(
        "PRAGMA busy_timeout = 5000"
    )

    conn.execute(
        "PRAGMA journal_mode = WAL"
    )

    return conn


def init_db():
    conn = get_connection()

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS missions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'Waiting',
            progress INTEGER NOT NULL DEFAULT 0,
            assigned_agent TEXT DEFAULT '',
            priority TEXT DEFAULT 'Normal',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """
    )

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS mission_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            mission_id INTEGER,
            agent TEXT NOT NULL,
            event_type TEXT NOT NULL,
            message TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (mission_id) REFERENCES missions(id)
        )
        """
    )

    conn.commit()
    conn.close()
