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
    New-Item -ItemType Directory -Path (Join-Path $serviceLog 'logs') -Force | Out-Null
    # WMI owns the launch, so closing a launcher/installer job cannot own this service tree.
    # The bootstrap preserves startup errors and native crash traces in a real file.
    $startup=New-CimInstance -ClassName Win32_ProcessStartup -ClientOnly -Property @{ShowWindow=[uint16]0}
    $bootstrap=Join-Path $PSScriptRoot 'service_bootstrap.py'
    $command='"'+$runtimePython+'" -E -s -u "'+$bootstrap+'" "'+$scriptPath+'" "'+$bootstrapOut+'"'
    $created=Invoke-CimMethod -ClassName Win32_Process -MethodName Create -Arguments @{CommandLine=$command;CurrentDirectory=$PSScriptRoot;ProcessStartupInformation=$startup}
    if($created.ReturnValue -ne 0){throw "Independent service launch failed: $($created.ReturnValue). Existing services were preserved."}
    $process=Get-Process -Id $created.ProcessId -ErrorAction Stop
    [IO.File]::WriteAllText((Join-Path $serviceLog $service.Pid),[string]$process.Id)
    $ready=$false
    for($attempt=0;$attempt -lt 60;$attempt++) {
        Start-Sleep -Milliseconds 250
        if($process.HasExited){throw "Service $($service.Name) exited; inspect daily\logs."}
        try {
            $r=Invoke-RestMethod "http://127.0.0.1:$($service.Port)/api/state" -TimeoutSec 1
            $listeners=@(Get-NetTCPConnection -State Listen -LocalPort $service.Port -ErrorAction Stop | Select-Object -ExpandProperty OwningProcess -Unique)
            if($listeners.Count -ne 1){throw 'Unexpected listener count'}
            $actual=Get-CimInstance Win32_Process -Filter "ProcessId = $($listeners[0])"
            if(-not $actual -or -not $actual.CommandLine.Contains($scriptPath)){throw 'Unexpected service owner'}
            [IO.File]::WriteAllText((Join-Path $serviceLog $service.Pid),[string]$listeners[0])
            $ready=$true;break
        }catch{}
    }
    if(-not $ready){throw "Service $($service.Name) did not become ready."}
}
if(-not $NoBrowser){Start-Process 'http://127.0.0.1:18079' -WindowStyle Hidden}
Write-Host 'AI services ready: http://127.0.0.1:18078'
