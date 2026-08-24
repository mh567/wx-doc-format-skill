$ErrorActionPreference = "Stop"
$PackageDir = Split-Path -Parent $PSScriptRoot
$env:WX_DOC_FORMAT_OFFLINE = "1"
$env:WX_DOC_FORMAT_RELEASE = "1"
& (Join-Path $PackageDir "runtime\wx-doc-format.exe") @args
exit $LASTEXITCODE
