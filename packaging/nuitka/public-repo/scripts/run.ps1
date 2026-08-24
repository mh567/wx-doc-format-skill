$ErrorActionPreference = "Stop"
$SkillDir = Split-Path -Parent $PSScriptRoot
$Version = (Get-Content -Raw (Join-Path $SkillDir "VERSION")).Trim()
if (-not [Environment]::Is64BitOperatingSystem -or $env:PROCESSOR_ARCHITECTURE -notin @("AMD64", "x86_64")) {
    [Console]::Error.WriteLine("WXDF-E-PLATFORM-UNSUPPORTED: Windows/$env:PROCESSOR_ARCHITECTURE")
    exit 3
}

$Platform = "windows-x86_64"
$ArchiveName = "wx-doc-format-skill-$Version-$Platform.zip"
$SumsFile = Join-Path $SkillDir "artifacts\SHA256SUMS.txt"
$ExpectedSha = $null
Get-Content $SumsFile | ForEach-Object {
    if ($_ -match '^([0-9a-f]{64})  (.+)$' -and $Matches[2] -eq $ArchiveName) {
        $ExpectedSha = $Matches[1]
    }
}
if ([string]::IsNullOrWhiteSpace($ExpectedSha)) {
    [Console]::Error.WriteLine("WXDF-E-RUNTIME-UNAVAILABLE: $Platform is not available in release v$Version")
    exit 3
}

$CacheRoot = if ($env:WX_DOC_FORMAT_CACHE_DIR) {
    $env:WX_DOC_FORMAT_CACHE_DIR
} else {
    Join-Path $env:LOCALAPPDATA "wx-doc-format"
}
$CacheRoot = [System.IO.Path]::GetFullPath($CacheRoot)
if (-not $env:WX_DOC_FORMAT_CACHE_DIR -and $CacheRoot -match '[^\x20-\x7E]') {
    $CommonData = [Environment]::GetFolderPath([Environment+SpecialFolder]::CommonApplicationData)
    $UserSid = [System.Security.Principal.WindowsIdentity]::GetCurrent().User.Value
    $CacheRoot = [System.IO.Path]::GetFullPath((Join-Path $CommonData "wx-doc-format\$UserSid"))
}
if ($CacheRoot -match '[^\x20-\x7E]') {
    [Console]::Error.WriteLine("WXDF-E-NONASCII-PATH: runtime cache path must use printable ASCII characters.")
    exit 2
}
$PackageName = "wx-doc-format-skill-$Version-$Platform"
$PackageDir = Join-Path $CacheRoot "$Version\$Platform\$PackageName"
$RunScript = Join-Path $PackageDir "scripts\run.ps1"
if (Test-Path $RunScript) {
    & $RunScript @args
    exit $LASTEXITCODE
}
if (Test-Path $PackageDir) {
    [Console]::Error.WriteLine("WXDF-E-CACHE-INCOMPLETE: remove $PackageDir and retry")
    exit 1
}

$TempRoot = Join-Path ([System.IO.Path]::GetTempPath()) ("wx-doc-format-bootstrap-" + [Guid]::NewGuid().ToString("N"))
New-Item -ItemType Directory -Path $TempRoot | Out-Null
try {
    $ArchivePath = Join-Path $TempRoot $ArchiveName
    $BundledArchive = Join-Path $SkillDir "artifacts\$ArchiveName"
    if (Test-Path $BundledArchive -PathType Leaf) {
        Copy-Item $BundledArchive $ArchivePath
    } elseif ($env:WX_DOC_FORMAT_ARCHIVE_DIR) {
        Copy-Item (Join-Path $env:WX_DOC_FORMAT_ARCHIVE_DIR $ArchiveName) $ArchivePath
    } else {
        $ReleaseBaseUrl = if ($env:WX_DOC_FORMAT_RELEASE_BASE_URL) {
            $env:WX_DOC_FORMAT_RELEASE_BASE_URL.TrimEnd('/')
        } else {
            "https://github.com/mh567/wx-doc-format-skill/releases/download/v$Version"
        }
        Invoke-WebRequest -Uri "$ReleaseBaseUrl/$ArchiveName" -OutFile $ArchivePath
    }
    $ActualSha = (Get-FileHash -Algorithm SHA256 $ArchivePath).Hash.ToLowerInvariant()
    if ($ActualSha -ne $ExpectedSha) {
        throw "WXDF-E-ARCHIVE-HASH: SHA256 mismatch for $ArchiveName"
    }
    $ExtractRoot = Join-Path $TempRoot "extracted"
    Expand-Archive -Path $ArchivePath -DestinationPath $ExtractRoot
    $ExtractedPackage = Join-Path $ExtractRoot $PackageName
    $InstallScript = Join-Path $ExtractedPackage "scripts\install.ps1"
    if (-not (Test-Path $InstallScript)) {
        throw "WXDF-E-ARCHIVE-LAYOUT: expected package directory is missing"
    }
    & $InstallScript -Destination $PackageDir
} finally {
    if (Test-Path $TempRoot) {
        Remove-Item -Recurse -Force $TempRoot
    }
}
& $RunScript @args
exit $LASTEXITCODE
