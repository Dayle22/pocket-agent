param(
    [switch]$SkipTests
)

$ErrorActionPreference = "Stop"
$RepoRoot = Split-Path -Parent $PSScriptRoot
Set-Location $RepoRoot

$VenvPython = Join-Path $RepoRoot ".venv\Scripts\python.exe"
if (Test-Path $VenvPython) {
    $Python = $VenvPython
} else {
    $Python = "python"
}

if (-not $SkipTests) {
    Write-Host "Running test suite..."
    & $Python -m pytest -v
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
}

$Version = (& $Python -c "import pocket_agent; print(pocket_agent.__version__)").Trim()
if (-not $Version) {
    throw "Could not determine pocket_agent.__version__"
}

Write-Host "Building PocketAgent v$Version (console-free Windows bundle)..."
$prevErrorAction = $ErrorActionPreference
$ErrorActionPreference = "Continue"
& $Python -m PyInstaller --clean --noconfirm (Join-Path $PSScriptRoot "pocket-agent.spec")
$pyiExit = $LASTEXITCODE
$ErrorActionPreference = $prevErrorAction
if ($pyiExit -ne 0) { exit $pyiExit }

$DistDir = Join-Path $RepoRoot "dist"
$BundleDir = Join-Path $DistDir "PocketAgent"
$ZipName = "PocketAgent-v$Version-win64.zip"
$ZipPath = Join-Path $DistDir $ZipName
$ShaPath = "$ZipPath.sha256"

if (Test-Path $ZipPath) { Remove-Item $ZipPath -Force }
Compress-Archive -Path (Join-Path $BundleDir "*") -DestinationPath $ZipPath -CompressionLevel Optimal

$Hash = (Get-FileHash -Path $ZipPath -Algorithm SHA256).Hash.ToLowerInvariant()
"$Hash  $ZipName" | Set-Content -Path $ShaPath -Encoding ascii

Write-Host ""
Write-Host "Build complete:"
Write-Host "  EXE : $(Join-Path $BundleDir 'PocketAgent.exe')"
Write-Host "  ZIP : $ZipPath"
Write-Host "  SHA : $ShaPath"
