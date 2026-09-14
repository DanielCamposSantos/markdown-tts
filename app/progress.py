from __future__ import annotations

import math
import re
import statistics
import time
from enum import Enum
from typing import Callable

from app.domain.models import GenerationProgress


ETA_MINIMUM_COMPLETED_UNITS = 3
ETA_RATE_WINDOW = 5
ETA_CONSERVATIVE_MARGIN = 1.15
PAUSE_WORK_PER_SECOND = 2.5
MINIMUM_UNIT_WORK = 1.0
MAXIMUM_UNIT_WORK = 10_000.0
_PAUSE_PATTERN = re.compile(r"\[pause\s+([0-9]+(?:\.[0-9]+)?)s\]", re.IGNORECASE)
_WORD_PATTERN = re.compile(r"\b[\wÀ-ÿ]+(?:[-'][\wÀ-ÿ]+)*\b", re.UNICODE)


def estimate_unit_work(unit) -> float:
    """Estimate acoustic work from synthesis text without model/tokenizer access."""
    text = str(getattr(unit, "synthesis_text", "") or "")
    pause_values = []
    for raw in _PAUSE_PATTERN.findall(text):
        value = float(raw)
        if math.isfinite(value) and value > 0:
            pause_values.append(value)
    spoken = _PAUSE_PATTERN.sub(" ", text)
    word_count = len(_WORD_PATTERN.findall(spoken))
    fallback = len("".join(spoken.split())) / 6.0 if word_count == 0 else 0.0
    work = word_count + sum(pause_values) * PAUSE_WORK_PER_SECOND
    return min(MAXIMUM_UNIT_WORK, max(MINIMUM_UNIT_WORK, fallback, work))


class ProgressPhase(str, Enum):
    QUEUED = "queued"
    PREPARING = "preparing"
    MODEL_LOADING = "model_loading"
    GENERATION = "generation"
    DECODE = "decode"
    ASR_VALIDATION = "asr_validation"
    ASSEMBLE = "assemble"
    EXPORT = "export"
    PUBLISH = "publish"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLING = "cancelling"
    CANCELLED = "cancelled"
    INTERRUPTED = "interrupted"


PHASE_RANGES: dict[ProgressPhase, tuple[float, float]] = {
    ProgressPhase.QUEUED: (0.0, 0.0),
    ProgressPhase.PREPARING: (1.0, 4.0),
    ProgressPhase.MODEL_LOADING: (4.0, 12.0),
    ProgressPhase.GENERATION: (12.0, 68.0),
    ProgressPhase.DECODE: (68.0, 80.0),
    ProgressPhase.ASR_VALIDATION: (80.0, 90.0),
    ProgressPhase.ASSEMBLE: (90.0, 93.0),
    ProgressPhase.EXPORT: (93.0, 97.0),
    ProgressPhase.PUBLISH: (97.0, 99.0),
    ProgressPhase.COMPLETED: (100.0, 100.0),
    ProgressPhase.FAILED: (0.0, 99.0),
    ProgressPhase.CANCELLING: (0.0, 99.0),
    ProgressPhase.CANCELLED: (0.0, 99.0),
    ProgressPhase.INTERRUPTED: (0.0, 99.0),
}

ENGINE_PHASES = {
    "model": ProgressPhase.MODEL_LOADING,
    "model_loading": ProgressPhase.MODEL_LOADING,
    "generation": ProgressPhase.GENERATION,
    "decode": ProgressPhase.DECODE,
    "asr_validation": ProgressPhase.ASR_VALIDATION,
    "assemble": ProgressPhase.ASSEMBLE,
    "export": ProgressPhase.EXPORT,
    "publish": ProgressPhase.PUBLISH,
}


def phase_percentage(phase: ProgressPhase, current: int = 0, total: int = 0) -> tuple[float, float]:
    start, end = PHASE_RANGES[phase]
    if phase is ProgressPhase.GENERATION:
        completed = max(0, min(total, current - 1))
        ratio = completed / max(total, 1)
    elif phase in {ProgressPhase.DECODE, ProgressPhase.ASR_VALIDATION}:
        completed = max(0, min(total, current - 1))
        ratio = completed / max(total, 1)
    elif phase in {ProgressPhase.ASSEMBLE, ProgressPhase.EXPORT, ProgressPhase.PUBLISH}:
        ratio = 0.0
    else:
        ratio = 1.0 if start == end else 0.0
    return start + (end - start) * ratio, ratio * 100.0


