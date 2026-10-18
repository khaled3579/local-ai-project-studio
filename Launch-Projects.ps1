param([switch]$NoBrowser)
$ErrorActionPreference = 'Stop'
& (Join-Path $PSScriptRoot 'Launch-Daily.ps1') -NoBrowser
if (-not $NoBrowser) { Start-Process 'http://127.0.0.1:18078' -WindowStyle Hidden }
Write-Host 'Project Studio: http://127.0.0.1:18078'
