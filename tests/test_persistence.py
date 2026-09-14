import json
import sqlite3
from dataclasses import replace
from pathlib import Path

import pytest

from app.domain.models import GenerationResult, TimelineEntry
from app.persistence.database import Database
from app.persistence.library import LibraryStore, file_sha256
from app.persistence.repositories import DocumentRecord, DocumentRepository
from app.speech_plan import build_speech_plan
from app.markdown_parser import parse_markdown


@pytest.fixture
def library(tmp_path):
    voice = tmp_path / "fake_voice.wav"
    voice.write_bytes(b"canonical fake voice")
    return LibraryStore(tmp_path / "library", voice)


def complete_fake(library, title="Redes", markdown="O HTTPS utiliza TLS."):
    document, generation, staging, final = library.create(title, markdown)
    library.mark_running(generation.generation_id)
    staging.parent.mkdir(parents=True, exist_ok=True)
    staging.write_bytes(b"fake mp3 bytes")
    units = build_speech_plan(parse_markdown(markdown))
    result = GenerationResult(
        staging,
        1.25,
        1.0,
        0.2,
        (TimelineEntry(1, "paragraph", markdown, 0.0, 1.0, 250),),
    )
    stored = library.complete(document, generation, staging, final, result, units)
    return document, stored, final


def test_database_migration_v1_is_idempotent_and_reopens(tmp_path):
    database = Database(tmp_path / "data" / "library.db")
    database.initialize()
    database.initialize()
    assert database.schema_version == 2
    reopened = Database(database.path)
    reopened.initialize()
    assert reopened.schema_version == 2
    with reopened.connect() as connection:
        assert connection.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM documents").fetchone()[0] == 0


def test_foreign_key_rejects_generation_without_document(library):
    with library.database.connect() as connection:
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "INSERT INTO generations "
                "(generation_id, document_id, status, created_at, updated_at, "
                "model_id, language, voice_reference_path, voice_sha256, markdown_hash) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                ("g", "missing", "queued", "now", "now", "m", "pt", "v", "h", "h"),
            )


def test_document_repository_create_get_and_list(tmp_path):
    database = Database(tmp_path / "library.db")
    database.initialize()
    repository = DocumentRepository(database)
    record = DocumentRecord("doc", "Título", "doc/document.md", "hash", "a", "a")
    repository.create(record)
    assert repository.get("doc") == record
    assert repository.list() == [record]


def test_library_persists_utf8_markdown_with_relative_paths(library):
    markdown = "# Redes\n\nSYN, SYN-ACK, ACK e comunicação."
    document, generation, _, _ = library.create("Redes", markdown)
    assert not Path(document.markdown_path).is_absolute()
    assert library.resolve(document.markdown_path).read_text(encoding="utf-8") == markdown
    assert document.content_hash == generation.markdown_hash
    assert library.documents.get(document.document_id) == document


def test_generation_transitions_and_failure_survive_restart(library):
    document, generation, _, _ = library.create("Falha", "Texto.")
    library.mark_running(generation.generation_id)
    library.mark_failed(generation.generation_id, "erro controlado")
    reopened = LibraryStore(library.root, library.voice_reference)
    stored = reopened.generations.get(generation.generation_id)
    assert stored.status == "failed"
    assert stored.error == "erro controlado"
    assert reopened.generations.list_for_document(document.document_id) == [stored]
    with pytest.raises(ValueError, match="Transição inválida"):
        reopened.generations.transition(generation.generation_id, "running", "later")


def test_completed_generation_metadata_round_trip_and_restart(library):
    document, generation, audio = complete_fake(library)
    reopened = LibraryStore(library.root, library.voice_reference)
    stored = reopened.generations.get(generation.generation_id)
    metadata = reopened.metadata(stored)
    assert stored.status == "completed"
    assert reopened.resolve(stored.audio_path).read_bytes() == b"fake mp3 bytes"
    assert metadata["schema_version"] == 1
    assert metadata["document_id"] == document.document_id
    assert metadata["timeline"][0]["text"] == "O HTTPS utiliza TLS."
    assert metadata["units"][0]["synthesis_text"] == "O HTTPS utiliza TLS."
    assert metadata["audio"]["mp3_path"] == stored.audio_path
    assert metadata["voice"]["sha256"] == file_sha256(library.voice_reference)
    assert str(library.root) not in json.dumps(metadata)
    assert audio.is_file()


def test_missing_audio_never_marks_generation_completed(library):
    _, generation, staging, final = library.create("Incompleto", "Texto.")
    library.mark_running(generation.generation_id)
    result = GenerationResult(staging, 0, 0, 0, ())
    with pytest.raises(RuntimeError, match="ausente"):
        library.complete(None, generation, staging, final, result, [])
    assert library.generations.get(generation.generation_id).status == "running"
    assert not final.exists()


def test_invalid_metadata_never_marks_generation_completed(library, monkeypatch):
    document, generation, staging, final = library.create("Inválido", "Texto.")
    library.mark_running(generation.generation_id)
    staging.parent.mkdir(parents=True, exist_ok=True)
    staging.write_bytes(b"audio")
    result = GenerationResult(staging, 1, 1, 1, ())

    def write_invalid(destination, text):
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text("{", encoding="utf-8")

    monkeypatch.setattr(library, "_atomic_text", write_invalid)
    with pytest.raises(json.JSONDecodeError):
        library.complete(document, generation, staging, final, result, [])
    assert library.generations.get(generation.generation_id).status == "running"


def test_atomic_text_failure_does_not_publish_destination(library, monkeypatch):
    destination = library.root / "doc" / "metadata.json"
    monkeypatch.setattr("os.replace", lambda source, target: (_ for _ in ()).throw(OSError("boom")))
    with pytest.raises(OSError, match="boom"):
        library._atomic_text(destination, "{}")
    assert not destination.exists()
    assert not list(destination.parent.glob("*.tmp"))


@pytest.mark.parametrize("unsafe", ["../secret", "..\\secret", "C:/secret", "/secret"])
def test_library_rejects_unsafe_paths(library, unsafe):
    with pytest.raises(ValueError):
        library.resolve(unsafe)


def test_voice_hash_is_cached(library, monkeypatch):
    first = library.voice_sha256()
    library.voice_reference.unlink()
    assert library.voice_sha256() == first
