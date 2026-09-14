import torch
import pytest

from app.audio_generation_guard import (
    AudioGenerationRunawayError,
    GenerationCancelled,
    RETRY_SEED_OFFSET,
    assess_generated_duration,
    count_generated_audio_frames,
    generate_with_runaway_guard,
)
from app.config import BASE_SEED, MAX_NEW_TOKENS
from app.speech_plan import SpeechUnit


def unit(text, *, kind="paragraph", index=7):
    return SpeechUnit(index, kind, text, text, (text,), 250)


def output_with_frames(frames, *, prefix=0, pad_id=0):
    ids = torch.full((prefix + frames + 2, 33), pad_id, dtype=torch.long)
    ids[:prefix + frames, 1:] = 1
    return [(prefix, ids)]


def test_counts_real_moss_layout_and_removes_reference_prefix():
    assert count_generated_audio_frames(output_with_frames(42, prefix=13), 0) == 42


def test_normal_short_sentence_passes():
    assessment = assess_generated_duration(
        unit("De repente, a chama se apagou."), 55
    )
    assert assessment.plausible
    assert assessment.estimated_seconds == 4.4


def test_same_short_sentence_with_runaway_fails():
    assessment = assess_generated_duration(
        unit("De repente, a chama se apagou."), 175
    )
    assert not assessment.plausible
    assert assessment.estimated_seconds == 14.0


def test_observed_7_12_second_runaway_is_suspicious():
    assessment = assess_generated_duration(
        unit("De repente, a chama se apagou."), 89
    )
    assert assessment.allowed_seconds == pytest.approx(6.3)
    assert assessment.estimated_seconds == pytest.approx(7.12)
    assert not assessment.plausible


def test_critical_sentence_frame_boundary_is_explicit():
    critical = unit("De repente, a chama se apagou.")
    accepted = assess_generated_duration(critical, 78)
    suspicious = assess_generated_duration(critical, 79)
    assert accepted.estimated_seconds == pytest.approx(6.24)
    assert accepted.plausible
    assert suspicious.estimated_seconds == pytest.approx(6.32)
    assert not suspicious.plausible


def test_long_paragraph_with_proportional_duration_passes():
    text = " ".join(f"palavra{i}" for i in range(100))
    assert assess_generated_duration(unit(text), 750).plausible


def test_short_heading_may_be_read_slowly():
    assert assess_generated_duration(unit("A chama", kind="heading"), 50).plausible


def test_explicit_list_pauses_expand_allowed_duration():
    item = unit("Primeiro. [pause 0.35s] Segundo. [pause 0.35s] Terceiro.", kind="list")
    assessment = assess_generated_duration(item, 60)
    assert assessment.plausible
    assert assessment.allowed_seconds == pytest.approx(5.2)


def test_technical_terms_have_proportional_margin():
    technical = unit("HTTPS utiliza TLS e SYN-ACK.")
    assessment = assess_generated_duration(technical, 65)
    assert assessment.allowed_seconds == pytest.approx(5.5)
    assert assessment.plausible


def test_short_unit_near_max_new_tokens_is_suspicious():
    assessment = assess_generated_duration(
        unit("Texto curto."), int(MAX_NEW_TOKENS * 0.95)
    )
    assert not assessment.plausible
    assert "safety ceiling" in assessment.reason


@pytest.mark.parametrize(
    "frames_by_attempt, expected_attempts",
    [([175, 55], 2), ([175, 180, 55], 3)],
)
def test_retries_only_suspicious_unit_with_deterministic_seeds(
    frames_by_attempt, expected_attempts
):
    seeds = []

    def generate(seed):
        seeds.append(seed)
        return output_with_frames(frames_by_attempt[len(seeds) - 1])

    result = generate_with_runaway_guard(
        unit("De repente, a chama se apagou."),
        generate,
        audio_pad_token_id=0,
        first_seed=BASE_SEED + 7,
        log=lambda message: None,
    )
    assert count_generated_audio_frames(result, 0) == 55
    assert seeds == [
        BASE_SEED + 7 + RETRY_SEED_OFFSET * attempt
        for attempt in range(expected_attempts)
    ]


def test_retry_exhaustion_raises_specific_error():
    with pytest.raises(AudioGenerationRunawayError, match="Unit 7"):
        generate_with_runaway_guard(
            unit("De repente, a chama se apagou."),
            lambda seed: output_with_frames(175),
            audio_pad_token_id=0,
            first_seed=BASE_SEED + 7,
            log=lambda message: None,
        )


def test_cancellation_wins_between_retries():
    cancelled = False
    attempts = 0

    def generate(seed):
        nonlocal cancelled, attempts
        attempts += 1
        cancelled = True
        return output_with_frames(175)

    with pytest.raises(GenerationCancelled):
        generate_with_runaway_guard(
            unit("De repente, a chama se apagou."),
            generate,
            audio_pad_token_id=0,
            first_seed=BASE_SEED + 7,
            should_cancel=lambda: cancelled,
            log=lambda message: None,
        )
    assert attempts == 1
