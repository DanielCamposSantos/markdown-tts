from __future__ import annotations

from dataclasses import asdict, dataclass
from importlib import metadata, util
import os
from pathlib import Path
import sys
import time
from typing import Callable, Mapping, Protocol
from app.config import ASR_VALIDATION_ENABLED


ASR_ENABLED = ASR_VALIDATION_ENABLED


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


class FasterWhisperAsrEngine:
    """Opt-in Faster-Whisper backend backed exclusively by a local model."""

    def __init__(
        self,
        model_path: Path,
        *,
        device: str = "cuda",
        compute_type: str = "float16",
        beam_size: int = 5,
        cpu_threads: int = 0,
    ) -> None:
        self.model_path = Path(model_path).resolve()
        self.device = device
        self.compute_type = compute_type
        self.beam_size = beam_size
        self.cpu_threads = cpu_threads
        self._model = None
        self._dll_directory = None
        self.load_seconds: float | None = None

    def _prepare_windows_dlls(self) -> None:
        if sys.platform != "win32" or self._dll_directory is not None:
            return
        torch_lib = Path(sys.prefix) / "Lib" / "site-packages" / "torch" / "lib"
        if not torch_lib.is_dir():
            raise RuntimeError(f"Diretório de DLLs do PyTorch não encontrado: {torch_lib}")
        self._dll_directory = os.add_dll_directory(str(torch_lib))

    def load(self) -> float:
        if self._model is not None:
            return self.load_seconds or 0.0
        if not self.model_path.is_dir():
            raise FileNotFoundError(f"Modelo Faster-Whisper local não encontrado: {self.model_path}")
        self._prepare_windows_dlls()
        from faster_whisper import WhisperModel

        started = time.perf_counter()
        self._model = WhisperModel(
            str(self.model_path),
            device=self.device,
            compute_type=self.compute_type,
            cpu_threads=self.cpu_threads,
            local_files_only=True,
        )
        self.load_seconds = time.perf_counter() - started
        return self.load_seconds

    def unload(self) -> None:
        self._model = None
        if self._dll_directory is not None:
            self._dll_directory.close()
            self._dll_directory = None

    def transcribe(self, audio_path: Path, language: str = "pt") -> AsrResult:
        self.load()
        started = time.perf_counter()
        segments, info = self._model.transcribe(
            str(Path(audio_path)),
            language=language,
            beam_size=self.beam_size,
            vad_filter=False,
        )
        materialized = tuple(segments)
        elapsed = time.perf_counter() - started
        text = " ".join(segment.text.strip() for segment in materialized if segment.text.strip()).strip()
        return AsrResult(
            text=text,
            language=getattr(info, "language", language),
            duration_seconds=getattr(info, "duration", None),
            confidence=None,
            backend="faster-whisper",
            model=self.model_path.name,
            processing_seconds=elapsed,
        )


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
