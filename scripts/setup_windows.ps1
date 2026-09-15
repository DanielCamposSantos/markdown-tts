param(
    [switch]$InstallAsr,
    [switch]$InstallDev
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$VenvPython = Join-Path $ProjectRoot ".venv\Scripts\python.exe"

if (-not (Test-Path -LiteralPath $VenvPython)) {
    $Python = Get-Command py -ErrorAction Stop
    & $Python.Source -3.12 -m venv (Join-Path $ProjectRoot ".venv")
}

$Version = & $VenvPython -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')"
if ($Version -ne "3.12") {
    throw "Python 3.12 is required; found $Version."
}

$Constraints = Join-Path $ProjectRoot "requirements\constraints.txt"
& $VenvPython -m pip install "torch==2.9.1+cu128" "torchaudio==2.9.1+cu128" --index-url "https://download.pytorch.org/whl/cu128"
& $VenvPython -m pip install -r (Join-Path $ProjectRoot "requirements\runtime.txt") -c $Constraints

if ($InstallAsr) {
    & $VenvPython -m pip install -r (Join-Path $ProjectRoot "requirements\asr.txt") -c $Constraints
}
if ($InstallDev) {
    & $VenvPython -m pip install -r (Join-Path $ProjectRoot "requirements\dev.txt") -c $Constraints
}

Write-Host "Dependencies installed. Models and narrator voice were not downloaded or modified."
