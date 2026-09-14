import threading

import pytest
import torch

from app.audio_generation_guard import GenerationCancelled
from app.tts import ModelManager, ModelState


class FakeEngine:
    def __init__(self):
        self.load_count = 0
        self.unload_count = 0
        self.generate_count = 0
        self.loaded = False
        self.error = None

    def load(self):
        self.load_count += 1
        self.loaded = True

    def unload(self):
        self.unload_count += 1
        self.loaded = False

    def generate(self, *args, **kwargs):
        assert self.loaded
        self.generate_count += 1
        if self.error:
            raise self.error
        return "result"


def test_initial_load_generate_release_unload_and_shutdown():
    engine = FakeEngine()
    manager = ModelManager(lambda: engine)
    assert manager.state is ModelState.UNLOADED
    assert manager.ensure_loaded() is engine
    assert manager.state is ModelState.READY
    assert manager.generate() == "result"
    assert manager.state is ModelState.READY
    manager.unload()
    assert manager.state is ModelState.UNLOADED
    manager.shutdown()
    with pytest.raises(RuntimeError, match="shut down"):
        manager.ensure_loaded()


def test_concurrent_ensure_loaded_calls_loader_once():
    engine = FakeEngine()
    entered = threading.Event()
    release = threading.Event()

    def factory():
        entered.set()
        release.wait(1)
        return engine

    manager = ModelManager(factory)
    results = []
    threads = [threading.Thread(target=lambda: results.append(manager.ensure_loaded())) for _ in range(3)]
    for thread in threads: thread.start()
    assert entered.wait(1)
    release.set()
    for thread in threads: thread.join()
    assert results == [engine, engine, engine]
    assert engine.load_count == 1


def test_nearby_generations_reuse_same_engine():
    engine = FakeEngine()
    manager = ModelManager(lambda: engine)
    manager.generate()
    manager.generate()
    assert engine.load_count == 1
    assert engine.generate_count == 2


def test_manager_never_runs_two_generations_concurrently():
    entered = threading.Event()
    release = threading.Event()
    second_done = threading.Event()

    class BlockingEngine(FakeEngine):
        def __init__(self):
            super().__init__()
            self.active = 0
            self.max_active = 0

        def generate(self, *args, **kwargs):
            self.active += 1
            self.max_active = max(self.max_active, self.active)
            entered.set()
            release.wait(1)
            self.active -= 1
            return "result"

    engine = BlockingEngine()
    manager = ModelManager(lambda: engine)
    first = threading.Thread(target=manager.generate)
    second = threading.Thread(target=lambda: (manager.generate(), second_done.set()))
    first.start()
    assert entered.wait(1)
    second.start()
    assert not second_done.wait(0.05)
    release.set()
    first.join(); second.join()
    assert engine.max_active == 1


def test_idle_timeout_unloads_with_fake_clock():
    now = [10.0]
    engine = FakeEngine()
    manager = ModelManager(lambda: engine, idle_timeout_seconds=5, clock=lambda: now[0])
    manager.generate()
    now[0] = 14.9
    assert not manager.check_idle()
    now[0] = 15.0
    assert manager.check_idle()
    assert manager.state is ModelState.UNLOADED
    assert engine.unload_count == 1


def test_new_acquire_waits_for_idle_unload_then_gets_loaded_engine():
    now = [0.0]
    unloading = threading.Event()
    release = threading.Event()

    class BlockingEngine(FakeEngine):
        def unload(self):
            unloading.set()
            release.wait(1)
            super().unload()

    engine = BlockingEngine()
    manager = ModelManager(lambda: engine, idle_timeout_seconds=1, clock=lambda: now[0])
    manager.generate()
    now[0] = 2
    idle_thread = threading.Thread(target=manager.check_idle)
    idle_thread.start()
    assert unloading.wait(1)
    result = []
    acquire_thread = threading.Thread(target=lambda: result.append(manager.generate()))
    acquire_thread.start()
    assert not result
    release.set()
    idle_thread.join(); acquire_thread.join()
    assert result == ["result"]
    assert engine.load_count == 2
    assert manager.state is ModelState.READY


def test_load_error_enters_error_and_next_attempt_recovers():
    class FlakyEngine(FakeEngine):
        def load(self):
            self.load_count += 1
            if self.load_count == 1:
                raise RuntimeError("load failed")
            self.loaded = True

    engine = FlakyEngine()
    manager = ModelManager(lambda: engine)
    with pytest.raises(RuntimeError, match="load failed"):
        manager.ensure_loaded()
    assert manager.state is ModelState.ERROR
    assert manager.generate() == "result"
    assert manager.state is ModelState.READY


def test_generation_error_unloads_and_recovers():
    engine = FakeEngine()
    manager = ModelManager(lambda: engine)
    engine.error = RuntimeError("generation failed")
    with pytest.raises(RuntimeError, match="generation failed"):
        manager.generate()
    assert manager.state is ModelState.ERROR
    assert not engine.loaded
    engine.error = None
    assert manager.generate() == "result"
    assert engine.load_count == 2


def test_cuda_oom_enters_recoverable_error_without_quality_fallback():
    engine = FakeEngine()
    manager = ModelManager(lambda: engine)
    engine.error = torch.cuda.OutOfMemoryError("out of memory")
    with pytest.raises(torch.cuda.OutOfMemoryError):
        manager.generate()
    assert manager.state is ModelState.ERROR
    assert engine.unload_count == 1
    engine.error = None
    assert manager.generate() == "result"


def test_cancellation_returns_manager_to_ready_without_unload():
    engine = FakeEngine()
    manager = ModelManager(lambda: engine)
    engine.error = GenerationCancelled("cancelled")
    with pytest.raises(GenerationCancelled):
        manager.generate()
    assert manager.state is ModelState.READY
    assert engine.unload_count == 0


def test_invalid_transition_is_rejected():
    manager = ModelManager(FakeEngine)
    with manager._condition:
        with pytest.raises(ValueError, match="unloaded -> generating"):
            manager._transition_locked(ModelState.GENERATING)


def test_monitor_is_single_and_shutdown_unloads_once():
    engine = FakeEngine()
    manager = ModelManager(lambda: engine, idle_timeout_seconds=100)
    manager.ensure_loaded()
    manager.start()
    monitor = manager._monitor
    manager.start()
    assert manager._monitor is monitor
    manager.shutdown()
    assert not monitor.is_alive()
    assert engine.unload_count == 1


def test_status_does_not_call_factory_and_reports_missing_cuda():
    calls = []
    manager = ModelManager(lambda: calls.append(1), cuda_available=lambda: False)
    assert manager.status() == {
        "state": "unloaded",
        "model_loaded": False,
        "device": "unavailable",
        "cuda_available": False,
        "error": None,
    }
    assert calls == []
