param(
    [switch]$Apply,
    [switch]$ConfirmCleanup
)

$ErrorActionPreference = 'Stop'
$root = [System.IO.Path]::GetFullPath((Split-Path -Parent $PSScriptRoot)).TrimEnd('\')
$prefix = $root + '\'
if (((Get-Item -LiteralPath $root -Force).Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) {
    throw 'ABORT: project root is a reparse point.'
}
$dangerous = @(
    [System.IO.Path]::GetPathRoot($root).TrimEnd('\'),
    [Environment]::GetFolderPath('UserProfile'),
    [Environment]::GetFolderPath('Desktop'),
    (Join-Path ([Environment]::GetFolderPath('UserProfile')) 'Downloads')
)
if ($dangerous -contains $root -or $root -match '^[A-Za-z]:\\Users$') { throw 'ABORT: project root is too broad.' }
foreach ($marker in @('.git', 'app', 'requirements', 'voices/narrator_reference.manifest.json')) {
    if (-not (Test-Path -LiteralPath (Join-Path $root $marker))) { throw "ABORT: missing project marker $marker" }
}
if ($Apply -and -not $ConfirmCleanup) { throw 'ABORT: -Apply also requires -ConfirmCleanup.' }
if (Get-NetTCPConnection -LocalPort 7860 -State Listen -ErrorAction SilentlyContinue) {
    throw 'ABORT: port 7860 has a listener. Stop the launcher/backend first.'
}
$commit = (& git -C $root rev-parse HEAD).Trim()
if ($LASTEXITCODE -ne 0 -or -not $commit) { throw 'ABORT: Git checkpoint unavailable.' }
$dirty = @(& git -C $root status --porcelain)
if ($Apply -and $dirty.Count -gt 0) { throw 'ABORT: Git is dirty. Commit/checkpoint source changes first.' }
Write-Host "Git checkpoint: $commit"
if ($dirty.Count -gt 0) { Write-Warning 'Git is dirty; dry-run is allowed, apply is not.' }

$protected = @('.git', '.venv', 'app', 'static', 'scripts', 'tests', 'requirements',
    'reproducibility', 'docs', 'vendor', 'voices', 'benchmarks/models')
$targets = @('library', 'backups', 'logs', 'runtime', 'temp', 'outputs',
    'benchmarks/audio', 'benchmarks/results', '.pytest_cache', 'htmlcov')
$items = [System.Collections.Generic.List[object]]::new()

function Add-CleanupItem([string]$relative, [string]$category) {
    $full = [System.IO.Path]::GetFullPath((Join-Path $root $relative))
    if (-not $full.StartsWith($prefix, [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "ABORT: path outside project: $relative"
    }
    foreach ($guard in $protected) {
        $guardFull = [System.IO.Path]::GetFullPath((Join-Path $root $guard))
        if ($full.Equals($guardFull, [System.StringComparison]::OrdinalIgnoreCase) -or
            $full.StartsWith($guardFull + '\', [System.StringComparison]::OrdinalIgnoreCase)) {
            $cacheException = $category -eq 'CACHE_SAFE_TO_DELETE' -and
                ((Split-Path -Leaf $full) -eq '__pycache__' -or $full.EndsWith('.tmp')) -and
                $guard -in @('app', 'tests', 'scripts', 'static', 'docs', 'reproducibility')
            if (-not $cacheException) { throw "ABORT: protected path: $relative" }
        }
    }
    if (-not (Test-Path -LiteralPath $full)) { return }
    $node = Get-Item -LiteralPath $full -Force
    if (($node.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) {
        Write-Warning "UNKNOWN_PRESERVE (reparse point): $relative"
        return
    }
    $tracked = @(& git -C $root ls-files -- $relative)
    if ($tracked.Count -gt 0) { throw "ABORT: tracked source inside cleanup target: $relative" }
    $files = if ($node.PSIsContainer) { @(Get-ChildItem -LiteralPath $full -Recurse -File -Force) } else { @($node) }
    $dirs = if ($node.PSIsContainer) { @(Get-ChildItem -LiteralPath $full -Recurse -Directory -Force) } else { @() }
    foreach ($child in @($files) + @($dirs)) {
        if (($child.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) {
            Write-Warning "UNKNOWN_PRESERVE (nested reparse point): $relative"
            return
        }
        if (-not $child.FullName.StartsWith($prefix, [System.StringComparison]::OrdinalIgnoreCase)) {
            throw "ABORT: child outside project: $relative"
        }
    }
    $bytes = ($files | Measure-Object -Property Length -Sum).Sum
    if (-not $bytes) { $bytes = 0 }
    $items.Add([pscustomobject]@{ Relative = $relative; Full = $full; Category = $category;
        Files = $files.Count; Bytes = [long]$bytes })
}

foreach ($target in $targets) {
    $category = if ($target -eq 'library') { 'USER_STATE_TO_DELETE' }
        elseif ($target -like 'benchmarks/*') { 'TEST_ARTIFACT_TO_DELETE' }
        elseif ($target -in @('.pytest_cache', 'htmlcov', 'temp', 'outputs')) { 'CACHE_SAFE_TO_DELETE' }
        else { 'USER_STATE_TO_DELETE' }
    Add-CleanupItem $target $category
}
foreach ($source in @('app', 'tests', 'scripts', 'static', 'docs', 'reproducibility')) {
    $base = Join-Path $root $source
    if (-not (Test-Path -LiteralPath $base)) { continue }
    $caches = @(Get-ChildItem -LiteralPath $base -Directory -Force -Recurse -ErrorAction SilentlyContinue |
        Where-Object { $_.Name -eq '__pycache__' -and $_.FullName -notlike (Join-Path $root '.venv\*') -and
            $_.FullName -notlike (Join-Path $root 'vendor\*') -and $_.FullName -notlike (Join-Path $root 'benchmarks\models\*') })
    foreach ($cache in $caches) {
        if (-not $cache.FullName.StartsWith($prefix, [System.StringComparison]::OrdinalIgnoreCase)) {
            throw 'ABORT: cache outside project.'
        }
        $relative = $cache.FullName.Substring($prefix.Length)
        if (-not ($items | Where-Object { $relative.StartsWith($_.Relative + '\') })) {
            Add-CleanupItem $relative 'CACHE_SAFE_TO_DELETE'
        }
    }
}
if (Test-Path -LiteralPath (Join-Path $root '__pycache__')) { Add-CleanupItem '__pycache__' 'CACHE_SAFE_TO_DELETE' }
if (Test-Path -LiteralPath (Join-Path $root 'benchmarks/__pycache__')) {
    Add-CleanupItem 'benchmarks/__pycache__' 'CACHE_SAFE_TO_DELETE'
}
if (Test-Path -LiteralPath (Join-Path $root '.coverage')) { Add-CleanupItem '.coverage' 'CACHE_SAFE_TO_DELETE' }
foreach ($source in @('app', 'tests', 'scripts', 'static', 'docs', 'reproducibility', 'benchmarks')) {
    $base = Join-Path $root $source
    if (-not (Test-Path -LiteralPath $base)) { continue }
    foreach ($file in @(Get-ChildItem -LiteralPath $base -File -Filter '*.tmp' -Force -Recurse -ErrorAction SilentlyContinue)) {
        if ($file.FullName -like (Join-Path $root 'benchmarks\models\*')) { continue }
        $relative = $file.FullName.Substring($prefix.Length)
        Add-CleanupItem $relative 'CACHE_SAFE_TO_DELETE'
    }
}

Write-Host 'WOULD PRESERVE: .venv, vendor/MOSS-TTS, benchmarks/models/faster-whisper-medium, voices, source, wsproto, baseline'
foreach ($item in $items) { Write-Host ("WOULD DELETE [{0}] {1} ({2} files, {3} bytes)" -f $item.Category,$item.Relative,$item.Files,$item.Bytes) }
$totalFiles = ($items | Measure-Object -Property Files -Sum).Sum
$totalBytes = ($items | Measure-Object -Property Bytes -Sum).Sum
Write-Host "TOTAL FILES: $totalFiles; TOTAL SIZE: $totalBytes bytes"
if (-not $Apply) { Write-Host 'DRY RUN ONLY: no files removed.'; exit 0 }
foreach ($item in $items) {
    if (-not $item.Full.StartsWith($prefix, [System.StringComparison]::OrdinalIgnoreCase)) { throw 'ABORT: unsafe target.' }
    # Only the enumerated, prevalidated managed target is removed; no global path/glob.
    Remove-Item -LiteralPath $item.Full -Recurse -Force
    Write-Host "DELETED $($item.Relative)"
}
Write-Host 'Cleanup applied. Run scripts/validate_clean_install.ps1 next.'
