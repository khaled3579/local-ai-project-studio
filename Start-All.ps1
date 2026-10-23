param([switch]$NoBrowser)
$ErrorActionPreference = 'Stop'
try {
    Set-Location -LiteralPath $PSScriptRoot
    & (Join-Path $PSScriptRoot 'Launch-Projects.ps1') -NoBrowser
    if (-not $NoBrowser) { Start-Process 'http://127.0.0.1:18078/' -WindowStyle Hidden }
    Write-Host 'Project Studio: http://127.0.0.1:18078/' -ForegroundColor Green
    Write-Host 'Models: http://127.0.0.1:18079/'
    Write-Host 'Waiting for the selected local model...'
    for ($attempt = 0; $attempt -lt 300; $attempt++) {
        $state = Invoke-RestMethod 'http://127.0.0.1:18079/api/state' -TimeoutSec 5
        if ($state.status -eq 'ready') { Write-Host 'Model ready. You can start working.' -ForegroundColor Green; exit 0 }
        if ($state.status -in @('failed','stopped')) {
            Write-Host 'The interface is running. Select a valid GGUF model in the Models page.' -ForegroundColor Yellow
            if ($state.error) { Write-Host $state.error -ForegroundColor Yellow }
            exit 0
        }
        Start-Sleep -Seconds 1
    }
    Write-Host 'The interface is running; check model loading progress in the Models page.' -ForegroundColor Yellow
} catch {
    Write-Host ('Startup failed: ' + $_.Exception.Message) -ForegroundColor Red
    Write-Host 'If another installation is running, close its tasks and services first. See README_START_AR.txt.'
    exit 1
}
