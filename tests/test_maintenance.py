from __future__ import annotations

import hashlib
import json
import os
import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pytest
import soundfile as sf

from app.maintenance.backup import BackupService, RestoreService, verify_backup
from app.maintenance.retention import RetentionService
from app.maintenance.voice_integrity import VoiceIntegrityError, VoiceIntegrityService
from app.persistence.database import Database
from app.persistence.library import LibraryStore


NOW = datetime(2026, 1, 2, tzinfo=timezone.utc)


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def voice_fixture(tmp_path):
    voice = tmp_path / "voices" / "narrator_reference.wav"
    voice.parent.mkdir()
    sf.write(voice, np.zeros((480, 2), dtype=np.float32), 48000, subtype="PCM_16")
    manifest = voice.with_name("narrator_reference.manifest.json")
    manifest.write_text(json.dumps({"schema_version": 1, "sha256": sha(voice), "size_bytes": voice.stat().st_size, "format": "WAV", "sample_rate": 48000, "channels": 2}), encoding="utf-8")
    return voice, manifest


def test_voice_integrity_healthy_and_blocks_missing(tmp_path):
    voice, manifest = voice_fixture(tmp_path)
    service = VoiceIntegrityService(voice, manifest)
    assert service.check().status == "healthy"
    voice.unlink()
    assert service.check().status == "missing"
    with pytest.raises(VoiceIntegrityError, match="Geração bloqueada"):
        service.require_healthy()


def test_voice_integrity_detects_hash_and_audio_properties(tmp_path):
    voice, manifest = voice_fixture(tmp_path)
    voice.write_bytes(b"not a wav")
    assert VoiceIntegrityService(voice, manifest).check().status == "hash_mismatch"
    manifest_data = json.loads(manifest.read_text(encoding="utf-8"))
    manifest_data.update(sha256=sha(voice), size_bytes=voice.stat().st_size)
    manifest.write_text(json.dumps(manifest_data), encoding="utf-8")
    assert VoiceIntegrityService(voice, manifest).check().status == "invalid_audio"


def test_library_refuses_new_generation_before_writing(tmp_path):
    voice, manifest = voice_fixture(tmp_path)
    voice.unlink()
    library = LibraryStore(tmp_path / "library", voice, VoiceIntegrityService(voice, manifest))
    with pytest.raises(VoiceIntegrityError):
        library.create("x", "texto")
    assert library.documents.list() == []


def retention_library(tmp_path, status=None):
    root = tmp_path / "library"; Database(root / "library.db").initialize()
    generation = root / "doc" / "gen"; generation.mkdir(parents=True)
    staging = generation / ".audio.staging.mp3"; staging.write_bytes(b"x")
    old = NOW.timestamp() - 25 * 3600; os.utime(staging, (old, old))
    if status:
        with closing(sqlite3.connect(root / "library.db")) as db:
            db.execute("INSERT INTO documents VALUES ('doc','d','doc/document.md','h','x','x')")
            db.execute("INSERT INTO generations (generation_id,document_id,status,created_at,updated_at,model_id,language,voice_reference_path,voice_sha256,markdown_hash,artifact_revision) VALUES ('gen','doc','queued','x','x','m','p','v','h','h',0)")
            db.execute("INSERT INTO generation_jobs(job_id,generation_id,document_id,status,created_at,updated_at) VALUES ('job','gen','doc',?,'x','x')", (status,))
            db.commit()
    return root, staging


def test_retention_dry_run_and_apply(tmp_path):
    root, staging = retention_library(tmp_path)
    service = RetentionService(root, now=lambda: NOW)
    report = service.cleanup_safe()
    assert [x.path for x in report.candidates] == ["doc/gen/.audio.staging.mp3"]
    assert staging.exists() and not report.removed
    assert service.cleanup_safe(apply=True).removed == ("doc/gen/.audio.staging.mp3",)
    assert not staging.exists()


@pytest.mark.parametrize("status", ["queued", "running", "cancelling"])
def test_retention_preserves_active_jobs(tmp_path, status):
    root, staging = retention_library(tmp_path, status)
    assert RetentionService(root, now=lambda: NOW).scan().candidates == ()
    assert staging.exists()


def test_retention_preserves_recent_published_unknown_and_symlink(tmp_path):
    root, staging = retention_library(tmp_path)
    os.utime(staging, (NOW.timestamp(), NOW.timestamp()))
    (staging.parent / "audio-r1.mp3").write_bytes(b"published")
    (staging.parent / "unknown.bin").write_bytes(b"unknown")
    assert RetentionService(root, now=lambda: NOW).scan().candidates == ()


