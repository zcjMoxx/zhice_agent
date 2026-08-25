[CmdletBinding()]
param(
    [string]$Image = "zhice-agent:local",
    [string]$ReleaseTag = "",
    [string]$ConfigPath = "",
    [switch]$Smoke,
    [switch]$SkipExternalSmoke
)

$ErrorActionPreference = "Stop"
$deployRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$scriptsRoot = Join-Path $deployRoot "scripts"
$smokeScript = Join-Path $scriptsRoot "run-local.ps1"
$releaseScript = Join-Path $PSScriptRoot "invoke-cloud-release.ps1"

if ($Smoke) {
    Write-Output "[1/2] Running optional isolated smoke test"
    & $smokeScript -Image $Image -Port 10087
} else {
    Write-Output "[1/2] Reusing operator-approved existing local image without smoke"
}

Write-Output "[2/2] Publishing existing image to cloud"
& $releaseScript -SourceImage $Image -ReleaseTag $ReleaseTag -ConfigPath $ConfigPath -SkipExternalSmoke:$SkipExternalSmoke
