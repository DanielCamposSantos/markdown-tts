from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path

import soundfile as sf

from app.persistence.library import file_sha256


class VoiceIntegrityError(RuntimeError):
    pass


@dataclass(frozen=True)
class VoiceIntegrityResult:
    status: str
    healthy: bool
    message: str
    expected_sha256: str | None = None
    actual_sha256: str | None = None

    def to_dict(self) -> dict:
        return asdict(self)


class VoiceIntegrityService:
    def __init__(self, voice_path: Path, manifest_path: Path) -> None:
        self.voice_path = Path(voice_path)
        self.manifest_path = Path(manifest_path)

    def check(self) -> VoiceIntegrityResult:
        if not self.voice_path.is_file():
            return VoiceIntegrityResult("missing", False, "Referência vocal canônica ausente.")
        try:
            manifest = json.loads(self.manifest_path.read_text(encoding="utf-8"))
        except Exception:
            return VoiceIntegrityResult("invalid_audio", False, "Manifesto da referência vocal ausente ou inválido.")
        expected = manifest.get("sha256")
        actual = file_sha256(self.voice_path)
        if expected != actual or self.voice_path.stat().st_size != manifest.get("size_bytes"):
            return VoiceIntegrityResult("hash_mismatch", False, "Identidade da referência vocal não confere.", expected, actual)
        try:
            info = sf.info(self.voice_path)
            valid = (
                info.format == manifest.get("format")
                and info.samplerate == manifest.get("sample_rate")
                and info.channels == manifest.get("channels")
                and info.subtype == manifest.get("subtype", info.subtype)
                and info.frames >= manifest.get("minimum_frames", 1)
            )
        except Exception:
            valid = False
        if not valid:
            return VoiceIntegrityResult("invalid_audio", False, "Referência vocal ilegível ou com propriedades inválidas.", expected, actual)
        return VoiceIntegrityResult("healthy", True, "Referência vocal íntegra.", expected, actual)

    def require_healthy(self) -> VoiceIntegrityResult:
        result = self.check()
        if not result.healthy:
            raise VoiceIntegrityError(f"Geração bloqueada: {result.message}")
        return result
