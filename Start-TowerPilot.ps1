$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
$python = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $python)) {
    Write-Host 'Creating Tower Pilot environment (requires Python 3.12)...'
    & py -3.12 -m venv .venv
    if ($LASTEXITCODE -ne 0) { throw 'Install Python 3.12, then run Start Tower Pilot again.' }
}
$requirements = Join-Path $PSScriptRoot 'requirements.txt'
$stamp = Join-Path $PSScriptRoot '.venv\requirements.sha256'
$hash = (Get-FileHash -LiteralPath $requirements -Algorithm SHA256).Hash
if (-not (Test-Path -LiteralPath $stamp) -or (Get-Content -LiteralPath $stamp -Raw).Trim() -ne $hash) {
    Write-Host 'Installing dependencies. First launch requires internet access...'
    & $python -m pip install -r $requirements
    if ($LASTEXITCODE -ne 0) { throw 'Dependency installation failed. Check the output and retry.' }
    Set-Content -LiteralPath $stamp -Value $hash
}
$port = if ($env:TOWER_PILOT_PORT) { $env:TOWER_PILOT_PORT } else { '8620' }
# Windowless from here: pythonw has no console, and the launcher puts Tower
# Pilot in the notification area instead (tools/portable_launcher.py). This
# window is only needed for the environment setup above.
$pythonw = Join-Path $PSScriptRoot '.venv\Scripts\pythonw.exe'
if (-not (Test-Path -LiteralPath $pythonw)) { $pythonw = $python }
Write-Host "Starting Tower Pilot. Its icon appears in the notification area; the dashboard is http://127.0.0.1:$port/ui/index.html#setup."
Start-Process -FilePath $pythonw -ArgumentList (Join-Path $PSScriptRoot 'tools\portable_launcher.py') -WorkingDirectory $PSScriptRoot
