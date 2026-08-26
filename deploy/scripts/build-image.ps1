[CmdletBinding()]
param(
    [string]$Image = "zhice-agent",
    [string]$Tag = "local",
    [string]$AptMirror = ""
)

$ErrorActionPreference = "Stop"
$deployRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$repoRoot = (Resolve-Path (Join-Path $deployRoot "..")).Path
$privateRoot = Join-Path $deployRoot "private"
$privateFiles = @(".env", "config.yml", "models.json")

foreach ($name in $privateFiles) {
    $path = Join-Path $privateRoot $name
    if (-not (Test-Path -LiteralPath $path -PathType Leaf)) {
        throw "Missing private deploy file: deploy/private/$name"
    }
    git -C $repoRoot check-ignore -q -- "deploy/private/$name"
    if ($LASTEXITCODE -ne 0) {
        throw "Private deploy file is not ignored by Git: deploy/private/$name"
    }
}

function Get-DotEnvKeys {
    param([Parameter(Mandatory = $true)][string]$Path)

    $keys = [System.Collections.Generic.List[string]]::new()
    $seen = [System.Collections.Generic.HashSet[string]]::new(
        [System.StringComparer]::Ordinal
    )
    foreach ($rawLine in Get-Content -LiteralPath $Path) {
        $line = $rawLine.Trim()
        if (-not $line -or $line.StartsWith("#")) { continue }
        if ($line.StartsWith("export ")) { $line = $line.Substring(7).TrimStart() }
        $match = [regex]::Match($line, '^([A-Za-z_][A-Za-z0-9_]*)=')
        if (-not $match.Success) { continue }
        $name = $match.Groups[1].Value
        if (-not $seen.Add($name)) {
            throw "Duplicate environment key in $Path`: $name"
        }
        $keys.Add($name)
    }
    return $keys.ToArray()
}

$publicEnvTemplatePath = Join-Path $repoRoot "config/.env.example"
$expectedEnvKeys = @(Get-DotEnvKeys -Path $publicEnvTemplatePath)
$privateEnvPath = Join-Path $privateRoot ".env"
$privateEnvKeys = @(Get-DotEnvKeys -Path $privateEnvPath)
if (($expectedEnvKeys -join "`n") -ne ($privateEnvKeys -join "`n")) {
    $missing = @($expectedEnvKeys | Where-Object { $_ -notin $privateEnvKeys })
    $unexpected = @($privateEnvKeys | Where-Object { $_ -notin $expectedEnvKeys })
    if ($missing.Count -gt 0) {
        throw "deploy/private/.env is missing fields from config/.env.example: $($missing -join ', ')"
    }
    if ($unexpected.Count -gt 0) {
        throw "deploy/private/.env has fields absent from config/.env.example: $($unexpected -join ', ')"
    }
    throw "deploy/private/.env field order differs from config/.env.example"
}

$modelsPath = Join-Path $privateRoot "models.json"
$null = Get-Content -Raw -LiteralPath $modelsPath | ConvertFrom-Json
$privateConfigText = Get-Content -Raw -LiteralPath (Join-Path $privateRoot "config.yml")
foreach ($section in @("site", "workflows", "official_email")) {
    if ($privateConfigText -notmatch "(?m)^$([regex]::Escape($section)):\s*$") {
        throw "deploy/private/config.yml is missing required section: $section"
    }
}
foreach ($name in $privateFiles) {
    $text = Get-Content -Raw -LiteralPath (Join-Path $privateRoot $name)
    if ($text -match '(?i)(replace[-_ ]?me|change[-_ ]?me|your[-_ ][a-z0-9_]+|<[^>]+>)') {
        throw "Placeholder value detected in deploy/private/$name"
    }
}
if ((Get-Content -Raw -LiteralPath (Join-Path $privateRoot ".env")) -match '(?m)^\s*ZHICE_AGENT_WORKSPACE\s*=') {
    throw "deploy/private/.env must not set ZHICE_AGENT_WORKSPACE; the container uses /home/zhice/.zhice by default"
}

function Read-DotEnvValue {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][string]$Name
    )

    $foundValues = @(Get-Content -LiteralPath $Path | ForEach-Object {
        $line = $_.Trim()
        if ($line.StartsWith("export ")) { $line = $line.Substring(7).TrimStart() }
        $match = [regex]::Match($line, "^$([regex]::Escape($Name))=(.*)$")
        if ($match.Success) { $match.Groups[1].Value }
    })
    if ($foundValues.Count -gt 1) {
        throw "Duplicate private environment key: $Name"
    }
    if ($foundValues.Count -eq 0 -or [string]::IsNullOrWhiteSpace($foundValues[0])) {
        throw "Missing private environment key: $Name"
    }
    if ($foundValues[0].IndexOfAny([char[]]@("`r", "`n", [char]0)) -ge 0) {
        throw "Invalid multiline private environment value: $Name"
    }
    return $foundValues[0]
}

$amapJsApiKey = Read-DotEnvValue -Path $privateEnvPath -Name "VITE_AMAP_JS_API_KEY"
$amapJsSecurityCode = Read-DotEnvValue -Path $privateEnvPath -Name "VITE_AMAP_JS_SECURITY_CODE"

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
$AptMirror = $AptMirror.Trim()
if ($AptMirror -and $AptMirror -notmatch '^[A-Za-z0-9](?:[A-Za-z0-9.-]*[A-Za-z0-9])?(?::[0-9]{1,5})?$') {
    throw "Invalid APT mirror host: $AptMirror"
}

$dockerArgs = @(
    "build",
    "--file", (Join-Path $deployRoot "Dockerfile"),
    "--build-arg", "ZHICE_VERSION=$version",
    "--build-arg", "ZHICE_REVISION=$revision",
    "--build-arg", "ZHICE_BUILD_DATE=$buildDate",
    "--build-arg", "VITE_AMAP_JS_API_KEY=$amapJsApiKey",
    "--build-arg", "VITE_AMAP_JS_SECURITY_CODE=$amapJsSecurityCode"
)
if ($AptMirror) {
    $dockerArgs += @("--build-arg", "APT_MIRROR=$AptMirror")
}
$dockerArgs += @("--tag", $imageRef, $repoRoot)

& docker @dockerArgs
if ($LASTEXITCODE -ne 0) { throw "Docker image build failed" }

$unexpected = docker run --rm --entrypoint sh $imageRef -c "find /home/zhice/.zhice -mindepth 1 -maxdepth 1 -type d | sort"
if ($LASTEXITCODE -ne 0) { throw "Built-image state scan failed" }
$allowed = @("/home/zhice/.zhice/config", "/home/zhice/.zhice/contexts", "/home/zhice/.zhice/extends", "/home/zhice/.zhice/integrations", "/home/zhice/.zhice/logs", "/home/zhice/.zhice/prompts", "/home/zhice/.zhice/state", "/home/zhice/.zhice/travel")
foreach ($path in $unexpected) {
    if ($path -and $path -notin $allowed) { throw "Unexpected workspace path in image: $path" }
}

Write-Output "Built private image: $imageRef"
