param(
  [string]$Symbols = "",
  [int]$RefreshSec = 30,
  [string]$Python = "C:\Users\lmhk2\anaconda3\python.exe"
)

$ErrorActionPreference = "Stop"

$ProjectDir = Split-Path -Parent $PSScriptRoot
Set-Location $ProjectDir

if (-not $Symbols.Trim()) {
  $watchlistPath = Join-Path $PSScriptRoot "watchlists\us_focus.json"
  try {
    $watchlist = Get-Content -Raw -Path $watchlistPath | ConvertFrom-Json
    $Symbols = (@($watchlist.symbols) | ForEach-Object { "$($_)".Trim().ToUpperInvariant() } | Where-Object { $_ }) -join ","
  } catch {
    $Symbols = ""
  }
}
if (-not $Symbols) {
  $Symbols = "MU,NVDA,AMD,AVGO,MRVL,ARM,TSM,ORCL,CRWV,PLTR,QQQ,SMH"
}

& $Python -m toss_trading_runtime.dashboard_window `
  --symbols $Symbols `
  --refresh-sec $RefreshSec

exit $LASTEXITCODE
