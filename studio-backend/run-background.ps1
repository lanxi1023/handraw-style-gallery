$ErrorActionPreference='Stop'
$studio=$PSScriptRoot
$pool='E:\'+[char]0x53f7+[char]0x6c60
$data=Join-Path $studio 'data'
$python=Join-Path $pool 'runtime\fpb-venv\Scripts\python.exe'
$mutex=New-Object System.Threading.Mutex($false,'Local\InspirationStudioBackground')
if (-not $mutex.WaitOne(0)) { exit 0 }
try {
    while ($true) {
        try {
            & (Join-Path $pool 'checks\start-services.ps1') | Out-Null
            if (-not (Get-NetTCPConnection -State Listen -LocalPort 8768 -ErrorAction SilentlyContinue)) {
                Start-Process -FilePath $python -ArgumentList @('-m','uvicorn','server:app','--host','127.0.0.1','--port','8768','--no-access-log','--log-level','warning') -WorkingDirectory $studio -WindowStyle Hidden -RedirectStandardOutput (Join-Path $data 'server.out.log') -RedirectStandardError (Join-Path $data 'server.err.log') | Out-Null
            }
            $config=Get-Content -LiteralPath (Join-Path $data 'remote-config.json') -Raw -Encoding UTF8 | ConvertFrom-Json
            if ($config.enabled) {
                & (Join-Path $studio 'start-remote.ps1') | Out-Null
                $exe=Join-Path $pool 'runtime\cloudflared\cloudflared.exe'
                $token=Join-Path $data 'tunnel-token.txt'
                $running=Get-CimInstance Win32_Process -Filter "Name='cloudflared.exe'" | Where-Object { $_.ExecutablePath -eq $exe -and $_.CommandLine -like '*--token-file*' -and $_.CommandLine -like ('*'+$token+'*') }
                if ((Test-Path -LiteralPath $token) -and -not $running) {
                    $env:TUNNEL_TRANSPORT_PROTOCOL='http2'
                    $env:TUNNEL_EDGE_IP_VERSION='4'
                    Start-Process -FilePath $exe -ArgumentList @('--no-autoupdate','tunnel','run','--token-file',('"'+$token+'"')) -WorkingDirectory $studio -WindowStyle Hidden -RedirectStandardOutput (Join-Path $data 'tunnel.out.log') -RedirectStandardError (Join-Path $data 'tunnel.err.log') | Out-Null
                }
            }
        } catch {
            ((Get-Date).ToString('o')+' '+$_.Exception.Message) | Add-Content -LiteralPath (Join-Path $data 'background.err.log') -Encoding UTF8
        }
        Start-Sleep -Seconds 30
    }
} finally {
    $mutex.ReleaseMutex()
    $mutex.Dispose()
}
