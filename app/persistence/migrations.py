from __future__ import annotations

import sqlite3


MIGRATIONS: tuple[tuple[int, str], ...] = (
    (
        1,
        """
        CREATE TABLE IF NOT EXISTS documents (
            document_id TEXT PRIMARY KEY,
            title TEXT NOT NULL,
            markdown_path TEXT NOT NULL UNIQUE,
            content_hash TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS generations (
            generation_id TEXT PRIMARY KEY,
            document_id TEXT NOT NULL REFERENCES documents(document_id),
            status TEXT NOT NULL CHECK (
                status IN ('queued', 'running', 'completed', 'failed')
            ),
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            completed_at TEXT,
            duration_seconds REAL,
            generation_seconds REAL,
            decode_seconds REAL,
            audio_path TEXT,
            metadata_path TEXT,
            model_id TEXT NOT NULL,
            language TEXT NOT NULL,
            voice_reference_path TEXT NOT NULL,
            voice_sha256 TEXT NOT NULL,
            markdown_hash TEXT NOT NULL,
            error TEXT
        );

        CREATE INDEX IF NOT EXISTS generations_document_created
            ON generations(document_id, created_at DESC);
        """,
    ),
    (
        2,
        """
        CREATE TABLE IF NOT EXISTS generation_jobs (
            queue_sequence INTEGER PRIMARY KEY AUTOINCREMENT,
            job_id TEXT NOT NULL UNIQUE,
            generation_id TEXT NOT NULL UNIQUE REFERENCES generations(generation_id),
            document_id TEXT NOT NULL REFERENCES documents(document_id),
            status TEXT NOT NULL CHECK (status IN (
                'queued', 'running', 'cancelling', 'cancelled',
                'completed', 'failed', 'interrupted'
            )),
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            started_at TEXT,
            completed_at TEXT,
            heartbeat_at TEXT,
            error TEXT,
            progress REAL NOT NULL DEFAULT 0,
            phase TEXT NOT NULL DEFAULT 'queued',
            current INTEGER NOT NULL DEFAULT 0,
            total INTEGER NOT NULL DEFAULT 0,
            message TEXT NOT NULL DEFAULT ''
        );
        CREATE INDEX IF NOT EXISTS generation_jobs_fifo
            ON generation_jobs(status, queue_sequence);
        """,
    ),
)


def migrate(connection: sqlite3.Connection) -> None:
    connection.execute(
        "CREATE TABLE IF NOT EXISTS schema_migrations ("
        "version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)"
    )
    applied = {
        row[0]
        for row in connection.execute("SELECT version FROM schema_migrations")
    }
    for version, sql in MIGRATIONS:
        if version in applied:
            continue
        connection.executescript(sql)
        connection.execute(
            "INSERT INTO schema_migrations(version, applied_at) "
            "VALUES (?, strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))",
            (version,),
        )
