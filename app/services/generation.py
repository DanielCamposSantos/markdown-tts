from __future__ import annotations

from pathlib import Path
from typing import Any, Callable, Protocol

from app.domain.models import GenerationProgress, GenerationResult
from app.audio_generation_guard import GenerationCancelled
from app.markdown_parser import parse_markdown
from app.speech_plan import SpeechUnit, build_speech_plan


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
    ) -> GenerationResult: ...


class GenerationStore(Protocol):
    def mark_running(self, generation_id: str) -> None: ...
    def mark_failed(self, generation_id: str, error: str) -> None: ...
    def complete(
        self, document: Any, generation: Any, staging_audio: Path,
        final_audio: Path, result: GenerationResult, units: list[SpeechUnit],
    ) -> Any: ...


class GenerationService:
    def __init__(self, engine: TtsEngine) -> None:
        self._engine = engine

    def generate(
        self,
        markdown: str,
        output_file: Path,
        progress_callback: ProgressCallback | None = None,
        plan_callback: PlanCallback | None = None,
        should_cancel: CancelCheck | None = None,
    ) -> GenerationResult:
        blocks = parse_markdown(markdown)
        plan = build_speech_plan(blocks)

        if not plan:
            raise RuntimeError(
                "O Markdown não contém texto narrável."
            )

        if plan_callback is not None:
            plan_callback(plan)

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

        arguments = {
            "units": plan,
            "output_file": output_file,
            "progress_callback": report_progress,
        }
        if should_cancel is not None:
            arguments["should_cancel"] = should_cancel
        return self._engine.generate(**arguments)

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
            result = self.generate(
                markdown,
                staging_file,
                progress_callback=progress_callback,
                plan_callback=capture_plan,
                should_cancel=should_cancel,
            )
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
            store.mark_failed(generation.generation_id, str(exc))
            raise


def progress_percentage(
    phase: str,
    current: int,
    total: int,
) -> float:
    total = max(total, 1)
    ratio = current / total

    if phase == "model":
        return 3.0
    if phase == "generation":
        return 5.0 + ratio * 72.0
    if phase == "decode":
        return 77.0 + ratio * 18.0
    if phase == "export":
        return 98.0
    return 0.0
