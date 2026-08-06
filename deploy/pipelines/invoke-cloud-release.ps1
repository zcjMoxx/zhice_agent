[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$SourceImage,
    [string]$ReleaseTag = "",
    [string]$ConfigPath = ""
)

$ErrorActionPreference = "Stop"
$deployRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$repoRoot = (Resolve-Path (Join-Path $deployRoot "..")).Path
$scriptsRoot = Join-Path $deployRoot "scripts"
$pushScript = Join-Path $scriptsRoot "push-image.ps1"
$remoteOpsHelper = Join-Path $scriptsRoot "remote_ops.py"
$imageName = "zhice-agent"

if (-not $ConfigPath) {
    $ConfigPath = Join-Path $deployRoot "private/cloud-target.json"
}
if (-not (Test-Path -LiteralPath $ConfigPath -PathType Leaf)) {
    throw "Missing cloud deployment config: $ConfigPath. Copy deploy/private/cloud-target.example.json to deploy/private/cloud-target.json and edit it."
}
$ConfigPath = (Resolve-Path -LiteralPath $ConfigPath).Path

if (-not (Get-Command python -ErrorAction SilentlyContinue)) {
    throw "Required command is unavailable: python"
}
if (-not (Test-Path -LiteralPath $remoteOpsHelper -PathType Leaf)) {
    throw "Missing Paramiko remote operations helper: $remoteOpsHelper"
}
$previousErrorActionPreference = $ErrorActionPreference
try {
    $ErrorActionPreference = "Continue"
    $paramikoPreflightOutput = @(& python $remoteOpsHelper --help 2>&1)
    $paramikoPreflightExitCode = $LASTEXITCODE
} finally {
    $ErrorActionPreference = $previousErrorActionPreference
}
if ($paramikoPreflightExitCode -ne 0) {
    throw 'Paramiko is unavailable. Install it with: python -m pip install ".[deploy]"'
}
$paramikoPreflightOutput = $null

$safeConfigJson = @(& python $remoteOpsHelper --config $ConfigPath inspect-config)
if ($LASTEXITCODE -ne 0) {
    throw "Cloud deployment config validation failed"
}
try {
    $config = ($safeConfigJson -join [Environment]::NewLine) | ConvertFrom-Json
} catch {
    throw "Remote operations helper returned invalid public config"
}
$requiredKeys = @("Registry", "SshHost", "SshUser", "RemoteOpsDir", "PublicUrl", "Port")
foreach ($key in $requiredKeys) {
    if ($config.PSObject.Properties.Name -notcontains $key -or [string]::IsNullOrWhiteSpace([string]$config.$key)) {
        throw "Missing cloud deployment config value: $key"
    }
}
foreach ($key in $requiredKeys) {
    if ([regex]::IsMatch([string]$config.$key, '[^\x00-\x7F]')) {
        throw "Replace the Chinese placeholder in cloud deployment config: $key"
    }
}
foreach ($key in $config.PSObject.Properties.Name) {
    if ([string]$key -match '(?i)(password|token|secret|private.?key)') {
        throw "Cloud deployment config must not contain credentials: $key"
    }
}

$registry = ([string]$config.Registry).Trim().TrimEnd("/")
$sshHost = ([string]$config.SshHost).Trim()
$sshUser = ([string]$config.SshUser).Trim()
$remoteOpsDir = ([string]$config.RemoteOpsDir).Trim().TrimEnd("/")
$publicUrl = ([string]$config.PublicUrl).Trim().TrimEnd("/")
$port = [int]$config.Port

if ($registry -notmatch '^[A-Za-z0-9.-]+(?::[0-9]{1,5})?(?:/[A-Za-z0-9._-]+)+$') {
    throw "Invalid cloud registry path: $registry"
}
if ($sshHost -notmatch '^[A-Za-z0-9](?:[A-Za-z0-9.-]*[A-Za-z0-9])?$') {
    throw "Invalid SSH host: $sshHost"
}
if ($sshUser -notmatch '^[a-z_][a-z0-9_-]*$') {
    throw "Invalid SSH user: $sshUser"
}
if ($remoteOpsDir -notmatch '^/[A-Za-z0-9._/-]+$' -or $remoteOpsDir -match '(^|/)\.\.(/|$)') {
    throw "Invalid remote operations directory: $remoteOpsDir"
}
if ($port -lt 1 -or $port -gt 65535) {
    throw "Invalid remote host port: $port"
}
try {
    $publicUri = [Uri]$publicUrl
} catch {
    throw "Invalid public deployment URL: $publicUrl"
}
if ($publicUri.Scheme -ne "https" -or -not $publicUri.Host -or $publicUri.Query -or $publicUri.Fragment) {
    throw "Public deployment URL must be an HTTPS origin without query or fragment: $publicUrl"
}
if ($SourceImage -notmatch '^zhice-agent:[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}$') {
    throw "Source image must use the fixed zhice-agent name and a local tag: $SourceImage"
}

