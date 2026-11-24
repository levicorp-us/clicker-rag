param([ValidateSet('start','stop')][string]$Action='start')
$ErrorActionPreference='Stop'
Set-Location -LiteralPath $PSScriptRoot
$package = Join-Path $PSScriptRoot '.runtime/node_modules/@embedded-postgres/windows-x64/native'
$pg = Join-Path $env:LOCALAPPDATA 'ClickerRag/pgsql17'
# PostgreSQL on Windows uses the system code page for some filesystem calls.
$cluster = Join-Path $env:LOCALAPPDATA 'ClickerRag/db17'
$pw = Join-Path $PSScriptRoot '.runtime/db.secret'
$dbLock=New-Object System.Threading.Mutex($false, 'Local\ClickerRag.Database.55438')
try {
try { $null=$dbLock.WaitOne() } catch [System.Threading.AbandonedMutexException] { }
if ($Action -eq 'stop') {
    if (Test-Path "$cluster/postmaster.pid") { & "$pg/bin/pg_ctl.exe" -D $cluster -m fast -w stop }
    return
}
$python=Join-Path $PSScriptRoot '.venv/Scripts/python.exe'
$probe=Join-Path $PSScriptRoot 'app/db_startup.py'
$state=& $python $probe $cluster $pw
if ($LASTEXITCODE) { throw 'Could not verify the local Clicker database.' }
if ($state -eq 'ready') { Write-Host 'Connected to the existing Clicker database.'; return }
New-Item -ItemType Directory -Force (Split-Path $cluster),'.runtime' | Out-Null
if (!(Test-Path "$package/bin/postgres.exe")) {
    npm.cmd install --prefix .runtime --no-audit --no-fund '@embedded-postgres/windows-x64@17.12.0-beta.19'
    if ($LASTEXITCODE) { throw 'PostgreSQL installation failed' }
}
if (!(Test-Path "$pg/bin/postgres.exe")) { Copy-Item -LiteralPath $package -Destination $pg -Recurse }
if (!(Test-Path "$pg/lib/vector.dll")) {
    $zip=Join-Path $PSScriptRoot '.runtime/vector.zip'
    if (!(Test-Path $zip)) { curl.exe -fL --retry 2 -o $zip https://github.com/andreiramani/pgvector_pgsql_windows/releases/download/0.8.7/vector.v0.8.7-pg17.zip }
    if ((Get-FileHash $zip).Hash -ne 'ACD9D678200E856623F0B7416F6F5D132EF8606A2273FF37F0FDF8A7F2C5D7D2') { throw 'pgvector checksum mismatch' }
    Expand-Archive $zip '.runtime/vector' -Force
    Copy-Item '.runtime/vector/lib/vector.dll' "$pg/lib"
    Copy-Item '.runtime/vector/share/extension/*' "$pg/share/extension" -Force
}
if (!(Test-Path "$cluster/PG_VERSION")) {
    if (!(Test-Path $pw)) { [IO.File]::WriteAllText($pw,([guid]::NewGuid().ToString('N')+[guid]::NewGuid().ToString('N'))) }
    & "$pg/bin/initdb.exe" -D $cluster -U clicker -A scram-sha-256 --encoding=UTF8 --locale=C "--pwfile=$pw"
    if ($LASTEXITCODE) { throw 'Database initialization failed' }
}
if (!(Test-Path $pw)) { throw 'The password for the existing ClickerRag cluster is missing.' }
$state=& $python $probe $cluster $pw --pg-ctl "$pg/bin/pg_ctl.exe"
if ($LASTEXITCODE -or $state -ne 'ready') { throw 'Could not connect to the Clicker database.' }
Write-Host 'Clicker database ready.'
} finally {
    $dbLock.ReleaseMutex()
    $dbLock.Dispose()
}

