# Local CI-equivalent check, mirroring .github/workflows/ci.yml exactly
# (flake8 . from backend/, black --check backend from repo root, pytest).
# Run this before every commit/PR -- if it's clean here, CI won't surprise you.
#
# Usage (from backend/, with the venv active):
#   .\scripts\verify.ps1

$ErrorActionPreference = "Stop"
$backend = Split-Path -Parent $PSScriptRoot
$root = Split-Path -Parent $backend

Write-Host "== migrate (dev SQLite) ==" -ForegroundColor Cyan
Push-Location $backend
python manage.py migrate
if ($LASTEXITCODE -ne 0) { throw "migrate failed" }

Write-Host "== flake8 . ==" -ForegroundColor Cyan
flake8 .
if ($LASTEXITCODE -ne 0) { throw "flake8 failed" }

Pop-Location
Write-Host "== black --check backend ==" -ForegroundColor Cyan
Push-Location $root
black --check backend
if ($LASTEXITCODE -ne 0) { throw "black --check failed" }

Pop-Location
Write-Host "== pytest ==" -ForegroundColor Cyan
Push-Location $backend
pytest
if ($LASTEXITCODE -ne 0) { throw "pytest failed" }

Pop-Location
Write-Host "`nAll checks passed -- matches CI." -ForegroundColor Green
