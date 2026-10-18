param([string]$BackupId)
$ErrorActionPreference='Stop'
$runtimePython=& (Join-Path $PSScriptRoot 'Prepare-Runtime.ps1') -SkipRecovery
& $runtimePython -E -s -c 'import sys;sys.path.insert(0,sys.argv[1]);from app_updates import stop_services;stop_services(sys.argv[1])' $PSScriptRoot
if($LASTEXITCODE -ne 0){throw 'Services are busy; rollback did not start.'}
try {
    $restoreArgs=@('-E','-s',(Join-Path $PSScriptRoot 'app_updates.py'),'--root',$PSScriptRoot,'rollback')
    if($BackupId){$restoreArgs+=@('--id',$BackupId)}
    & $runtimePython @restoreArgs
    if($LASTEXITCODE -ne 0){throw 'Rollback failed; data was preserved.'}
} finally { & (Join-Path $PSScriptRoot 'Launch-Daily.ps1') -NoBrowser }
