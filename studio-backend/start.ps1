$ErrorActionPreference = 'Stop'
$studio = $PSScriptRoot
$pool = 'E:\' + [char]0x53F7 + [char]0x6C60
$data = Join-Path $studio 'data'
New-Item -ItemType Directory -Force -Path $data | Out-Null
$mutex = New-Object System.Threading.Mutex($false, 'Local\InspirationStudioLauncher')
$locked = $false
try {
    $locked = $mutex.WaitOne(0)
    if (-not $locked) { exit 0 }
    & (Join-Path $pool 'checks\start-services.ps1') | Out-Null
    Start-Process -FilePath 'powershell.exe' -ArgumentList @('-NoProfile','-ExecutionPolicy','Bypass','-WindowStyle','Hidden','-File',('"'+(Join-Path $studio 'run-background.ps1')+'"')) -WorkingDirectory $studio -WindowStyle Hidden | Out-Null
    if (-not (Get-NetTCPConnection -State Listen -LocalPort 8768 -ErrorAction SilentlyContinue)) {
        $python = Join-Path $pool 'runtime\fpb-venv\Scripts\python.exe'
        $p = Start-Process -FilePath $python -ArgumentList @('-m','uvicorn','server:app','--host','127.0.0.1','--port','8768','--no-access-log','--log-level','warning') -WorkingDirectory $studio -WindowStyle Hidden -RedirectStandardOutput (Join-Path $data 'server.out.log') -RedirectStandardError (Join-Path $data 'server.err.log') -PassThru
        $p.Id | Set-Content -LiteralPath (Join-Path $data 'server.pid')
    }
    for ($i = 0; $i -lt 60; $i++) {
        try {
            $r = Invoke-RestMethod -Uri 'http://127.0.0.1:8768/api/health' -TimeoutSec 2
            if ($r.service -eq 'inspiration-studio' -and $r.ready) {
                Start-Process 'http://127.0.0.1:8768/'
                exit 0
            }
        } catch {}
        Start-Sleep -Seconds 1
    }
    throw 'Studio did not become ready within 60 seconds.'
} catch {
    ($_ | Out-String) | Add-Content -LiteralPath (Join-Path $data 'launcher.err.log') -Encoding UTF8
    exit 1
} finally {
    if ($locked) { $mutex.ReleaseMutex() }
    $mutex.Dispose()
}
