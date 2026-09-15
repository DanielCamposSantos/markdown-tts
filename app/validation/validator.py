from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Literal

from app.validation.asr import AsrEngine, AsrResult
from app.validation.scoring import ScoreResult, score_transcription


ValidationStatus = Literal["pass", "warn", "fail"]
TECHNICAL_TOKENS = frozenset({"https", "tls", "syn", "syn-ack", "ack", "c++"})


@dataclass(frozen=True)
class ValidationConfig:
    pass_similarity: float = 0.90
    pass_coverage: float = 0.90
    fail_coverage: float = 0.45
    fail_similarity: float = 0.35
    truncation_fail_coverage: float = 0.90
    extra_fail_ratio: float = 0.50
    minimum_extra_tokens_for_fail: int = 3
    minimum_pass_confidence: float = 0.50


@dataclass(frozen=True)
class ValidationResult:
    status: ValidationStatus
    expected_text: str
    transcription: str
    normalized_expected: str
    normalized_transcription: str
    similarity_score: float
    token_coverage: float
    missing_tokens: tuple[str, ...]
    extra_tokens: tuple[str, ...]
    reasons: tuple[str, ...]
    backend: str
    model: str | None
    confidence: float | None
    processing_seconds: float | None

    def to_dict(self) -> dict:
        return asdict(self)


def classify_score(score: ScoreResult, config: ValidationConfig) -> tuple[ValidationStatus, tuple[str, ...]]:
    expected_tokens = score.normalized_expected.tokens
    has_technical = bool(set(expected_tokens) & TECHNICAL_TOKENS)
    extra_ratio = len(score.extra_tokens) / max(1, len(expected_tokens))
    reasons = []
    if score.empty_transcription:
        return "fail", ("empty_transcription",)
    if score.truncated:
        reasons.append("probable_truncation")
    if score.missing_tokens:
        reasons.append("missing_expected_tokens")
    if score.extra_tokens:
        reasons.append("extra_transcription_tokens")
    if (
        score.token_coverage >= config.pass_coverage
        and score.similarity_score >= config.pass_similarity
        and not score.missing_tokens
        and not score.extra_tokens
    ):
        return "pass", tuple(reasons)
    clearly_incompatible = (
        score.token_coverage < config.fail_coverage
        or score.similarity_score < config.fail_similarity
        or (score.truncated and score.token_coverage < config.truncation_fail_coverage)
        or (len(score.extra_tokens) >= config.minimum_extra_tokens_for_fail and extra_ratio >= config.extra_fail_ratio)
    )
    if clearly_incompatible and not has_technical:
        reasons.append("low_transcription_agreement")
        return "fail", tuple(dict.fromkeys(reasons))
    if clearly_incompatible and has_technical:
        reasons.append("technical_token_ambiguity")
    return "warn", tuple(dict.fromkeys(reasons or ["uncertain_transcription"]))


def validate_result(expected_text: str, asr_result: AsrResult, config: ValidationConfig | None = None) -> ValidationResult:
    current = config or ValidationConfig()
    score = score_transcription(expected_text, asr_result.text)
    status, reasons = classify_score(score, current)
    if (
        status == "pass"
        and asr_result.confidence is not None
        and asr_result.confidence < current.minimum_pass_confidence
    ):
        status, reasons = "warn", ("low_asr_confidence",)
    return ValidationResult(
        status=status,
        expected_text=expected_text,
        transcription=asr_result.text,
        normalized_expected=score.normalized_expected.text,
        normalized_transcription=score.normalized_transcription.text,
        similarity_score=score.similarity_score,
        token_coverage=score.token_coverage,
        missing_tokens=score.missing_tokens,
        extra_tokens=score.extra_tokens,
        reasons=reasons,
        backend=asr_result.backend,
        model=asr_result.model,
        confidence=asr_result.confidence,
        processing_seconds=asr_result.processing_seconds,
    )


def validate_acceptable_results(
    expected_texts: tuple[str, ...] | list[str],
    asr_result: AsrResult,
    config: ValidationConfig | None = None,
) -> ValidationResult:
    """Return the strongest normal validation among explicit equivalent forms."""
    unique = tuple(dict.fromkeys(expected_texts))
    if not unique:
        raise ValueError("At least one expected form is required")
    results = [validate_result(text, asr_result, config) for text in unique]
    rank = {"fail": 0, "warn": 1, "pass": 2}
    return max(
        results,
        key=lambda result: (
            rank[result.status], result.token_coverage, result.similarity_score,
            -len(result.missing_tokens), -len(result.extra_tokens),
        ),
    )


def validate_audio(expected_text: str, audio_path: Path, engine: AsrEngine, config: ValidationConfig | None = None) -> ValidationResult:
    return validate_result(expected_text, engine.transcribe(Path(audio_path), language="pt"), config)
