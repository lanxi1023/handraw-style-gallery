$ErrorActionPreference='Stop'
$studio=$PSScriptRoot
$data=Join-Path $studio 'data'
$config=Get-Content -LiteralPath (Join-Path $data 'remote-config.json') -Raw -Encoding UTF8 | ConvertFrom-Json
if (-not $config.enabled) { throw 'Remote access is not enabled. Configure and verify the HTTPS tunnel first.' }
if (-not (Get-NetTCPConnection -State Listen -LocalPort 8768 -ErrorAction SilentlyContinue)) { throw 'Start the local studio first.' }
if (-not (Get-NetTCPConnection -State Listen -LocalPort 8769 -ErrorAction SilentlyContinue)) {
    $python='E:\'+[char]0x53f7+[char]0x6c60+'\runtime\fpb-venv\Scripts\python.exe'
    $p=Start-Process -FilePath $python -ArgumentList @('-m','uvicorn','remote_gateway:app','--host','127.0.0.1','--port','8769','--no-access-log','--log-level','warning') -WorkingDirectory $studio -WindowStyle Hidden -RedirectStandardOutput (Join-Path $data 'remote.out.log') -RedirectStandardError (Join-Path $data 'remote.err.log') -PassThru
    $p.Id | Set-Content -LiteralPath (Join-Path $data 'remote.pid')
}
