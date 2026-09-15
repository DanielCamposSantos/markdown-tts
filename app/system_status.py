from __future__ import annotations

import os
import platform
import shutil
import subprocess
import sys
import time
import threading
from importlib.util import find_spec
from pathlib import Path
from typing import Callable

from app.config import ASR_MODEL_PATH, LIBRARY_DIR, MODEL_ID, ROOT
from app.operational import OperationalSettings
from app.reproducibility import baseline_lock_consistent


STATUS_CACHE_SECONDS = 0.9
DISK_WARNING_BYTES = 10 * 1024**3
DISK_BLOCK_BYTES = 2 * 1024**3


class ReadinessError(RuntimeError):
    pass


def default_moss_available() -> bool:
    try:
        from huggingface_hub.constants import HF_HUB_CACHE
        model_dir = Path(HF_HUB_CACHE) / ("models--" + MODEL_ID.replace("/", "--")) / "snapshots"
        return any((snapshot / "config.json").is_file() and (snapshot / "model.safetensors").is_file() for snapshot in model_dir.iterdir())
    except (ImportError, OSError):
        return False


def default_gpu_probe() -> dict:
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        output = subprocess.run(
            ["nvidia-smi", "--query-gpu=name,memory.total,memory.used,memory.free", "--format=csv,noheader,nounits"],
            capture_output=True, text=True, check=True, timeout=2, creationflags=flags,
        ).stdout.splitlines()[0]
        name, total, used, free = [item.strip() for item in output.split(",", 3)]
        return {"cuda_available": True, "gpu_name": name, "vram_total_mb": int(total), "vram_used_mb": int(used), "vram_free_mb": int(free), "vram_scope": "global", "telemetry": "available"}
    except Exception:
        try:
            import torch
            available = torch.cuda.is_available()
            if not available:
                return {"cuda_available": False, "gpu_name": None, "vram_total_mb": None, "vram_used_mb": None, "vram_free_mb": None, "vram_scope": "global", "telemetry": "unavailable"}
            free, total = torch.cuda.mem_get_info()
            return {"cuda_available": True, "gpu_name": torch.cuda.get_device_name(0), "vram_total_mb": round(total / 1024**2), "vram_used_mb": round((total - free) / 1024**2), "vram_free_mb": round(free / 1024**2), "vram_scope": "global", "telemetry": "fallback"}
        except Exception:
            return {"cuda_available": None, "gpu_name": None, "vram_total_mb": None, "vram_used_mb": None, "vram_free_mb": None, "vram_scope": "global", "telemetry": "unavailable"}


class SystemStatusService:
    def __init__(
        self, *, model_status: Callable[[], dict], asr_status: Callable[[], dict],
        voice_check: Callable[[], object], gpu_probe=default_gpu_probe,
        moss_available=default_moss_available, asr_model_path: Path = ASR_MODEL_PATH,
        library_path: Path = LIBRARY_DIR, ffmpeg_probe=None, disk_probe=None,
        asr_backend_probe=None, baseline_probe=None,
        clock=time.monotonic, cache_seconds: float = STATUS_CACHE_SECONDS,
    ) -> None:
        self.model_status, self.asr_status, self.voice_check = model_status, asr_status, voice_check
        self.gpu_probe, self.moss_available = gpu_probe, moss_available
        self.asr_model_path, self.library_path = Path(asr_model_path), Path(library_path)
        self.ffmpeg_probe = ffmpeg_probe or (lambda: shutil.which("ffmpeg") is not None)
        self.asr_backend_probe = asr_backend_probe or (lambda: find_spec("faster_whisper") is not None)
        self.baseline_probe = baseline_probe or baseline_lock_consistent
        self.disk_probe = disk_probe or (lambda: shutil.disk_usage(self.library_path).free)
        self.clock, self.cache_seconds = clock, cache_seconds
        self._cached_at = float("-inf")
        self._cached_static: dict | None = None
        self._cache_lock = threading.Lock()

    def _static(self) -> dict:
        with self._cache_lock:
            now = self.clock()
            if self._cached_static is not None and now - self._cached_at < self.cache_seconds:
                return self._cached_static
            try:
                gpu = self.gpu_probe()
            except Exception:
                gpu = {"cuda_available": None, "gpu_name": None, "vram_total_mb": None, "vram_used_mb": None, "vram_free_mb": None, "vram_scope": "global", "telemetry": "unavailable"}
            try:
                disk_free = self.disk_probe()
            except Exception:
                disk_free = None
            def available(probe) -> bool:
                try:
                    return bool(probe())
                except Exception:
                    return False
            self._cached_static = {
                "system": {"python": platform.python_version(), "platform": platform.system(), **gpu, "library_disk_free_bytes": disk_free},
                "availability": {"moss_model": available(self.moss_available), "asr_model": self.asr_model_path.is_dir(), "asr_backend": available(self.asr_backend_probe), "ffmpeg": available(self.ffmpeg_probe), "library": self.library_path.is_dir() and os.access(self.library_path, os.W_OK), "baseline": available(self.baseline_probe)},
            }
            self._cached_at = now
            return self._cached_static

    def status(self, settings: OperationalSettings) -> dict:
        static = self._static()
        voice = self.voice_check()
        tts_status, asr_status = self.model_status(), self.asr_status()
        errors, warnings = [], []
        system, availability = static["system"], static["availability"]
        if system["cuda_available"] is not True: errors.append("CUDA não está disponível.")
        if not availability["moss_model"]: errors.append("Modelo MOSS local não encontrado.")
        if not availability["ffmpeg"]: errors.append("FFmpeg não encontrado.")
        if not availability["library"]: errors.append("Library indisponível para escrita.")
        if not availability["baseline"]: errors.append("Baseline de reprodutibilidade inconsistente.")
        if not voice.healthy: errors.append("Referência vocal inválida.")
        if tts_status.get("state") == "error": errors.append("MOSS está em estado de erro.")
        if settings.asr_enabled and not availability["asr_model"]: errors.append("Modelo Faster-Whisper não encontrado. Desative Validação ASR ou restaure o modelo local.")
        if settings.asr_enabled and not availability["asr_backend"]: errors.append("Backend Faster-Whisper não está instalado. Desative Validação ASR ou restaure o ambiente.")
        if settings.asr_enabled and asr_status.get("state") == "error": errors.append("ASR está em estado de erro.")
        free = system["library_disk_free_bytes"]
        if free is not None and free < DISK_BLOCK_BYTES: errors.append("Espaço livre insuficiente na Library (menos de 2 GB).")
        elif free is not None and free < DISK_WARNING_BYTES: warnings.append("Pouco espaço livre na Library (menos de 10 GB).")
        if system["telemetry"] == "unavailable": warnings.append("Telemetria de VRAM indisponível.")
        if tts_status.get("error"): tts_status["error"] = "model_error"
        if asr_status.get("error"): asr_status["error"] = "backend_error"
        return {
            **static,
            "tts": {**tts_status, "model_available": availability["moss_model"]},
            "asr": {**asr_status, "enabled": settings.asr_enabled, "model_available": availability["asr_model"]},
            "voice": {"status": voice.status},
            "preset": settings.to_dict(),
            "readiness": {"ready": not errors, "errors": errors, "warnings": warnings},
        }

    def require_ready(self, settings: OperationalSettings) -> None:
        readiness = self.status(settings)["readiness"]
        if not readiness["ready"]:
            raise ReadinessError("Não é possível iniciar: " + " ".join(readiness["errors"]))
