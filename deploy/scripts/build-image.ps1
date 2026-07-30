[CmdletBinding()]
param(
    [string]$Image = "zhice-agent",
    [string]$Tag = "local"
)

$ErrorActionPreference = "Stop"
$deployRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$repoRoot = (Resolve-Path (Join-Path $deployRoot "..")).Path
$privateFiles = @(".env", "config.yml", "models.json")

foreach ($name in $privateFiles) {
    $path = Join-Path $deployRoot $name
    if (-not (Test-Path -LiteralPath $path -PathType Leaf)) {
        throw "Missing private deploy file: deploy/$name"
    }
    git -C $repoRoot check-ignore -q -- "deploy/$name"
    if ($LASTEXITCODE -ne 0) {
        throw "Private deploy file is not ignored by Git: deploy/$name"
    }
}

$modelsPath = Join-Path $deployRoot "models.json"
$null = Get-Content -Raw -LiteralPath $modelsPath | ConvertFrom-Json
foreach ($name in $privateFiles) {
    $text = Get-Content -Raw -LiteralPath (Join-Path $deployRoot $name)
    if ($text -match '(?i)(replace[-_ ]?me|change[-_ ]?me|your[-_ ][a-z0-9_]+|<[^>]+>)') {
        throw "Placeholder value detected in deploy/$name"
    }
}
if ((Get-Content -Raw -LiteralPath (Join-Path $deployRoot ".env")) -match '(?m)^\s*ZHICE_AGENT_WORKSPACE\s*=') {
    throw "deploy/.env must not set ZHICE_AGENT_WORKSPACE; the container uses /home/zhice/.zhice by default"
}

$forbidden = @("contexts", "logs", ".tmp", ".git")
foreach ($name in $forbidden) {
    if (Test-Path -LiteralPath (Join-Path $deployRoot $name)) {
        throw "Local runtime/build data must not exist under deploy/: $name"
    }
}

$revision = (git -C $repoRoot rev-parse --short=12 HEAD).Trim()
$version = (git -C $repoRoot describe --tags --always --dirty).Trim()
$buildDate = [DateTime]::UtcNow.ToString("yyyy-MM-ddTHH:mm:ssZ")
$imageRef = "${Image}:${Tag}"

docker build --file (Join-Path $deployRoot "Dockerfile") `
    --build-arg "ZHICE_VERSION=$version" `
    --build-arg "ZHICE_REVISION=$revision" `
    --build-arg "ZHICE_BUILD_DATE=$buildDate" `
    --tag $imageRef $repoRoot
if ($LASTEXITCODE -ne 0) { throw "Docker image build failed" }

$unexpected = docker run --rm --entrypoint sh $imageRef -c "find /home/zhice/.zhice -mindepth 1 -maxdepth 1 -type d | sort"
if ($LASTEXITCODE -ne 0) { throw "Built-image state scan failed" }
$allowed = @("/home/zhice/.zhice/config", "/home/zhice/.zhice/contexts", "/home/zhice/.zhice/extends", "/home/zhice/.zhice/logs", "/home/zhice/.zhice/prompts", "/home/zhice/.zhice/state")
foreach ($path in $unexpected) {
    if ($path -and $path -notin $allowed) { throw "Unexpected workspace path in image: $path" }
}

Write-Output "Built private image: $imageRef"
