from __future__ import annotations

import argparse
from dataclasses import replace
import gc
from importlib import metadata
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import time


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.validation.asr import FasterWhisperAsrEngine
from app.validation.benchmark import load_corpus, run_benchmark, write_csv_report, write_json_report


def load_good_cases(corpus_path: Path, manifest_path: Path):
    cases = load_corpus(corpus_path)
    manifest = json.loads(Path(manifest_path).read_text(encoding="utf-8"))
    entries = {entry["case_id"]: entry for entry in manifest["fixtures"]}
    good = []
    excluded = []
    for case in cases:
        entry = entries.get(case.case_id)
        status = entry.get("review_status") if entry else "MISSING"
        if status == "GOOD":
            good.append(replace(case, duration_seconds=float(entry["duration_seconds"])))
        else:
            excluded.append({"case_id": case.case_id, "review_status": status})
    if not good:
        raise ValueError("o manifesto não contém fixtures com review_status=GOOD")
    return tuple(good), tuple(excluded)


def _server_is_running() -> bool:
    with socket.socket() as probe:
        probe.settimeout(0.25)
        return probe.connect_ex(("127.0.0.1", 7860)) == 0


def _ram_mb() -> float | None:
    if sys.platform != "win32":
        return None
    import ctypes
    from ctypes import wintypes

    class Counters(ctypes.Structure):
        _fields_ = [
            ("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD),
            ("PeakWorkingSetSize", ctypes.c_size_t), ("WorkingSetSize", ctypes.c_size_t),
            ("QuotaPeakPagedPoolUsage", ctypes.c_size_t), ("QuotaPagedPoolUsage", ctypes.c_size_t),
            ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t), ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
            ("PagefileUsage", ctypes.c_size_t), ("PeakPagefileUsage", ctypes.c_size_t),
        ]

    counters = Counters()
    counters.cb = ctypes.sizeof(counters)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    psapi = ctypes.WinDLL("psapi", use_last_error=True)
    kernel32.GetCurrentProcess.restype = wintypes.HANDLE
    psapi.GetProcessMemoryInfo.argtypes = (wintypes.HANDLE, ctypes.POINTER(Counters), wintypes.DWORD)
    psapi.GetProcessMemoryInfo.restype = wintypes.BOOL
    ok = psapi.GetProcessMemoryInfo(kernel32.GetCurrentProcess(), ctypes.byref(counters), counters.cb)
    return counters.WorkingSetSize / 1024**2 if ok else None


def _vram_mb() -> float | None:
    try:
        output = subprocess.check_output(
            ["nvidia-smi", "--query-compute-apps=pid,used_memory", "--format=csv,noheader,nounits"],
            text=True,
            stderr=subprocess.DEVNULL,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    for line in output.splitlines():
        fields = [field.strip() for field in line.split(",")]
        if len(fields) == 2 and fields[0] == str(os.getpid()):
            try:
                return float(fields[1])
            except ValueError:
                break
    try:
        total = subprocess.check_output(
            ["nvidia-smi", "--query-gpu=memory.used", "--format=csv,noheader,nounits"],
            text=True,
            stderr=subprocess.DEVNULL,
        ).strip().splitlines()[0]
        return float(total)
    except (OSError, subprocess.SubprocessError, ValueError, IndexError):
        return None


def main() -> int:
    parser = argparse.ArgumentParser(description="Benchmark isolado do Faster-Whisper")
    parser.add_argument("--model-path", type=Path, required=True)
    parser.add_argument("--compute-type", choices=("float16", "int8_float16"), required=True)
    parser.add_argument("--corpus", type=Path, default=ROOT / "benchmarks" / "corpus-pt-br.json")
    parser.add_argument("--manifest", type=Path, default=ROOT / "benchmarks" / "audio" / "manifest.json")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if _server_is_running():
        parser.error("o servidor Markdown TTS deve estar parado (porta 7860 ocupada)")

    try:
        cases, excluded = load_good_cases(args.corpus, args.manifest)
    except (OSError, ValueError, KeyError, json.JSONDecodeError) as exc:
        parser.error(f"manifesto acústico inválido: {exc}")
    missing = [str(case.audio_path) for case in cases if not case.audio_path.is_file()]
    if missing:
        parser.error("áudios neutros ausentes:\n" + "\n".join(missing))

    engine = FasterWhisperAsrEngine(args.model_path, compute_type=args.compute_type)
    before = {"ram_mb": _ram_mb(), "vram_mb": _vram_mb()}
    load_seconds = engine.load()
    loaded = {"ram_mb": _ram_mb(), "vram_mb": _vram_mb()}
    report = run_benchmark(cases, engine, metrics=lambda: {"ram_mb": _ram_mb(), "vram_mb": _vram_mb()})
    after_transcription = {"ram_mb": _ram_mb(), "vram_mb": _vram_mb()}
    metadata_payload = {
        "backend": "faster-whisper", "backend_version": metadata.version("faster-whisper"),
        "model": args.model_path.name, "compute_type": args.compute_type, "device": "cuda",
        "language": "pt", "beam_size": 5, "batch_size": 1, "vad_filter": False,
        "load_seconds": load_seconds, "memory_before": before, "memory_loaded": loaded,
        "memory_after_transcription": after_transcription,
        "good_fixture_count": len(cases),
        "excluded_fixtures": [
            {**item, "status": "SKIPPED", "reason": "blocked_pronunciation" if item["review_status"] == "BLOCKED_PRONUNCIATION" else "not_human_approved"}
            for item in excluded
        ],
    }
    engine.unload()
    gc.collect()
    time.sleep(0.5)
    metadata_payload["memory_after_unload"] = {"ram_mb": _ram_mb(), "vram_mb": _vram_mb()}
    report = replace(report, metadata=metadata_payload)
    write_json_report(report, args.output.with_suffix(".json"))
    write_csv_report(report, args.output.with_suffix(".csv"))
    print(json.dumps(report.to_dict(), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
