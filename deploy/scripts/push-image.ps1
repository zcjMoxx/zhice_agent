[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$Registry,
    [string]$Image = "zhice-agent",
    [string]$Tag = "local"
)

$ErrorActionPreference = "Stop"
$source = "${Image}:${Tag}"
$target = "$($Registry.TrimEnd('/'))/${Image}:${Tag}"
$repository = "$($Registry.TrimEnd('/'))/${Image}"
docker image inspect $source *> $null
if ($LASTEXITCODE -ne 0) { throw "Local image does not exist: $source" }
docker tag $source $target
docker push $target
if ($LASTEXITCODE -ne 0) { throw "Docker push failed" }
$repoDigestsJson = [string](docker image inspect --format '{{json .RepoDigests}}' $target)
if ($LASTEXITCODE -ne 0 -or -not $repoDigestsJson) {
    throw "Unable to inspect pushed image digests"
}
$parsedRepoDigests = ConvertFrom-Json -InputObject $repoDigestsJson
$digestPrefix = "${repository}@sha256:"
$matchingDigests = @($parsedRepoDigests | Where-Object {
    $_ -is [string] -and $_.StartsWith($digestPrefix)
})
if ($matchingDigests.Count -ne 1) {
    throw "Expected exactly one digest for pushed repository: $repository"
}
$digest = $matchingDigests[0]
Write-Output "Pushed image digest: $digest"
