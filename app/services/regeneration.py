from __future__ import annotations

import json
import os
import shutil
from typing import Callable

from app.audio_generation_guard import GenerationCancelled
from app.audio_io import combine_audio, export_mp3, read_unit_wav
from app.persistence.library import LibraryStore, utc_now
from app.playback import active_unit_at, unit_id
from app.speech_plan import SpeechUnit
from app.config import ASR_VALIDATION_ENABLED
from app.services.asr_validation import AsrValidationCoordinator
from app.validation.manager import AsrManager
from app.pronunciation import PT_BR_RESOLVER, PronunciationResolver, resolve_speech_plan


MANUAL_REGENERATION_SEED_OFFSET = 100_000


class RegenerationUnavailableError(RuntimeError):
    pass


def merge_asr_metadata(previous: dict | None, current: dict) -> dict:
    if not previous or not previous.get("enabled"):
        return current
    merged = dict(current)
    units = {**previous.get("units", {}), **current.get("units", {})}
    regenerated = set(previous.get("summary", {}).get("auto_regenerated_units", []))
    regenerated.update(current.get("summary", {}).get("auto_regenerated_units", []))
    counts = {
        status: sum(item.get("status") == status for item in units.values())
        for status in ("pass", "warn", "fail")
    }
    merged["units"] = units
    merged["summary"] = {**counts, "auto_regenerated_units": sorted(regenerated)}
    return merged


