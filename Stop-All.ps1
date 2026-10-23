$ErrorActionPreference = 'Stop'
try {
    $python = & (Join-Path $PSScriptRoot 'Prepare-Runtime.ps1')
    & $python -E -s -c "import sys;sys.path.insert(0,sys.argv[1]);from app_updates import stop_services;print(stop_services(sys.argv[1]))" $PSScriptRoot
    if ($LASTEXITCODE -ne 0) { throw 'Stop rejected. Stop active tasks and project applications in the interface first.' }
    Write-Host 'Services stopped.' -ForegroundColor Green
} catch { Write-Host $_.Exception.Message -ForegroundColor Red; exit 1 }
