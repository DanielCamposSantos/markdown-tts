from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

from app.validation.asr import FasterWhisperAsrEngine


def test_backend_import_is_lazy():
    assert "faster_whisper" not in sys.modules
    FasterWhisperAsrEngine(Path("unused"))
    assert "faster_whisper" not in sys.modules


def test_backend_requires_local_model(tmp_path):
    with pytest.raises(FileNotFoundError, match="local não encontrado"):
        FasterWhisperAsrEngine(tmp_path / "missing", device="cpu").load()


def test_backend_loads_local_only_and_transcribes(monkeypatch, tmp_path):
    model_path = tmp_path / "medium"
    model_path.mkdir()
    calls = {}

    class FakeModel:
        def __init__(self, path, **kwargs):
            calls["load"] = (path, kwargs)

        def transcribe(self, path, **kwargs):
            calls["transcribe"] = (path, kwargs)
            word = SimpleNamespace(word=" Olá ", start=0.1, end=0.5, probability=0.9)
            return iter((SimpleNamespace(text=" Olá ", words=(word,), avg_logprob=-0.1),)), SimpleNamespace(language="pt", duration=1.2)

    monkeypatch.setitem(sys.modules, "faster_whisper", SimpleNamespace(WhisperModel=FakeModel))
    engine = FasterWhisperAsrEngine(model_path, device="cpu", compute_type="float32")
    result = engine.transcribe(tmp_path / "audio.wav")
    assert calls["load"][1]["local_files_only"] is True
    assert calls["transcribe"][1] == {"language": "pt", "beam_size": 5, "vad_filter": False, "word_timestamps": True}
    assert result.text == "Olá" and result.duration_seconds == 1.2
    assert result.backend == "faster-whisper" and result.model == "medium"
    assert result.words[0].text == "Olá" and result.words[0].start_seconds == 0.1
    engine.unload()
