$ErrorActionPreference='Stop'
$runtimePython=& (Join-Path $PSScriptRoot 'Prepare-Runtime.ps1')
& $runtimePython -I (Join-Path $PSScriptRoot 'app_runtime.py') --root $PSScriptRoot doctor
if($LASTEXITCODE -ne 0){throw 'Environment doctor found a problem.'}
