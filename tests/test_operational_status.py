from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

import web
from app import config
from app.operational import DEFAULT_PRESET, OperationalSettings, OperationalSettingsStore, PRESETS
from app.system_status import ReadinessError, SystemStatusService
from app.validation.manager import AsrBackendError, AsrManager


def status_service(tmp_path, **overrides):
    library = tmp_path / "library"; library.mkdir()
    model = tmp_path / "asr"; model.mkdir()
    arguments = dict(
        model_status=lambda: {"state": "unloaded", "model_loaded": False, "error": None},
        asr_status=lambda: {"state": "unloaded", "model_loaded": False, "error": None},
        voice_check=lambda: SimpleNamespace(healthy=True, status="healthy"),
        gpu_probe=lambda: {"cuda_available": True, "gpu_name": "RTX Test", "vram_total_mb": 12288, "vram_used_mb": 2048, "vram_free_mb": 10240, "vram_scope": "global", "telemetry": "available"},
        moss_available=lambda: True, asr_model_path=model, library_path=library,
        ffmpeg_probe=lambda: True, disk_probe=lambda: 20 * 1024**3,
        asr_backend_probe=lambda: True, baseline_probe=lambda: True,
    )
    arguments.update(overrides)
    return SystemStatusService(**arguments)


def test_system_status_cuda_gpu_vram_and_schema(tmp_path):
    result = status_service(tmp_path).status(OperationalSettings())
    assert result["system"]["cuda_available"] is True
    assert result["system"]["gpu_name"] == "RTX Test"
    assert result["system"]["vram_used_mb"] == 2048
    assert result["system"]["vram_scope"] == "global"
    assert result["readiness"] == {"ready": True, "errors": [], "warnings": []}
    assert set(result) == {"system", "availability", "tts", "asr", "voice", "preset", "readiness"}


def test_telemetry_failure_is_degraded_not_exception(tmp_path):
    result = status_service(tmp_path, gpu_probe=lambda: (_ for _ in ()).throw(RuntimeError("boom"))).status(OperationalSettings())
    assert result["system"]["telemetry"] == "unavailable"
    assert result["readiness"]["ready"] is False
    assert "Telemetria de VRAM indisponível." in result["readiness"]["warnings"]


def test_status_cache_avoids_repeated_gpu_probe(tmp_path):
    calls = []
    clock = SimpleNamespace(value=0)
    def probe(): calls.append(1); return {"cuda_available": True, "gpu_name": "GPU", "vram_total_mb": 1, "vram_used_mb": 0, "vram_free_mb": 1, "vram_scope": "global", "telemetry": "available"}
    service = status_service(tmp_path, gpu_probe=probe, clock=lambda: clock.value)
    service.status(OperationalSettings()); service.status(OperationalSettings())
    assert len(calls) == 1
    clock.value = 4; service.status(OperationalSettings())
    assert len(calls) == 2


@pytest.mark.parametrize(
    "override,error",
    [
        ({"gpu_probe": lambda: {"cuda_available": False, "gpu_name": None, "vram_total_mb": None, "vram_used_mb": None, "vram_free_mb": None, "vram_scope": "global", "telemetry": "available"}}, "CUDA"),
        ({"voice_check": lambda: SimpleNamespace(healthy=False, status="hash_mismatch")}, "vocal"),
        ({"moss_available": lambda: False}, "MOSS"),
        ({"ffmpeg_probe": lambda: False}, "FFmpeg"),
        ({"baseline_probe": lambda: False}, "Baseline"),
        ({"disk_probe": lambda: 1024}, "Espaço"),
    ],
)
def test_readiness_blocks_critical_conditions(tmp_path, override, error):
    service = status_service(tmp_path, **override)
    with pytest.raises(ReadinessError, match=error): service.require_ready(OperationalSettings())


def test_missing_asr_only_blocks_validated_preset(tmp_path):
    service = status_service(tmp_path, asr_model_path=tmp_path / "missing", asr_backend_probe=lambda: False)
    assert service.status(OperationalSettings("standard"))["readiness"]["ready"]
    validated = service.status(OperationalSettings("validated"))["readiness"]
    assert not validated["ready"] and len(validated["errors"]) == 2


def test_low_disk_is_warning_but_not_block(tmp_path):
    result = status_service(tmp_path, disk_probe=lambda: 5 * 1024**3).status(OperationalSettings())
    assert result["readiness"]["ready"] and result["readiness"]["warnings"]


def test_public_status_sanitizes_internal_model_errors(tmp_path):
    service = status_service(tmp_path, model_status=lambda: {"state": "error", "model_loaded": False, "error": "C:/private/trace"}, asr_status=lambda: {"state": "error", "model_loaded": False, "error": "secret"})
    payload = json.dumps(service.status(OperationalSettings()))
    assert "private" not in payload and "secret" not in payload
    assert "model_error" in payload and "backend_error" in payload
    assert service.status(OperationalSettings())["readiness"]["ready"] is False
    assert service.status(OperationalSettings("validated"))["readiness"]["ready"] is False


