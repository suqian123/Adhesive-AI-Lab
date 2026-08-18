$ErrorActionPreference = "Stop"

$candidates = @(
    (Join-Path $PSScriptRoot ".venv\Scripts\python.exe"),
    (Get-Command python -ErrorAction SilentlyContinue).Source,
    "C:\Users\Administrator\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe"
)

$python = $candidates | Where-Object { $_ -and (Test-Path $_) } | Select-Object -First 1
if (-not $python) {
    throw "未找到 Python。请安装 Python 3.10+，或在 VS Code 中配置 Python 解释器。"
}

& $python (Join-Path $PSScriptRoot "offline_app.py")
