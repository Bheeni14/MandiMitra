# MandiMitra supervised local daily capture (Windows Task Scheduler entrypoint).
# Runs full Agmarknet pagination. Does not enable Storage upload, Gemini, or advisor persistence.
# Logs append to D:\mandimitra_backups\logs\daily_capture_YYYYMMDD.log

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$Python = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
$LogDir = "D:\mandimitra_backups\logs"
$Stamp = Get-Date -Format "yyyyMMdd"
$LogFile = Join-Path $LogDir "daily_capture_$Stamp.log"

New-Item -ItemType Directory -Force -Path $LogDir | Out-Null

if (-not (Test-Path $Python)) {
    throw "MandiMitra venv python not found at $Python"
}

Set-Location $ProjectRoot
$env:DEMO_MODE = "false"
$env:RAW_STORAGE_UPLOAD_ENABLED = "false"
$env:GEMINI_ENABLED = "false"
$env:ADVISOR_PERSIST = "false"
Remove-Item Env:PGOPTIONS -ErrorAction SilentlyContinue

function Write-Log([string]$Message) {
    $line = "{0:yyyy-MM-ddTHH:mm:ssK} {1}" -f (Get-Date), $Message
    Add-Content -Path $LogFile -Value $line -Encoding utf8
    Write-Host $line
}

Write-Log "START MandiMitra daily capture (local Task Scheduler)"
Write-Log "cwd=$ProjectRoot python=$Python"

& $Python "scripts\ingest_all.py" --daily *>> $LogFile 2>&1
$code = $LASTEXITCODE
Write-Log "END exit_code=$code"
exit $code
