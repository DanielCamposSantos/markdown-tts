from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

import web
from app.status_stream import ACTIVE_SECONDS, IDLE_SECONDS, StatusStream, safe_snapshot, sample_interval


def status(tts="unloaded", asr="unloaded"):
    return {
        "system": {"cuda_available": True, "gpu_name": "GPU", "vram_scope": "global", "vram_used_mb": 100,
                   "vram_total_mb": 1000, "vram_free_mb": 900, "telemetry": "available", "private_path": "C:/private"},
        "tts": {"state": tts}, "asr": {"state": asr, "enabled": True},
        "voice": {"status": "healthy"}, "preset": {"preset": "validated"},
        "readiness": {"ready": True, "errors": [], "warnings": []},
    }


def test_safe_snapshot_privacy_and_active_idle_intervals():
    idle = safe_snapshot(status())
    assert sample_interval(idle) == IDLE_SECONDS
    for tts, asr, job in (("loading", "unloaded", None), ("generating", "unloaded", None),
                          ("unloading", "unloaded", None), ("unloaded", "validating", None),
                          ("unloaded", "unloading", None), ("unloaded", "unloaded", {"status": "running", "phase": "decode"})):
        assert sample_interval(safe_snapshot(status(tts, asr), job)) == ACTIVE_SECONDS
    assert "private" not in json.dumps(idle) and "C:/" not in json.dumps(idle)
    assert idle["system"]["vram_scope"] == "global"


@pytest.mark.anyio
async def test_one_broker_multiple_clients_signal_reconnect_and_disconnect():
    calls = []
    current = {"value": safe_snapshot(status())}

    def provider():
        calls.append(1)
        return current["value"]

    class Client:
        def __init__(self): self.frames = []
        async def accept(self): pass
        async def send_json(self, frame): self.frames.append(frame)

    broker = StatusStream(provider)
    first, second = Client(), Client()
    await broker.connect(first); await broker.connect(second)
    await asyncio.sleep(0.05)
    assert first.frames and second.frames and len(calls) < 6
    current["value"] = safe_snapshot(status("loading"))
    broker.signal(); await asyncio.sleep(0.05)
    assert first.frames[-1]["tts"]["state"] == second.frames[-1]["tts"]["state"] == "loading"
    broker.disconnect(first); broker.disconnect(second)
    await asyncio.sleep(0.05)
    assert not broker.clients and broker.task is None
    await broker.connect(first)
    assert first.frames[-1]["tts"]["state"] == "loading"
    broker.disconnect(first)


def test_websocket_initial_snapshot_origin_policy_and_rest_compat(monkeypatch):
    monkeypatch.setattr(web, "stream_snapshot", lambda: safe_snapshot(status()))
    monkeypatch.setattr(web, "status_stream", StatusStream(lambda: web.stream_snapshot()))
    monkeypatch.setattr(web, "get_system_status_service", lambda: SimpleNamespace(status=lambda settings: status()))
    monkeypatch.setattr(web, "get_operational_settings_store", lambda: SimpleNamespace(load=lambda: None))
    client = TestClient(web.app, base_url="http://127.0.0.1:7860")
    with client.websocket_connect("/ws/system-status", headers={"origin": "http://127.0.0.1:7860", "host": "127.0.0.1:7860"}) as socket:
        frame = socket.receive_json()
        assert frame["type"] == "system_status" and frame["tts"]["state"] == "unloaded"
    assert client.get("/api/system-status").status_code == 200
    with pytest.raises(Exception):
        with client.websocket_connect("/ws/system-status", headers={"origin": "http://remote.invalid", "host": "127.0.0.1:7860"}) as socket:
            socket.receive_json()