def backup_fixture(tmp_path):
    project = tmp_path / "project"; library = project / "library"; library.mkdir(parents=True)
    voice, voice_manifest = voice_fixture(project)
    for relative in ("app/pronunciation/pt_br.py", "app/persistence/migrations.py"):
        path = project / relative; path.parent.mkdir(parents=True, exist_ok=True); path.write_text("# recovery", encoding="utf-8")
    Database(library / "library.db").initialize()
    doc = library / "doc/document.md"; doc.parent.mkdir(); doc.write_text("conteúdo", encoding="utf-8")
    generation = library / "doc/gen"; generation.mkdir()
    audio = generation / "audio-r1.mp3"; audio.write_bytes(b"audio")
    unit = generation / "units-r1/000000.wav"; unit.parent.mkdir(); unit.write_bytes(b"wav")
    metadata = generation / "metadata.json"; metadata.write_text(json.dumps({"unit_artifacts": ["doc/gen/units-r1/000000.wav"]}), encoding="utf-8")
    with closing(sqlite3.connect(library / "library.db")) as db:
        db.execute("INSERT INTO documents VALUES ('doc','d','doc/document.md','h','x','x')")
        db.execute("INSERT INTO generations (generation_id,document_id,status,created_at,updated_at,audio_path,metadata_path,model_id,language,voice_reference_path,voice_sha256,markdown_hash,artifact_revision) VALUES ('gen','doc','completed','x','x','doc/gen/audio-r1.mp3','doc/gen/metadata.json','m','p','v','h','h',1)")
        db.commit()
    service = BackupService(project, library, project / "backups", voice, voice_manifest, now=lambda: NOW)
    return service, library, voice


def test_backup_is_consistent_and_excludes_unreferenced(tmp_path):
    service, library, _ = backup_fixture(tmp_path)
    (library / "orphan.bin").write_bytes(b"no")
    backup = service.create()
    result = verify_backup(backup)
    assert result.healthy and result.manifest["counts"]["documents"] == 1
    assert (backup / "library/doc/gen/units-r1/000000.wav").is_file()
    assert not (backup / "library/orphan.bin").exists()


def test_backup_detects_corruption_missing_file_and_unsafe_manifest(tmp_path):
    service, _, _ = backup_fixture(tmp_path); backup = service.create()
    target = backup / "library/doc/document.md"; target.write_text("tampered", encoding="utf-8")
    assert not verify_backup(backup).healthy
    target.unlink(); assert not verify_backup(backup).healthy
    manifest = json.loads((backup / "manifest.json").read_text(encoding="utf-8")); manifest["files"][0]["path"] = "../escape"
    (backup / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    assert not verify_backup(backup).healthy


def test_backup_failure_removes_staging(tmp_path):
    service, library, _ = backup_fixture(tmp_path)
    (library / "doc/document.md").unlink()
    with pytest.raises(FileNotFoundError): service.create()
    assert not list(service.backup_root.glob(".backup-*.tmp"))


def test_restore_roundtrip_and_voice_opt_in(tmp_path):
    service, library, voice = backup_fixture(tmp_path); backup = service.create()
    original_voice = voice.read_bytes(); (library / "doc/document.md").write_text("mudou", encoding="utf-8"); voice.write_bytes(b"different")
    RestoreService(service).restore(backup, skip_safety_backup=True)
    assert (library / "doc/document.md").read_text(encoding="utf-8") == "conteúdo"
    assert voice.read_bytes() == b"different"
    RestoreService(service).restore(backup, restore_voice=True, skip_safety_backup=True)
    assert voice.read_bytes() == original_voice


def test_restore_rejects_active_job_and_rolls_back(tmp_path):
    service, library, _ = backup_fixture(tmp_path); backup = service.create()
    with closing(sqlite3.connect(library / "library.db")) as db:
        db.execute("INSERT INTO generation_jobs(job_id,generation_id,document_id,status,created_at,updated_at) VALUES ('job','gen','doc','queued','x','x')")
        db.commit()
    with pytest.raises(RuntimeError, match="jobs ativos"): RestoreService(service).restore(backup, skip_safety_backup=True)
    with closing(sqlite3.connect(library / "library.db")) as db:
        db.execute("DELETE FROM generation_jobs"); db.commit()
    marker = library / "marker"; marker.write_text("current", encoding="utf-8")
    with pytest.raises(RuntimeError): RestoreService(service).restore(backup, skip_safety_backup=True, failure_hook=lambda: (_ for _ in ()).throw(RuntimeError("boom")))
    assert marker.read_text() == "current"
