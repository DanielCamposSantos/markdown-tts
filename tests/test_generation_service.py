from pathlib import Path

import pytest

from app.domain.models import GenerationResult
from app.markdown_parser import parse_markdown
from app.services.generation import GenerationService
from app.speech_plan import build_speech_plan


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
    assert progress[0].progress == 29.0


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
