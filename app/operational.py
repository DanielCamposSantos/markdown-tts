from __future__ import annotations

import json
import os
import uuid
from dataclasses import dataclass
from pathlib import Path


DEFAULT_PRESET = "standard"
PRESETS = {
    "standard": {"label": "Padrão", "asr_enabled": False},
    "validated": {"label": "Validação ASR", "asr_enabled": True},
}


@dataclass(frozen=True)
class OperationalSettings:
    preset: str = DEFAULT_PRESET

    @property
    def asr_enabled(self) -> bool:
        return bool(PRESETS[self.preset]["asr_enabled"])

    def to_dict(self) -> dict:
        return {"preset": self.preset, "label": PRESETS[self.preset]["label"], "asr_enabled": self.asr_enabled}


class OperationalSettingsStore:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)

    def load(self) -> OperationalSettings:
        try:
            preset = json.loads(self.path.read_text(encoding="utf-8")).get("preset", DEFAULT_PRESET)
        except (FileNotFoundError, OSError, json.JSONDecodeError, AttributeError):
            preset = DEFAULT_PRESET
        return OperationalSettings(preset if preset in PRESETS else DEFAULT_PRESET)

    def save(self, preset: str) -> OperationalSettings:
        if preset not in PRESETS:
            raise ValueError("Preset operacional inválido.")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_name(f".{self.path.name}.{uuid.uuid4().hex}.tmp")
        try:
            temporary.write_text(json.dumps({"schema_version": 1, "preset": preset}, indent=2), encoding="utf-8")
            os.replace(temporary, self.path)
        finally:
            temporary.unlink(missing_ok=True)
        return OperationalSettings(preset)
