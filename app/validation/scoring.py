from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from difflib import SequenceMatcher

from app.validation.normalization import NormalizedText, normalize_for_comparison


@dataclass(frozen=True)
class ScoreResult:
    normalized_expected: NormalizedText
    normalized_transcription: NormalizedText
    similarity_score: float
    token_coverage: float
    missing_tokens: tuple[str, ...]
    extra_tokens: tuple[str, ...]
    truncated: bool
    empty_transcription: bool


def _counter_difference(left: Counter, right: Counter) -> tuple[str, ...]:
    return tuple(token for token in sorted(left) for _ in range(max(0, left[token] - right[token])))


def score_transcription(expected: str, transcription: str) -> ScoreResult:
    normalized_expected = normalize_for_comparison(expected)
    normalized_transcription = normalize_for_comparison(transcription)
    expected_counts = Counter(normalized_expected.tokens)
    actual_counts = Counter(normalized_transcription.tokens)
    expected_total = sum(expected_counts.values())
    matched = sum(min(count, actual_counts[token]) for token, count in expected_counts.items())
    coverage = matched / expected_total if expected_total else 1.0
    similarity = SequenceMatcher(
        None, normalized_expected.text, normalized_transcription.text, autojunk=False,
    ).ratio()
    actual_tokens = normalized_transcription.tokens
    expected_tokens = normalized_expected.tokens
    prefix_match = bool(actual_tokens) and expected_tokens[:len(actual_tokens)] == actual_tokens
    truncated = prefix_match and len(actual_tokens) < len(expected_tokens)
    return ScoreResult(
        normalized_expected=normalized_expected,
        normalized_transcription=normalized_transcription,
        similarity_score=similarity,
        token_coverage=coverage,
        missing_tokens=_counter_difference(expected_counts, actual_counts),
        extra_tokens=_counter_difference(actual_counts, expected_counts),
        truncated=truncated,
        empty_transcription=bool(expected_tokens) and not actual_tokens,
    )
