import csv
import json

import pytest

from app.validation.asr import AsrResult, FakeAsrEngine
from app.validation.benchmark import (
    BenchmarkCase,
    load_corpus,
    run_benchmark,
    write_csv_report,
    write_json_report,
)


def test_neutral_corpus_is_loadable_and_covers_required_categories():
    cases = load_corpus("benchmarks/corpus-pt-br.json")
    text = " ".join(case.expected_text for case in cases).lower()
    assert len(cases) >= 9
    assert all(case.duration_seconds > 0 for case in cases)
    for term in ("https", "tls", "syn-ack", "c++", "24"):
        assert term in text


def test_fake_benchmark_calculates_rtf_summary_and_writes_reports(tmp_path):
    cases = (
        BenchmarkCase("one", tmp_path / "one.wav", "Texto exato.", 2.0),
        BenchmarkCase("two", tmp_path / "two.wav", "Outro texto.", 4.0),
    )
    engine = FakeAsrEngine({
        "one.wav": AsrResult("Texto exato", backend="fake", model="fixture", processing_seconds=1.0),
        "two.wav": AsrResult("", backend="fake", model="fixture", processing_seconds=1.0),
    })
    report = run_benchmark(cases, engine, metrics=lambda: {"ram_mb": 12.5, "vram_mb": None})
    assert report.entries[0].real_time_factor == 0.5
    assert report.summary == {
        "cases": 2,
        "status_counts": {"pass": 1, "warn": 0, "fail": 1},
        "mean_rtf": 0.375,
        "median_rtf": 0.375,
        "total_audio_seconds": 6.0,
        "total_processing_seconds": 2.0,
        "total_rtf": 1 / 3,
    }
    assert report.entries[0].ram_mb == 12.5 and report.entries[0].vram_mb is None
    assert report.entries[0].transcription == "Texto exato"
    json_path, csv_path = tmp_path / "report.json", tmp_path / "report.csv"
    write_json_report(report, json_path)
    write_csv_report(report, csv_path)
    assert json.loads(json_path.read_text(encoding="utf-8"))["summary"]["cases"] == 2
    with csv_path.open(encoding="utf-8", newline="") as stream:
        assert len(list(csv.DictReader(stream))) == 2


def test_benchmark_invalid_duration_does_not_produce_invalid_rtf(tmp_path):
    case = BenchmarkCase("zero", tmp_path / "zero.wav", "Texto", 0.0)
    engine = FakeAsrEngine(default=AsrResult("Texto", processing_seconds=1.0, backend="fake"))
    assert run_benchmark([case], engine).entries[0].real_time_factor is None


def test_benchmark_measures_processing_when_backend_does_not_report_it(tmp_path):
    ticks = iter((10.0, 11.25))
    case = BenchmarkCase("measured", tmp_path / "audio.wav", "Texto", 2.5)
    engine = FakeAsrEngine(default=AsrResult("Texto", processing_seconds=None, backend="fake"))
    entry = run_benchmark([case], engine, clock=lambda: next(ticks)).entries[0]
    assert entry.processing_seconds == 1.25
    assert entry.real_time_factor == 0.5


def test_benchmark_records_backend_error_and_continues(tmp_path):
    def fail(_path, _language):
        raise RuntimeError("decoder failed")

    case = BenchmarkCase("broken", tmp_path / "audio.wav", "Texto", 2.0)
    report = run_benchmark([case], FakeAsrEngine(callback=fail), clock=iter((1.0, 1.5)).__next__)
    entry = report.entries[0]
    assert entry.status == "fail" and entry.reasons == ("backend_error",)
    assert entry.error == "RuntimeError: decoder failed"
