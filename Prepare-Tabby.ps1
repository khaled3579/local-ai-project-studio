param([string]$PythonPath = (Join-Path $PSScriptRoot '.environments/a5fff534f875230afcea/Scripts/python.exe'))
$ErrorActionPreference = 'Stop'
$root = [IO.Path]::GetFullPath($PSScriptRoot)
$runtime = Join-Path $root 'runtime/tabbyapi'
$source = Join-Path $runtime 'source'
$python = Join-Path $runtime '.venv/Scripts/python.exe'
$commit = '564f8fbb231a2d7e3b339574c85ae7d54ffd9618'
$expected = 'C2FFB96ABB518C32CA6696E80E5231577B39878CB94B1A0AF4DB90CDD8893268'
New-Item -ItemType Directory -Path $runtime -Force | Out-Null
$log = Join-Path $runtime ('prepare-' + [Guid]::NewGuid().ToString('N') + '.log')
Start-Transcript -Path $log | Out-Null
try {
    try { $state = Invoke-RestMethod 'http://127.0.0.1:18079/api/state' -TimeoutSec 4 }
    catch { $state = $null }
    if ($state -and ($state.engine -eq 'tabbyapi') -and $state.pid) { throw 'Stop the owned Tabby model before changing its environment.' }
    if (Test-Path -LiteralPath (Join-Path $runtime 'installation.json')) {
        $receipt = Get-Content -LiteralPath (Join-Path $runtime 'installation.json') -Raw | ConvertFrom-Json
        if ($receipt.commit -eq $commit -and (Test-Path -LiteralPath $python)) {
            $inventoryPath = Join-Path $runtime 'installed-packages.json'
            if (-not (Test-Path -LiteralPath $inventoryPath)) { throw 'Dependency inventory missing; existing environment preserved for review.' }
            $installed = @(Get-Content -LiteralPath $inventoryPath -Raw | ConvertFrom-Json)
            $torchVersion = @($installed | Where-Object { $_.name -eq 'torch' })[0].version
            $tritonVersion = @($installed | Where-Object { $_.name -eq 'triton-windows' })[0].version
            if ($torchVersion -ne '2.9.0+cu128' -or $tritonVersion -notmatch '^3\.5\.') {
                throw 'Existing dependency inventory differs from the pinned torch/CUDA/Triton combination; review before reuse.'
            }
            Write-Host 'The pinned environment is already prepared. No engine or tests were started.'
            return
        }
        throw 'An existing runtime receipt needs review; no environment was overwritten.'
    }
    $archive = Join-Path $root 'runtime/tabbyapi-source.zip'
    if (-not (Test-Path -LiteralPath $archive)) {
        Invoke-WebRequest "https://codeload.github.com/theroyallab/tabbyAPI/zip/$commit" -OutFile $archive
    }
    if ((Get-FileHash -LiteralPath $archive -Algorithm SHA256).Hash -ne $expected) { throw 'Pinned Tabby source hash mismatch.' }
    if (-not (Test-Path -LiteralPath $source)) {
        $staging = Join-Path $runtime ('extract-' + [Guid]::NewGuid().ToString('N'))
        Expand-Archive -LiteralPath $archive -DestinationPath $staging
        $extracted = [IO.Path]::GetFullPath((Join-Path $staging "tabbyAPI-$commit"))
        if (-not $extracted.StartsWith($runtime + [IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase)) { throw 'Source path escaped the runtime.' }
        if (-not ([IO.Path]::GetFullPath($source)).StartsWith($runtime + [IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase)) { throw 'Target escaped the runtime.' }
        Move-Item -LiteralPath $extracted -Destination $source
        $createdSource = $true
    }
    $pin = Join-Path $runtime 'source-pin.json'
    if (Test-Path -LiteralPath $pin) {
        if ((Get-Content -LiteralPath $pin -Raw | ConvertFrom-Json).commit -ne $commit) { throw 'Existing source belongs to a different revision.' }
    } else {
        if (-not $createdSource) { throw 'Unpinned existing source preserved; review before installation.' }
        @{commit=$commit; archive_sha256=$expected} | ConvertTo-Json | Set-Content -LiteralPath $pin -Encoding UTF8
    }
    if (-not (Test-Path -LiteralPath $python)) {
        & $PythonPath -E -s -m venv (Join-Path $runtime '.venv')
        if ($LASTEXITCODE -ne 0) { throw 'Creating the isolated Python environment failed.' }
    }
    $report = Join-Path $runtime 'pip-install-report.json'
    # Dependency installation only. Never import the engine, load weights, generate,
    # run a self-test, change drivers, clocks, WSL, or the Studio Python environment.
    # The pinned cu12 extra selects torch 2.9.0. Its supported Triton branch is
    # 3.5; an unconstrained newer Triton is not a compatible default.
    & $python -E -s -m pip install --no-cache-dir --report $report ($source + '[cu12]') 'requests==2.32.4' 'triton-windows>=3.5,<3.6'
    if ($LASTEXITCODE -ne 0) { throw 'Tabby dependency installation failed. Logs retained; llama.cpp unchanged.' }
    $inventory = Join-Path $runtime 'installed-packages.json'
    & $python -E -s -m pip list --format=json | Set-Content -LiteralPath $inventory -Encoding UTF8
    if ($LASTEXITCODE -ne 0) { throw 'Cannot record the installed dependency inventory.' }
    $hashes = @{}
    Get-ChildItem -LiteralPath $source -Recurse -File | ForEach-Object {
        $relative = $_.FullName.Substring($source.Length + 1)
        if ($relative -notmatch '(^|[\\/])(__pycache__|\.git|build|.*\.egg-info)([\\/]|$)') {
            $hashes[$relative] = (Get-FileHash -LiteralPath $_.FullName -Algorithm SHA256).Hash.ToLowerInvariant()
        }
    }
    $record = @{commit=$commit; source_sha256=$hashes; dependency_inventory_sha256=(Get-FileHash -LiteralPath $inventory -Algorithm SHA256).Hash.ToLowerInvariant(); new_tests_run=0; new_inference_requests=0; runtime_verified=$false; completed=[DateTime]::UtcNow.ToString('o')}
    $temporary = Join-Path $runtime 'installation.json.tmp'
    $record | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $temporary -Encoding UTF8
    Move-Item -LiteralPath $temporary -Destination (Join-Path $runtime 'installation.json')
    Write-Host 'TabbyAPI prepared in isolation. Runtime compatibility and performance remain unverified. No model, tests or measurements were started.'
} finally { Stop-Transcript | Out-Null }
