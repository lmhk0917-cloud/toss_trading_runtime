param(
    [string]$Symbols = "MU,NVDA,AMD,AVGO,MRVL,ARM,TSM,ORCL,CRWV,PLTR,QQQ,SMH",
    [string]$StartKst = "22:31",
    [string]$UntilKst = "05:00",
    [int[]]$WaitForPid = @(),
    [string]$WaitForPidCsv = "",
    [int]$MaxWaitAfterStartSec = 60,
    [switch]$SkipUi
)

$ErrorActionPreference = "Stop"
$RuntimeDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$Root = Split-Path -Parent $RuntimeDir
$Python = "C:\Users\lmhk2\anaconda3\python.exe"
$Pythonw = "C:\Users\lmhk2\anaconda3\pythonw.exe"
$ReportsDir = Join-Path $RuntimeDir "reports"
$LogDir = Join-Path $ReportsDir "run_logs"
New-Item -ItemType Directory -Force -Path $LogDir | Out-Null

function Resolve-TargetTime([string]$hhmm) {
    $parts = $hhmm.Split(":")
    $now = Get-Date
    $target = Get-Date -Hour ([int]$parts[0]) -Minute ([int]$parts[1]) -Second 0
    if ($target -le $now) {
        $target = $target.AddDays(1)
    }
    return $target
}

$startAt = Resolve-TargetTime $StartKst
$waitPids = @()
foreach ($pidValue in $WaitForPid) {
    if ($pidValue -gt 0) {
        $waitPids += [int]$pidValue
    }
}
foreach ($pidText in $WaitForPidCsv.Split(",")) {
    $pidText = $pidText.Trim()
    if ($pidText) {
        $waitPids += [int]$pidText
    }
}
$waitPids = @($waitPids | Select-Object -Unique)
$watcherLog = Join-Path $LogDir "regular_session_watcher_latest.log"
"REGULAR_WATCHER_STARTED=$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') START=$($startAt.ToString('yyyy-MM-dd HH:mm:ss')) SYMBOLS=$Symbols WAIT_FOR=$($waitPids -join ',')" |
    Out-File -FilePath $watcherLog -Encoding utf8

while ((Get-Date) -lt $startAt) {
    Start-Sleep -Seconds 15
}

foreach ($pidToWait in $waitPids) {
    $waitStarted = Get-Date
    while (Get-Process -Id $pidToWait -ErrorAction SilentlyContinue) {
        "WAITING_FOR_PID=$pidToWait $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')" | Out-File -FilePath $watcherLog -Append -Encoding utf8
        $elapsed = [int]((Get-Date) - $waitStarted).TotalSeconds
        if ($elapsed -ge $MaxWaitAfterStartSec) {
            "WAIT_TIMEOUT_PID=$pidToWait ELAPSED_SEC=$elapsed ACTION=stop_and_continue $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')" |
                Out-File -FilePath $watcherLog -Append -Encoding utf8
            Stop-Process -Id $pidToWait -Force -ErrorAction SilentlyContinue
            break
        }
        Start-Sleep -Seconds 10
    }
}

$stamp = Get-Date -Format "yyyyMMdd_HHmmss"
$marketOut = Join-Path $LogDir "regular_session_$stamp.out.log"
$marketErr = Join-Path $LogDir "regular_session_$stamp.err.log"
$tickOut = Join-Path $LogDir "regular_tick_collector_$stamp.out.log"
$tickErr = Join-Path $LogDir "regular_tick_collector_$stamp.err.log"
$gptOut = Join-Path $LogDir "regular_periodic_gpt_$stamp.out.log"
$gptErr = Join-Path $LogDir "regular_periodic_gpt_$stamp.err.log"

$now = Get-Date
$stop = Resolve-TargetTime $UntilKst
$seconds = [Math]::Max(60, [int]($stop - $now).TotalSeconds)
$tickIterations = [Math]::Max(1, [int][Math]::Ceiling($seconds / 60))

$marketArgs = @(
    "-u", "-m", "toss_trading_runtime.market_session_run",
    "--symbols", $Symbols,
    "--until-kst", $UntilKst,
    "--interval-sec", "60",
    "--minute-count", "120",
    "--daily-count", "20",
    "--max-tokens", "2200"
)
$tickArgs = @(
    "-u", "-m", "toss_trading_runtime.tick_collector",
    "--symbols", $Symbols,
    "--iterations", "$tickIterations",
    "--interval-sec", "60",
    "--trade-count", "50"
)
$gptArgs = @(
    "-u", "-m", "toss_trading_runtime.periodic_gpt_loop",
    "--symbols", $Symbols,
    "--until-kst", $UntilKst,
    "--interval-min", "30",
    "--minute-count", "120",
    "--daily-count", "20",
    "--max-tokens", "2200",
    "--initial-delay-sec", "180"
)

$market = Start-Process -FilePath $Python -ArgumentList $marketArgs -WorkingDirectory $Root -WindowStyle Hidden -RedirectStandardOutput $marketOut -RedirectStandardError $marketErr -PassThru
$tick = Start-Process -FilePath $Python -ArgumentList $tickArgs -WorkingDirectory $Root -WindowStyle Hidden -RedirectStandardOutput $tickOut -RedirectStandardError $tickErr -PassThru
$gpt = Start-Process -FilePath $Python -ArgumentList $gptArgs -WorkingDirectory $Root -WindowStyle Hidden -RedirectStandardOutput $gptOut -RedirectStandardError $gptErr -PassThru

$ui = $null
if (-not $SkipUi) {
    $uiExe = if (Test-Path $Pythonw) { $Pythonw } else { $Python }
    $ui = Start-Process -FilePath $uiExe -ArgumentList @("-m", "toss_trading_runtime.dashboard_window", "--symbols", $Symbols, "--refresh-sec", "30") -WorkingDirectory $Root -PassThru
}

@(
    "REGULAR_SESSION_STARTED=$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')",
    "SYMBOLS=$Symbols",
    "MARKET_PID=$($market.Id)",
    "TICK_PID=$($tick.Id)",
    "GPT_PID=$($gpt.Id)",
    "UI_PID=$(if ($ui) { $ui.Id } else { 'skipped' })",
    "TICK_ITERATIONS=$tickIterations",
    "STOP_KST=$($stop.ToString('yyyy-MM-dd HH:mm:ss'))",
    "MARKET_STDOUT=$marketOut",
    "MARKET_STDERR=$marketErr",
    "TICK_STDOUT=$tickOut",
    "TICK_STDERR=$tickErr",
    "GPT_STDOUT=$gptOut",
    "GPT_STDERR=$gptErr"
) | Out-File -FilePath $watcherLog -Append -Encoding utf8
