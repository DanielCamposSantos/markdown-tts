from __future__ import annotations

from dataclasses import asdict, dataclass
from importlib import metadata, util
from pathlib import Path
from typing import Callable, Mapping, Protocol


ASR_ENABLED = False


@dataclass(frozen=True)
class AsrResult:
    text: str
    language: str | None = None
    duration_seconds: float | None = None
    confidence: float | None = None
    backend: str = "unknown"
    model: str | None = None
    processing_seconds: float | None = None

    def to_dict(self) -> dict:
        return asdict(self)


class AsrEngine(Protocol):
    def transcribe(self, audio_path: Path, language: str = "pt") -> AsrResult: ...


class AsrDisabledError(RuntimeError):
    pass


class DisabledAsrEngine:
    def transcribe(self, audio_path: Path, language: str = "pt") -> AsrResult:
        raise AsrDisabledError("Validação ASR está desativada.")


class FakeAsrEngine:
    def __init__(
        self,
        results: Mapping[str, AsrResult | str] | None = None,
        default: AsrResult | str = "",
        callback: Callable[[Path, str], AsrResult] | None = None,
    ) -> None:
        self.results = dict(results or {})
        self.default = default
        self.callback = callback
        self.calls: list[tuple[Path, str]] = []

    def transcribe(self, audio_path: Path, language: str = "pt") -> AsrResult:
        path = Path(audio_path)
        self.calls.append((path, language))
        if self.callback is not None:
            return self.callback(path, language)
        value = self.results.get(str(path), self.results.get(path.name, self.default))
        if isinstance(value, AsrResult):
            return value
        return AsrResult(text=str(value), language=language, backend="fake", processing_seconds=0.0)


@dataclass(frozen=True)
class AsrCapability:
    backend: str
    module: str
    distribution: str
    package_installed: bool
    version: str | None
    cuda_available: bool | None = None

    def to_dict(self) -> dict:
        return asdict(self)


_CANDIDATES = (
    ("faster-whisper", "faster_whisper", "faster-whisper"),
    ("whisper", "whisper", "openai-whisper"),
)


def detect_asr_capabilities() -> tuple[AsrCapability, ...]:
    """Inspect package metadata without importing ASR, Torch, or CUDA runtimes."""
    capabilities = []
    for backend, module, distribution in _CANDIDATES:
        installed = util.find_spec(module) is not None
        try:
            version = metadata.version(distribution) if installed else None
        except metadata.PackageNotFoundError:
            version = None
        capabilities.append(AsrCapability(
            backend=backend,
            module=module,
            distribution=distribution,
            package_installed=installed,
            version=version,
            cuda_available=None,
        ))
    return tuple(capabilities)
