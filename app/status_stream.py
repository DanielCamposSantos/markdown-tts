from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone


ACTIVE_TTS = {"loading", "generating", "unloading"}
ACTIVE_ASR = {"loading", "validating", "unloading"}
ACTIVE_JOB = {"running", "cancelling"}
IDLE_SECONDS = 4.0
ACTIVE_SECONDS = 1.0


def safe_snapshot(status: dict, job: dict | None = None) -> dict:
    system = status["system"]
    return {
        "type": "system_status",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "system": {key: system.get(key) for key in (
            "cuda_available", "gpu_name", "vram_scope", "vram_used_mb",
            "vram_total_mb", "vram_free_mb", "telemetry",
        )},
        "tts": {"state": status["tts"]["state"]},
        "asr": {"state": status["asr"]["state"], "enabled": status["asr"]["enabled"]},
        "job": {"status": job.get("status"), "phase": job.get("phase")} if job else None,
        "voice": {"status": status["voice"]["status"]},
        "preset": status["preset"]["preset"],
        "readiness": status["readiness"],
    }


def sample_interval(snapshot: dict) -> float:
    if (snapshot["tts"]["state"] in ACTIVE_TTS or
        snapshot["asr"]["state"] in ACTIVE_ASR or
        snapshot["job"] and snapshot["job"]["status"] in ACTIVE_JOB):
        return ACTIVE_SECONDS
    return IDLE_SECONDS


class StatusStream:
    """One telemetry sampler for all connected local UI clients."""

    def __init__(self, snapshot_provider):
        self.snapshot_provider = snapshot_provider
        self.clients: set = set()
        self.loop = None
        self.changed = None
        self.task = None

    async def connect(self, client):
        await client.accept()
        self.clients.add(client)
        self.loop = asyncio.get_running_loop()
        if self.changed is None:
            self.changed = asyncio.Event()
        await client.send_json(await asyncio.to_thread(self.snapshot_provider))
        if self.task is None or self.task.done():
            self.task = asyncio.create_task(self._run())

    def disconnect(self, client):
        self.clients.discard(client)
        self.signal()

    def signal(self):
        if self.loop and self.changed:
            try:
                self.loop.call_soon_threadsafe(self.changed.set)
            except RuntimeError:
                self.loop = None

    async def _run(self):
        last = None
        while self.clients:
            self.changed.clear()
            try:
                snapshot = await asyncio.to_thread(self.snapshot_provider)
            except Exception:
                try:
                    await asyncio.wait_for(self.changed.wait(), IDLE_SECONDS)
                except asyncio.TimeoutError:
                    pass
                continue
            comparable = json.dumps({key: value for key, value in snapshot.items() if key != "timestamp"}, sort_keys=True)
            if comparable != last:
                await self._broadcast(snapshot)
                last = comparable
            try:
                await asyncio.wait_for(self.changed.wait(), sample_interval(snapshot))
            except asyncio.TimeoutError:
                pass
        self.task = None

    async def _broadcast(self, snapshot):
        for client in tuple(self.clients):
            try:
                await client.send_json(snapshot)
            except Exception:
                self.clients.discard(client)
