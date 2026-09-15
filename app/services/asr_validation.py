from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Mapping

from app.audio_generation_guard import GenerationCancelled
from app.config import (
    ASR_AUTO_REGENERATION_SEED_OFFSET,
    ASR_BACKEND,
    ASR_COMPUTE_TYPE,
    ASR_LANGUAGE,
    ASR_MAX_AUTO_REGENERATION_ROUNDS,
)
from app.speech_plan import SpeechUnit
from app.validation.manager import AsrManager
from app.validation.validator import ValidationResult, validate_acceptable_results


class AsrValidationFailedError(RuntimeError):
    pass


@dataclass(frozen=True)
class AsrValidationOutcome:
    metadata: dict
    generation_seconds: float = 0.0
    decode_seconds: float = 0.0


def _cancel(should_cancel) -> None:
    if should_cancel and should_cancel():
        raise GenerationCancelled("Geração cancelada.")


def _result_metadata(result: ValidationResult, rounds: int) -> dict:
    return {
        "status": result.status,
        "similarity": result.similarity_score,
        "token_coverage": result.token_coverage,
        "missing_tokens": list(result.missing_tokens),
        "extra_tokens": list(result.extra_tokens),
        "reasons": list(result.reasons),
        "auto_regeneration_rounds": rounds,
    }


class AsrValidationCoordinator:
    def __init__(self, asr_manager: AsrManager) -> None:
        self.asr_manager = asr_manager

    def validate_and_correct(
        self,
        units: list[SpeechUnit],
        unit_dir: Path,
        tts_engine,
        *,
        progress_callback: Callable[[str, int, int, str], None] | None = None,
        should_cancel=None,
        base_units: Mapping[int, SpeechUnit] | None = None,
    ) -> AsrValidationOutcome:
        pending = list(units)
        latest: dict[int, ValidationResult] = {}
        rounds_by_unit = {unit.index: 0 for unit in units}
        regenerated: set[int] = set()
        generation_seconds = decode_seconds = 0.0
        try:
            for round_number in range(ASR_MAX_AUTO_REGENERATION_ROUNDS + 1):
                _cancel(should_cancel)
                tts_engine.unload()
                _cancel(should_cancel)
                if progress_callback:
                    progress_callback("asr_validation", 0, len(pending), "Carregando validação ASR...")
                self.asr_manager.load()
                failures = []
                for position, unit in enumerate(pending, start=1):
                    _cancel(should_cancel)
                    if progress_callback:
                        label = "Revalidando áudio" if round_number else "Validando áudio"
                        progress_callback("asr_validation", position, len(pending), f"{label} {position} de {len(pending)}")
                    expected_forms = [unit.synthesis_text]
                    base_unit = base_units.get(unit.index) if base_units else None
                    if base_unit is not None and base_unit.synthesis_text != unit.synthesis_text:
                        expected_forms.insert(0, base_unit.synthesis_text)
                    result = validate_acceptable_results(
                        expected_forms,
                        self.asr_manager.transcribe(unit_dir / f"{unit.index:06d}.wav", ASR_LANGUAGE),
                    )
                    latest[unit.index] = result
                    if result.status == "fail":
                        failures.append(unit)
                if not failures:
                    break
                if round_number >= ASR_MAX_AUTO_REGENERATION_ROUNDS:
                    ids = ", ".join(str(unit.index) for unit in failures)
                    raise AsrValidationFailedError(f"Validação ASR falhou após retries nas unidades: {ids}")
                _cancel(should_cancel)
                self.asr_manager.unload()
                _cancel(should_cancel)
                if progress_callback:
                    progress_callback("model_loading", 0, len(failures), f"Corrigindo {len(failures)} unidades com falha de validação")
                result = tts_engine.generate_units(
                    failures,
                    progress_callback=progress_callback,
                    should_cancel=should_cancel,
                    unit_output_dir=unit_dir,
                    seed_offset=ASR_AUTO_REGENERATION_SEED_OFFSET * (round_number + 1),
                )
                generation_seconds += result.generation_seconds
                decode_seconds += result.decode_seconds
                for unit in failures:
                    rounds_by_unit[unit.index] += 1
                    regenerated.add(unit.index)
                pending = failures
            counts = {status: sum(result.status == status for result in latest.values()) for status in ("pass", "warn", "fail")}
            return AsrValidationOutcome({
                "enabled": True,
                "backend": ASR_BACKEND,
                "model": "faster-whisper-medium",
                "compute_type": ASR_COMPUTE_TYPE,
                "language": ASR_LANGUAGE,
                "summary": {
                    **counts,
                    "auto_regenerated_units": sorted(regenerated),
                },
                "units": {str(unit_id): _result_metadata(result, rounds_by_unit[unit_id]) for unit_id, result in latest.items()},
            }, generation_seconds, decode_seconds)
        finally:
            self.asr_manager.unload()
            tts_engine.unload()
