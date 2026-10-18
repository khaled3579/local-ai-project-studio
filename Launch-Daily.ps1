param([switch]$NoBrowser)
$ErrorActionPreference='Stop'
$runtimePython=& (Join-Path $PSScriptRoot 'Prepare-Runtime.ps1')
$env:PYTHONNOUSERSITE='1'
Remove-Item Env:PYTHONPATH -ErrorAction SilentlyContinue
Remove-Item Env:PYTHONHOME -ErrorAction SilentlyContinue
$env:PATH=(Join-Path $PSScriptRoot 'runtime\node')+';'+$env:PATH
$serviceLog=Join-Path $PSScriptRoot 'daily'
New-Item -ItemType Directory -Path $serviceLog -Force | Out-Null
foreach($service in @(@{Name='studio';Script='project_studio.py';Port=18078;Pid='projects.pid'},@{Name='manager';Script='daily_ai.py';Port=18079;Pid='manager.pid'})) {
    $running=$false
    try { $state=Invoke-RestMethod "http://127.0.0.1:$($service.Port)/api/state" -TimeoutSec 2; $running=$true } catch {}
    if ($running) {
        $ownerId=[int](Get-Content (Join-Path $serviceLog $service.Pid) -ErrorAction Stop)
        $owner=Get-CimInstance Win32_Process -Filter "ProcessId = $ownerId"
        $scriptPath=Join-Path $PSScriptRoot $service.Script
        if (-not $owner -or -not $owner.CommandLine.Contains($scriptPath)) { throw "Port $($service.Port) belongs to another installation." }
        continue
    }
    $scriptPath=Join-Path $PSScriptRoot $service.Script
    $bootstrapId=[Guid]::NewGuid().ToString('N')
    $bootstrapOut=Join-Path $serviceLog ('logs/'+$service.Name+'-bootstrap-'+$bootstrapId+'.stdout.log')
    $bootstrapErr=Join-Path $serviceLog ('logs/'+$service.Name+'-bootstrap-'+$bootstrapId+'.stderr.log')
    New-Item -ItemType Directory -Path (Join-Path $serviceLog 'logs') -Force | Out-Null
    $process=Start-Process -FilePath $runtimePython -ArgumentList @('-E','-s','-u',('"'+$scriptPath+'"')) -WorkingDirectory $PSScriptRoot -WindowStyle Hidden -PassThru -RedirectStandardOutput $bootstrapOut -RedirectStandardError $bootstrapErr
    [IO.File]::WriteAllText((Join-Path $serviceLog $service.Pid),[string]$process.Id)
    $ready=$false
    for($attempt=0;$attempt -lt 60;$attempt++) {
        Start-Sleep -Milliseconds 250
        if($process.HasExited){throw "Service $($service.Name) exited; inspect daily\logs."}
        try {$r=Invoke-RestMethod "http://127.0.0.1:$($service.Port)/api/state" -TimeoutSec 1;$ready=$true;break}catch{}
    }
    if(-not $ready){throw "Service $($service.Name) did not become ready."}
}
if(-not $NoBrowser){Start-Process 'http://127.0.0.1:18079' -WindowStyle Hidden}
Write-Host 'AI services ready: http://127.0.0.1:18078'
