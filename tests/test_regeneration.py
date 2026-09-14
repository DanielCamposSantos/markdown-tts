import hashlib

import pytest
import torch
from fastapi import HTTPException

import app.services.regeneration as regeneration_module
import web
from app.audio_io import combine_audio, write_unit_wav
from app.domain.models import GenerationResult, PlaybackState, TimelineEntry
from app.jobs import JobRepository, JobWorker
from app.persistence import LibraryStore
from app.persistence.database import Database
from app.persistence.migrations import MIGRATIONS
from app.persistence.library import utc_now
from app.services.generation import GenerationService
from app.services.regeneration import MANUAL_REGENERATION_SEED_OFFSET, RegenerationService, merge_asr_metadata
from app.config import ASR_AUTO_REGENERATION_SEED_OFFSET, SAMPLE_RATE
from app.validation.asr import AsrResult, FakeAsrEngine
from app.validation.manager import AsrManager
from app.services.asr_validation import AsrValidationFailedError


class ArtifactEngine:
    def __init__(self, target_samples=4, error=None, before_generate=None):
        self.target_samples = target_samples
        self.error = error
        self.calls = []
        self.before_generate = before_generate

    def generate(self, units, output_file, progress_callback=None, should_cancel=None, unit_output_dir=None, seed_offset=0):
        self.calls.append(([unit.index for unit in units], seed_offset))
        if self.before_generate:
            self.before_generate()
        if should_cancel and should_cancel():
            from app.audio_generation_guard import GenerationCancelled
            raise GenerationCancelled("cancelled")
        if self.error:
            raise self.error
        decoded = []
        for unit in units:
            samples = self.target_samples if len(units) == 1 else unit.index + 1
            audio = torch.full((1, samples), float(unit.index))
            write_unit_wav(audio, SAMPLE_RATE, unit_output_dir / f"{unit.index:06d}.wav")
            decoded.append((unit.index, unit.kind, unit.display_text, unit.pause_after_ms, audio))
        master, timeline = combine_audio(decoded, SAMPLE_RATE)
        output_file.parent.mkdir(parents=True, exist_ok=True)
        output_file.write_bytes(b"mp3")
        return GenerationResult(output_file, master.shape[-1] / SAMPLE_RATE, 0.1, 0.1, tuple(timeline))

    def generate_units(self, units, progress_callback=None, should_cancel=None, unit_output_dir=None, seed_offset=0):
        self.calls.append(([unit.index for unit in units], seed_offset))
        if should_cancel and should_cancel():
            from app.audio_generation_guard import GenerationCancelled
            raise GenerationCancelled("cancelled")
        for unit in units:
            write_unit_wav(torch.full((1, self.target_samples), float(unit.index)), SAMPLE_RATE, unit_output_dir / f"{unit.index:06d}.wav")
        return type("Timing", (), {"generation_seconds": 0.1, "decode_seconds": 0.1})()

    def unload(self):
        pass


def capable_generation(tmp_path):
    voice = tmp_path / "voice.wav"; voice.write_bytes(b"voice")
    library = LibraryStore(tmp_path / "library", voice)
    document, generation, staging, final = library.create("Doc", "Um. Dois.")
    engine = ArtifactEngine()
    result, stored = GenerationService(engine).generate_persisted(
        "Um. Dois.", staging, final, document, generation, library
    )
    return library, document, stored, engine


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_new_generation_persists_ordered_nonempty_unit_artifacts(tmp_path):
    library, _, generation, _ = capable_generation(tmp_path)
    metadata = library.metadata(generation)
    assert generation.artifact_revision == 1
    assert metadata["individual_regeneration_available"] is True
    assert len(metadata["unit_artifacts"]) == 2
    assert [path.split("/")[-1] for path in metadata["unit_artifacts"]] == ["000001.wav", "000002.wav"]
    assert all(library.resolve(path).stat().st_size > 0 for path in metadata["unit_artifacts"])


def test_migration_v4_adds_revision_operation_and_history(tmp_path):
    voice = tmp_path / "voice.wav"; voice.write_bytes(b"voice")
    library = LibraryStore(tmp_path / "library", voice)
    assert library.database.schema_version == 5
    with library.database.connect() as connection:
        generation_columns = {row["name"] for row in connection.execute("PRAGMA table_info(generations)")}
        job_columns = {row["name"] for row in connection.execute("PRAGMA table_info(generation_jobs)")}
        history = connection.execute("SELECT name FROM sqlite_master WHERE name='unit_regenerations'").fetchone()
    assert "artifact_revision" in generation_columns
    assert {"operation", "unit_id"} <= job_columns
    assert history is not None


