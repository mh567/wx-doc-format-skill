param(
    [string]$Destination = (Join-Path $HOME ".codex\skills\wx-doc-format")
)

$ErrorActionPreference = "Stop"
$PackageDir = Split-Path -Parent $PSScriptRoot
if ([string]::IsNullOrWhiteSpace($Destination)) {
    throw "Installation destination must not be empty."
}
$DestinationFullPath = [System.IO.Path]::GetFullPath($Destination).TrimEnd('\')
if ($DestinationFullPath -match '[^\x20-\x7E]') {
    throw "Installation destination must use printable ASCII characters."
}
$RootPath = [System.IO.Path]::GetPathRoot($DestinationFullPath).TrimEnd('\')
$HomeFullPath = [System.IO.Path]::GetFullPath($HOME).TrimEnd('\')
if ($DestinationFullPath -eq $RootPath -or $DestinationFullPath -eq $HomeFullPath) {
    throw "Installation to the filesystem root or home directory is forbidden."
}

$ExpectedPaths = [System.Collections.Generic.HashSet[string]]::new([System.StringComparer]::Ordinal)
Get-Content (Join-Path $PackageDir "SHA256SUMS.txt") | ForEach-Object {
    if ($_ -notmatch '^([0-9a-f]{64})  (.+)$') {
        throw "Invalid SHA256SUMS.txt entry: $_"
    }
    $Expected = $Matches[1]
    $ManifestPath = $Matches[2]
    if ($ManifestPath.Contains("`n") -or $ManifestPath.Contains("`r")) {
        throw "Package checksum path contains a newline."
    }
    if (-not $ExpectedPaths.Add($ManifestPath)) {
        throw "Duplicate SHA256SUMS.txt path: $ManifestPath"
    }
    $RelativePath = $ManifestPath.Replace('/', '\')
    $Actual = (Get-FileHash -Algorithm SHA256 (Join-Path $PackageDir $RelativePath)).Hash.ToLowerInvariant()
    if ($Actual -ne $Expected) {
        throw "SHA-256 mismatch: $RelativePath"
    }
}

$ActualPaths = [System.Collections.Generic.HashSet[string]]::new([System.StringComparer]::Ordinal)
Get-ChildItem -File -Recurse -Force $PackageDir | ForEach-Object {
    $RelativePath = $_.FullName.Substring($PackageDir.Length).TrimStart('\', '/').Replace('\', '/')
    if ($RelativePath -eq "SHA256SUMS.txt") {
        return
    }
    if ($RelativePath.Contains("`n") -or $RelativePath.Contains("`r")) {
        throw "Package contains an unsafe filename."
    }
    [void]$ActualPaths.Add($RelativePath)
}
if ($ActualPaths.Count -ne $ExpectedPaths.Count) {
    throw "Package file set does not match SHA256SUMS.txt."
}
foreach ($Path in $ActualPaths) {
    if (-not $ExpectedPaths.Contains($Path)) {
        throw "Package file set does not match SHA256SUMS.txt: $Path"
    }
}

$Parent = Split-Path -Parent $DestinationFullPath
New-Item -ItemType Directory -Force -Path $Parent | Out-Null
$Unique = [Guid]::NewGuid().ToString("N")
$Staging = "$DestinationFullPath.staging.$Unique"
$Backup = "$DestinationFullPath.backup.$((Get-Date).ToString('yyyyMMddHHmmss')).$Unique"

try {
    New-Item -ItemType Directory -Path $Staging | Out-Null
    Copy-Item -Recurse -Force (Join-Path $PackageDir '*') $Staging
    $ExpectedVersion = (Get-Content -Raw (Join-Path $Staging "VERSION")).Trim()
    $RuntimeVersion = (& (Join-Path $Staging "runtime\wx-doc-format.exe") --version | Out-String).Trim()
    if ($LASTEXITCODE -ne 0) {
        throw "Staged runtime version check failed."
    }
    if ($RuntimeVersion -ne $ExpectedVersion) {
        throw "Staged runtime version does not match package VERSION."
    }
    if (Test-Path $DestinationFullPath) {
        Move-Item $DestinationFullPath $Backup
    }
    Move-Item $Staging $DestinationFullPath
} catch {
    if (Test-Path $Staging) {
        Remove-Item -Recurse -Force $Staging
    }
    if ((Test-Path $Backup) -and -not (Test-Path $DestinationFullPath)) {
        Move-Item $Backup $DestinationFullPath
    }
    throw
}

Write-Output "Installed wx-doc-format-skill to $DestinationFullPath"
if (Test-Path $Backup) {
    Write-Output "Previous installation retained at $Backup"
}
