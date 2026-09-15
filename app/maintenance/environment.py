from __future__ import annotations

import json
import shutil
import sys
from dataclasses import asdict, dataclass
from importlib import metadata
from pathlib import Path
from typing import Callable

from app.config import ROOT
from app.maintenance.voice_integrity import VoiceIntegrityService
from app.pronunciation import PT_BR_PROFILE
from app.reproducibility import baseline_lock_consistent, file_sha256, load_baseline


CRITICAL_DISTRIBUTIONS = (
    "torch", "transformers", "huggingface-hub", "tokenizers", "numpy",
    "soundfile", "fastapi", "uvicorn", "markdown-it-py", "pydantic",
)
ASR_DISTRIBUTIONS = ("faster-whisper", "ctranslate2", "av")


@dataclass(frozen=True)
class EnvironmentCheckItem:
    name: str
    status: str
    detail: str


@dataclass(frozen=True)
class EnvironmentReport:
    status: str
    checks: tuple[EnvironmentCheckItem, ...]

    def to_dict(self) -> dict:
        return {"status": self.status, "checks": [asdict(item) for item in self.checks]}


class EnvironmentChecker:
    def __init__(
        self, root: Path = ROOT, *, python_version=None,
        version_getter: Callable[[str], str] = metadata.version,
        which: Callable[[str], str | None] = shutil.which,
        cuda_probe=None, voice_check=None,
    ) -> None:
        self.root = Path(root)
        self.python_version = python_version or sys.version_info
        self.version_getter, self.which = version_getter, which
        self.cuda_probe = cuda_probe or self._cuda_probe
        self.voice_check = voice_check

    @staticmethod
    def _cuda_probe() -> tuple[bool, str | None, str | None]:
        import torch
        available = torch.cuda.is_available()
        return available, torch.version.cuda, torch.cuda.get_device_name(0) if available else None

    def run(self, *, require_asr: bool = False) -> EnvironmentReport:
        baseline = load_baseline(self.root / "reproducibility/baseline.json")
        checks: list[EnvironmentCheckItem] = []
        version = f"{self.python_version.major}.{self.python_version.minor}.{self.python_version.micro}"
        checks.append(EnvironmentCheckItem("python", "PASS" if (self.python_version.major, self.python_version.minor) == (3, 12) else "FAIL", f"{version}; supported >=3.12,<3.13"))
        pins = self._pins(self.root / baseline["dependencies"]["lock_path"])
        for distribution in CRITICAL_DISTRIBUTIONS + (ASR_DISTRIBUTIONS if require_asr else ()):
            try:
                installed = self.version_getter(distribution)
                expected = pins.get(distribution.lower())
                ok = installed == expected
                checks.append(EnvironmentCheckItem(f"package:{distribution}", "PASS" if ok else "FAIL", f"installed={installed}; expected={expected}"))
            except metadata.PackageNotFoundError:
                checks.append(EnvironmentCheckItem(f"package:{distribution}", "FAIL", "not installed"))
        available, cuda_runtime, device = self.cuda_probe()
        checks.append(EnvironmentCheckItem("cuda", "PASS" if available else "FAIL", f"runtime={cuda_runtime or 'unavailable'}; device={device or 'unavailable'}"))
        checks.append(EnvironmentCheckItem("ffmpeg", "PASS" if self.which("ffmpeg") else "FAIL", "available" if self.which("ffmpeg") else "not found"))
        moss = self.root / baseline["tts"]["vendor_path"]
        checks.append(EnvironmentCheckItem("moss_vendor", "PASS" if moss.is_dir() else "FAIL", "present" if moss.is_dir() else "missing"))
        voice_result = self.voice_check() if self.voice_check else VoiceIntegrityService(self.root / baseline["voice"]["path"], self.root / "voices/narrator_reference.manifest.json").check()
        checks.append(EnvironmentCheckItem("voice", "PASS" if voice_result.healthy else "FAIL", voice_result.status))
        checks.append(EnvironmentCheckItem("pronunciation", "PASS" if PT_BR_PROFILE == baseline["pronunciation"]["profile"] else "FAIL", PT_BR_PROFILE))
        actual_lock = file_sha256(self.root / baseline["dependencies"]["lock_path"])
        checks.append(EnvironmentCheckItem("dependency_lock", "PASS" if baseline_lock_consistent(self.root) else "FAIL", actual_lock))
        if require_asr:
            model = self.root / baseline["asr"]["model_path"]
            checks.append(EnvironmentCheckItem("asr_model", "PASS" if model.is_dir() else "FAIL", "present" if model.is_dir() else "missing"))
        overall = "FAIL" if any(item.status == "FAIL" for item in checks) else ("WARN" if any(item.status == "WARN" for item in checks) else "PASS")
        return EnvironmentReport(overall, tuple(checks))

    @staticmethod
    def _pins(path: Path) -> dict[str, str]:
        result = {}
        for raw in path.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if line and not line.startswith("#") and "==" in line:
                name, version = line.split("==", 1)
                result[name.lower()] = version
        return result


def format_report(report: EnvironmentReport) -> str:
    lines = [f"Environment check: {report.status}"]
    lines.extend(f"[{item.status}] {item.name}: {item.detail}" for item in report.checks)
    return "\n".join(lines)
