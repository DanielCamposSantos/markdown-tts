$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$Pythonw = Join-Path $ProjectRoot ".venv\Scripts\pythonw.exe"
$Desktop = [Environment]::GetFolderPath("Desktop")
$ShortcutPath = Join-Path $Desktop "Markdown TTS.lnk"
$Icon = Join-Path $ProjectRoot "assets\markdown_tts.ico"

if (-not (Test-Path -LiteralPath $Pythonw)) {
    throw "Ambiente virtual não encontrado: $Pythonw"
}

$Shell = New-Object -ComObject WScript.Shell
$Shortcut = $Shell.CreateShortcut($ShortcutPath)
$Shortcut.TargetPath = $Pythonw
$Shortcut.Arguments = "-m app.launcher"
$Shortcut.WorkingDirectory = $ProjectRoot
$Shortcut.Description = "Markdown TTS"
if (Test-Path -LiteralPath $Icon) {
    $Shortcut.IconLocation = "$Icon,0"
}
$Shortcut.Save()
Write-Output "Atalho criado: $ShortcutPath"
