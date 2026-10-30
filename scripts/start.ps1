param([string]$Python = "", [int]$Port = 8765)
$ErrorActionPreference = 'Stop'
$projectDir = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
Set-Location -LiteralPath $projectDir
if (-not $Python) { $Python = Join-Path $projectDir '.venv\Scripts\python.exe' }
if (-not (Test-Path -LiteralPath $Python)) { throw 'Python not found. Follow README.md to create .venv, or pass -Python with an executable path.' }
if (-not (Test-Path -LiteralPath (Join-Path $projectDir 'web\dist\index.html'))) { throw 'Build the interface first: cd web; npm ci; npm run build' }
Write-Host "The application will open its authorized browser session. To stop: & '$Python' -m exhibit.launch --stop --port $Port"
& $Python -m exhibit.launch --port $Port
