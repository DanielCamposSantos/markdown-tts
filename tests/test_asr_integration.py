from pathlib import Path
from types import SimpleNamespace

import pytest

from app.audio_generation_guard import GenerationCancelled, RETRY_SEED_OFFSET
from app.config import ASR_AUTO_REGENERATION_SEED_OFFSET, BASE_SEED
from app.services.asr_validation import AsrValidationCoordinator, AsrValidationFailedError
from app.services.regeneration import MANUAL_REGENERATION_SEED_OFFSET
from app.progress import ProgressTracker
from app.speech_plan import SpeechUnit
from app.validation.asr import AsrResult
from app.validation.manager import AsrBackendError, AsrManager


def unit(index, text="texto correto"):
    return SpeechUnit(index, "paragraph", text, text, (text,), 0)


class QueueAsr:
    def __init__(self, values):
        self.values = {name: list(items) for name, items in values.items()}
        self.calls = []
        self.loads = self.unloads = 0

    def load(self):
        self.loads += 1

    def unload(self):
        self.unloads += 1

    def transcribe(self, path, language="pt"):
        self.calls.append((Path(path).name, language))
        value = self.values[Path(path).name].pop(0)
        if isinstance(value, Exception):
            raise value
        return AsrResult(value, backend="fake")


class FakeTts:
    def __init__(self):
        self.unloads = 0
        self.batches = []

    def unload(self):
        self.unloads += 1

    def generate_units(self, units, **kwargs):
        self.batches.append(([item.index for item in units], kwargs["seed_offset"]))
        return SimpleNamespace(generation_seconds=1.0, decode_seconds=0.5)


def coordinator(tmp_path, values):
    backend = QueueAsr(values)
    model = tmp_path / "model"
    model.mkdir(parents=True)
    manager = AsrManager(lambda: backend, model_path=model)
    return AsrValidationCoordinator(manager), backend


def test_all_pass_and_warn_are_accepted_without_regeneration(tmp_path):
    units = [unit(1), unit(2, "O HTTPS utiliza TLS.")]
    service, backend = coordinator(tmp_path, {
        "000001.wav": ["texto correto"],
        "000002.wav": ["agá tê tê pê esse utiliza tê ele esse"],
    })
    tts = FakeTts()
    outcome = service.validate_and_correct(units, tmp_path, tts)
    assert outcome.metadata["summary"] == {"pass": 1, "warn": 1, "fail": 0, "auto_regenerated_units": []}
    assert tts.batches == []


def test_failures_are_regenerated_in_batches_and_second_round_can_resolve(tmp_path):
    units = [unit(1, "um dois três"), unit(2, "quatro cinco seis"), unit(3)]
    service, _ = coordinator(tmp_path, {
        "000001.wav": ["ruído", "ainda errado", "um dois três"],
        "000002.wav": ["ruído", "quatro cinco seis"],
        "000003.wav": ["texto correto"],
    })
    tts = FakeTts()
    outcome = service.validate_and_correct(units, tmp_path, tts)
    assert tts.batches == [
        ([1, 2], ASR_AUTO_REGENERATION_SEED_OFFSET),
        ([1], ASR_AUTO_REGENERATION_SEED_OFFSET * 2),
    ]
    assert outcome.metadata["summary"] == {"pass": 3, "warn": 0, "fail": 0, "auto_regenerated_units": [1, 2]}
    assert outcome.metadata["units"]["1"]["auto_regeneration_rounds"] == 2


def test_persistent_fail_and_backend_error_abort(tmp_path):
    service, _ = coordinator(tmp_path, {"000001.wav": ["ruído"] * 3})
    with pytest.raises(AsrValidationFailedError):
        service.validate_and_correct([unit(1)], tmp_path, FakeTts())
    broken, _ = coordinator(tmp_path / "other", {"000001.wav": [RuntimeError("decoder")]})
    with pytest.raises(AsrBackendError, match="decoder"):
        broken.validate_and_correct([unit(1)], tmp_path, FakeTts())


