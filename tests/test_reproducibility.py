from __future__ import annotations

import json
import re
import sys
from importlib import metadata
from pathlib import Path
from types import SimpleNamespace

import pytest

from app import config
from app.maintenance.environment import EnvironmentChecker
from app.pronunciation import PT_BR_PROFILE
from app.reproducibility import DEPENDENCY_LOCK_PATH, file_sha256, generation_reproducibility, load_baseline


ROOT = Path(__file__).resolve().parents[1]


def requirement_lines(name):
    return [line.strip() for line in (ROOT / "requirements" / name).read_text(encoding="utf-8").splitlines() if line.strip() and not line.lstrip().startswith("#")]


def test_official_requirements_exist_and_are_parseable():
    for name in ("runtime.txt", "asr.txt", "dev.txt", "constraints.txt"):
        lines = requirement_lines(name)
        assert lines
        assert all(" " not in line or line.startswith("-r ") for line in lines)


@pytest.mark.parametrize("package", ["torch", "transformers", "numpy", "soundfile", "faster-whisper", "ctranslate2", "wsproto"])
def test_critical_packages_have_exact_pins(package):
    pins = {line.split("==", 1)[0].lower(): line.split("==", 1)[1] for line in requirement_lines("constraints.txt") if "==" in line}
    assert package in pins and pins[package] and not re.search(r"[<>=,]", pins[package])


def test_python_support_and_baseline_are_safe():
    baseline = load_baseline()
    assert baseline["schema_version"] == 1
    assert baseline["python"]["supported"] == ">=3.12,<3.13"
    serialized = json.dumps(baseline)
    assert not re.search(r"[A-Za-z]:[\\/]", serialized)
    assert "Mig e Dan" not in serialized


def test_baseline_matches_voice_pronunciation_and_lock():
    baseline = load_baseline()
    voice = json.loads((ROOT / "voices/narrator_reference.manifest.json").read_text(encoding="utf-8"))
    assert baseline["voice"]["sha256"] == voice["sha256"]
    assert baseline["pronunciation"]["profile"] == PT_BR_PROFILE
    assert baseline["dependencies"]["lock_sha256"] == file_sha256(DEPENDENCY_LOCK_PATH)


def test_baseline_matches_tts_and_asr_configuration():
    baseline = load_baseline(); sampling = baseline["tts"]["sampling"]
    assert baseline["tts"]["model_id"] == config.MODEL_ID
    assert baseline["tts"]["language"] == config.LANGUAGE
    assert sampling == {"do_sample": True, "max_new_tokens": config.MAX_NEW_TOKENS, "audio_temperature": config.AUDIO_TEMPERATURE, "audio_top_p": config.AUDIO_TOP_P, "audio_top_k": config.AUDIO_TOP_K, "audio_repetition_penalty": config.AUDIO_REPETITION_PENALTY}
    assert baseline["tts"]["guard_policy"] == config.AUDIO_GUARD_POLICY_ID
    asr = baseline["asr"]
    assert (asr["backend"], asr["compute_type"], asr["language"], asr["beam_size"], asr["enabled_by_default"]) == (config.ASR_BACKEND, config.ASR_COMPUTE_TYPE, config.ASR_LANGUAGE, config.ASR_BEAM_SIZE, False)
    assert asr["validator_policy"] == config.ASR_VALIDATOR_POLICY_ID


def test_generation_reproducibility_ids_and_optional_asr():
    regular = generation_reproducibility(asr_used=False)
    assert regular["pronunciation_profile"] == "pt-BR-v3"
    assert regular["voice_id"] == "narrator-reference-v1"
    assert "asr_config_id" not in regular
    assert generation_reproducibility(asr_used=True)["asr_config_id"] == "faster-whisper-medium-int8-float16-v1"


def fake_environment(tmp_path):
    (tmp_path / "reproducibility").mkdir(); (tmp_path / "requirements").mkdir(); (tmp_path / "vendor/MOSS-TTS").mkdir(parents=True)
    baseline = load_baseline(); baseline["dependencies"]["lock_path"] = "requirements/constraints.txt"
    constraints = DEPENDENCY_LOCK_PATH.read_bytes(); (tmp_path / "requirements/constraints.txt").write_bytes(constraints)
    baseline["dependencies"]["lock_sha256"] = file_sha256(tmp_path / "requirements/constraints.txt")
    (tmp_path / "reproducibility/baseline.json").write_text(json.dumps(baseline), encoding="utf-8")
    pins = EnvironmentChecker._pins(tmp_path / "requirements/constraints.txt")
    getter = lambda name: pins[name.lower()]
    voice = lambda: SimpleNamespace(healthy=True, status="healthy")
    return dict(root=tmp_path, python_version=SimpleNamespace(major=3, minor=12, micro=10), version_getter=getter, which=lambda name: "ffmpeg", cuda_probe=lambda: (True, "12.8", "RTX 3060"), voice_check=voice)


def test_environment_check_passes_with_valid_fakes(tmp_path):
    assert EnvironmentChecker(**fake_environment(tmp_path)).run().status == "PASS"


def test_environment_check_reports_wrong_python_missing_ffmpeg_cuda_moss_voice(tmp_path):
    kwargs = fake_environment(tmp_path); kwargs.update(python_version=SimpleNamespace(major=3, minor=11, micro=9), which=lambda _: None, cuda_probe=lambda: (False, None, None), voice_check=lambda: SimpleNamespace(healthy=False, status="hash_mismatch"))
    (tmp_path / "vendor/MOSS-TTS").rmdir()
    report = EnvironmentChecker(**kwargs).run()
    assert report.status == "FAIL"
    failed = {item.name for item in report.checks if item.status == "FAIL"}
    assert {"python", "ffmpeg", "cuda", "moss_vendor", "voice"} <= failed


def test_environment_check_requires_asr_model_only_when_requested(tmp_path):
    checker = EnvironmentChecker(**fake_environment(tmp_path))
    assert checker.run(require_asr=False).status == "PASS"
    report = checker.run(require_asr=True)
    assert any(x.name == "asr_model" and x.status == "FAIL" for x in report.checks)


def test_environment_check_detects_package_version_mismatch(tmp_path):
    kwargs = fake_environment(tmp_path); original = kwargs["version_getter"]
    kwargs["version_getter"] = lambda name: "0.0" if name == "torch" else original(name)
    assert EnvironmentChecker(**kwargs).run().status == "FAIL"


def test_setup_script_is_safe_and_does_not_download_models_or_change_global_path():
    script = (ROOT / "scripts/setup_windows.ps1").read_text(encoding="utf-8").lower()
    assert "huggingface-cli" not in script
    assert "snapshot_download" not in script
    assert "from_pretrained" not in script
    assert "faster-whisper-medium" not in script and "moss-tts-local-transformer" not in script
    assert "setx" not in script and "$env:path" not in script
    assert "-installasr" not in script  # PowerShell declares InstallAsr, not a hidden invocation.
    assert "https://download.pytorch.org/whl/cu128" in script


def test_environment_current_remains_historical_not_a_lock():
    assert (ROOT / "environment-current.txt").is_file()
    assert "environment-current.txt" not in load_baseline()["dependencies"]["lock_path"]
