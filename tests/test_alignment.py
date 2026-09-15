from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

import web
from app.alignment import ALIGNMENT_SCHEMA_VERSION, align_unit, build_global_alignment, rebase_alignment
from app.domain.models import GenerationResult, TimelineEntry
from app.markdown_parser import parse_markdown
from app.persistence import LibraryStore
from app.pronunciation import PT_BR_RESOLVER
from app.speech_plan import build_speech_plan
from app.validation.asr import AsrResult, AsrWord


def result(*items):
    words = tuple(AsrWord(text, start, end, 0.9) for text, start, end in items)
    return AsrResult(" ".join(word.text for word in words), words=words)


def test_asr_off_alignment_is_not_available(tmp_path):
    voice = tmp_path / "voice.wav"; voice.write_bytes(b"voice")
    library = LibraryStore(tmp_path / "library", voice)
    document, generation, staging, final = library.create("d", "Texto.")
    library.mark_running(generation.generation_id); staging.parent.mkdir(parents=True); staging.write_bytes(b"mp3")
    unit = build_speech_plan(parse_markdown("Texto."))[0]
    stored = library.complete(document, generation, staging, final, GenerationResult(staging, 1, 1, 1, (TimelineEntry(unit.index, unit.kind, unit.display_text, 0, 1, unit.pause_after_ms),)), [unit])
    assert library.metadata(stored)["alignment"] == {"schema_version": 1, "status": "not_available", "artifact": None}


def test_normal_words_punctuation_and_offsets():
    aligned = align_unit(1, "Olá, mundo!", result(("Olá", 0.1, 0.4), ("mundo", 0.5, 0.9)))
    assert aligned["status"] == "aligned"
    assert [(x["text"], x["display_start"], x["display_end"]) for x in aligned["tokens"]] == [("Olá", 0, 3), ("mundo", 5, 10)]


@pytest.mark.parametrize("text,expected_count", [("HTTPS", 5), ("TLS", 3), ("SYN", 3), ("SYN-ACK", 6), ("ACK", 3), ("C++", 3)])
def test_pronunciation_expansion_groups_spoken_words_into_display_token(text, expected_count):
    resolved = PT_BR_RESOLVER.resolve(text); spoken = resolved.synthesis_text.split()
    asr = result(*[(word, index * 0.1, (index + 1) * 0.1) for index, word in enumerate(spoken)])
    token = align_unit(7, text, asr, resolved.applied_rules)["tokens"][0]
    assert token["text"] == text and token["display_start"] == 0 and token["display_end"] == len(text)
    assert len(token["source_asr_word_indices"]) == expected_count
    assert token["start_seconds"] == 0 and token["end_seconds"] == pytest.approx(expected_count * 0.1)


def test_partial_alignment_does_not_invent_timestamp():
    aligned = align_unit(1, "Um termo ausente", result(("Um", 0, 0.2), ("termo", 0.3, 0.5)))
    assert aligned["status"] == "partial"
    assert aligned["tokens"][-1]["status"] == "unaligned" and aligned["tokens"][-1]["start_seconds"] is None


def test_global_timeline_uses_authoritative_offsets_and_leaves_pause_empty():
    units = build_speech_plan(parse_markdown("Um.\n\nDois."))
    pronunciation = {unit.index: PT_BR_RESOLVER.resolve(unit.synthesis_text) for unit in units}
    asr = {units[0].index: result(("Um", 0, 0.4)), units[1].index: result(("Dois", 0.1, 0.5))}
    timeline = (TimelineEntry(units[0].index, units[0].kind, units[0].display_text, 10, 10.4, 600), TimelineEntry(units[1].index, units[1].kind, units[1].display_text, 11, 11.5, 0))
    artifact = build_global_alignment(units, pronunciation, asr, timeline)
    assert artifact["schema_version"] == ALIGNMENT_SCHEMA_VERSION and artifact["status"] == "complete"
    assert artifact["units"][0]["tokens"][0]["end_seconds"] == 10.4
    assert artifact["units"][1]["tokens"][0]["start_seconds"] == 11.1


def test_rebase_regeneration_changes_target_and_shifts_later_without_retranscription():
    previous = {"units": [{"unit_id": 1, "status": "aligned", "tokens": [{"start_seconds": 0.0, "end_seconds": 0.5}]}, {"unit_id": 2, "status": "aligned", "tokens": [{"start_seconds": 1.0, "end_seconds": 1.4}]}]}
    old = [{"index": 1, "start_seconds": 0, "end_seconds": 0.5}, {"index": 2, "start_seconds": 1, "end_seconds": 1.4}]; new = [{"index": 1, "start_seconds": 0, "end_seconds": 1.5}, {"index": 2, "start_seconds": 2, "end_seconds": 2.4}]
    target = {"unit_id": 1, "status": "aligned", "tokens": [{"start_seconds": 0.1, "end_seconds": 1.5}]}
    rebased = rebase_alignment(previous, old, new, 1, target)
    assert rebased["units"][0]["tokens"][0]["end_seconds"] == 1.5
    assert rebased["units"][1]["tokens"][0]["start_seconds"] == 2.0


def test_alignment_artifact_endpoint_and_legacy(monkeypatch, tmp_path):
    voice = tmp_path / "voice.wav"; voice.write_bytes(b"voice"); library = LibraryStore(tmp_path / "library", voice)
    document, generation, staging, final = library.create("d", "Texto."); library.mark_running(generation.generation_id)
    staging.parent.mkdir(parents=True); staging.write_bytes(b"mp3"); unit = build_speech_plan(parse_markdown("Texto."))[0]
    alignment = {"schema_version": 1, "status": "complete", "units": [{"unit_id": unit.index, "status": "aligned", "tokens": []}]}
    stored = library.complete(document, generation, staging, final, GenerationResult(staging, 1, 1, 1, (TimelineEntry(unit.index, unit.kind, unit.display_text, 0, 1, 0),), alignment=alignment), [unit])
    monkeypatch.setattr(web, "library_store", library)
    response = web.generation_alignment(stored.generation_id)
    assert response == alignment and str(tmp_path) not in json.dumps(response)
    with pytest.raises(web.HTTPException): web.generation_alignment("missing")


def test_frontend_uses_binary_search_click_seek_and_phrase_fallback():
    javascript = open("static/app.js", encoding="utf-8").read()
    assert "/alignment" in javascript and "data-word-start" in javascript
    assert "while (low <= high)" in javascript and "seekTo(Number(word.dataset.wordStart))" in javascript
    assert "timelineIndexAt(currentTime)" in javascript