foreach ($command in @("docker")) {
    if (-not (Get-Command $command -ErrorAction SilentlyContinue)) {
        throw "Required command is unavailable: $command"
    }
}

$remoteScriptNames = @("deploy.sh", "status.sh", "logs.sh", "stop.sh", "restart.sh")
$remoteScripts = @($remoteScriptNames | ForEach-Object {
    $scriptPath = Join-Path $scriptsRoot $_
    if (-not (Test-Path -LiteralPath $scriptPath -PathType Leaf)) {
        throw "Missing remote deployment script: $scriptPath"
    }
    $scriptPath
})

docker info *> $null
if ($LASTEXITCODE -ne 0) {
    throw "Docker Engine is unavailable. Start Docker Desktop and retry."
}
docker image inspect $SourceImage *> $null
if ($LASTEXITCODE -ne 0) {
    throw "Local image does not exist: $SourceImage"
}
$platform = [string](docker image inspect --format "{{.Os}}/{{.Architecture}}" $SourceImage)
$platform = $platform.Trim()
if ($LASTEXITCODE -ne 0 -or $platform -ne "linux/amd64") {
    throw "Cloud image must target linux/amd64; found: $platform"
}

if (-not $ReleaseTag) {
    $revision = [string](git -C $repoRoot rev-parse --short=8 HEAD)
    $revision = $revision.Trim()
    if ($LASTEXITCODE -ne 0 -or -not $revision) {
        throw "Unable to resolve Git revision for release tag"
    }
    $ReleaseTag = "{0}-{1}" -f (Get-Date).ToString("yyyyMMdd-HHmmss"), $revision
}
$ReleaseTag = $ReleaseTag.Trim()
if ($ReleaseTag -notmatch '^[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}$') {
    throw "Invalid release tag: $ReleaseTag"
}

$sshTarget = "${sshUser}@${sshHost}"
Write-Output "[cloud 1/6] Checking known_hosts and password SSH access to $sshTarget"
& python $remoteOpsHelper --config $ConfigPath check
if ($LASTEXITCODE -ne 0) {
    throw "SSH host-key or password authentication failed for $sshTarget"
}

$releaseImage = "${imageName}:${ReleaseTag}"
Write-Output "[cloud 2/6] Tagging release image $releaseImage"
docker tag $SourceImage $releaseImage
if ($LASTEXITCODE -ne 0) { throw "Release image tagging failed" }

Write-Output "[cloud 3/6] Pushing release image to $registry"
$pushOutput = @(& $pushScript -Registry $registry -Image $imageName -Tag $ReleaseTag)
$pushOutput | Write-Output
$digestLines = @($pushOutput | Where-Object { $_ -is [string] -and $_.StartsWith("Pushed image digest: ") })
if ($digestLines.Count -ne 1) {
    throw "Push script did not return exactly one immutable image digest"
}
$digest = $digestLines[0].Substring("Pushed image digest: ".Length).Trim()
if ($digest -notmatch ('^' + [regex]::Escape("${registry}/${imageName}") + '@sha256:[0-9a-f]{64}$')) {
    throw "Push script returned an unexpected image digest: $digest"
}

Write-Output "[cloud 4/6] Uploading versioned remote operations scripts"
$remoteScripts | ForEach-Object { Write-Verbose "Validated remote operations script: $_" }
Write-Output "[cloud 5/6] Switching scripts atomically and deploying immutable image digest"
& python $remoteOpsHelper --config $ConfigPath deploy --scripts-dir $scriptsRoot --release-id $ReleaseTag --digest $digest --port $port
if ($LASTEXITCODE -ne 0) { throw "Remote operations sync or cloud deployment failed" }

Write-Output "[cloud 6/6] Verifying public HTTPS health"
try {
    $health = Invoke-RestMethod -UseBasicParsing -Uri "${publicUrl}/health" -TimeoutSec 20
    if ([string]$health.status -eq "ok") {
        Write-Output "Local public HTTPS health passed"
    } else {
        Write-Warning "Local public health returned unexpected status; remote public HTTPS health already passed. Check local proxy, DNS, and TLS settings."
    }
} catch {
    Write-Warning "Local public health check failed at ${publicUrl}/health; remote public HTTPS health already passed. Check local proxy, DNS, and TLS settings."
}

Write-Output "Cloud deployment passed"
Write-Output "image: $releaseImage"
Write-Output "digest: $digest"
Write-Output "url: $publicUrl"
Write-Output "remote ops: $remoteOpsDir/current"
