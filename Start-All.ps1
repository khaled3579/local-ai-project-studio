param([switch]$NoBrowser)
$ErrorActionPreference = 'Stop'
$requiredPorts = @(18078, 18079, 18080)
$startupLock = $null

function Get-RequiredListeners {
    # Query all listeners once so an unused individual port is not a cmdlet error.
    @(Get-NetTCPConnection -State Listen -ErrorAction Stop |
        Where-Object { $requiredPorts -contains [int]$_.LocalPort })
}

function Get-PortOwnerPlan {
    $plan = @()
    foreach ($group in @(Get-RequiredListeners | Group-Object OwningProcess)) {
        $ownerId = [int]$group.Name
        $process = Get-Process -Id $ownerId -ErrorAction SilentlyContinue
        if ($null -eq $process) { continue } # It exited; the final rescan still checks the port.
        if ($ownerId -le 4 -or $ownerId -eq $PID -or
            $process.ProcessName -in @('System','Registry','smss','csrss','wininit','services','lsass','winlogon','svchost')) {
            throw "Required port is held by protected Windows process $ownerId ($($process.ProcessName)); it cannot be terminated safely."
        }
        $plan += [pscustomobject]@{
            Id = $ownerId
            Name = $process.ProcessName
            Started = $process.StartTime
            Ports = @($group.Group.LocalPort | Sort-Object -Unique)
        }
    }
    $plan
}

function Test-LocalServiceOwner($ownerId, $scriptName) {
    $owner = Get-CimInstance Win32_Process -Filter "ProcessId = $ownerId" -ErrorAction Stop
    $scriptPath = Join-Path $PSScriptRoot $scriptName
    return ($null -ne $owner -and $owner.CommandLine -and
        $owner.CommandLine.IndexOf($scriptPath, [StringComparison]::OrdinalIgnoreCase) -ge 0)
}

function Invoke-LocalServiceAction($port, $path, $body) {
    $session = Invoke-RestMethod "http://127.0.0.1:$port/api/client-session" -Headers @{'X-Local-Session'='refresh'} -TimeoutSec 5
    Invoke-RestMethod "http://127.0.0.1:$port$path" -Method Post -ContentType 'application/json; charset=utf-8' `
        -Headers @{'X-Local-Token'=$session.token} -Body ($body | ConvertTo-Json -Compress) -TimeoutSec 10 | Out-Null
}

function Prepare-OwnedServices($plan) {
    foreach ($owner in $plan) {
        if ($owner.Ports -contains 18078 -and (Test-LocalServiceOwner $owner.Id 'project_studio.py')) {
            try { $studioState = Invoke-RestMethod 'http://127.0.0.1:18078/api/state' -TimeoutSec 5 }
            catch {
                Write-Warning ('Studio is unresponsive; its listener will be stopped: ' + $_.Exception.Message)
                continue
            }
            # Do not orphan a user application or interrupt a file import/manual tool.
            if ($studioState.running_pid -or $studioState.importing -or $studioState.tools_busy) {
                throw 'Stop the project application, import or manual tool in the interface before restarting.'
            }
            if ($studioState.repair_run.status -eq 'running') {
                Write-Host 'Saving a pause checkpoint for the active task before restarting...' -ForegroundColor Yellow
                Invoke-LocalServiceAction 18078 '/api/chain-pause' @{
                    project=$studioState.project; session_id=$studioState.session_id; id=$studioState.repair_run.id
                }
                $deadline = [DateTime]::UtcNow.AddSeconds(150)
                do {
                    Start-Sleep -Milliseconds 500
                    $studioState = Invoke-RestMethod 'http://127.0.0.1:18078/api/state' -TimeoutSec 5
                    if ([DateTime]::UtcNow -gt $deadline) { throw 'The task did not reach a safe pause; services were not forced to stop.' }
                } while ($studioState.repair_run.status -eq 'running' -or $studioState.job.status -eq 'running')
                Write-Host 'Task progress saved. Use Resume in the interface after startup.' -ForegroundColor Yellow
            }
            if ($studioState.job.status -eq 'running') { throw 'An active background operation must finish before restarting.' }
        }
    }
    foreach ($owner in $plan) {
        if ($owner.Ports -contains 18079 -and (Test-LocalServiceOwner $owner.Id 'daily_ai.py')) {
            try {
                Invoke-LocalServiceAction 18079 '/api/stop' @{}
                $modelStopDeadline = [DateTime]::UtcNow.AddSeconds(40)
                do {
                    $modelState = Invoke-RestMethod 'http://127.0.0.1:18079/api/state' -TimeoutSec 5
                    if ($modelState.status -eq 'stopped') { break }
                    if ([DateTime]::UtcNow -gt $modelStopDeadline) { throw 'Timed out waiting for model shutdown.' }
                    Start-Sleep -Milliseconds 250
                } while ($true)
            }
            catch { Write-Warning ('Model stop request failed; required port owners will be stopped: ' + $_.Exception.Message) }
        }
    }
}

function Clear-RequiredPorts {
    $plan = @(Get-PortOwnerPlan)
    if (-not $plan.Count) { Write-Host 'All required TCP ports are available.'; return }
    Prepare-OwnedServices $plan
    # Re-enumerate after graceful shutdown. Never trust a saved PID file or kill
    # every process with a matching executable name.
    $remainingPlan = @(Get-PortOwnerPlan)
    foreach ($owner in $remainingPlan) {
        if (-not @($plan | Where-Object { $_.Id -eq $owner.Id -and $_.Started -eq $owner.Started }).Count) {
            throw 'A different process occupied a required port during startup; restart to inspect its owner.'
        }
    }
    foreach ($owner in $remainingPlan) {
        $process = Get-Process -Id $owner.Id -ErrorAction SilentlyContinue
        if ($null -eq $process) { continue }
        if ($process.StartTime -ne $owner.Started) { throw 'A port owner changed during startup; restart to inspect its new owner.' }
        Write-Host ("Releasing TCP port(s) {0}: {1} (PID {2})" -f ($owner.Ports -join ', '), $owner.Name, $owner.Id) -ForegroundColor Yellow
        try { Stop-Process -InputObject $process -Force -ErrorAction Stop }
        catch { throw ("Cannot release port(s) {0}: {1}. Run Start.cmd as administrator if access was denied." -f ($owner.Ports -join ', '), $_.Exception.Message) }
    }
    $deadline = [DateTime]::UtcNow.AddSeconds(15)
    do {
        $remaining = @(Get-RequiredListeners)
        if (-not $remaining.Count) { Write-Host 'All required TCP ports have been released.' -ForegroundColor Green; return }
        Start-Sleep -Milliseconds 250
    } while ([DateTime]::UtcNow -lt $deadline)
    throw ('Required ports are still occupied or were reopened: ' + (($remaining.LocalPort | Sort-Object -Unique) -join ', '))
}

try {
    Set-Location -LiteralPath $PSScriptRoot
    $stateDirectory = Join-Path $PSScriptRoot 'daily'
    New-Item -ItemType Directory -Path $stateDirectory -Force | Out-Null
    # A second double-click must not stop the services being started by the first.
    try { $startupLock = [IO.File]::Open((Join-Path $stateDirectory 'startup.lock'), [IO.FileMode]::OpenOrCreate, [IO.FileAccess]::ReadWrite, [IO.FileShare]::None) }
    catch { throw 'Another startup is already in progress; wait for it to finish.' }
    Write-Host ('Checking required TCP ports: ' + ($requiredPorts -join ', '))
    Clear-RequiredPorts
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
    exit 1
} finally {
    if ($null -ne $startupLock) { $startupLock.Dispose() }
}
