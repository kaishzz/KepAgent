param(
    [string]$Version = "1.0"
)

$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $PSScriptRoot
$distDir = Join-Path $repoRoot "dist"
$output = Join-Path $distDir "kepagent-linux-amd64"

New-Item -ItemType Directory -Force -Path $distDir | Out-Null

$env:CGO_ENABLED = "0"
$env:GOOS = "linux"
$env:GOARCH = "amd64"

go build `
    -trimpath `
    -ldflags "-s -w -X github.com/kaishzz/kepagent/internal/version.Version=$Version" `
    -o $output `
    ./cmd/kepagent

Write-Host "Built $output"
