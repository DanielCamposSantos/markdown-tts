from __future__ import annotations

import csv
import gc
from importlib import metadata
import json
import os
from pathlib import Path
import sys
import time


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.validation.asr import FasterWhisperAsrEngine
from app.validation.validator import validate_acceptable_results
from benchmarks.run_asr_benchmark import _ram_mb, _server_is_running, _vram_mb


CASE_IDS = ("cplusplus", "https-tls", "tcp-flags")
MODEL_PATH = ROOT / "benchmarks" / "models" / "faster-whisper-medium"
MANIFEST_PATH = ROOT / "benchmarks" / "audio" / "manifest.json"
OUTPUT_PATH = ROOT / "benchmarks" / "results" / "pronunciation-technical-final.json"


def main() -> int:
    if _server_is_running():
        raise RuntimeError("O servidor Markdown TTS deve estar parado")
    if not MODEL_PATH.is_dir():
        raise FileNotFoundError(f"Modelo local ausente: {MODEL_PATH}")
    if sys.platform == "win32":
        torch_lib = Path(sys.prefix) / "Lib" / "site-packages" / "torch" / "lib"
        if not torch_lib.is_dir():
            raise FileNotFoundError(f"Diretório local de DLLs ausente: {torch_lib}")
        # CTranslate2 resolves transitive CUDA DLLs through this process only.
        os.environ["PATH"] = str(torch_lib) + os.pathsep + os.environ.get("PATH", "")
    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    entries = {entry["case_id"]: entry for entry in manifest["fixtures"]}
    selected = [entries[case_id] for case_id in CASE_IDS]
    if any(entry.get("review_status") != "GOOD" for entry in selected):
        raise RuntimeError("Todas as fixtures técnicas precisam estar GOOD")

    engine = FasterWhisperAsrEngine(
        MODEL_PATH, device="cuda", compute_type="int8_float16", beam_size=5,
    )
    memory_before = {"ram_mb": _ram_mb(), "vram_mb": _vram_mb()}
    load_seconds = engine.load()
    memory_loaded = {"ram_mb": _ram_mb(), "vram_mb": _vram_mb()}
    results = []
    try:
        for fixture in selected:
            audio_path = ROOT / "benchmarks" / "audio" / fixture["filename"]
            canonical = fixture["expected_text"]
            effective = fixture["effective_synthesis_text"]
            asr_result = engine.transcribe(audio_path, language="pt")
            validation = validate_acceptable_results([canonical, effective], asr_result)
            duration = float(fixture["duration_seconds"])
            elapsed = float(asr_result.processing_seconds or 0.0)
            results.append({
                "fixture_id": fixture["case_id"],
                "pronunciation_profile": fixture["pronunciation"]["profile"],
                "canonical_expected": canonical,
                "effective_expected": effective,
                "transcript": asr_result.text,
                "best_expected_form": "canonical" if validation.expected_text == canonical else "effective",
                "similarity": validation.similarity_score,
                "token_coverage": validation.token_coverage,
                "missing_tokens": list(validation.missing_tokens),
                "extra_tokens": list(validation.extra_tokens),
                "status": validation.status,
                "reasons": list(validation.reasons),
                "audio_duration_seconds": duration,
                "transcription_seconds": elapsed,
                "rtf": elapsed / duration,
                "ram_mb": _ram_mb(),
                "vram_mb": _vram_mb(),
            })
        memory_after_transcription = {"ram_mb": _ram_mb(), "vram_mb": _vram_mb()}
    finally:
        engine.unload()
        gc.collect()
        time.sleep(0.5)

    total_audio = sum(item["audio_duration_seconds"] for item in results)
    total_transcription = sum(item["transcription_seconds"] for item in results)
    payload = {
        "backend": "faster-whisper",
        "backend_version": metadata.version("faster-whisper"),
        "model": MODEL_PATH.name,
        "device": "cuda",
        "compute_type": "int8_float16",
        "language": "pt",
        "beam_size": 5,
        "batch_size": 1,
        "vad_filter": False,
        "load_seconds": load_seconds,
        "memory_before": memory_before,
        "memory_loaded": memory_loaded,
        "memory_after_transcription": memory_after_transcription,
        "memory_after_unload": {"ram_mb": _ram_mb(), "vram_mb": _vram_mb()},
        "results": results,
        "summary": {
            "pass": sum(item["status"] == "pass" for item in results),
            "warn": sum(item["status"] == "warn" for item in results),
            "fail": sum(item["status"] == "fail" for item in results),
            "total_audio_seconds": total_audio,
            "total_transcription_seconds": total_transcription,
            "total_rtf": total_transcription / total_audio,
        },
    }
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    temporary = OUTPUT_PATH.with_name(f".{OUTPUT_PATH.name}.tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(OUTPUT_PATH)
    csv_path = OUTPUT_PATH.with_suffix(".csv")
    with csv_path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=tuple(results[0]))
        writer.writeheader()
        writer.writerows(results)
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
