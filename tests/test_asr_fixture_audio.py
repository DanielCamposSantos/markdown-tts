import json

import pytest

from benchmarks.generate_asr_fixture_audio import ensure_destinations_available, load_fixture_cases
from benchmarks.run_asr_benchmark import load_good_cases


def test_fixture_cases_match_neutral_corpus_destinations():
    cases = load_fixture_cases()
    assert len(cases) == 9
    assert [case["index"] for case in cases] == list(range(1, 10))
    assert all(case["destination"].parent.name == "audio" for case in cases)
    assert all(case["destination"].suffix == ".wav" for case in cases)


def test_existing_fixture_requires_explicit_overwrite(tmp_path):
    destination = tmp_path / "existing.wav"
    destination.write_bytes(b"audio")
    cases = ({"destination": destination},)
    with pytest.raises(FileExistsError, match="--overwrite"):
        ensure_destinations_available(cases, overwrite=False)
    ensure_destinations_available(cases, overwrite=True)


def test_fixture_case_selection_can_preserve_original_indexes():
    selected = tuple(case for case in load_fixture_cases() if case["id"] in {"frase-longa", "punctuation"})
    assert [(case["id"], case["index"]) for case in selected] == [
        ("frase-longa", 2),
        ("punctuation", 8),
    ]


def test_benchmark_selects_only_human_approved_fixtures(tmp_path):
    corpus = tmp_path / "corpus.json"
    corpus.write_text(json.dumps({"cases": [
        {"id": "good", "audio_path": "good.wav", "expected_text": "Bom", "duration_seconds": 99},
        {"id": "blocked", "audio_path": "blocked.wav", "expected_text": "Sigla", "duration_seconds": 99},
    ]}), encoding="utf-8")
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({"fixtures": [
        {"case_id": "good", "review_status": "GOOD", "duration_seconds": 1.25},
        {"case_id": "blocked", "review_status": "BLOCKED_PRONUNCIATION", "duration_seconds": 2},
    ]}), encoding="utf-8")
    cases, excluded = load_good_cases(corpus, manifest)
    assert [(case.case_id, case.duration_seconds) for case in cases] == [("good", 1.25)]
    assert excluded == ({"case_id": "blocked", "review_status": "BLOCKED_PRONUNCIATION"},)
