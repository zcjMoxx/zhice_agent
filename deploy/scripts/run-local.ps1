[CmdletBinding()]
param(
    [string]$Image = "zhice-agent:local",
    [int]$Port = 10086
)

$ErrorActionPreference = "Stop"
$name = "zhice-agent-smoke"

function Test-SmokeContainerExists {
    $existing = @(docker ps -a --filter "name=^/$name$" --format "{{.Names}}")
    if ($LASTEXITCODE -ne 0) { throw "Smoke container lookup failed" }
    return $existing -contains $name
}

function Remove-SmokeContainer {
    if (-not (Test-SmokeContainerExists)) { return }

    $running = @(docker ps --filter "name=^/$name$" --format "{{.Names}}")
    if ($LASTEXITCODE -ne 0) { throw "Smoke container state lookup failed" }
    if ($running -contains $name) {
        docker stop --time 20 $name | Out-Null
        if ($LASTEXITCODE -ne 0) { throw "Smoke container failed to stop" }
    }

    docker rm --volumes $name | Out-Null
    if ($LASTEXITCODE -ne 0) { throw "Smoke container cleanup failed" }
}

Remove-SmokeContainer
try {
    docker run --detach --name $name --init -p "${Port}:10086" $Image | Out-Null
    if ($LASTEXITCODE -ne 0) { throw "Container failed to start" }

    $deadline = (Get-Date).AddSeconds(90)
    do {
        Start-Sleep -Seconds 2
        try {
            $health = Invoke-WebRequest -UseBasicParsing -Uri "http://127.0.0.1:${Port}/health" -TimeoutSec 3
            $homeResponse = Invoke-WebRequest -UseBasicParsing -Uri "http://127.0.0.1:${Port}/" -TimeoutSec 3
            if ($health.StatusCode -eq 200 -and $homeResponse.StatusCode -eq 200) { break }
        } catch { }
    } while ((Get-Date) -lt $deadline)
    if (-not $health -or $health.StatusCode -ne 200 -or $homeResponse.StatusCode -ne 200) {
        docker logs --tail 100 $name
        throw "Gateway smoke test timed out"
    }
    docker exec $name zcagent --env-file /home/zhice/.zhice/config/.env gateway --host 127.0.0.1 --port 10086 --check
    if ($LASTEXITCODE -ne 0) { throw "Gateway configuration check failed" }
    Write-Output "Image smoke test passed: $Image"
} finally {
    Remove-SmokeContainer
}
