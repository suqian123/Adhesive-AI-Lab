param(
    [int]$LocalPort = 18501
)

$ErrorActionPreference = "Stop"
if (-not (Get-Command ssh -ErrorAction SilentlyContinue)) {
    throw "OpenSSH client (ssh) is required."
}

Write-Host "Server: user@119.147.202.74:8005"
Write-Host "After SSH login, open http://127.0.0.1:$LocalPort"
Write-Host "Keep this terminal open. Press Ctrl+C to close the tunnel."

& ssh -N `
    -o ExitOnForwardFailure=yes `
    -o ServerAliveInterval=30 `
    -o ServerAliveCountMax=3 `
    -L "127.0.0.1:${LocalPort}:127.0.0.1:8501" `
    -p 8005 user@119.147.202.74

if ($LASTEXITCODE -ne 0) {
    throw "SSH tunnel exited with code $LASTEXITCODE."
}
