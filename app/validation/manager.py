from __future__ import annotations

from pathlib import Path
from typing import Callable

from app.config import ASR_BEAM_SIZE, ASR_COMPUTE_TYPE, ASR_MODEL_PATH
from app.validation.asr import AsrEngine, FasterWhisperAsrEngine


class AsrBackendError(RuntimeError):
    pass


class AsrManager:
    """Owns the single opt-in ASR instance independently from MOSS."""

    def __init__(self, factory: Callable[[], AsrEngine] | None = None, model_path: Path = ASR_MODEL_PATH) -> None:
        self._model_path = Path(model_path)
        self._factory = factory or (
            lambda: FasterWhisperAsrEngine(
                self._model_path, device="cuda", compute_type=ASR_COMPUTE_TYPE,
                beam_size=ASR_BEAM_SIZE,
            )
        )
        self._engine: AsrEngine | None = None

    def preflight(self) -> None:
        if not self._model_path.is_dir():
            raise FileNotFoundError(f"Modelo ASR local não encontrado: {self._model_path}")

    def load(self) -> AsrEngine:
        self.preflight()
        if self._engine is None:
            try:
                self._engine = self._factory()
                load = getattr(self._engine, "load", None)
                if callable(load):
                    load()
            except Exception as exc:
                self.unload()
                raise AsrBackendError(f"Falha ao carregar backend ASR: {exc}") from exc
        return self._engine

    def transcribe(self, audio_path: Path, language: str):
        try:
            return self.load().transcribe(audio_path, language=language)
        except AsrBackendError:
            raise
        except Exception as exc:
            raise AsrBackendError(f"Falha no backend ASR: {exc}") from exc

    def unload(self) -> None:
        engine, self._engine = self._engine, None
        if engine is not None:
            unload = getattr(engine, "unload", None)
            if callable(unload):
                unload()
