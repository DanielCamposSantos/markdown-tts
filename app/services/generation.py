from __future__ import annotations

from pathlib import Path
from typing import Callable, Protocol

from app.domain.models import GenerationProgress, GenerationResult
from app.markdown_parser import parse_markdown
from app.speech_plan import SpeechUnit, build_speech_plan


ProgressCallback = Callable[[GenerationProgress], None]
PlanCallback = Callable[[list[SpeechUnit]], None]


class TtsEngine(Protocol):
    def generate(
        self,
        units: list[SpeechUnit],
        output_file: Path,
        progress_callback: Callable[[str, int, int, str], None] | None = None,
    ) -> GenerationResult: ...


class GenerationService:
    def __init__(self, engine: TtsEngine) -> None:
        self._engine = engine

    def generate(
        self,
        markdown: str,
        output_file: Path,
        progress_callback: ProgressCallback | None = None,
        plan_callback: PlanCallback | None = None,
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

        return self._engine.generate(
            units=plan,
            output_file=output_file,
            progress_callback=report_progress,
        )


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
