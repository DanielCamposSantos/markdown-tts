from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from app.persistence.database import Database
from app.domain.models import PlaybackState


@dataclass(frozen=True)
class DocumentRecord:
    document_id: str
    title: str
    markdown_path: str
    content_hash: str
    created_at: str
    updated_at: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class GenerationRecord:
    generation_id: str
    document_id: str
    status: str
    created_at: str
    updated_at: str
    completed_at: str | None
    duration_seconds: float | None
    generation_seconds: float | None
    decode_seconds: float | None
    audio_path: str | None
    metadata_path: str | None
    model_id: str
    language: str
    voice_reference_path: str
    voice_sha256: str
    markdown_hash: str
    error: str | None
    artifact_revision: int = 0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _record(model, row):
    return None if row is None else model(**dict(row))


class DocumentRepository:
    def __init__(self, database: Database) -> None:
        self.database = database

    def create(self, record: DocumentRecord) -> None:
        with self.database.connect() as connection:
            connection.execute(
                "INSERT INTO documents VALUES (?, ?, ?, ?, ?, ?)",
                tuple(asdict(record).values()),
            )

    def get(self, document_id: str) -> DocumentRecord | None:
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT * FROM documents WHERE document_id = ?", (document_id,)
            ).fetchone()
        return _record(DocumentRecord, row)

    def list(self) -> list[DocumentRecord]:
        with self.database.connect() as connection:
            rows = connection.execute(
                "SELECT * FROM documents ORDER BY created_at DESC"
            ).fetchall()
        return [_record(DocumentRecord, row) for row in rows]


class GenerationRepository:
    def __init__(self, database: Database) -> None:
        self.database = database

    def create(self, record: GenerationRecord) -> None:
        with self.database.connect() as connection:
            connection.execute(
                "INSERT INTO generations (generation_id, document_id, status, "
                "created_at, updated_at, completed_at, duration_seconds, "
                "generation_seconds, decode_seconds, audio_path, metadata_path, "
                "model_id, language, voice_reference_path, voice_sha256, "
                "markdown_hash, error, artifact_revision) VALUES "
                "(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                tuple(asdict(record).values()),
            )

    def get(self, generation_id: str) -> GenerationRecord | None:
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT * FROM generations WHERE generation_id = ?",
                (generation_id,),
            ).fetchone()
        return _record(GenerationRecord, row)

    def list_for_document(self, document_id: str) -> list[GenerationRecord]:
        with self.database.connect() as connection:
            rows = connection.execute(
                "SELECT * FROM generations WHERE document_id = ? "
                "ORDER BY created_at DESC",
                (document_id,),
            ).fetchall()
        return [_record(GenerationRecord, row) for row in rows]

    def transition(self, generation_id: str, status: str, now: str, error=None) -> None:
        allowed = {
            "queued": {"running", "failed"},
            "running": {"completed", "failed"},
        }
        current = self.get(generation_id)
        if current is None:
            raise KeyError(generation_id)
        if status not in allowed.get(current.status, set()):
            raise ValueError(f"Transição inválida: {current.status} -> {status}")
        with self.database.connect() as connection:
            connection.execute(
                "UPDATE generations SET status = ?, updated_at = ?, error = ? "
                "WHERE generation_id = ?",
                (status, now, error, generation_id),
            )

    def complete(self, generation_id: str, now: str, result, audio_path: str, metadata_path: str) -> None:
        current = self.get(generation_id)
        if current is None or current.status != "running":
            raise ValueError("Somente geração running pode ser concluída")
        with self.database.connect() as connection:
            connection.execute(
                "UPDATE generations SET status = 'completed', updated_at = ?, "
                "completed_at = ?, duration_seconds = ?, generation_seconds = ?, "
                "decode_seconds = ?, audio_path = ?, metadata_path = ?, error = NULL, "
                "artifact_revision = CASE WHEN ? LIKE '%audio-r1.mp3' THEN 1 ELSE 0 END "
                "WHERE generation_id = ?",
                (now, now, result.duration_seconds, result.generation_seconds,
                 result.decode_seconds, audio_path, metadata_path, audio_path, generation_id),
            )


class PlaybackRepository:
    def __init__(self, database: Database) -> None:
        self.database = database

    def get(self, generation_id: str) -> PlaybackState | None:
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT * FROM playback_state WHERE generation_id = ?",
                (generation_id,),
            ).fetchone()
        return _record(PlaybackState, row)

    def get_or_default(self, generation_id: str) -> PlaybackState:
        state = self.get(generation_id)
        return state or PlaybackState(generation_id, 0.0, None, 1.0, None)

    def upsert(self, state: PlaybackState) -> PlaybackState:
        with self.database.connect() as connection:
            connection.execute(
                "INSERT INTO playback_state "
                "(generation_id, position_seconds, active_unit_id, playback_rate, updated_at) "
                "VALUES (?, ?, ?, ?, ?) "
                "ON CONFLICT(generation_id) DO UPDATE SET "
                "position_seconds = excluded.position_seconds, "
                "active_unit_id = excluded.active_unit_id, "
                "playback_rate = excluded.playback_rate, "
                "updated_at = excluded.updated_at",
                (
                    state.generation_id,
                    state.position_seconds,
                    state.active_unit_id,
                    state.playback_rate,
                    state.updated_at,
                ),
            )
        stored = self.get(state.generation_id)
        if stored is None:
            raise RuntimeError("PlaybackState não persistido")
        return stored
