from __future__ import annotations

import argparse
from dataclasses import replace
import hashlib
import json
import os
from pathlib import Path
import sys
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

CORPUS_PATH = ROOT / "benchmarks" / "corpus-pt-br.json"
AUDIO_DIR = ROOT / "benchmarks" / "audio"
MANIFEST_PATH = AUDIO_DIR / "manifest.json"


def load_fixture_cases(corpus_path: Path = CORPUS_PATH) -> tuple[dict[str, Any], ...]:
    source = Path(corpus_path).resolve()
    payload = json.loads(source.read_text(encoding="utf-8"))
    cases = []
    for position, item in enumerate(payload["cases"], start=1):
        destination = (source.parent / item["audio_path"]).resolve()
        if destination.parent != AUDIO_DIR.resolve() or destination.suffix.lower() != ".wav":
            raise ValueError(f"Destino de fixture inválido: {destination}")
        cases.append({
            "index": position,
            "id": item["id"],
            "expected_text": item["expected_text"],
            "destination": destination,
        })
    return tuple(cases)


def ensure_destinations_available(cases: tuple[dict[str, Any], ...], overwrite: bool) -> None:
    existing = [str(case["destination"]) for case in cases if case["destination"].exists()]
    if existing and not overwrite:
        raise FileExistsError(
            "Fixtures existentes; use --overwrite para substituí-las:\n" + "\n".join(existing)
        )


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _atomic_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temporary, path)


def _existing_manifest() -> dict[str, Any] | None:
    if not MANIFEST_PATH.is_file():
        return None
    return json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))


def generate_fixtures(
    cases: tuple[dict[str, Any], ...],
    *,
    fixture_seed_offset: int = 0,
) -> dict[str, Any]:
    import soundfile as sf

    from app.audio_generation_guard import generate_with_runaway_guard
    from app.audio_io import write_unit_wav
    from app.config import BASE_SEED, LANGUAGE, MODEL_ID, REFERENCE_AUDIO
    from app.moss_engine import MossEngine, cleanup_cuda
    from app.pronunciation import PT_BR_RESOLVER
    from app.speech_plan import SpeechUnit

    previous_manifest = _existing_manifest()
    previous_entries = {
        entry["case_id"]: entry for entry in (previous_manifest or {}).get("fixtures", [])
    }
    engine = MossEngine()
    engine.load()
    generated = []
    failures = []
    try:
        for case in cases:
            unit = SpeechUnit(
                index=case["index"],
                kind="list" if case["id"] == "context-list" else "paragraph",
                display_text=case["expected_text"],
                synthesis_text=case["expected_text"],
                source_atoms=(case["expected_text"],),
                pause_after_ms=0,
            )
            pronunciation = PT_BR_RESOLVER.resolve(unit.synthesis_text)
            effective_unit = replace(unit, synthesis_text=pronunciation.synthesis_text)
            seeds = []
            messages = []

            def attempt(seed: int):
                seeds.append(seed)
                return engine._generate_unit(engine.model, effective_unit, seed=seed)

            try:
                output = generate_with_runaway_guard(
                    unit=effective_unit,
                    generate_attempt=attempt,
                    audio_pad_token_id=int(engine.processor.model_config.audio_pad_token_id),
                    first_seed=BASE_SEED + unit.index + fixture_seed_offset,
                    log=lambda message: (messages.append(message), print(message)),
                )
                generated.append((case, unit, pronunciation, output, seeds, messages))
            except Exception as exc:
                failures.append({"id": case["id"], "error": f"{type(exc).__name__}: {exc}"})

        model = engine.model
        engine.model = model.to("cpu")
        cleanup_cuda()
        engine.processor.audio_tokenizer = engine.processor.audio_tokenizer.to("cuda:0")
        new_entries = []
        try:
            for case, unit, pronunciation, output, seeds, messages in generated:
                destination = case["destination"]
                temporary = destination.with_name(f".{destination.stem}.tmp.wav")
                try:
                    audio = engine._decode_output(output)
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    write_unit_wav(audio, int(engine.processor.model_config.sampling_rate), temporary)
                    os.replace(temporary, destination)
                    info = sf.info(str(destination))
                    previous = previous_entries.get(case["id"])
                    entry = {
                        "case_id": case["id"],
                        "filename": destination.name,
                        "expected_text": case["expected_text"],
                        "effective_synthesis_text": pronunciation.synthesis_text,
                        "pronunciation": {
                            "profile": PT_BR_RESOLVER.profile,
                            "rules": [rule.to_dict() for rule in pronunciation.applied_rules],
                        },
                        "seed": seeds[-1],
                        "attempts": len(seeds),
                        "guard_retries": max(0, len(seeds) - 1),
                        "guard_messages": messages,
                        "review_status": "PENDING",
                        "fixture_attempt": int((previous or {}).get("fixture_attempt", 1)) + (1 if previous else 0),
                        "duration_seconds": info.frames / info.samplerate,
                        "sample_rate": info.samplerate,
                        "channels": info.channels,
                        "sha256": _sha256(destination),
                    }
                    if previous is not None:
                        entry["previous_seed"] = previous["seed"]
                    new_entries.append(entry)
                except Exception as exc:
                    temporary.unlink(missing_ok=True)
                    failures.append({"id": case["id"], "error": f"{type(exc).__name__}: {exc}"})
        finally:
            engine.processor.audio_tokenizer = engine.processor.audio_tokenizer.to("cpu")
            cleanup_cuda()
    finally:
        engine.unload()

    for entry in new_entries:
        previous_entries[entry["case_id"]] = entry
    ordered_entries = [
        previous_entries[case["id"]]
        for case in load_fixture_cases()
        if case["id"] in previous_entries
    ]
    manifest = {
        "schema_version": 2,
        "model": MODEL_ID,
        "reference_audio": str(REFERENCE_AUDIO.relative_to(ROOT)).replace("\\", "/"),
        "language": LANGUAGE,
        "base_seed": BASE_SEED,
        "fixtures": ordered_entries,
        "failures": failures,
    }
    _atomic_json(MANIFEST_PATH, manifest)
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser(description="Gera fixtures PT-BR neutras com o pipeline MOSS")
    parser.add_argument("--overwrite", action="store_true", help="substitui WAVs locais existentes")
    parser.add_argument("--case", action="append", dest="case_ids", help="gera somente o case id informado")
    parser.add_argument("--fixture-seed-offset", type=int, default=0, help="offset determinístico da fixture")
    args = parser.parse_args()
    cases = load_fixture_cases()
    if args.case_ids:
        requested = set(args.case_ids)
        known = {case["id"] for case in cases}
        unknown = sorted(requested - known)
        if unknown:
            parser.error("case id desconhecido: " + ", ".join(unknown))
        cases = tuple(case for case in cases if case["id"] in requested)
    ensure_destinations_available(cases, args.overwrite)
    manifest = generate_fixtures(cases, fixture_seed_offset=args.fixture_seed_offset)
    print(json.dumps(manifest, ensure_ascii=False, indent=2))
    return 1 if manifest["failures"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
