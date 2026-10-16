param(
    [int]$LocalPort = 18501,
    [int]$DatabasePort = 13306,
    [switch]$DatabaseOnly
)

$ErrorActionPreference = "Stop"
if (-not (Get-Command ssh -ErrorAction SilentlyContinue)) {
    throw "OpenSSH client (ssh) is required."
}

Write-Host "Server: user@119.147.202.74:8005"
if (-not $DatabaseOnly) {
    Write-Host "After SSH login, open http://127.0.0.1:$LocalPort"
}
Write-Host "Shared database: 127.0.0.1:$DatabasePort"
Write-Host "Keep this terminal open. Press Ctrl+C to close the tunnel."

$forwardArgs = @(
    '-N', '-o', 'ExitOnForwardFailure=yes',
    '-o', 'ServerAliveInterval=30', '-o', 'ServerAliveCountMax=3',
    '-L', "127.0.0.1:${DatabasePort}:127.0.0.1:3306"
)
$tunnelKey = Join-Path $PSScriptRoot 'work/server_access/id_ed25519'
if (Test-Path -LiteralPath $tunnelKey) {
    $forwardArgs += @('-i', $tunnelKey, '-o', 'IdentitiesOnly=yes', '-o', 'BatchMode=yes')
}
if (-not $DatabaseOnly) {
    $forwardArgs += @('-L', "127.0.0.1:${LocalPort}:127.0.0.1:8501")
}
$forwardArgs += @('-p', '8005', 'user@119.147.202.74')
& ssh @forwardArgs

if ($LASTEXITCODE -ne 0) {
    throw "SSH tunnel exited with code $LASTEXITCODE."
}
