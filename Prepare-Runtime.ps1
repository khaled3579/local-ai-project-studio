param([switch]$SkipRecovery)
$ErrorActionPreference='Stop'
$runtimeBase=(Get-Command python.exe -ErrorAction Stop).Source
$runtimeVersion=& $runtimeBase -I -c 'import sys,struct;print(str(sys.version_info.major)+chr(46)+str(sys.version_info.minor)+chr(45)+str(struct.calcsize(chr(80))*8))'
if ($runtimeVersion -ne '3.13-64') { throw 'Python 3.13 x64 is required.' }
if (-not $SkipRecovery) {
    $recoveryPython=$runtimeBase
    $runtimePointer=Join-Path $PSScriptRoot 'daily/runtime.json'
    if(Test-Path -LiteralPath $runtimePointer){
        $runtimeEntry=(Get-Content -LiteralPath $runtimePointer -Raw | ConvertFrom-Json).environment
        $runtimeCandidate=[IO.Path]::GetFullPath((Join-Path $PSScriptRoot ($runtimeEntry+'/Scripts/python.exe')))
        if(-not $runtimeCandidate.StartsWith(([IO.Path]::GetFullPath($PSScriptRoot)+[IO.Path]::DirectorySeparatorChar),[StringComparison]::OrdinalIgnoreCase)){throw 'Invalid saved runtime location.'}
        if(Test-Path -LiteralPath $runtimeCandidate){$recoveryPython=$runtimeCandidate}
    }
    & $recoveryPython -E -s (Join-Path $PSScriptRoot 'app_updates.py') --root $PSScriptRoot recover | Out-Null
    if ($LASTEXITCODE -ne 0) { throw 'Interrupted upgrade recovery failed. Existing files were preserved.' }
}
$runtimeOutput=& $runtimeBase -I (Join-Path $PSScriptRoot 'app_runtime.py') --root $PSScriptRoot prepare
if ($LASTEXITCODE -ne 0) { throw 'Offline runtime preparation failed.' }
($runtimeOutput | ConvertFrom-Json).python
