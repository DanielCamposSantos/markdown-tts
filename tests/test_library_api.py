from pathlib import Path

import pytest
from fastapi import HTTPException

import web
from app.domain.models import GenerationResult, TimelineEntry
from app.markdown_parser import parse_markdown
from app.persistence import LibraryStore
from app.speech_plan import build_speech_plan


def completed_library(tmp_path):
    voice = tmp_path / "voice.wav"
    voice.write_bytes(b"voice")
    library = LibraryStore(tmp_path / "library", voice)
    document, generation, staging, final = library.create("Redes", "Texto.")
    library.mark_running(generation.generation_id)
    staging.parent.mkdir(parents=True, exist_ok=True)
    staging.write_bytes(b"audio")
    result = GenerationResult(
        staging, 1.0, 0.7, 0.2,
        (TimelineEntry(1, "paragraph", "Texto.", 0.0, 0.75, 250),),
    )
    stored = library.complete(
        document, generation, staging, final, result,
        build_speech_plan(parse_markdown("Texto.")),
    )
    return library, document, stored


def test_empty_library_api(monkeypatch, tmp_path):
    voice = tmp_path / "voice.wav"
    voice.write_bytes(b"voice")
    monkeypatch.setattr(web, "library_store", LibraryStore(tmp_path / "library", voice))
    assert web.list_library() == {"documents": []}


def test_library_document_and_generation_apis_survive_reopen(monkeypatch, tmp_path):
    library, document, generation = completed_library(tmp_path)
    reopened = LibraryStore(library.root, library.voice_reference)
    monkeypatch.setattr(web, "library_store", reopened)
    listing = web.list_library()
    detail = web.library_document(document.document_id)
    persisted = web.persisted_generation(generation.generation_id)
    assert listing["documents"][0]["document_id"] == document.document_id
    assert detail["markdown"] == "Texto."
    assert detail["generations"][0]["status"] == "completed"
    assert persisted["metadata"]["timeline"][0]["end_seconds"] == 0.75
    assert "/audio?revision=" in persisted["audio_url"]


def test_library_apis_return_404_for_unknown_ids(monkeypatch, tmp_path):
    library, _, _ = completed_library(tmp_path)
    monkeypatch.setattr(web, "library_store", library)
    with pytest.raises(HTTPException) as document_error:
        web.library_document("missing")
    with pytest.raises(HTTPException) as generation_error:
        web.persisted_generation("missing")
    assert document_error.value.status_code == 404
    assert generation_error.value.status_code == 404


def test_persisted_audio_and_download_resolve_repository_path(monkeypatch, tmp_path):
    library, document, generation = completed_library(tmp_path)
    monkeypatch.setattr(web, "library_store", library)
    audio = web.persisted_audio(generation.generation_id)
    download = web.persisted_download(generation.generation_id)
    assert Path(audio.path) == library.resolve(generation.audio_path)
    assert download.filename == f"{document.title}.mp3"


def test_adulterated_audio_path_cannot_escape_library(monkeypatch, tmp_path):
    library, _, generation = completed_library(tmp_path)
    with library.database.connect() as connection:
        connection.execute(
            "UPDATE generations SET audio_path = ? WHERE generation_id = ?",
            ("../secret.mp3", generation.generation_id),
        )
    monkeypatch.setattr(web, "library_store", library)
    with pytest.raises(HTTPException) as error:
        web.persisted_audio(generation.generation_id)
    assert error.value.status_code == 404
