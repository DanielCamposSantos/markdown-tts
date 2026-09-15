from __future__ import annotations

from pathlib import Path
from dataclasses import replace
import inspect
import shutil
import tempfile
from typing import Any, Callable, Protocol

from app.domain.models import GenerationProgress, GenerationResult
from app.audio_generation_guard import GenerationCancelled
from app.markdown_parser import parse_markdown
from app.speech_plan import SpeechUnit, build_speech_plan
from app.progress import ENGINE_PHASES, phase_percentage
from app.audio_io import combine_audio, export_mp3, read_unit_wav
from app.config import ASR_VALIDATION_ENABLED
from app.validation.manager import AsrManager
from app.services.asr_validation import AsrValidationCoordinator
from app.pronunciation import PT_BR_RESOLVER, PronunciationResolver, resolve_speech_plan
from app.alignment import build_global_alignment


ProgressCallback = Callable[[GenerationProgress], None]
PlanCallback = Callable[[list[SpeechUnit]], None]
CancelCheck = Callable[[], bool]


class TtsEngine(Protocol):
    def generate(
        self,
        units: list[SpeechUnit],
        output_file: Path,
        progress_callback: Callable[[str, int, int, str], None] | None = None,
        should_cancel: CancelCheck | None = None,
        unit_output_dir: Path | None = None,
        seed_offset: int = 0,
    ) -> GenerationResult: ...
    def generate_units(self, units, **kwargs): ...
    def unload(self) -> None: ...


class GenerationStore(Protocol):
    def mark_running(self, generation_id: str) -> None: ...
    def mark_failed(self, generation_id: str, error: str) -> None: ...
    def complete(
        self, document: Any, generation: Any, staging_audio: Path,
        final_audio: Path, result: GenerationResult, units: list[SpeechUnit],
    ) -> Any: ...


