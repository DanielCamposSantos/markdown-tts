from dataclasses import replace

import pytest

from app.pronunciation import (
    CONTROLLED_ACRONYMS, PT_BR_CANONICAL_LETTER_NAMES, PT_BR_ENTRIES, PT_BR_PROFILE, PT_BR_RESOLVER,
    PT_BR_TTS_LETTER_CUES, resolve_speech_plan,
)
from app.speech_plan import SpeechUnit
from app.validation.asr import AsrResult
from app.validation.validator import validate_acceptable_results


@pytest.mark.parametrize(
    "source,spoken",
    [
        ("C++", "cê mais mais"),
        ("HTTP", "agá tê tê pê"),
        ("HTTPS", "agá tê tê pê éssi"),
        ("TLS", "tê éli éssi"),
        ("TCP", "tê cê pê"),
        ("SYN", "éssi ípsilom êni"),
        ("SYN-ACK", "éssi ípsilom êni á cê cá"),
        ("ACK", "á cê cá"),
    ],
)
def test_controlled_pt_br_rules(source, spoken):
    result = PT_BR_RESOLVER.resolve(source)
    assert result.synthesis_text == spoken
    assert len(result.applied_rules) == 1


def test_specific_rules_boundaries_case_punctuation_and_multiple_occurrences():
    result = PT_BR_RESOLVER.resolve("(https), HTTP; SYN-ACK e syn. C++. HTTPServer")
    assert result.synthesis_text == (
        "(agá tê tê pê éssi), agá tê tê pê; "
        "éssi ípsilom êni á cê cá e éssi ípsilom êni. cê mais mais. HTTPServer"
    )
    assert [rule.rule_id for rule in result.applied_rules] == [
        "https", "http", "syn-ack", "syn", "cplusplus",
    ]
    assert "[pause" not in result.synthesis_text and "  " not in result.synthesis_text


def test_unregistered_uppercase_and_plain_text_are_unchanged():
    text = "NASA aparece em texto comum."
    result = PT_BR_RESOLVER.resolve(text)
    assert result.synthesis_text == text
    assert result.applied_rules == ()


def test_resolved_plan_preserves_canonical_speech_unit_structure():
    canonical = SpeechUnit(
        index=7, kind="paragraph", display_text="HTTPS utiliza TLS.",
        synthesis_text="HTTPS utiliza TLS.", source_atoms=("HTTPS", "TLS"),
        pause_after_ms=350, previous_id=6, next_id=8, section_id=2,
        paragraph_id=4, sentence_index=1,
    )
    resolved = resolve_speech_plan([canonical], PT_BR_RESOLVER)
    effective = resolved.units[0]
    assert canonical.synthesis_text == canonical.display_text == "HTTPS utiliza TLS."
    assert replace(effective, synthesis_text=canonical.synthesis_text) == canonical
    assert effective.synthesis_text == "agá tê tê pê éssi utiliza tê éli éssi."
    assert resolved.metadata["profile"] == PT_BR_PROFILE
    assert [rule["rule_id"] for rule in resolved.metadata["applied_units"]["7"]["rules"]] == ["https", "tls"]


def test_pronunciation_aware_asr_accepts_only_explicit_equivalent_forms():
    original = "HTTPS utiliza TLS."
    expanded = "agá tê tê pê éssi utiliza tê éli éssi."
    assert validate_acceptable_results([original, expanded], AsrResult(original)).status == "pass"
    assert validate_acceptable_results([original, expanded], AsrResult(expanded)).status == "pass"
    assert validate_acceptable_results([original, expanded], AsrResult("HTTP utiliza")).status != "pass"
    syn_effective = PT_BR_RESOLVER.resolve("SYN").synthesis_text
    assert validate_acceptable_results(["SYN", syn_effective], AsrResult("SYN")).status == "pass"
    assert validate_acceptable_results(["SYN", syn_effective], AsrResult(syn_effective)).status == "pass"


def test_tts_letter_cues_cover_alphabet_and_critical_phonetic_forms():
    assert PT_BR_PROFILE == "pt-BR-v3"
    assert set(PT_BR_TTS_LETTER_CUES) == set("ABCDEFGHIJKLMNOPQRSTUVWXYZ")
    assert {key: PT_BR_TTS_LETTER_CUES[key] for key in "LSNMRF"} == {
        "L": "éli", "S": "éssi", "N": "êni", "M": "êmi", "R": "érri", "F": "éfi",
    }
    assert PT_BR_CANONICAL_LETTER_NAMES["Y"] == "ípsilon"
    assert PT_BR_TTS_LETTER_CUES["Y"] == "ípsilom"


@pytest.mark.parametrize(
    "source,expected",
    [
        ("VPN", "vê pê êni"),
        ("CVE SIEM XSS", "cê vê é éssi i é êmi xis éssi éssi"),
        ("WPA2 WPA3", "dáblio pê á dois dáblio pê á três"),
        ("TCP/IP", "tê cê pê i pê"),
    ],
)
def test_cybersecurity_acronyms_use_controlled_spelling(source, expected):
    assert PT_BR_RESOLVER.resolve(source).synthesis_text == expected


def test_controlled_lexicon_is_valid_and_safe():
    assert len(CONTROLLED_ACRONYMS) == len(set(CONTROLLED_ACRONYMS))
    assert len(PT_BR_ENTRIES) == len(CONTROLLED_ACRONYMS) + 1
    for entry in PT_BR_ENTRIES:
        assert entry.mode in {"replace", "spell_letters"}
        assert entry.spoken and "  " not in entry.spoken and "[pause" not in entry.spoken
        if entry.mode == "spell_letters":
            assert all(char in PT_BR_TTS_LETTER_CUES or char in "23-/" for char in entry.token.upper())


def test_external_punctuation_case_and_unregistered_token_are_preserved():
    source = '(tls), vpn. "CVE" HTTPServer MyTLSConfig NASA'
    result = PT_BR_RESOLVER.resolve(source)
    assert result.original_text == source
    assert result.synthesis_text == (
        '(tê éli éssi), vê pê êni. "cê vê é" HTTPServer MyTLSConfig NASA'
    )


def test_compound_rules_do_not_create_punctuation_or_pauses():
    result = PT_BR_RESOLVER.resolve("SYN-ACK TCP/IP")
    assert result.synthesis_text == "éssi ípsilom êni á cê cá tê cê pê i pê"
    assert not any(mark in result.synthesis_text for mark in (",", "/", "-", "[pause"))