class RegenerationService:
    def __init__(
        self, engine, *, asr_manager: AsrManager | None = None,
        asr_enabled: bool = ASR_VALIDATION_ENABLED,
        pronunciation_resolver: PronunciationResolver = PT_BR_RESOLVER,
    ) -> None:
        self.engine = engine
        self.asr_manager = asr_manager
        self.asr_enabled = asr_enabled
        self.pronunciation_resolver = pronunciation_resolver

    def regenerate(
        self,
        library: LibraryStore,
        generation_id: str,
        unit_id_value: int,
        job_id: str,
        progress_callback=None,
        should_cancel: Callable[[], bool] | None = None,
    ) -> int:
        library.require_voice_integrity()
        generation = library.generations.get(generation_id)
        if generation is None:
            raise KeyError(generation_id)
        metadata = library.metadata(generation)
        artifacts = metadata.get("unit_artifacts") or []
        units_data = metadata.get("units") or []
        if not metadata.get("individual_regeneration_available") or len(artifacts) != len(units_data):
            raise RegenerationUnavailableError(
                "Esta geração foi criada antes do suporte a regeneração individual."
            )
        by_id = {int(item["index"]): item for item in units_data}
        if unit_id_value not in by_id:
            raise KeyError(unit_id_value)
        old_revision = int(generation.artifact_revision or metadata.get("artifact_revision", 0))
        new_revision = old_revision + 1
        unit = SpeechUnit(**by_id[unit_id_value])
        resolved = resolve_speech_plan([unit], self.pronunciation_resolver)
        effective_unit = resolved.units[0]
        generation_dir = library.resolve(generation.metadata_path).parent
        staging = generation_dir / f".regeneration-{job_id}"
        unit_stage = staging / "units"
        one_unit_mp3 = staging / "unit-preview.mp3"
        final_unit = generation_dir / "units" / f"{unit_id_value:06d}-r{new_revision}.wav"
        final_audio = generation_dir / f"audio-r{new_revision}.mp3"
        final_metadata = generation_dir / f"metadata-r{new_revision}.json"
        try:
            if should_cancel and should_cancel():
                raise GenerationCancelled("Regeneração cancelada.")
            def engine_progress(phase, current, total, message):
                if progress_callback is not None and phase not in {"assemble", "export"}:
                    progress_callback(phase, current, total, message)

            asr_metadata = None
            if self.asr_enabled:
                manager = self.asr_manager or AsrManager()
                manager.preflight()
                self.engine.generate_units(
                    [effective_unit],
                    progress_callback=engine_progress if progress_callback is not None else None,
                    should_cancel=should_cancel,
                    unit_output_dir=unit_stage,
                    seed_offset=(new_revision - 1) * MANUAL_REGENERATION_SEED_OFFSET,
                )
                outcome = AsrValidationCoordinator(manager).validate_and_correct(
                    [effective_unit], unit_stage, self.engine,
                    progress_callback=engine_progress if progress_callback is not None else None,
                    should_cancel=should_cancel,
                    base_units={unit.index: unit},
                )
                asr_metadata = outcome.metadata
            else:
                self.engine.generate(
                    [effective_unit], one_unit_mp3,
                    progress_callback=engine_progress if progress_callback is not None else None,
                    should_cancel=should_cancel,
                    unit_output_dir=unit_stage,
                    seed_offset=(new_revision - 1) * MANUAL_REGENERATION_SEED_OFFSET,
                )
            new_unit_stage = unit_stage / f"{unit_id_value:06d}.wav"
            if not new_unit_stage.is_file() or new_unit_stage.stat().st_size == 0:
                raise RuntimeError("Novo artefato de unidade ausente")
            revised_artifacts = list(artifacts)
            target_index = [int(item["index"]) for item in units_data].index(unit_id_value)
            revised_artifacts[target_index] = library._relative(final_unit)

            if progress_callback is not None:
                progress_callback("assemble", 1, 1, "Remontando áudio...")
            decoded = []
            sample_rate = None
            for index, (unit_data, artifact) in enumerate(zip(units_data, revised_artifacts)):
                source = new_unit_stage if index == target_index else library.resolve(artifact)
                audio, rate = read_unit_wav(source)
                if sample_rate is not None and rate != sample_rate:
                    raise RuntimeError("Sample rates incompatíveis")
                sample_rate = rate
                decoded.append((
                    int(unit_data["index"]), unit_data["kind"], unit_data["display_text"],
                    int(unit_data["pause_after_ms"]), audio,
                ))
            master, timeline = combine_audio(decoded, int(sample_rate))
            staged_audio = staging / final_audio.name
            if progress_callback is not None:
                progress_callback("export", 1, 1, "Exportando MP3...")
            export_mp3(master, int(sample_rate), staged_audio)
            revised = dict(metadata)
            revised.update({
                "artifact_revision": new_revision,
                "updated_at": utc_now(),
                "duration_seconds": master.shape[-1] / int(sample_rate),
                "audio": {**metadata["audio"], "mp3_path": library._relative(final_audio)},
                "unit_artifacts": revised_artifacts,
                "timeline": [entry.to_dict() for entry in timeline],
            })
            if asr_metadata is not None:
                revised["asr_validation"] = merge_asr_metadata(metadata.get("asr_validation"), asr_metadata)
            previous_pronunciation = metadata.get("pronunciation") or {}
            applied_units = dict(previous_pronunciation.get("applied_units", {}))
            applied_units.pop(str(unit_id_value), None)
            applied_units.update(resolved.metadata["applied_units"])
            revised["pronunciation"] = {
                "profile": resolved.metadata["profile"],
                "applied_units": applied_units,
            }
            staged_metadata = staging / final_metadata.name
            staged_metadata.write_text(json.dumps(revised, ensure_ascii=False, indent=2), encoding="utf-8")
            json.loads(staged_metadata.read_text(encoding="utf-8"))
            if should_cancel and should_cancel():
                raise GenerationCancelled("Regeneração cancelada.")
            final_unit.parent.mkdir(parents=True, exist_ok=True)
            os.replace(new_unit_stage, final_unit)
            os.replace(staged_audio, final_audio)
            os.replace(staged_metadata, final_metadata)
            if progress_callback is not None:
                progress_callback("publish", 1, 1, f"Publicando revisão {new_revision}...")
            self._commit(library, generation, metadata, revised, unit_id_value, job_id, old_revision, new_revision)
            return new_revision
        finally:
            shutil.rmtree(staging, ignore_errors=True)

    def _commit(self, library, generation, old_metadata, new_metadata, unit, job_id, old_revision, new_revision):
        now = utc_now()
        playback = library.playback.get(generation.generation_id)
        new_position = None
        new_active = None
        if playback is not None:
            old_timeline = old_metadata.get("timeline", [])
            new_timeline = new_metadata.get("timeline", [])
            active = playback.active_unit_id or active_unit_at(old_timeline, playback.position_seconds)
            old_entry = next((x for x in old_timeline if unit_id(x) == active), None)
            new_entry = next((x for x in new_timeline if unit_id(x) == active), None)
            if new_entry:
                offset = max(0.0, playback.position_seconds - float(old_entry["start_seconds"])) if old_entry else 0.0
                length = float(new_entry["end_seconds"]) - float(new_entry["start_seconds"])
                new_position = float(new_entry["start_seconds"]) + min(offset, max(0.0, length))
                new_active = active
        with library.database.connect() as connection:
            connection.execute(
                "UPDATE generations SET artifact_revision=?, audio_path=?, metadata_path=?, duration_seconds=?, updated_at=? WHERE generation_id=? AND artifact_revision=?",
                (new_revision, new_metadata["audio"]["mp3_path"], library._relative(library.resolve(new_metadata["audio"]["mp3_path"]).parent / f"metadata-r{new_revision}.json"), new_metadata["duration_seconds"], now, generation.generation_id, old_revision),
            )
            if connection.total_changes != 1:
                raise RuntimeError("Revisão da geração mudou durante regeneração")
            connection.execute(
                "UPDATE unit_regenerations SET status='completed', completed_at=? WHERE job_id=?",
                (now, job_id),
            )
            if playback is not None and new_position is not None:
                connection.execute(
                    "UPDATE playback_state SET position_seconds=?, active_unit_id=?, updated_at=? WHERE generation_id=?",
                    (new_position, new_active, now, generation.generation_id),
                )