def test_migration_v3_to_v4_preserves_existing_jobs(tmp_path):
    path = tmp_path / "v3.db"
    database = Database(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with database.connect() as connection:
        connection.execute("CREATE TABLE schema_migrations (version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)")
        for version, sql in MIGRATIONS[:3]:
            connection.executescript(sql)
            connection.execute("INSERT INTO schema_migrations VALUES (?, 'now')", (version,))
        connection.execute("INSERT INTO documents VALUES ('d','Doc','d.md','hash','now','now')")
        connection.execute(
            "INSERT INTO generations (generation_id,document_id,status,created_at,updated_at,model_id,language,voice_reference_path,voice_sha256,markdown_hash) VALUES ('g','d','completed','now','now','m','Portuguese','voice.wav','v','hash')"
        )
        connection.execute(
            "INSERT INTO generation_jobs (job_id,generation_id,document_id,status,created_at,updated_at) VALUES ('j','g','d','completed','now','now')"
        )
    database.initialize()
    database.initialize()
    assert database.schema_version == 5
    with database.connect() as connection:
        row = connection.execute("SELECT * FROM generation_jobs WHERE job_id='j'").fetchone()
        assert row["operation"] == "generate" and row["generation_id"] == "g"


def test_regeneration_changes_only_target_reassembles_and_reconciles_playback(tmp_path, monkeypatch):
    library, _, generation, _ = capable_generation(tmp_path)
    old = library.metadata(generation)
    old_hashes = [digest(library.resolve(path)) for path in old["unit_artifacts"]]
    library.playback.upsert(PlaybackState(generation.generation_id, old["timeline"][1]["start_seconds"] + 0.01, 2, 1.5, utc_now()))
    job = JobRepository(library.database).enqueue_regeneration(generation.generation_id, generation.document_id, 1, 1)
    monkeypatch.setattr(regeneration_module, "export_mp3", lambda audio, rate, path: path.write_bytes(b"reassembled"))
    engine = ArtifactEngine(target_samples=20)
    revision = RegenerationService(engine).regenerate(library, generation.generation_id, 1, job.job_id)
    current = library.generations.get(generation.generation_id)
    metadata = library.metadata(current)
    assert revision == current.artifact_revision == 2
    assert engine.calls == [([1], MANUAL_REGENERATION_SEED_OFFSET)]
    assert digest(library.resolve(metadata["unit_artifacts"][1])) == old_hashes[1]
    assert digest(library.resolve(metadata["unit_artifacts"][0])) != old_hashes[0]
    assert metadata["timeline"][1]["start_seconds"] > old["timeline"][1]["start_seconds"]
    playback = library.playback.get(generation.generation_id)
    assert playback.active_unit_id == 2 and playback.playback_rate == 1.5
    assert playback.position_seconds > old["timeline"][1]["start_seconds"]
    assert library.resolve(current.audio_path).read_bytes() == b"reassembled"


def test_regeneration_failure_keeps_old_revision_authoritative(tmp_path, monkeypatch):
    library, _, generation, _ = capable_generation(tmp_path)
    old = library.generations.get(generation.generation_id)
    job = JobRepository(library.database).enqueue_regeneration(generation.generation_id, generation.document_id, 1, 1)
    engine = ArtifactEngine(error=RuntimeError("tts failed"))
    with pytest.raises(RuntimeError, match="tts failed"):
        RegenerationService(engine).regenerate(library, generation.generation_id, 1, job.job_id)
    assert library.generations.get(generation.generation_id) == old


def test_asr_validated_manual_regeneration_publishes_metadata(tmp_path, monkeypatch):
    library, _, generation, _ = capable_generation(tmp_path)
    job = JobRepository(library.database).enqueue_regeneration(generation.generation_id, generation.document_id, 1, 1)
    model = tmp_path / "model"; model.mkdir()
    manager = AsrManager(
        lambda: FakeAsrEngine(default=AsrResult("Um.", backend="fake")), model_path=model
    )
    monkeypatch.setattr(regeneration_module, "export_mp3", lambda audio, rate, path: path.write_bytes(b"new"))
    revision = RegenerationService(
        ArtifactEngine(), asr_manager=manager, asr_enabled=True,
    ).regenerate(library, generation.generation_id, 1, job.job_id)
    metadata = library.metadata(library.generations.get(generation.generation_id))
    assert revision == 2
    assert metadata["asr_validation"]["units"]["1"]["status"] == "pass"


def test_manual_regeneration_accepts_asr_warning(tmp_path, monkeypatch):
    library, _, generation, _ = capable_generation(tmp_path)
    job = JobRepository(library.database).enqueue_regeneration(generation.generation_id, generation.document_id, 1, 1)
    model = tmp_path / "model"; model.mkdir()
    manager = AsrManager(
        lambda: FakeAsrEngine(default=AsrResult("Um.", backend="fake", confidence=0.2)), model_path=model
    )
    monkeypatch.setattr(regeneration_module, "export_mp3", lambda audio, rate, path: path.write_bytes(b"new"))
    RegenerationService(
        ArtifactEngine(), asr_manager=manager, asr_enabled=True,
    ).regenerate(library, generation.generation_id, 1, job.job_id)
    metadata = library.metadata(library.generations.get(generation.generation_id))
    assert metadata["asr_validation"]["units"]["1"]["status"] == "warn"


def test_manual_regeneration_asr_fail_then_retry_resolves(tmp_path, monkeypatch):
    library, _, generation, _ = capable_generation(tmp_path)
    job = JobRepository(library.database).enqueue_regeneration(generation.generation_id, generation.document_id, 1, 1)
    model = tmp_path / "model"; model.mkdir()
    values = iter(("ruído", "Um."))
    backend = FakeAsrEngine(callback=lambda path, language: AsrResult(next(values), backend="fake"))
    manager = AsrManager(lambda: backend, model_path=model)
    monkeypatch.setattr(regeneration_module, "export_mp3", lambda audio, rate, path: path.write_bytes(b"new"))
    engine = ArtifactEngine()
    RegenerationService(
        engine, asr_manager=manager, asr_enabled=True,
    ).regenerate(library, generation.generation_id, 1, job.job_id)
    metadata = library.metadata(library.generations.get(generation.generation_id))
    assert engine.calls == [
        ([1], MANUAL_REGENERATION_SEED_OFFSET),
        ([1], ASR_AUTO_REGENERATION_SEED_OFFSET),
    ]
    assert metadata["asr_validation"]["units"]["1"]["auto_regeneration_rounds"] == 1


def test_persistent_asr_failure_rolls_back_manual_regeneration(tmp_path):
    library, _, generation, _ = capable_generation(tmp_path)
    old = library.generations.get(generation.generation_id)
    job = JobRepository(library.database).enqueue_regeneration(generation.generation_id, generation.document_id, 1, 1)
    model = tmp_path / "model"; model.mkdir()
    manager = AsrManager(
        lambda: FakeAsrEngine(default=AsrResult("ruído", backend="fake")), model_path=model
    )
    with pytest.raises(AsrValidationFailedError):
        RegenerationService(
            ArtifactEngine(), asr_manager=manager, asr_enabled=True,
        ).regenerate(library, generation.generation_id, 1, job.job_id)
    assert library.generations.get(generation.generation_id) == old


def test_manual_regeneration_merges_validation_with_previous_units():
    previous = {
        "enabled": True,
        "summary": {"pass": 1, "warn": 1, "fail": 0, "auto_regenerated_units": [2]},
        "units": {"1": {"status": "pass"}, "2": {"status": "warn"}},
    }
    current = {
        "enabled": True,
        "summary": {"pass": 1, "warn": 0, "fail": 0, "auto_regenerated_units": []},
        "units": {"2": {"status": "pass"}},
    }
    merged = merge_asr_metadata(previous, current)
    assert merged["summary"] == {"pass": 2, "warn": 0, "fail": 0, "auto_regenerated_units": [2]}


@pytest.mark.parametrize("failure_point", ["unit", "assemble", "export", "metadata", "commit"])
def test_regeneration_stage_failures_keep_old_pointers(tmp_path, monkeypatch, failure_point):
    library, _, generation, _ = capable_generation(tmp_path)
    old = library.generations.get(generation.generation_id)
    job = JobRepository(library.database).enqueue_regeneration(generation.generation_id, generation.document_id, 1, 1)
    engine = ArtifactEngine()
    if failure_point == "unit":
        engine.generate = lambda *args, **kwargs: None
    elif failure_point == "assemble":
        monkeypatch.setattr(regeneration_module, "combine_audio", lambda *args: (_ for _ in ()).throw(RuntimeError("assemble")))
    elif failure_point == "export":
        monkeypatch.setattr(regeneration_module, "export_mp3", lambda *args: (_ for _ in ()).throw(RuntimeError("export")))
    elif failure_point == "metadata":
        monkeypatch.setattr(regeneration_module.json, "dumps", lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("metadata")))
    else:
        monkeypatch.setattr(RegenerationService, "_commit", lambda *args: (_ for _ in ()).throw(RuntimeError("commit")))
        monkeypatch.setattr(regeneration_module, "export_mp3", lambda audio, rate, path: path.write_bytes(b"new"))
    with pytest.raises((RuntimeError, AttributeError)):
        RegenerationService(engine).regenerate(library, generation.generation_id, 1, job.job_id)
    assert library.generations.get(generation.generation_id) == old


