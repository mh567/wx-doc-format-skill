$ErrorActionPreference = "Stop"
$PackageDir = Split-Path -Parent $PSScriptRoot
if ($PackageDir -match '[^\x20-\x7E]') {
    throw "Installed Skill path must use printable ASCII characters."
}
$env:WX_DOC_FORMAT_OFFLINE = "1"
$env:WX_DOC_FORMAT_RELEASE = "1"
& (Join-Path $PackageDir "runtime\wx-doc-format.exe") @args
exit $LASTEXITCODE
