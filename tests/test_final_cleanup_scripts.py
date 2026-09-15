from pathlib import Path
import os
import subprocess

import pytest


ROOT = Path(__file__).resolve().parents[1]
CLEANUP = ROOT / "scripts" / "final_cleanup.ps1"
VALIDATE = ROOT / "scripts" / "validate_clean_install.ps1"


def test_scripts_exist_and_are_powershell():
    assert CLEANUP.is_file() and VALIDATE.is_file()
    assert CLEANUP.read_text(encoding="utf-8").startswith("param(")


@pytest.mark.parametrize("phrase", [
    "[switch]$Apply", "[switch]$ConfirmCleanup", "-Apply also requires -ConfirmCleanup",
    "git -C $root status --porcelain", "Get-NetTCPConnection -LocalPort 7860",
    "missing project marker", "project root is too broad", "path outside project",
    "reparse point", "tracked source inside cleanup target", "DRY RUN ONLY",
])
def test_cleanup_safety_gates_present(phrase):
    assert phrase in CLEANUP.read_text(encoding="utf-8")


@pytest.mark.parametrize("phrase", [
    "'library'", "'benchmarks/audio'", "'benchmarks/results'", "'logs'",
    "'runtime'", "'backups'", "'.pytest_cache'", "'__pycache__'",
])
def test_managed_targets_declared(phrase):
    assert phrase in CLEANUP.read_text(encoding="utf-8")


def test_protected_runtime_and_models_not_cleanup_targets():
    script = CLEANUP.read_text(encoding="utf-8")
    assert "'.venv'" in script and "'vendor'" in script and "'voices'" in script
    assert "'benchmarks/models'" in script
    assert "wsproto" in script
    assert "pip uninstall" not in script
    assert "taskkill" not in script


def test_validation_script_has_no_deletion_or_installation():
    script = VALIDATE.read_text(encoding="utf-8")
    assert "Remove-Item" not in script
    assert "pip uninstall" not in script
    assert "environment-check --require-asr" in script
    assert "PRAGMA quick_check" in script
    assert "assert count == 0" in script
    assert "operational-settings.json" in script


@pytest.mark.skipif(os.name != "nt", reason="PowerShell Windows only")
def test_dry_run_keeps_a_managed_file():
    marker = ROOT / "voices" / "narrator_reference.manifest.json"
    assert marker.is_file()
    before = marker.read_bytes()
    result = subprocess.run(
        ["powershell", "-NoProfile", "-File", str(CLEANUP)],
        cwd=ROOT, capture_output=True, text=True, check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "DRY RUN ONLY" in result.stdout
    assert marker.read_bytes() == before


@pytest.mark.skipif(os.name != "nt", reason="PowerShell Windows only")
def test_apply_without_second_confirmation_rejected():
    marker = ROOT / "voices" / "narrator_reference.manifest.json"
    before = marker.read_bytes()
    result = subprocess.run(
        ["powershell", "-NoProfile", "-File", str(CLEANUP), "-Apply"],
        cwd=ROOT, capture_output=True, text=True, check=False,
    )
    assert result.returncode != 0
    assert marker.read_bytes() == before
