[CmdletBinding()]
param(
    [string]$ReleaseTag = "",
    [string]$ConfigPath = "",
    [switch]$SkipExternalSmoke
)

$ErrorActionPreference = "Stop"
$deployRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$scriptsRoot = Join-Path $deployRoot "scripts"
$buildScript = Join-Path $scriptsRoot "build-image.ps1"
$smokeScript = Join-Path $scriptsRoot "run-local.ps1"
$releaseScript = Join-Path $PSScriptRoot "invoke-cloud-release.ps1"
$imageName = "zhice-agent"
$imageTag = "local"
$imageRef = "${imageName}:${imageTag}"
$aptMirror = "mirrors.aliyun.com"
$smokePort = 10087

Write-Output "[1/3] Building private cloud release image from current source: $imageRef"
& $buildScript -Image $imageName -Tag $imageTag -AptMirror $aptMirror

Write-Output "[2/3] Running isolated image smoke test on port $smokePort"
& $smokeScript -Image $imageRef -Port $smokePort

Write-Output "[3/3] Publishing verified image to cloud"
& $releaseScript -SourceImage $imageRef -ReleaseTag $ReleaseTag -ConfigPath $ConfigPath -SkipExternalSmoke:$SkipExternalSmoke
