import web
from app.tts import ModelManager


def test_model_status_endpoint_is_lazy(monkeypatch):
    calls = []
    manager = ModelManager(lambda: calls.append("loaded"), cuda_available=lambda: False)
    monkeypatch.setattr(web, "model_manager", manager)
    result = web.model_status()
    assert result["state"] == "unloaded"
    assert result["model_loaded"] is False
    assert result["device"] == "unavailable"
    assert calls == []