class ProgressTracker:
    """Converts real pipeline callbacks into monotonic, persisted telemetry."""

    def __init__(
        self,
        emit: Callable[[GenerationProgress], None],
        *,
        operation: str = "generate",
        unit_id: int | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._emit = emit
        self._operation = operation
        self._unit_id = unit_id
        self._clock = clock
        self._started = clock()
        self._last_progress = 0.0
        self._generation_boundary: float | None = None
        self._unit_rates: list[float] = []
        self._unit_work: list[float] = []
        self._recorded_completed = 0
        self._model_loading_expected = True

    def expect_model_loading(self, expected: bool) -> None:
        self._model_loading_expected = expected

    def set_units(self, units) -> None:
        self._unit_work = [estimate_unit_work(unit) for unit in units]

    def report(self, phase: str | ProgressPhase, current: int = 0, total: int = 0, message: str = "") -> GenerationProgress:
        if isinstance(phase, ProgressPhase):
            canonical = phase
        else:
            canonical = ENGINE_PHASES.get(phase)
            if canonical is None:
                canonical = ProgressPhase(phase)
        if canonical is ProgressPhase.MODEL_LOADING and not self._model_loading_expected:
            return GenerationProgress(
                phase=ProgressPhase.PREPARING.value, current=current, total=total,
                message=message, progress=self._last_progress,
            )
        now = self._clock()
        progress, phase_progress = phase_percentage(canonical, current, total)
        progress = max(self._last_progress, progress)
        self._last_progress = progress
        eta = self._eta(canonical, current, total, now)
        if self._operation == "generate" and canonical is ProgressPhase.GENERATION:
            message = f"Gerando unidade {current} de {total}"
        if self._operation == "regenerate_unit":
            message = self._regeneration_message(canonical, message)
            current = 1 if canonical in {ProgressPhase.GENERATION, ProgressPhase.DECODE} else current
            total = 1 if canonical in {ProgressPhase.GENERATION, ProgressPhase.DECODE} else total
        event = GenerationProgress(
            phase=canonical.value,
            current=current,
            total=total,
            message=message,
            progress=progress,
            eta_seconds=eta,
            elapsed_seconds=max(0.0, now - self._started),
            phase_progress=phase_progress,
        )
        self._emit(event)
        return event

    def _eta(self, phase: ProgressPhase, current: int, total: int, now: float) -> float | None:
        if phase is not ProgressPhase.GENERATION or self._operation != "generate":
            return None
        if self._generation_boundary is None:
            self._generation_boundary = now
            return None
        completed = max(0, current - 1)
        if completed > self._recorded_completed:
            duration = max(0.0, now - self._generation_boundary)
            work = self._unit_work[completed - 1] if completed <= len(self._unit_work) else 0.0
            rate = duration / work if work > 0 else 0.0
            if math.isfinite(rate) and rate > 0:
                self._unit_rates.append(rate)
            self._recorded_completed = completed
            self._generation_boundary = now
        if completed < ETA_MINIMUM_COMPLETED_UNITS or len(self._unit_rates) < ETA_MINIMUM_COMPLETED_UNITS:
            return None
        remaining_work = sum(self._unit_work[completed:total])
        if not math.isfinite(remaining_work) or remaining_work <= 0:
            return None
        recent = self._unit_rates[-ETA_RATE_WINDOW:]
        eta = statistics.median(recent) * remaining_work * ETA_CONSERVATIVE_MARGIN
        return eta if math.isfinite(eta) and eta >= 0 else None

    def _regeneration_message(self, phase: ProgressPhase, fallback: str) -> str:
        unit = self._unit_id
        return {
            ProgressPhase.PREPARING: f"Preparando regeneração da unidade {unit}...",
            ProgressPhase.MODEL_LOADING: "Carregando modelo...",
            ProgressPhase.GENERATION: f"Regenerando unidade {unit}",
            ProgressPhase.DECODE: f"Decodificando unidade {unit}...",
            ProgressPhase.ASSEMBLE: "Remontando áudio...",
            ProgressPhase.EXPORT: "Exportando MP3...",
            ProgressPhase.PUBLISH: "Publicando nova revisão...",
        }.get(phase, fallback)
