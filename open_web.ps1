$ErrorActionPreference = "SilentlyContinue"

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$port = 8765
$url = "http://127.0.0.1:$port"

Set-Location -LiteralPath $scriptDir

$listening = netstat -ano | Select-String ":$port .*LISTENING"
if (-not $listening) {
    Start-Process -FilePath "python" -ArgumentList "web_app.py" -WorkingDirectory $scriptDir -WindowStyle Hidden
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
