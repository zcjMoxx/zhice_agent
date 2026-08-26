[CmdletBinding()]
param(
    [string]$ReleaseTag = "",
    [string]$ConfigPath = "",
    [switch]$Smoke
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

if ($Smoke) {
    Write-Output "[2/3] Running explicitly requested isolated image smoke test on port $smokePort"
    & $smokeScript -Image $imageRef -Port $smokePort
} else {
    Write-Output "[2/3] Skipping image and deployment smoke by default"
}

Write-Output "[3/3] Publishing image to cloud"
& $releaseScript -SourceImage $imageRef -ReleaseTag $ReleaseTag -ConfigPath $ConfigPath -Smoke:$Smoke
