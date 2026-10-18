param([Parameter(Mandatory=$true)][string]$Package,[Parameter(Mandatory=$true)][string]$SHA256)
$ErrorActionPreference='Stop'
$runtimePython=& (Join-Path $PSScriptRoot 'Prepare-Runtime.ps1')
$upgradeOk=$false
$modelWasReady=$false
try{$previousModel=Invoke-RestMethod 'http://127.0.0.1:18079/api/state' -TimeoutSec 3;$modelWasReady=$previousModel.status -eq 'ready'}catch{}
try {
    $upgradeResult=& $runtimePython -E -s (Join-Path $PSScriptRoot 'app_updates.py') --root $PSScriptRoot install $Package --sha256 $SHA256 --stop-services
    if($LASTEXITCODE -ne 0){throw 'Upgrade rejected or rolled back; inspect the error above.'}
    $upgradeOk=$true
    Write-Output $upgradeResult
} finally {
    # Restart even when a pre-application backup failure stopped the services.
    try {
        & (Join-Path $PSScriptRoot 'Launch-Daily.ps1') -NoBrowser
        if($upgradeOk -and $modelWasReady){
            $modelReady=$false
            for($healthAttempt=0;$healthAttempt -lt 240;$healthAttempt++){
                $health=Invoke-RestMethod 'http://127.0.0.1:18079/api/state' -TimeoutSec 3
                if($health.status -eq 'ready'){$modelReady=$true;break}
                if($health.status -in @('failed','stopped')){throw ('Model startup failed: '+$health.error)}
                Start-Sleep -Milliseconds 500
            }
            if(-not $modelReady){throw 'Model readiness timed out after upgrade.'}
        }
    }
    catch {
        if($upgradeOk){
            & $runtimePython -E -s -c 'import sys;sys.path.insert(0,sys.argv[1]);from app_updates import stop_services;stop_services(sys.argv[1])' $PSScriptRoot
            & (Join-Path $PSScriptRoot 'Rollback-App.ps1')
        }
        throw
    }
}