def test_cancellation_wins_before_reload(tmp_path):
    service, _ = coordinator(tmp_path, {"000001.wav": ["ruído"]})
    checks = [False, False, False, False, True]
    with pytest.raises(GenerationCancelled):
        service.validate_and_correct([unit(1)], tmp_path, FakeTts(), should_cancel=lambda: checks.pop(0))


def test_cancellation_between_asr_units_stops_serial_validation(tmp_path):
    service, backend = coordinator(tmp_path, {
        "000001.wav": ["texto correto"], "000002.wav": ["texto correto"],
    })
    checks = [False, False, False, True]
    with pytest.raises(GenerationCancelled):
        service.validate_and_correct(
            [unit(1), unit(2)], tmp_path, FakeTts(),
            should_cancel=lambda: checks.pop(0),
        )
    assert backend.calls == [("000001.wav", "pt")]


def test_seed_namespaces_do_not_collide():
    normal = BASE_SEED + 7
    guard = normal + RETRY_SEED_OFFSET
    manual = normal + MANUAL_REGENERATION_SEED_OFFSET
    asr_one = normal + ASR_AUTO_REGENERATION_SEED_OFFSET
    asr_two = normal + 2 * ASR_AUTO_REGENERATION_SEED_OFFSET
    assert len({normal, guard, manual, asr_one, asr_two}) == 5
    assert asr_one + RETRY_SEED_OFFSET not in {normal, guard, manual, asr_two}
    for asr_seed in (asr_one, asr_two):
        for guard_attempt in range(3):
            assert (asr_seed + RETRY_SEED_OFFSET * guard_attempt - normal) % MANUAL_REGENERATION_SEED_OFFSET != 0


def test_asr_load_exception_is_explicit(tmp_path):
    model = tmp_path / "model"; model.mkdir()

    class Broken:
        def load(self): raise RuntimeError("CUDA unavailable")
        def unload(self): pass

    manager = AsrManager(Broken, model_path=model)
    with pytest.raises(AsrBackendError, match="carregar.*CUDA unavailable"):
        manager.load()


def test_stress_50_units_five_initial_failures_two_corrective_rounds(tmp_path):
    units = [unit(index, "um dois três") for index in range(1, 51)]
    failed_ids = {1, 2, 3, 4, 5}
    values = {}
    for item in units:
        values[f"{item.index:06d}.wav"] = (
            ["ruído", "um dois três"] if item.index in failed_ids - {1} else
            ["ruído", "ainda errado", "um dois três"] if item.index == 1 else
            ["um dois três"]
        )
    service, backend = coordinator(tmp_path, values)
    tts = FakeTts()
    progress = []
    tracker = ProgressTracker(progress.append)
    tracker.set_units(units)
    outcome = service.validate_and_correct(
        units, tmp_path, tts,
        progress_callback=lambda phase, current, total, message: tracker.report(phase, current, total, message),
    )
    assert len(backend.calls) == 56
    assert tts.batches == [
        ([1, 2, 3, 4, 5], ASR_AUTO_REGENERATION_SEED_OFFSET),
        ([1], ASR_AUTO_REGENERATION_SEED_OFFSET * 2),
    ]
    assert outcome.metadata["summary"] == {
        "pass": 50, "warn": 0, "fail": 0,
        "auto_regenerated_units": [1, 2, 3, 4, 5],
    }
    assert progress and all(a.progress <= b.progress for a, b in zip(progress, progress[1:]))


def test_persistent_asr_fail_does_not_break_health(tmp_path):
    service, _ = coordinator(tmp_path, {"000001.wav": ["ruído"] * 3})
    with pytest.raises(AsrValidationFailedError):
        service.validate_and_correct([unit(1, "um dois três")], tmp_path, FakeTts())
    import web
    assert web.health() == {"application": "markdown-tts", "status": "ok"}
