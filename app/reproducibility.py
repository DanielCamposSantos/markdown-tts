from __future__ import annotations

import hashlib
import json
from pathlib import Path

from app.config import ROOT


BASELINE_PATH = ROOT / "reproducibility" / "baseline.json"
DEPENDENCY_LOCK_PATH = ROOT / "requirements" / "constraints.txt"


def file_sha256(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def load_baseline(path: Path = BASELINE_PATH) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def baseline_lock_consistent(root: Path = ROOT) -> bool:
    baseline = load_baseline(Path(root) / "reproducibility/baseline.json")
    return file_sha256(Path(root) / baseline["dependencies"]["lock_path"]) == baseline["dependencies"]["lock_sha256"]


def generation_reproducibility(*, asr_used: bool) -> dict:
    baseline = load_baseline()
    result = {
        "baseline_schema_version": baseline["schema_version"],
        "baseline_id": baseline["baseline_id"],
        "dependency_lock_sha256": baseline["dependencies"]["lock_sha256"],
        "voice_id": baseline["voice"]["voice_id"],
        "voice_sha256": baseline["voice"]["sha256"],
        "pronunciation_profile": baseline["pronunciation"]["profile"],
        "tts_model_id": baseline["tts"]["model_id"],
        "audio_guard_policy": baseline["tts"]["guard_policy"],
    }
    if asr_used:
        result["asr_config_id"] = baseline["asr"]["config_id"]
        result["asr_validator_policy"] = baseline["asr"]["validator_policy"]
    return result
