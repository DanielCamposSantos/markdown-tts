$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$Pythonw = Join-Path $ProjectRoot ".venv\Scripts\pythonw.exe"

if (-not (Test-Path -LiteralPath $Pythonw)) {
    throw "Ambiente virtual não encontrado. Execute scripts/setup_windows.ps1 primeiro."
}

Start-Process -FilePath $Pythonw -ArgumentList "-m", "app.launcher" -WorkingDirectory $ProjectRoot
