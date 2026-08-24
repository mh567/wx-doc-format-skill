param(
    [string]$BootstrapPython = "py"
)

$ErrorActionPreference = "Stop"
$ProjectDir = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$BuildRoot = Join-Path $ProjectDir "build\nuitka\local-windows-x86_64"
$VenvDir = Join-Path $BuildRoot "venv"
$OutputDir = Join-Path $ProjectDir "dist\local"
$Version = (Get-Content -Raw (Join-Path $ProjectDir "VERSION")).Trim()
$PackageDir = Join-Path $OutputDir "wx-doc-format-skill-$Version-windows-x86_64"

if (-not [Environment]::Is64BitOperatingSystem -or $env:PROCESSOR_ARCHITECTURE -notin @("AMD64", "x86_64")) {
    throw "This build requires Windows x86_64."
}

New-Item -ItemType Directory -Force -Path $BuildRoot, $OutputDir | Out-Null
if (-not (Test-Path (Join-Path $VenvDir "Scripts\python.exe"))) {
    if ($BootstrapPython -eq "py") {
        & py -3.13 -m venv $VenvDir
    } else {
        & $BootstrapPython -m venv $VenvDir
    }
}
$Python = Join-Path $VenvDir "Scripts\python.exe"
& $Python -m pip install --disable-pip-version-check `
    -r (Join-Path $ProjectDir "packaging\nuitka\requirements-runtime.txt") `
    -r (Join-Path $ProjectDir "packaging\nuitka\requirements-build.txt")
& $Python -m pytest (Join-Path $ProjectDir "tests") -q

$env:NUITKA_CACHE_DIR = Join-Path $BuildRoot "cache"
& $Python (Join-Path $ProjectDir "packaging\nuitka\build_distribution.py") `
    --build-dir (Join-Path $BuildRoot "compile") `
    --output-dir $OutputDir `
    --platform-id windows-x86_64 `
    --archive
& $Python (Join-Path $ProjectDir "packaging\nuitka\smoke_distribution.py") $PackageDir

Write-Output "Windows x86_64 package: $PackageDir.zip"
