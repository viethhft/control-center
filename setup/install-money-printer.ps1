$ErrorActionPreference = 'Stop'
$controlRoot = Split-Path $PSScriptRoot -Parent
$projectRoot = Join-Path $controlRoot 'projects/money-printer-turbo'
$bootstrapPython = Join-Path $controlRoot '.venv/Scripts/python.exe'
$uvRoot = Join-Path $controlRoot 'runtime/uv-bootstrap'
$uvExe = Join-Path $uvRoot 'bin/uv.exe'

if (-not (Test-Path -LiteralPath $bootstrapPython)) {
    throw 'Run Control Center run.bat first to create its Python environment.'
}

# Keep the downloaded interpreter and cache inside Control Center.
$env:UV_PYTHON_INSTALL_DIR = Join-Path $controlRoot 'runtime/python'
$env:UV_CACHE_DIR = Join-Path $controlRoot 'runtime/uv-cache'
$env:UV_PYTHON_BIN_DIR = Join-Path $controlRoot 'runtime/bin'
$env:UV_PROJECT_ENVIRONMENT = Join-Path $projectRoot '.venv'
$env:PIP_NO_INDEX = '0'
if (-not (Test-Path -LiteralPath $uvExe)) {
    & $bootstrapPython -m pip install --index-url https://pypi.org/simple --target $uvRoot uv
    if ($LASTEXITCODE -ne 0) { throw 'Could not download uv. Check network access.' }
}

& $uvExe sync --project $projectRoot --frozen --no-dev --python 3.11
if ($LASTEXITCODE -ne 0) { throw 'Money Printer Turbo environment installation failed.' }

Write-Host 'Money Printer Turbo is ready. Start it from Control Center (port 8050).'