def test_presets_default_persistence_and_legacy_fallback(tmp_path):
    store = OperationalSettingsStore(tmp_path / "settings.json")
    assert store.load().preset == DEFAULT_PRESET == "standard"
    assert store.load().asr_enabled is False
    assert store.save("validated").asr_enabled is True
    assert OperationalSettingsStore(store.path).load().preset == "validated"
    store.path.write_text('{"old": true}', encoding="utf-8")
    assert store.load().preset == "standard"
    with pytest.raises(ValueError): store.save("unsafe")


def test_presets_change_only_asr_operational_flag():
    assert set(PRESETS) == {"standard", "validated"}
    assert PRESETS["standard"]["asr_enabled"] is False
    assert PRESETS["validated"]["asr_enabled"] is True
    frozen = (config.AUDIO_TEMPERATURE, config.AUDIO_TOP_P, config.AUDIO_TOP_K, config.MAX_NEW_TOKENS, config.BASE_SEED)
    for preset in PRESETS:
        OperationalSettings(preset)
        assert frozen == (config.AUDIO_TEMPERATURE, config.AUDIO_TOP_P, config.AUDIO_TOP_K, config.MAX_NEW_TOKENS, config.BASE_SEED)
    assert config.ASR_VALIDATION_ENABLED is False


class FakeAsr:
    def __init__(self, fail=False, callback=None): self.fail = fail; self.unloaded = False; self.callback = callback
    def load(self):
        if self.callback: self.callback("load")
    def transcribe(self, path, language):
        if self.callback: self.callback("transcribe")
        if self.fail: raise RuntimeError("bad")
        return "ok"
    def unload(self): self.unloaded = True


def test_asr_manager_reports_real_states(tmp_path):
    model = tmp_path / "model"; model.mkdir(); engine = FakeAsr()
    manager = AsrManager(lambda: engine, model)
    assert manager.status()["state"] == "unloaded"
    manager.load(); assert manager.status()["state"] == "ready"
    assert manager.transcribe(tmp_path / "a.wav", "pt") == "ok"
    manager.unload(); assert manager.status()["state"] == "unloaded" and engine.unloaded


def test_asr_manager_reports_error(tmp_path):
    model = tmp_path / "model"; model.mkdir(); manager = AsrManager(lambda: FakeAsr(True), model)
    with pytest.raises(AsrBackendError): manager.transcribe(tmp_path / "a.wav", "pt")
    assert manager.status()["state"] == "error"


def test_asr_manager_exposes_loading_and_validating_states(tmp_path):
    model = tmp_path / "model"; model.mkdir(); seen = []; manager = None
    engine = FakeAsr(callback=lambda phase: seen.append((phase, manager.status()["state"])))
    manager = AsrManager(lambda: engine, model)
    manager.transcribe(tmp_path / "a.wav", "pt")
    assert seen == [("load", "loading"), ("transcribe", "validating")]


def test_system_status_api_and_settings_api(monkeypatch, tmp_path):
    service = status_service(tmp_path)
    store = OperationalSettingsStore(tmp_path / "settings.json")
    monkeypatch.setattr(web, "system_status_service", service)
    monkeypatch.setattr(web, "operational_settings_store", store)
    payload = web.system_status()
    assert payload["readiness"]["ready"] and "path" not in json.dumps(payload).lower()
    assert web.operational_settings()["current"]["preset"] == "standard"
    assert web.update_operational_settings(web.PresetRequest(preset="validated"))["asr_enabled"] is True


def test_generation_preflight_rejects_before_library_write(monkeypatch):
    class Blocked:
        def require_ready(self, settings): raise ReadinessError("Não é possível iniciar: CUDA não está disponível.")
    monkeypatch.setattr(web, "system_status_service", Blocked())
    monkeypatch.setattr(web, "operational_settings_store", SimpleNamespace(load=lambda: OperationalSettings()))
    with pytest.raises(web.HTTPException) as error:
        web.generate(web.GenerateRequest(markdown="Texto."))
    assert error.value.status_code == 503 and "CUDA" in error.value.detail


def test_frontend_has_status_presets_moderate_polling_and_preflight():
    html = (Path("static/index.html")).read_text(encoding="utf-8")
    js = (Path("static/app.js")).read_text(encoding="utf-8")
    assert 'id="presetSelect"' in html and 'aria-label="Status operacional"' in html
    for label in ("GPU:", "VRAM global:", "MOSS:", "ASR:", "Voz:"): assert label in html
    assert 'fetch("/api/system-status")' in js
    assert "SYSTEM_STATUS_IDLE_MS = 4000" in js
    assert "SYSTEM_STATUS_ACTIVE_MS = 1000" in js
    assert "systemStatusPollInterval()" in js
    assert "latestSystemStatus.readiness.ready" in js
    for state in ("cancelling", "cancelled", "interrupted", "completed"): assert state in js


def test_frontend_live_vram_polling_states_and_global_label():
    js = Path("static/app.js").read_text(encoding="utf-8")
    assert 'new Set(["loading", "generating", "unloading"])' in js
    assert 'new Set(["loading", "validating", "unloading"])' in js
    assert "Boolean(activeJobId)" in js
    assert "VRAM global:" in js
