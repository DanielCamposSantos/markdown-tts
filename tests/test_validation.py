from pathlib import Path
import sys

import pytest

from app.validation.asr import ASR_ENABLED, AsrDisabledError, AsrResult, DisabledAsrEngine, FakeAsrEngine, detect_asr_capabilities
from app.validation.normalization import normalize_for_comparison
from app.validation.scoring import score_transcription
from app.validation.validator import validate_audio, validate_result


@pytest.mark.parametrize(
    "source,expected",
    [
        ("  OLÁ,   Mundo! ", ("olá", "mundo")),
        ("HTTPS usa TLS 1.3.", ("https", "usa", "tls", "1", "3")),
        ("SYN, SYN-ACK e ACK.", ("syn", "syn-ack", "e", "ack")),
        ("Código em C++.", ("código", "em", "c++")),
        ("ação AÇÃO", ("ação", "ação")),
        ("um [pause 0.35s] dois", ("um", "dois")),
    ],
)
def test_conservative_normalization_preserves_semantic_tokens(source, expected):
    normalized = normalize_for_comparison(source)
    assert normalized.tokens == expected
    assert normalized.text == " ".join(expected)


def test_scoring_exact_and_punctuation_only_difference():
    exact = score_transcription("Olá, mundo!", "Olá, mundo!")
    punctuation = score_transcription("Olá, mundo!", "olá mundo")
    assert exact.similarity_score == exact.token_coverage == 1
    assert punctuation.similarity_score == punctuation.token_coverage == 1
    assert not punctuation.missing_tokens and not punctuation.extra_tokens


def test_scoring_missing_extra_truncation_and_empty():
    missing = score_transcription("um dois três", "um três")
    extra = score_transcription("um dois", "um dois quatro")
    truncated = score_transcription("um dois três quatro", "um dois")
    empty = score_transcription("texto esperado", "")
    assert missing.missing_tokens == ("dois",)
    assert extra.extra_tokens == ("quatro",)
    assert truncated.truncated is True
    assert empty.empty_transcription is True and empty.token_coverage == 0


def test_validator_pass_warn_fail_and_reasons():
    passed = validate_result("A frase está correta.", AsrResult("a frase está correta", backend="fake"))
    warned = validate_result(
        "O HTTPS utiliza TLS para proteger a comunicação.",
        AsrResult("o agá tê tê pê esse utiliza tê ele esse para proteger comunicação", backend="fake"),
    )
    failed = validate_result("Um texto completamente diferente.", AsrResult("ruído sem relação", backend="fake"))
    assert passed.status == "pass"
    assert warned.status == "warn" and "technical_token_ambiguity" in warned.reasons
    assert failed.status == "fail" and "low_transcription_agreement" in failed.reasons


def test_validator_detects_empty_truncation_and_runaway_extras():
    empty = validate_result("Texto esperado.", AsrResult(""))
    truncated = validate_result("um dois três quatro cinco", AsrResult("um dois três"))
    runaway = validate_result(
        "De repente, a chama se apagou.",
        AsrResult("De repente, a chama se apagou mitat mitat seteum faum"),
    )
    assert empty.status == "fail" and empty.reasons == ("empty_transcription",)
    assert truncated.status == "fail" and "probable_truncation" in truncated.reasons
    assert runaway.status == "fail" and len(runaway.extra_tokens) == 4


def test_fake_engine_and_disabled_engine_are_lightweight(tmp_path):
    assert ASR_ENABLED is False
    audio = tmp_path / "unit.wav"
    fake = FakeAsrEngine({"unit.wav": AsrResult("Texto.", backend="fake", processing_seconds=0.2)})
    result = validate_audio("Texto.", audio, fake)
    assert result.status == "pass" and fake.calls == [(audio, "pt")]
    with pytest.raises(AsrDisabledError, match="desativada"):
        DisabledAsrEngine().transcribe(Path("unused.wav"))


def test_technical_syn_variation_is_not_automatic_failure():
    result = validate_result("SYN, SYN-ACK e ACK.", AsrResult("sin sin ack e ack"))
    assert result.status == "warn"


@pytest.mark.parametrize(
    "transcription,expected_status",
    [
        ("O HTTPS utiliza TLS para proteger a comunicação.", "pass"),
        ("o https utiliza tls para proteger a comunicação", "pass"),
        ("O HTTPS utiliza TLS para proteger comunicação", "warn"),
        ("O HTTPS utiliza", "warn"),
        ("O HTTPS utiliza TLS para proteger a comunicação palavras adicionais", "warn"),
        ("O agá tê tê pê esse utiliza tê ele esse para proteger a comunicação", "warn"),
    ],
)
def test_https_tls_variations_are_classified_conservatively(transcription, expected_status):
    expected = "O HTTPS utiliza TLS para proteger a comunicação."
    assert validate_result(expected, AsrResult(transcription)).status == expected_status


def test_low_backend_confidence_downgrades_otherwise_exact_match():
    result = validate_result("Texto exato.", AsrResult("texto exato", confidence=0.2))
    assert result.status == "warn" and result.reasons == ("low_asr_confidence",)


def test_capability_detection_does_not_import_optional_backends():
    capabilities = detect_asr_capabilities()
    assert {item.backend for item in capabilities} == {"faster-whisper", "whisper"}
    assert "faster_whisper" not in sys.modules
    assert "whisper" not in sys.modules
