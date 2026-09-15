$ErrorActionPreference = 'Stop'
$root = [System.IO.Path]::GetFullPath((Split-Path -Parent $PSScriptRoot))
$python = Join-Path $root '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $python)) { throw 'Missing .venv Python.' }
if (Get-NetTCPConnection -LocalPort 7860 -State Listen -ErrorAction SilentlyContinue) { throw 'Stop backend before validation.' }
Push-Location $root
try {
    & $python -m app.maintenance environment-check --require-asr
    if ($LASTEXITCODE -ne 0) { throw 'Environment check failed.' }
    & $python -m pytest
    if ($LASTEXITCODE -ne 0) { throw 'Tests failed.' }
    & $python -m pip check
    if ($LASTEXITCODE -ne 0) { throw 'pip check failed.' }
    & node --check static/app.js
    if ($LASTEXITCODE -ne 0) { throw 'app.js syntax failed.' }
    & node --check static/file_input.js
    if ($LASTEXITCODE -ne 0) { throw 'file_input.js syntax failed.' }
    & git diff --check
    if ($LASTEXITCODE -ne 0) { throw 'git diff check failed.' }
    $check = @'
import sqlite3
from app.config import LIBRARY_DIR
from app.persistence.library import LibraryStore
LibraryStore(LIBRARY_DIR)
db = sqlite3.connect(LIBRARY_DIR / "library.db")
assert db.execute("PRAGMA quick_check").fetchone()[0] == "ok"
tables = ("documents", "generations", "generation_jobs", "playback_state", "unit_regenerations")
for table in tables:
    count = db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
    print(f"{table}: {count}")
    assert count == 0, f"{table} is not empty"
assert not (LIBRARY_DIR / "operational-settings.json").exists()
print("Clean DB: PASS; default preset=standard; ASR=OFF")
'@
    & $python -c $check
    if ($LASTEXITCODE -ne 0) { throw 'Clean DB validation failed.' }
    foreach ($path in @('benchmarks/audio', 'benchmarks/results', 'logs', 'runtime', 'backups')) {
        if (Test-Path -LiteralPath $path) {
            $found = @(Get-ChildItem -LiteralPath $path -Recurse -File -Force)
            if ($found.Count -gt 0) { throw "Generated files remain in $path" }
        }
    }
    $legacy = @(Get-ChildItem -LiteralPath library -Recurse -File -Force |
        Where-Object { $_.Name -ne 'library.db' })
    if ($legacy.Count -gt 0) { throw 'Old Library artifacts remain.' }
    Write-Host 'Clean install validation: PASS'
} finally { Pop-Location }
