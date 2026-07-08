$ErrorActionPreference = "Stop"

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location -LiteralPath $scriptDir

$python = Get-Command python -ErrorAction SilentlyContinue
if ($python -and $python.Source) {
    & $python.Source (Join-Path $scriptDir "start_web.py")
    exit $LASTEXITCODE
}

$py = Get-Command py -ErrorAction SilentlyContinue
if ($py -and $py.Source) {
    & $py.Source (Join-Path $scriptDir "start_web.py")
    exit $LASTEXITCODE
}

throw "Python was not found. Please install Python or make sure the python command is available."
