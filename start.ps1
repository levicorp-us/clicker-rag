param([switch]$Setup,[switch]$Stop,[switch]$Restart,[switch]$OpenBrowser)
$ErrorActionPreference='Stop'
Set-Location -LiteralPath $PSScriptRoot
$py=Join-Path $PSScriptRoot '.venv/Scripts/python.exe'
$env:PYTHONUTF8='1'
$env:OLLAMA_NO_CLOUD='1'
$env:OLLAMA_HOST='127.0.0.1:11434'
$stateFile=Join-Path $PSScriptRoot '.runtime/services.json'
$runtimeState=Join-Path $PSScriptRoot '.runtime/ollama-service.json'
$lifecycle=New-Object System.Threading.Mutex($false, 'Local\ClickerRag.Lifecycle.8765')
try {
try { $null=$lifecycle.WaitOne() } catch [System.Threading.AbandonedMutexException] { }
if ($Stop -or $Restart) {
    if (Test-Path $stateFile) {
        foreach ($service in (Get-Content $stateFile -Raw | ConvertFrom-Json)) {
            $proc=Get-CimInstance Win32_Process -Filter "ProcessId=$($service.id)"
            if ($proc -and $proc.CreationDate.ToUniversalTime().Ticks.ToString() -eq $service.started -and $proc.CommandLine.Contains($service.module)) {
                Get-CimInstance Win32_Process -Filter "ParentProcessId=$($proc.ProcessId)" | Where-Object { $_.Name -eq 'python.exe' -and $_.CommandLine.Contains($service.module) } | ForEach-Object { Stop-Process -Id $_.ProcessId }
                Stop-Process -Id $service.id -ErrorAction SilentlyContinue
            }
        }
        Remove-Item -LiteralPath $stateFile
    }
    if ($Stop) {
        & "$PSScriptRoot/localdb.ps1" stop
        if (Test-Path $runtimeState) {
            $record=Get-Content -Raw -LiteralPath $runtimeState | ConvertFrom-Json
            $proc=Get-CimInstance Win32_Process -Filter "ProcessId=$($record.id)"
            if ($proc -and $proc.Name -eq 'ollama.exe' -and $proc.CreationDate.ToUniversalTime().Ticks.ToString() -eq $record.started) {
                $runners=Get-CimInstance Win32_Process -Filter "ParentProcessId=$($proc.ProcessId)" | Where-Object Name -eq 'llama-server.exe'
                Stop-Process -Id $proc.ProcessId -ErrorAction SilentlyContinue
                foreach ($runner in $runners) { Stop-Process -Id $runner.ProcessId -ErrorAction SilentlyContinue }
            }
            Remove-Item -LiteralPath $runtimeState
        }
        return
    }
}
if ($Setup -or !(Test-Path $py)) {
    if (!(Test-Path $py)) { py -3.11 -m venv .venv }
    & $py -m pip install -r requirements.txt
    if ($LASTEXITCODE) { throw 'Python installation failed.' }
}
& "$PSScriptRoot/localdb.ps1"
if (-not $env:CLICKER_OLLAMA_URL) {
    $ollama=Join-Path $env:LOCALAPPDATA 'Programs/Ollama/ollama.exe'
    if (!(Test-Path $ollama)) {
        winget install --id Ollama.Ollama -e --scope user --silent --accept-source-agreements --accept-package-agreements --disable-interactivity
        if (!(Test-Path $ollama)) { throw 'Install Ollama for Windows and retry.' }
    }
    try { $null=Invoke-RestMethod http://127.0.0.1:11434/api/version -TimeoutSec 3 }
    catch {
        $daemon=Start-Process -FilePath $ollama -ArgumentList 'serve' -WindowStyle Hidden -PassThru
        $proc=Get-CimInstance Win32_Process -Filter "ProcessId=$($daemon.Id)"
        if ($proc) {
            @{id=$daemon.Id; started=$proc.CreationDate.ToUniversalTime().Ticks.ToString()} | ConvertTo-Json | Set-Content -LiteralPath $runtimeState
        }
        $online=$false
        for ($i=0; $i -lt 40; $i++) {
            Start-Sleep -Seconds 1
            try { $null=Invoke-RestMethod http://127.0.0.1:11434/api/version -TimeoutSec 2; $online=$true; break } catch {}
        }
        if (!$online) { throw 'Ollama did not become ready.' }
    }
    $installed=(Invoke-RestMethod http://127.0.0.1:11434/api/tags).models.name
    $gpuTool=Get-Command nvidia-smi -ErrorAction SilentlyContinue
    if ($gpuTool -and ($installed -contains 'qwen3.8:27b')) {
        $gpu=& $gpuTool.Source --query-gpu=name,memory.total --format=csv,noheader,nounits
        if ($gpu -match 'RTX 2060, 6144') {
            if ($installed -notcontains 'clicker-qwen3.8:27b') { & "$PSScriptRoot/text-model.ps1" -Ollama $ollama }
            [IO.File]::WriteAllText((Join-Path $PSScriptRoot '.runtime/model-profile.json'),'{"model":"clicker-qwen3.8:27b"}')
        }
    }
}
$known=@()
if (Test-Path $stateFile) { $known=Get-Content $stateFile -Raw | ConvertFrom-Json }
$services=@()
foreach ($svc in @(@{module='app.worker';args='-m app.worker';name='worker'},@{module='app.api:app';args='-m uvicorn app.api:app --host 127.0.0.1 --port 8765 --no-access-log';name='api'})) {
    $old=$known | Where-Object module -eq $svc.module | Select-Object -First 1
    if ($old) {
        $proc=Get-CimInstance Win32_Process -Filter "ProcessId=$($old.id)"
        if ($proc -and $proc.CreationDate.ToUniversalTime().Ticks.ToString() -eq $old.started -and $proc.CommandLine.Contains($svc.module)) { $services+=$old;continue }
    }
    # The launcher can be closed after starting a process but before saving its PID.
    $existing=Get-CimInstance Win32_Process -Filter "Name='python.exe'" | Where-Object {
        $_.ExecutablePath -eq [IO.Path]::GetFullPath($py) -and $_.CommandLine.TrimEnd().EndsWith($svc.args)
    } | Select-Object -First 1
    if ($existing) {
        $services+=@{id=$existing.ProcessId;started=$existing.CreationDate.ToUniversalTime().Ticks.ToString();module=$svc.module}
        continue
    }
    if ($svc.name -eq 'api' -and ([Net.NetworkInformation.IPGlobalProperties]::GetIPGlobalProperties().GetActiveTcpListeners() |
        Where-Object { $_.Port -eq 8765 -and $_.Address.ToString() -in @('127.0.0.1','0.0.0.0','::') })) {
        # Check the existing listener below. Do not launch a competing web server.
        continue
    }
    $logBase=Join-Path $PSScriptRoot ('.runtime/'+$svc.name)
    $p=Start-Process -FilePath $py -ArgumentList $svc.args -WorkingDirectory $PSScriptRoot -RedirectStandardError "$logBase.err" -RedirectStandardOutput "$logBase.log" -WindowStyle Hidden -PassThru
    $proc=Get-CimInstance Win32_Process -Filter "ProcessId=$($p.Id)"
    $services+=@{id=$p.Id;started=$proc.CreationDate.ToUniversalTime().Ticks.ToString();module=$svc.module}
}
ConvertTo-Json -InputObject $services | Set-Content $stateFile
$url='http://127.0.0.1:8765/'
for ($i=0;$i -lt 30;$i++) {
    try {
        $page=Invoke-WebRequest $url -UseBasicParsing -TimeoutSec 3 -SessionVariable clickerSession
    } catch { Start-Sleep 1; continue }
    if (!$clickerSession.Cookies.GetCookies([uri]$url)['clicker_session'] -or $page.Content -notmatch '/static/app\.js') {
        throw 'Port 8765 is occupied by a different web service.'
    }
    if ($OpenBrowser) { Start-Process $url }
    Write-Host "Clicker ready  $url"
    return
}
throw 'Check .runtime/api.err for startup details.'
} finally {
    $lifecycle.ReleaseMutex()
    $lifecycle.Dispose()
}

