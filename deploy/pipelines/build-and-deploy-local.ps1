[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"
$deployRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$scriptsRoot = Join-Path $deployRoot "scripts"
$buildScript = Join-Path $scriptsRoot "build-image.ps1"
$smokeScript = Join-Path $scriptsRoot "run-local.ps1"
$composeFile = Join-Path $deployRoot "docker-compose.yml"
$imageName = "zhice-agent"
$imageTag = "local"
$imageRef = "${imageName}:${imageTag}"
$aptMirror = "mirrors.aliyun.com"
$service = "zcagent"
$publicPort = 10086
$smokePort = 10087

Write-Output "[1/5] Checking Docker Engine"
docker info *> $null
if ($LASTEXITCODE -ne 0) {
    throw "Docker Engine is unavailable. Start Docker Desktop and retry."
}

Write-Output "[2/5] Building private image $imageRef"
& $buildScript -Image $imageName -Tag $imageTag -AptMirror $aptMirror

Write-Output "[3/5] Running isolated image smoke test on port $smokePort"
& $smokeScript -Image $imageRef -Port $smokePort

Write-Output "[4/5] Creating or updating the local Compose service"
docker compose -f $composeFile up -d --force-recreate --no-build
if ($LASTEXITCODE -ne 0) {
    throw "Local Compose deployment failed."
}

$containerId = [string](docker compose -f $composeFile ps --all -q $service)
$containerId = $containerId.Trim()
if ($LASTEXITCODE -ne 0 -or -not $containerId) {
    throw "Local Compose container was not created."
}

Write-Output "[5/5] Waiting for container health"
$deadline = (Get-Date).AddSeconds(120)
$health = ""
do {
    $health = [string](docker inspect --format "{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}" $containerId)
    $health = $health.Trim()
    if ($LASTEXITCODE -ne 0 -or $health -in @("unhealthy", "exited", "dead")) {
        break
    }
    if ($health -eq "healthy") {
        break
    }
    Start-Sleep -Seconds 2
} while ((Get-Date) -lt $deadline)

if ($health -ne "healthy") {
    docker compose -f $composeFile logs --tail 100 $service
    throw "Local container did not become healthy; final status: $health"
}

docker compose -f $composeFile ps $service
if ($LASTEXITCODE -ne 0) {
    throw "Local Compose status check failed."
}

Write-Output "Build and local deployment passed"
Write-Output "image: $imageRef"
Write-Output "url: http://127.0.0.1:${publicPort}"
Write-Output "data: existing deploy_zhice-* named volumes are preserved"