def test_queued_regeneration_cancellation_preserves_completed_generation(monkeypatch, tmp_path):
    library, _, generation, _ = capable_generation(tmp_path)
    repository = JobRepository(library.database)
    job = repository.enqueue_regeneration(generation.generation_id, generation.document_id, 1, 1)
    monkeypatch.setattr(web, "library_store", library)
    class Worker:
        def __init__(self): self.library = library
        def wake(self): pass
    monkeypatch.setattr(web, "job_worker", Worker())
    web.cancel_job(job.job_id)
    assert repository.get(job.job_id).status == "cancelled"
    assert library.generations.get(generation.generation_id).status == "completed"


def test_running_regeneration_cancellation_keeps_old_revision(tmp_path):
    library, _, generation, _ = capable_generation(tmp_path)
    repository = JobRepository(library.database)
    job = repository.enqueue_regeneration(generation.generation_id, generation.document_id, 1, 1)
    old = library.generations.get(generation.generation_id)
    engine = ArtifactEngine(before_generate=lambda: repository.request_cancel(job.job_id))
    JobWorker(library, lambda: GenerationService(engine)).run_once()
    assert repository.get(job.job_id).status == "cancelled"
    assert library.generations.get(generation.generation_id) == old


def test_regeneration_uses_same_fifo_worker_and_history(tmp_path, monkeypatch):
    library, _, generation, _ = capable_generation(tmp_path)
    repository = JobRepository(library.database)
    job = repository.enqueue_regeneration(generation.generation_id, generation.document_id, 1, 1)
    monkeypatch.setattr(regeneration_module, "export_mp3", lambda audio, rate, path: path.write_bytes(b"new"))
    worker = JobWorker(library, lambda: GenerationService(ArtifactEngine(8)))
    assert worker.run_once()
    assert repository.get(job.job_id).status == "completed"
    with library.database.connect() as connection:
        history = connection.execute("SELECT * FROM unit_regenerations WHERE job_id=?", (job.job_id,)).fetchone()
    assert history["status"] == "completed"


