# deploy.ps1 — Pull latest code from Git and restart the bot on Windows VPS
#
# Usage (from VPS PowerShell in the bot folder):
#   .\deploy.ps1
#
# What it does:
#   1. git pull origin main          — fetches your latest local changes
#   2. Kills any running bot process (run_bot.py or main.py)
#   3. Restarts run_bot.py in the background, logs to logs\bot_output.log

$ErrorActionPreference = "Stop"
$BotDir   = $PSScriptRoot
$VenvPy   = "C:\forex_venv\Scripts\python.exe"   # update if your venv path differs
$LogFile  = Join-Path $BotDir "logs\bot_output.log"
$ErrFile  = Join-Path $BotDir "logs\bot_error.log"

Write-Host ""
Write-Host "=== Mr Bera's Bot — Deploy ===" -ForegroundColor Cyan
Write-Host "Directory: $BotDir" -ForegroundColor Gray
Write-Host ""

# ── 1. Pull latest code ──────────────────────────────────────────────────────
Write-Host "[1/3] Pulling latest code from Git..." -ForegroundColor Yellow
Set-Location $BotDir
$gitResult = git pull origin main 2>&1
Write-Host $gitResult

if ($LASTEXITCODE -ne 0) {
    Write-Host "Git pull failed — aborting deploy. Fix conflicts first." -ForegroundColor Red
    exit 1
}

# ── 2. Stop any running bot process ─────────────────────────────────────────
Write-Host ""
Write-Host "[2/3] Stopping existing bot process..." -ForegroundColor Yellow

$stopped = 0
Get-Process -Name "python" -ErrorAction SilentlyContinue | ForEach-Object {
    try {
        $wmi = Get-WmiObject Win32_Process -Filter "ProcessId = $($_.Id)" -ErrorAction Stop
        $cmd = $wmi.CommandLine
        if ($cmd -like "*run_bot.py*" -or $cmd -like "*main.py*") {
            Stop-Process -Id $_.Id -Force
            Write-Host "  Stopped PID $($_.Id): $cmd" -ForegroundColor Green
            $stopped++
        }
    } catch {}
}

if ($stopped -eq 0) {
    Write-Host "  No running bot process found." -ForegroundColor Gray
} else {
    Write-Host "  Waiting 3 seconds for clean shutdown..." -ForegroundColor Gray
    Start-Sleep -Seconds 3
}

# ── 3. Start the bot ─────────────────────────────────────────────────────────
Write-Host ""
Write-Host "[3/3] Starting bot..." -ForegroundColor Yellow

New-Item -ItemType Directory -Force -Path (Join-Path $BotDir "logs") | Out-Null

$proc = Start-Process `
    -FilePath $VenvPy `
    -ArgumentList "run_bot.py" `
    -WorkingDirectory $BotDir `
    -RedirectStandardOutput $LogFile `
    -RedirectStandardError  $ErrFile `
    -PassThru `
    -WindowStyle Hidden

Write-Host ""
Write-Host "=== Bot started ===" -ForegroundColor Green
Write-Host "PID:    $($proc.Id)" -ForegroundColor White
Write-Host "Stdout: $LogFile"    -ForegroundColor Gray
Write-Host "Stderr: $ErrFile"    -ForegroundColor Gray
Write-Host ""
Write-Host "Monitor live output:" -ForegroundColor Cyan
Write-Host "  Get-Content '$LogFile' -Wait -Tail 40"
Write-Host ""
