# Start CR Automation ADK Pipeline Server
Set-Location $PSScriptRoot

# Activate venv if present
if (Test-Path ".venv\Scripts\Activate.ps1") { & .venv\Scripts\Activate.ps1 }

# Ensure data directories
@("data\uploads", "data\runs", "data\logs") | ForEach-Object {
    New-Item -ItemType Directory -Path $_ -Force | Out-Null
}

# Kill existing instance
$existing = Get-NetTCPConnection -LocalPort 8000 -State Listen -ErrorAction SilentlyContinue
if ($existing) {
    Stop-Process -Id ($existing.OwningProcess | Select-Object -First 1) -Force -ErrorAction SilentlyContinue
    Start-Sleep -Seconds 1
}

# Set PYTHONPATH for cr_automation-agentic imports
$env:PYTHONPATH = "$PSScriptRoot;D:\my_workspace\cr_automation-agentic"
$env:PYTHONWARNINGS = "ignore"

Write-Host "============================================"
Write-Host "  CR Automation ADK Pipeline Server"
Write-Host "============================================"
Write-Host "  URL:  http://localhost:8000"
Write-Host "============================================"

python -u -W ignore src/ui/app.py
