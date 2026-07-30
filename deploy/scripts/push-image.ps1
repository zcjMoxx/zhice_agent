[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$Registry,
    [string]$Image = "zhice-agent",
    [string]$Tag = "local"
)

$ErrorActionPreference = "Stop"
$source = "${Image}:${Tag}"
$target = "$($Registry.TrimEnd('/'))/${Image}:${Tag}"
docker image inspect $source *> $null
if ($LASTEXITCODE -ne 0) { throw "Local image does not exist: $source" }
docker tag $source $target
docker push $target
if ($LASTEXITCODE -ne 0) { throw "Docker push failed" }
$digest = docker image inspect --format '{{index .RepoDigests 0}}' $target
Write-Output "Pushed image digest: $digest"
