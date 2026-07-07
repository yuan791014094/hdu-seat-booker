$ErrorActionPreference = "SilentlyContinue"

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$port = 8765
$url = "http://127.0.0.1:$port"
$stdoutLog = Join-Path $scriptDir "web_app_stdout.log"
$stderrLog = Join-Path $scriptDir "web_app_stderr.log"

Set-Location -LiteralPath $scriptDir

function Get-PythonLauncher {
    $python = Get-Command python -ErrorAction SilentlyContinue
    if ($python -and $python.Source) {
        $pythonw = Join-Path (Split-Path -Parent $python.Source) "pythonw.exe"
        if (Test-Path -LiteralPath $pythonw) {
            return $pythonw
        }
        return $python.Source
    }

    $pythonw = Get-Command pythonw -ErrorAction SilentlyContinue
    if ($pythonw -and $pythonw.Source) {
        return $pythonw.Source
    }

    return "python"
}

function Start-WebApp {
    $pythonExe = Get-PythonLauncher
    $cmd = "cd /d `"$scriptDir`" && `"$pythonExe`" `"$scriptDir\web_app.py`" 1>>`"$stdoutLog`" 2>>`"$stderrLog`""
    cmd.exe /c start "" /min cmd.exe /c $cmd | Out-Null
}

$listening = netstat -ano | Select-String ":$port .*LISTENING"
if (-not $listening) {
    Start-WebApp
}

for ($i = 0; $i -lt 20; $i++) {
    try {
        Invoke-WebRequest -UseBasicParsing -Uri $url -TimeoutSec 1 | Out-Null
        break
    } catch {
        Start-Sleep -Milliseconds 500
    }
}

Start-Process $url
