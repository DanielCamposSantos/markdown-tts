from pathlib import Path

import pytest

from app.domain.models import GenerationResult
from app.markdown_parser import parse_markdown
from app.services.generation import GenerationService
from app.speech_plan import build_speech_plan
from app.audio_io import write_unit_wav
from app.config import SAMPLE_RATE
from app.validation.asr import AsrResult, FakeAsrEngine
from app.validation.manager import AsrManager
import app.services.generation as generation_module
import torch


class FakeEngine:
    def __init__(self, result=None, error=None):
        self.result = result
        self.error = error
        self.units = None
        self.output_file = None

    def generate(self, units, output_file, progress_callback=None):
        self.units = units
        self.output_file = output_file
        if progress_callback:
            progress_callback("generation", 1, len(units), "Gerando")
        if self.error:
            raise self.error
        return self.result


def make_result(output_file: Path) -> GenerationResult:
    return GenerationResult(output_file, 1.0, 0.8, 0.1, ())


def test_generation_service_builds_plan_and_passes_output_and_progress(tmp_path):
    output_file = tmp_path / "resultado.mp3"
    engine = FakeEngine(make_result(output_file))
    service = GenerationService(engine)
    progress = []
    planned = []

    result = service.generate(
        "# Titulo\n\nPrimeira. Segunda.",
        output_file,
        progress_callback=progress.append,
        plan_callback=lambda units: planned.extend(units),
    )

    expected = build_speech_plan(
        parse_markdown("# Titulo\n\nPrimeira. Segunda.")
    )
    assert engine.units == expected
    assert planned == expected
    assert engine.units is not planned
    assert engine.output_file == output_file
    assert result is engine.result
    assert progress[0].phase == "generation"
    assert progress[0].current == 1
    assert progress[0].total == 3
    assert progress[0].progress == 12.0


def test_generation_service_rejects_markdown_without_narratable_text(tmp_path):
    engine = FakeEngine()

    with pytest.raises(RuntimeError, match="texto narrável"):
        GenerationService(engine).generate("<div></div>", tmp_path / "x.mp3")

    assert engine.units is None


def test_generation_service_propagates_engine_error(tmp_path):
    failure = RuntimeError("engine failure")
    engine = FakeEngine(error=failure)

    with pytest.raises(RuntimeError, match="engine failure"):
        GenerationService(engine).generate("Texto.", tmp_path / "x.mp3")


def test_persisted_generation_marks_failure_and_removes_staging(tmp_path):
    failure = RuntimeError("engine failure")
    engine = FakeEngine(error=failure)
    events = []

    class FakeStore:
        def mark_running(self, generation_id):
            events.append(("running", generation_id))

        def mark_failed(self, generation_id, error):
            events.append(("failed", generation_id, error))

        def complete(self, *args):
            raise AssertionError("must not complete")

    class Generation:
        generation_id = "generation-id"

    staging = tmp_path / "staging.mp3"
    staging.write_bytes(b"partial")
    with pytest.raises(RuntimeError, match="engine failure"):
        GenerationService(engine).generate_persisted(
            "Texto.", staging, tmp_path / "audio.mp3",
            object(), Generation(), FakeStore(),
        )
    assert events == [
        ("running", "generation-id"),
        ("failed", "generation-id", "engine failure"),
    ]
    assert not staging.exists()


class StagedEngine:
    def __init__(self):
        self.batches = []
        self.unloads = 0

    def generate_units(self, units, *, unit_output_dir, seed_offset=0, **kwargs):
        self.batches.append(([unit.index for unit in units], seed_offset))
        for unit in units:
            write_unit_wav(torch.zeros((1, 48)), SAMPLE_RATE, unit_output_dir / f"{unit.index:06d}.wav")
        return type("Timing", (), {"generation_seconds": 0.2, "decode_seconds": 0.1})()

    def unload(self):
        self.unloads += 1


def test_enabled_pipeline_validates_before_assembly_and_records_metadata(tmp_path, monkeypatch):
    model = tmp_path / "model"
    model.mkdir()
    transcripts = {"000001.wav": "Um.", "000002.wav": "Dois."}
    asr = FakeAsrEngine(callback=lambda path, language: AsrResult(transcripts[path.name], backend="fake"))
    manager = AsrManager(lambda: asr, model_path=model)
    engine = StagedEngine()
    monkeypatch.setattr(generation_module, "export_mp3", lambda audio, rate, path: path.write_bytes(b"mp3"))
    events = []
    result = GenerationService(engine, asr_manager=manager, asr_enabled=True).generate(
        "Um. Dois.", tmp_path / "audio.mp3", progress_callback=events.append,
        unit_output_dir=tmp_path / "units",
    )
    assert engine.batches == [([1, 2], 0)]
    assert result.asr_validation["summary"] == {
        "pass": 2, "warn": 0, "fail": 0, "auto_regenerated_units": [],
    }
    assert [event.phase for event in events][-3:] == ["asr_validation", "assemble", "export"]


def test_disabled_pipeline_does_not_touch_asr(tmp_path):
    class Bomb:
        def preflight(self):
            raise AssertionError("ASR must remain disabled")

    engine = FakeEngine(make_result(tmp_path / "audio.mp3"))
    GenerationService(engine, asr_manager=Bomb(), asr_enabled=False).generate("Texto.", tmp_path / "audio.mp3")
    assert engine.units is not None


def test_persistent_asr_failure_never_publishes(tmp_path):
    model = tmp_path / "model"; model.mkdir()
    manager = AsrManager(
        lambda: FakeAsrEngine(default=AsrResult("ruído", backend="fake")), model_path=model
    )
    events = []

    class Store:
        def mark_running(self, generation_id): events.append("running")
        def mark_failed(self, generation_id, error): events.append("failed")
        def complete(self, *args): raise AssertionError("must not publish")

    generation = type("Generation", (), {"generation_id": "g"})()
    staging = tmp_path / "staging" / ".audio.mp3"
    with pytest.raises(Exception, match="Validação ASR falhou"):
        GenerationService(
            StagedEngine(), asr_manager=manager, asr_enabled=True,
        ).generate_persisted(
            "Texto correto.", staging, tmp_path / "audio.mp3",
            object(), generation, Store(),
        )
    assert events == ["running", "failed"]
    assert not staging.exists() and not (staging.parent / ".units.staging").exists()
