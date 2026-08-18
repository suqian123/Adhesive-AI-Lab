$ErrorActionPreference = "Stop"

$venvPython = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $venvPython)) {
    throw "未找到 .venv。请先执行：python -m venv .venv，然后安装 requirements.txt。"
}

& $venvPython -m streamlit run (Join-Path $PSScriptRoot "app.py")
