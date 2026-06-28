$BotDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$VenvPy = "C:\forex_venv\Scripts\python.exe"
$LogFile = "$BotDir\logs\bot_output.log"
$ErrFile = "$BotDir\logs\bot_error.log"

Write-Host "=== Mr Bera's Bot - Deploy ===" -ForegroundColor Cyan
Set-Location $BotDir

Write-Host "[1/3] Pulling latest code..." -ForegroundColor Yellow
git pull origin main
Write-Host ""

Write-Host "[2/3] Stopping existing bot process..." -ForegroundColor Yellow
$procs = Get-Process -Name "python" -ErrorAction SilentlyContinue
foreach ($p in $procs) {
    $wmi = Get-WmiObject Win32_Process -Filter "ProcessId = $($p.Id)"
    if ($wmi -and ($wmi.CommandLine -like "*run_bot*" -or $wmi.CommandLine -like "*main.py*")) {
        Stop-Process -Id $p.Id -Force
        Write-Host "  Stopped PID $($p.Id)" -ForegroundColor Green
    }
}
Start-Sleep -Seconds 3
Write-Host ""

Write-Host "[3/3] Starting bot..." -ForegroundColor Yellow
New-Item -ItemType Directory -Force -Path "$BotDir\logs" | Out-Null
$proc = Start-Process -FilePath $VenvPy -ArgumentList "run_bot.py" -WorkingDirectory $BotDir -RedirectStandardOutput $LogFile -RedirectStandardError $ErrFile -PassThru -WindowStyle Hidden

Write-Host "=== Bot started - PID $($proc.Id) ===" -ForegroundColor Green
Write-Host "Live output: Get-Content '$LogFile' -Wait -Tail 40" -ForegroundColor Cyan
