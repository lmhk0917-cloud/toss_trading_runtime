param(
  [string]$Symbols = "MU,NVDA,QQQ",
  [int]$RefreshSec = 30,
  [string]$Python = "C:\Users\lmhk2\anaconda3\python.exe"
)

$ErrorActionPreference = "Stop"

$ProjectDir = Split-Path -Parent $PSScriptRoot
Set-Location $ProjectDir

& $Python -m toss_trading_runtime.dashboard_window `
  --symbols $Symbols `
  --refresh-sec $RefreshSec

exit $LASTEXITCODE