class GenerationService:
    def __init__(
        self, engine: TtsEngine, *, asr_manager: AsrManager | None = None,
        asr_enabled: bool = ASR_VALIDATION_ENABLED,
        pronunciation_resolver: PronunciationResolver = PT_BR_RESOLVER,
    ) -> None:
        self._engine = engine
        self._asr_enabled = asr_enabled
        self._asr_manager = asr_manager
        self._pronunciation_resolver = pronunciation_resolver

    @property
    def engine(self) -> TtsEngine:
        return self._engine

    @property
    def asr_enabled(self) -> bool:
        return self._asr_enabled

    @property
    def asr_manager(self) -> AsrManager | None:
        return self._asr_manager

    def generate(
        self,
        markdown: str,
        output_file: Path,
        progress_callback: ProgressCallback | None = None,
        plan_callback: PlanCallback | None = None,
        should_cancel: CancelCheck | None = None,
        unit_output_dir: Path | None = None,
        seed_offset: int = 0,
    ) -> GenerationResult:
        blocks = parse_markdown(markdown)
        plan = build_speech_plan(blocks)

        if not plan:
            raise RuntimeError(
                "O Markdown não contém texto narrável."
            )

        if plan_callback is not None:
            plan_callback(plan)

        resolved = resolve_speech_plan(plan, self._pronunciation_resolver)

        def report_progress(
            phase: str,
            current: int,
            total: int,
            message: str,
        ) -> None:
            if progress_callback is not None:
                progress_callback(
                    GenerationProgress(
                        phase=phase,
                        current=current,
                        total=total,
                        message=message,
                        progress=progress_percentage(phase, current, total),
                    )
                )

        if self._asr_enabled:
            manager = self._asr_manager or AsrManager()
            manager.preflight()
            if unit_output_dir is None:
                with tempfile.TemporaryDirectory(prefix="markdown_tts_asr_") as temporary:
                    return self._generate_validated(
                        plan, resolved, output_file, Path(temporary), manager,
                        report_progress, should_cancel, seed_offset,
                    )
            return self._generate_validated(
                plan, resolved, output_file, unit_output_dir, manager,
                report_progress, should_cancel, seed_offset,
            )

        arguments = {
            "units": resolved.units,
            "output_file": output_file,
            "progress_callback": report_progress,
        }
        if should_cancel is not None:
            arguments["should_cancel"] = should_cancel
        if unit_output_dir is not None:
            arguments["unit_output_dir"] = unit_output_dir
        if seed_offset:
            arguments["seed_offset"] = seed_offset
        return replace(self._engine.generate(**arguments), pronunciation=resolved.metadata)

    def _generate_validated(
        self, plan, resolved, output_file, unit_output_dir, manager, report_progress,
        should_cancel, seed_offset,
    ) -> GenerationResult:
        unit_output_dir.mkdir(parents=True, exist_ok=True)
        initial = self._engine.generate_units(
            resolved.units,
            progress_callback=report_progress,
            should_cancel=should_cancel,
            unit_output_dir=unit_output_dir,
            seed_offset=seed_offset,
        )
        outcome = AsrValidationCoordinator(manager).validate_and_correct(
            resolved.units, unit_output_dir, self._engine,
            progress_callback=report_progress,
            should_cancel=should_cancel,
            base_units={unit.index: unit for unit in plan},
        )
        if should_cancel and should_cancel():
            raise GenerationCancelled("Geração cancelada.")
        report_progress("assemble", len(plan), len(plan), "Montando áudio...")
        decoded = []
        sample_rate = None
        for unit in resolved.units:
            audio, rate = read_unit_wav(unit_output_dir / f"{unit.index:06d}.wav")
            if sample_rate is not None and rate != sample_rate:
                raise RuntimeError("Sample rates incompatíveis")
            sample_rate = rate
            decoded.append((unit.index, unit.kind, unit.display_text, unit.pause_after_ms, audio))
        master, timeline = combine_audio(decoded, int(sample_rate))
        if should_cancel and should_cancel():
            raise GenerationCancelled("Geração cancelada.")
        report_progress("export", len(plan), len(plan), "Criando MP3...")
        export_mp3(master, int(sample_rate), output_file)
        return GenerationResult(
            output_file.resolve(), master.shape[-1] / int(sample_rate),
            initial.generation_seconds + outcome.generation_seconds,
            initial.decode_seconds + outcome.decode_seconds,
            tuple(timeline), outcome.metadata, resolved.metadata,
            build_global_alignment(plan, resolved.results, outcome.asr_results or {}, timeline),
        )

    def generate_persisted(
        self,
        markdown: str,
        staging_file: Path,
        final_file: Path,
        document: Any,
        generation: Any,
        store: GenerationStore,
        progress_callback: ProgressCallback | None = None,
        plan_callback: PlanCallback | None = None,
        should_cancel: CancelCheck | None = None,
    ) -> tuple[GenerationResult, Any]:
        units: list[SpeechUnit] = []

        def capture_plan(plan: list[SpeechUnit]) -> None:
            units.extend(plan)
            if plan_callback is not None:
                plan_callback(plan)

        store.mark_running(generation.generation_id)
        try:
            unit_staging = staging_file.parent / ".units.staging"
            supports_artifacts = self._asr_enabled or any(
                parameter.kind is inspect.Parameter.VAR_KEYWORD or name == "unit_output_dir"
                for name, parameter in inspect.signature(self._engine.generate).parameters.items()
            )
            result = self.generate(
                markdown,
                staging_file,
                progress_callback=progress_callback,
                plan_callback=capture_plan,
                should_cancel=should_cancel,
                unit_output_dir=unit_staging if supports_artifacts else None,
            )
            if progress_callback is not None:
                progress_callback(GenerationProgress(
                    phase="publish", current=len(units), total=len(units),
                    message="Salvando geração...",
                    progress=progress_percentage("publish", len(units), len(units)),
                ))
            stored = store.complete(
                document,
                generation,
                staging_file,
                final_file,
                result,
                units,
            )
            return result, stored
        except Exception as exc:
            staging_file.unlink(missing_ok=True)
            shutil.rmtree(staging_file.parent / ".units.staging", ignore_errors=True)
            store.mark_failed(generation.generation_id, str(exc))
            raise


def progress_percentage(
    phase: str,
    current: int,
    total: int,
) -> float:
    canonical = ENGINE_PHASES.get(phase)
    return phase_percentage(canonical, current, total)[0] if canonical else 0.0
