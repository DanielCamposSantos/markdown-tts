from __future__ import annotations

import threading
import time
from enum import Enum
from typing import Any, Callable

from app.audio_generation_guard import GenerationCancelled
from app.config import MODEL_IDLE_TIMEOUT_SECONDS


class ModelState(str, Enum):
    UNLOADED = "unloaded"
    LOADING = "loading"
    READY = "ready"
    GENERATING = "generating"
    UNLOADING = "unloading"
    ERROR = "error"


TRANSITIONS = {
    ModelState.UNLOADED: {ModelState.LOADING},
    ModelState.LOADING: {ModelState.READY, ModelState.ERROR},
    ModelState.READY: {ModelState.GENERATING, ModelState.UNLOADING},
    ModelState.GENERATING: {ModelState.READY, ModelState.ERROR},
    ModelState.UNLOADING: {ModelState.UNLOADED, ModelState.ERROR},
    ModelState.ERROR: {ModelState.LOADING, ModelState.UNLOADING},
}


class ModelManager:
    def __init__(
        self,
        engine_factory: Callable[[], Any],
        *,
        idle_timeout_seconds: float = MODEL_IDLE_TIMEOUT_SECONDS,
        clock: Callable[[], float] = time.monotonic,
        cuda_available: Callable[[], bool] | None = None,
        on_state_change: Callable[[], None] | None = None,
    ) -> None:
        self._engine_factory = engine_factory
        self._idle_timeout = idle_timeout_seconds
        self._clock = clock
        self._cuda_available = cuda_available
        self._on_state_change = on_state_change
        self._condition = threading.Condition(threading.RLock())
        self._generation_lock = threading.Lock()
        self._stop = threading.Event()
        self._monitor: threading.Thread | None = None
        self._engine: Any | None = None
        self._state = ModelState.UNLOADED
        self._last_used: float | None = None
        self._shutdown = False
        self._last_error: str | None = None

    @property
    def state(self) -> ModelState:
        with self._condition:
            return self._state

    def _transition_locked(self, target: ModelState) -> None:
        if target not in TRANSITIONS[self._state]:
            raise ValueError(f"Invalid model transition: {self._state.value} -> {target.value}")
        self._state = target
        self._condition.notify_all()
        if self._on_state_change:
            self._on_state_change()

    def start(self) -> None:
        with self._condition:
            if self._shutdown:
                raise RuntimeError("ModelManager is shut down")
            if self._monitor and self._monitor.is_alive():
                return
            self._stop.clear()
            self._monitor = threading.Thread(
                target=self._monitor_idle,
                name="model-idle-monitor",
                daemon=True,
            )
            self._monitor.start()

    def _monitor_idle(self) -> None:
        interval = max(0.01, min(1.0, self._idle_timeout / 4))
        while not self._stop.wait(interval):
            self.check_idle()

    def ensure_loaded(self):
        with self._condition:
            while self._state in {ModelState.LOADING, ModelState.UNLOADING}:
                self._condition.wait()
            if self._shutdown:
                raise RuntimeError("ModelManager is shut down")
            if self._state in {ModelState.READY, ModelState.GENERATING}:
                return self._engine
            self._transition_locked(ModelState.LOADING)
            engine = self._engine

        try:
            if engine is None:
                engine = self._engine_factory()
            engine.load()
        except Exception as exc:
            if engine is not None:
                try:
                    engine.unload()
                except Exception:
                    pass
            with self._condition:
                self._engine = engine
                self._last_error = str(exc)
                self._transition_locked(ModelState.ERROR)
            raise

        with self._condition:
            self._engine = engine
            self._last_error = None
            self._transition_locked(ModelState.READY)
            self._last_used = self._clock()
            return engine

    def generate(self, *args, **kwargs):
        return self._invoke("generate", *args, **kwargs)

    def generate_units(self, *args, **kwargs):
        return self._invoke("generate_units", *args, **kwargs)

    def _invoke(self, method: str, *args, **kwargs):
        with self._generation_lock:
            engine = self.ensure_loaded()
            with self._condition:
                if self._state is not ModelState.READY:
                    raise RuntimeError("ModelManager is not ready")
                self._transition_locked(ModelState.GENERATING)
            try:
                result = getattr(engine, method)(*args, **kwargs)
            except GenerationCancelled:
                with self._condition:
                    self._last_used = self._clock()
                    self._transition_locked(ModelState.READY)
                raise
            except Exception as exc:
                self._handle_generation_error(engine, exc)
                raise
            with self._condition:
                self._last_used = self._clock()
                self._transition_locked(ModelState.READY)
            return result

    def _handle_generation_error(self, engine, error: Exception) -> None:
        try:
            engine.unload()
        except Exception:
            pass
        with self._condition:
            self._last_error = str(error)
            self._transition_locked(ModelState.ERROR)

    def check_idle(self) -> bool:
        if not self._generation_lock.acquire(blocking=False):
            return False
        try:
            with self._condition:
                if self._state is not ModelState.READY or self._last_used is None:
                    return False
                if self._clock() - self._last_used < self._idle_timeout:
                    return False
            self.unload()
            return True
        finally:
            self._generation_lock.release()

    def unload(self) -> None:
        with self._condition:
            if self._state is ModelState.UNLOADED:
                return
            if self._state not in {ModelState.READY, ModelState.ERROR}:
                raise RuntimeError(f"Cannot unload while {self._state.value}")
            self._transition_locked(ModelState.UNLOADING)
            engine = self._engine
        try:
            if engine is not None:
                engine.unload()
        except Exception as exc:
            with self._condition:
                self._last_error = str(exc)
                self._transition_locked(ModelState.ERROR)
            raise
        with self._condition:
            self._engine = None
            self._last_used = None
            self._transition_locked(ModelState.UNLOADED)

    def shutdown(self) -> None:
        self._stop.set()
        monitor = self._monitor
        if monitor and monitor is not threading.current_thread():
            monitor.join(timeout=2.0)
        with self._generation_lock:
            with self._condition:
                if self._state in {ModelState.READY, ModelState.ERROR}:
                    pass
                elif self._state is ModelState.UNLOADED:
                    self._engine = None
                    self._shutdown = True
                    return
            self.unload()
            with self._condition:
                self._engine = None
                self._shutdown = True

    def status(self) -> dict[str, Any]:
        with self._condition:
            state = self._state
            loaded = state in {ModelState.READY, ModelState.GENERATING}
            error = self._last_error
        available = self._cuda_available() if self._cuda_available else None
        return {
            "state": state.value,
            "model_loaded": loaded,
            "device": "cuda:0" if available is not False else "unavailable",
            "cuda_available": available,
            "error": error,
        }
