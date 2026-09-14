from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Callable

from app.config import MAX_NEW_TOKENS
from app.speech_plan import SpeechUnit


AUDIO_FRAMES_PER_SECOND = 12.5
SECONDS_PER_WORD_LIMIT = 0.8
BASE_DURATION_MARGIN_SECONDS = 1.5
MINIMUM_ALLOWED_SECONDS = 4.5
HARD_CAP_FRACTION = 0.95
HARD_CAP_SHORT_UNIT_WORDS = 50
MAX_GENERATION_ATTEMPTS = 3
RETRY_SEED_OFFSET = 10_000
PAUSE_PATTERN = re.compile(r"\[pause\s+([0-9]+(?:\.[0-9]+)?)s\]", re.IGNORECASE)
WORD_PATTERN = re.compile(r"\b[\wÀ-ÿ]+(?:[-'][\wÀ-ÿ]+)*\b", re.UNICODE)


class AudioGenerationRunawayError(RuntimeError):
    pass


class GenerationCancelled(RuntimeError):
    pass


@dataclass(frozen=True)
class AudioGenerationAssessment:
    plausible: bool
    generated_frames: int
    estimated_seconds: float
    allowed_seconds: float
    reason: str | None = None


def count_generated_audio_frames(output: Any, audio_pad_token_id: int) -> int:
    total = 0
    for start_length, generation_ids in output:
        if generation_ids.ndim != 2 or generation_ids.shape[1] < 2:
            raise ValueError("Formato de output MOSS inesperado")
        audio_codes = generation_ids[:, 1:]
        non_pad = ~audio_codes.eq(int(audio_pad_token_id)).all(dim=1)
        encoded_frames = int(non_pad.sum().item())
        total += max(0, encoded_frames - int(start_length))
    return total


def explicit_pause_seconds(text: str) -> float:
    return sum(float(value) for value in PAUSE_PATTERN.findall(text))


def assess_generated_duration(
    unit: SpeechUnit,
    generated_frames: int,
    *,
    max_new_tokens: int = MAX_NEW_TOKENS,
) -> AudioGenerationAssessment:
    words = len(WORD_PATTERN.findall(PAUSE_PATTERN.sub(" ", unit.synthesis_text)))
    pauses = explicit_pause_seconds(unit.synthesis_text)
    estimated = generated_frames / AUDIO_FRAMES_PER_SECOND
    allowed = max(
        MINIMUM_ALLOWED_SECONDS,
        BASE_DURATION_MARGIN_SECONDS + words * SECONDS_PER_WORD_LIMIT,
    ) + pauses
    near_ceiling = (
        generated_frames >= max_new_tokens * HARD_CAP_FRACTION
        and words <= HARD_CAP_SHORT_UNIT_WORDS
    )
    too_long = estimated > allowed
    reason = None
    if near_ceiling:
        reason = "generated frames reached the safety ceiling"
    elif too_long:
        reason = "estimated duration exceeds the conservative unit limit"
    return AudioGenerationAssessment(
        plausible=not (near_ceiling or too_long),
        generated_frames=generated_frames,
        estimated_seconds=estimated,
        allowed_seconds=allowed,
        reason=reason,
    )


def generate_with_runaway_guard(
    unit: SpeechUnit,
    generate_attempt: Callable[[int], Any],
    audio_pad_token_id: int,
    first_seed: int,
    should_cancel: Callable[[], bool] | None = None,
    log: Callable[[str], None] = print,
) -> Any:
    last_assessment: AudioGenerationAssessment | None = None
    for attempt in range(MAX_GENERATION_ATTEMPTS):
        if should_cancel and should_cancel():
            raise GenerationCancelled("Geração cancelada.")
        seed = first_seed + RETRY_SEED_OFFSET * attempt
        output = generate_attempt(seed)
        frames = count_generated_audio_frames(output, audio_pad_token_id)
        assessment = assess_generated_duration(unit, frames)
        if assessment.plausible:
            return output
        last_assessment = assessment
        log(
            f"Unit {unit.index}: suspicious duration "
            f"{assessment.estimated_seconds:.1f}s (attempt {attempt + 1})"
        )
        if attempt + 1 < MAX_GENERATION_ATTEMPTS:
            log(
                f"Unit {unit.index}: retry {attempt + 1}/"
                f"{MAX_GENERATION_ATTEMPTS - 1} using alternate seed"
            )
    raise AudioGenerationRunawayError(
        f"Unit {unit.index} exceeded the generated-audio duration guard after "
        f"{MAX_GENERATION_ATTEMPTS} attempts "
        f"({last_assessment.estimated_seconds:.1f}s estimated)."
    )
