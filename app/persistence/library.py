from __future__ import annotations

import hashlib
import json
import os
import uuid
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

from app.config import (
    AUDIO_REPETITION_PENALTY, AUDIO_TEMPERATURE, AUDIO_TOP_K, AUDIO_TOP_P,
    BASE_SEED, LANGUAGE, MAX_NEW_TOKENS, MODEL_ID, MP3_BITRATE,
    REFERENCE_AUDIO, SAMPLE_RATE,
)
from app.persistence.database import Database
from app.audio_io import read_unit_wav
from app.persistence.repositories import (
    DocumentRecord,
    DocumentRepository,
    GenerationRecord,
    GenerationRepository,
    PlaybackRepository,
)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


class LibraryStore:
    def __init__(self, root: Path, voice_reference: Path = REFERENCE_AUDIO) -> None:
        self.root = Path(root).resolve()
        self.voice_reference = Path(voice_reference)
        self.database = Database(self.root / "library.db")
        self.database.initialize()
        self.documents = DocumentRepository(self.database)
        self.generations = GenerationRepository(self.database)
        self.playback = PlaybackRepository(self.database)
        self._voice_sha256: str | None = None

    def _relative(self, path: Path) -> str:
        resolved = path.resolve()
        try:
            relative = resolved.relative_to(self.root)
        except ValueError as exc:
            raise ValueError("Path fora da biblioteca") from exc
        return relative.as_posix()

    def resolve(self, relative_path: str) -> Path:
        path = Path(relative_path)
        if path.is_absolute():
            raise ValueError("Path absoluto não permitido")
        resolved = (self.root / path).resolve()
        try:
            resolved.relative_to(self.root)
        except ValueError as exc:
            raise ValueError("Path fora da biblioteca") from exc
        return resolved

    def _atomic_text(self, destination: Path, text: str) -> None:
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = destination.with_name(f".{destination.name}.{uuid.uuid4().hex}.tmp")
        try:
            temporary.write_text(text, encoding="utf-8")
            os.replace(temporary, destination)
        finally:
            temporary.unlink(missing_ok=True)

    def voice_sha256(self) -> str:
        if self._voice_sha256 is None:
            self._voice_sha256 = file_sha256(self.voice_reference)
        return self._voice_sha256

    def create(self, title: str, markdown: str) -> tuple[DocumentRecord, GenerationRecord, Path, Path]:
        document_id = uuid.uuid4().hex
        generation_id = uuid.uuid4().hex
        now = utc_now()
        content_hash = hashlib.sha256(markdown.encode("utf-8")).hexdigest()
        voice_sha256 = self.voice_sha256()
        document_path = self.root / document_id / "document.md"
        generation_dir = self.root / document_id / generation_id
        final_audio = generation_dir / "audio.mp3"
        staging_audio = generation_dir / ".audio.staging.mp3"
        self._atomic_text(document_path, markdown)
        document = DocumentRecord(document_id, title, self._relative(document_path), content_hash, now, now)
        generation = GenerationRecord(
            generation_id, document_id, "queued", now, now, None, None, None, None,
            None, None, MODEL_ID, LANGUAGE, "voices/narrator_reference.wav",
            voice_sha256, content_hash, None, 0,
        )
        try:
            with self.database.connect() as connection:
                connection.execute(
                    "INSERT INTO documents VALUES (?, ?, ?, ?, ?, ?)",
                    tuple(asdict(document).values()),
                )
                connection.execute(
                    "INSERT INTO generations (generation_id, document_id, status, "
                    "created_at, updated_at, completed_at, duration_seconds, "
                    "generation_seconds, decode_seconds, audio_path, metadata_path, "
                    "model_id, language, voice_reference_path, voice_sha256, "
                    "markdown_hash, error, artifact_revision) VALUES "
                    "(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    tuple(asdict(generation).values()),
                )
        except Exception:
            document_path.unlink(missing_ok=True)
            raise
        return document, generation, staging_audio, final_audio

    def mark_running(self, generation_id: str) -> None:
        self.generations.transition(generation_id, "running", utc_now())

    def mark_failed(self, generation_id: str, error: str) -> None:
        record = self.generations.get(generation_id)
        if record and record.status in {"queued", "running"}:
            self.generations.transition(
                generation_id, "failed", utc_now(), self.sanitize_error(error)
            )

    def sanitize_error(self, error: str) -> str:
        return str(error).replace(str(self.root), "<library>").replace(
            str(self.voice_reference), "voices/narrator_reference.wav"
        )[:1000]

    def complete(self, document, generation, staging_audio: Path, final_audio: Path, result, units) -> GenerationRecord:
        if not staging_audio.is_file() or staging_audio.stat().st_size == 0:
            raise RuntimeError("Áudio final ausente ou vazio")
        final_audio.parent.mkdir(parents=True, exist_ok=True)
        unit_staging = staging_audio.parent / ".units.staging"
        capable = unit_staging.is_dir()
        if capable:
            expected = [unit_staging / f"{unit.index:06d}.wav" for unit in units]
            if len(list(unit_staging.glob("*.wav"))) != len(units) or any(
                not path.is_file() or path.stat().st_size == 0 for path in expected
            ):
                raise RuntimeError("Artefatos de unidade incompletos")
            artifact_sample_rate = None
            for path in expected:
                audio, sample_rate = read_unit_wav(path)
                if audio.numel() == 0 or (
                    artifact_sample_rate is not None and sample_rate != artifact_sample_rate
                ):
                    raise RuntimeError("Artefato de unidade inválido")
                artifact_sample_rate = sample_rate
        units_dir = final_audio.parent / "units-r1"
        revision_audio = final_audio.parent / "audio-r1.mp3" if capable else final_audio
        if capable:
            os.replace(unit_staging, units_dir)
        os.replace(staging_audio, revision_audio)
        metadata_path = final_audio.parent / "metadata.json"
        now = utc_now()
        metadata = {
            "schema_version": 2,
            "document_id": document.document_id,
            "generation_id": generation.generation_id,
            "title": document.title,
            "status": "completed",
            "created_at": generation.created_at,
            "updated_at": now,
            "markdown_path": document.markdown_path,
            "artifact_revision": 1 if capable else 0,
            "individual_regeneration_available": capable,
            "audio": {"mp3_path": self._relative(revision_audio), "wav_path": None},
            "unit_artifacts": [self._relative(units_dir / f"{unit.index:06d}.wav") for unit in units] if capable else [],
            "model": {"id": MODEL_ID},
            "voice": {"reference_path": "voices/narrator_reference.wav", "sha256": generation.voice_sha256},
            "audio_format": {"sample_rate": SAMPLE_RATE, "mp3_bitrate": MP3_BITRATE},
            "duration_seconds": result.duration_seconds,
            "generation_seconds": result.generation_seconds,
            "decode_seconds": result.decode_seconds,
            "units": [asdict(unit) for unit in units],
            "timeline": [entry.to_dict() for entry in result.timeline],
            "generation_config": {
                "language": LANGUAGE,
                "max_new_tokens": MAX_NEW_TOKENS,
                "audio_temperature": AUDIO_TEMPERATURE,
                "audio_top_p": AUDIO_TOP_P,
                "audio_top_k": AUDIO_TOP_K,
                "audio_repetition_penalty": AUDIO_REPETITION_PENALTY,
                "base_seed": BASE_SEED,
                "direct_tts_per_unit": True,
            },
        }
        if result.asr_validation is not None:
            metadata["asr_validation"] = result.asr_validation
        self._atomic_text(metadata_path, json.dumps(metadata, ensure_ascii=False, indent=2))
        json.loads(metadata_path.read_text(encoding="utf-8"))
        self.generations.complete(
            generation.generation_id, now, result,
            self._relative(revision_audio), self._relative(metadata_path),
        )
        stored = self.generations.get(generation.generation_id)
        if stored is None:
            raise RuntimeError("Geração concluída não encontrada")
        return stored

    def metadata(self, record: GenerationRecord) -> dict:
        if not record.metadata_path:
            raise FileNotFoundError("Geração sem metadata")
        return json.loads(self.resolve(record.metadata_path).read_text(encoding="utf-8"))
