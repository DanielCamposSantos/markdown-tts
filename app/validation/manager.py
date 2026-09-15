from __future__ import annotations

from pathlib import Path
from typing import Callable
import threading

from app.config import ASR_BEAM_SIZE, ASR_COMPUTE_TYPE, ASR_MODEL_PATH
from app.validation.asr import AsrEngine, FasterWhisperAsrEngine


class AsrBackendError(RuntimeError):
    pass


class AsrManager:
    """Owns the single opt-in ASR instance independently from MOSS."""

    def __init__(self, factory: Callable[[], AsrEngine] | None = None, model_path: Path = ASR_MODEL_PATH, on_state_change: Callable[[], None] | None = None) -> None:
        self._model_path = Path(model_path)
        self._factory = factory or (
            lambda: FasterWhisperAsrEngine(
                self._model_path, device="cuda", compute_type=ASR_COMPUTE_TYPE,
                beam_size=ASR_BEAM_SIZE,
            )
        )
        self._engine: AsrEngine | None = None
        self._state = "unloaded"
        self._last_error: str | None = None
        self._lock = threading.RLock()
        self._on_state_change = on_state_change

    def _notify(self):
        if self._on_state_change:
            self._on_state_change()

    def preflight(self) -> None:
        if not self._model_path.is_dir():
            raise FileNotFoundError(f"Modelo ASR local não encontrado: {self._model_path}")

    def load(self) -> AsrEngine:
        self.preflight()
        with self._lock:
            if self._engine is not None:
                return self._engine
            self._state = "loading"
            self._notify()
        if self._engine is None:
            try:
                engine = self._factory()
                load = getattr(engine, "load", None)
                if callable(load):
                    load()
            except Exception as exc:
                with self._lock:
                    self._engine = None
                    self._state = "error"
                    self._notify()
                    self._last_error = str(exc)
                raise AsrBackendError(f"Falha ao carregar backend ASR: {exc}") from exc
            with self._lock:
                self._engine = engine
                self._state = "ready"
                self._notify()
                self._last_error = None
        return self._engine

    def transcribe(self, audio_path: Path, language: str):
        try:
            engine = self.load()
            with self._lock:
                self._state = "validating"
                self._notify()
            result = engine.transcribe(audio_path, language=language)
            with self._lock:
                self._state = "ready"
                self._notify()
            return result
        except AsrBackendError:
            raise
        except Exception as exc:
            with self._lock:
                self._state = "error"
                self._notify()
                self._last_error = str(exc)
            raise AsrBackendError(f"Falha no backend ASR: {exc}") from exc

    def unload(self) -> None:
        with self._lock:
            engine, self._engine = self._engine, None
            self._state = "unloading" if engine is not None else "unloaded"
            self._notify()
        if engine is not None:
            unload = getattr(engine, "unload", None)
            try:
                if callable(unload):
                    unload()
            except Exception as exc:
                with self._lock:
                    self._state = "error"
                    self._notify()
                    self._last_error = str(exc)
                raise
        with self._lock:
            self._state = "unloaded"
            self._notify()

    def status(self) -> dict:
        with self._lock:
            return {
                "state": self._state,
                "model_loaded": self._engine is not None,
                "error": self._last_error,
            }
