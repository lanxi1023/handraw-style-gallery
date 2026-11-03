$ErrorActionPreference='Stop'
$pythonw='E:\'+[char]0x53f7+[char]0x6c60+'\runtime\fpb-venv\Scripts\pythonw.exe'
$entry=Join-Path $PSScriptRoot 'background.py'
$p=Start-Process -FilePath $pythonw -ArgumentList @(('"'+$entry+'"'),'run') -WorkingDirectory $PSScriptRoot -WindowStyle Hidden -PassThru -Wait
exit $p.ExitCode