def test_legacy_api_blocks_regeneration_without_extracting_mp3(monkeypatch, tmp_path):
    from tests.test_library_api import completed_library
    library, _, generation = completed_library(tmp_path)
    monkeypatch.setattr(web, "library_store", library)
    assert web.persisted_generation(generation.generation_id)["regeneration_available"] is False
    with pytest.raises(HTTPException) as error:
        web.regenerate_unit(generation.generation_id, 1)
    assert error.value.status_code == 409


def test_regeneration_api_validates_generation_and_unit(monkeypatch, tmp_path):
    library, _, generation, _ = capable_generation(tmp_path)
    monkeypatch.setattr(web, "library_store", library)
    class Worker:
        def __init__(self): self.library = library
        def wake(self): pass
    monkeypatch.setattr(web, "job_worker", Worker())
    response = web.regenerate_unit(generation.generation_id, 1)
    assert response["status"] == "queued"
    job = JobRepository(library.database).get(response["job_id"])
    assert job.operation == "regenerate_unit" and job.unit_id == 1
    assert web.regeneration_history(generation.generation_id)["regenerations"][0]["status"] == "queued"
    with pytest.raises(HTTPException) as duplicate:
        web.regenerate_unit(generation.generation_id, 2)
    assert duplicate.value.status_code == 409
    with pytest.raises(HTTPException) as missing_unit:
        web.regenerate_unit(generation.generation_id, 999)
    assert missing_unit.value.status_code == 404
    with pytest.raises(HTTPException) as missing_generation:
        web.regenerate_unit("missing", 1)
    assert missing_generation.value.status_code == 404
    with pytest.raises(HTTPException) as unsafe_unit:
        web.regenerate_unit(generation.generation_id, -1)
    assert unsafe_unit.value.status_code in {404, 409}
