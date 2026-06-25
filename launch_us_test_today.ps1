param(
    [string]$Symbols = "MU,NVDA,AMD,AVGO,MRVL,ARM,TSM,ORCL,CRWV,PLTR,QQQ,SMH",
    [string]$RegularStartKst = "22:31",
    [string]$RegularUntilKst = "05:00",
    [string]$PostCloseKst = "05:08",
    [int]$IntervalSec = 60,
    [int]$GptIntervalMin = 30
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

$regularStart = Resolve-TargetTime $RegularStartKst
$now = Get-Date
$premarketSeconds = [Math]::Max(60, [int]($regularStart - $now).TotalSeconds)
$premarketTickIterations = [Math]::Max(1, [int][Math]::Ceiling($premarketSeconds / $IntervalSec))
$stamp = Get-Date -Format "yyyyMMdd_HHmmss"

$launchLog = Join-Path $LogDir "us_test_launcher_latest.log"
@(
    "US_TEST_LAUNCHER_STARTED=$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')",
    "SYMBOLS=$Symbols",
    "REGULAR_START_KST=$($regularStart.ToString('yyyy-MM-dd HH:mm:ss'))",
    "REGULAR_UNTIL_KST=$RegularUntilKst",
    "POST_CLOSE_KST=$PostCloseKst",
    "PREMARKET_TICK_ITERATIONS=$premarketTickIterations"
) | Out-File -FilePath $launchLog -Encoding utf8

$premarketOut = Join-Path $LogDir "premarket_session_$stamp.out.log"
$premarketErr = Join-Path $LogDir "premarket_session_$stamp.err.log"
$premarketTickOut = Join-Path $LogDir "premarket_tick_collector_$stamp.out.log"
$premarketTickErr = Join-Path $LogDir "premarket_tick_collector_$stamp.err.log"
$premarketGptOut = Join-Path $LogDir "premarket_periodic_gpt_$stamp.out.log"
$premarketGptErr = Join-Path $LogDir "premarket_periodic_gpt_$stamp.err.log"
$regularWatcherOut = Join-Path $LogDir "regular_watcher_$stamp.out.log"
$regularWatcherErr = Join-Path $LogDir "regular_watcher_$stamp.err.log"
$postCloseOut = Join-Path $LogDir "post_close_watcher_$stamp.out.log"
$postCloseErr = Join-Path $LogDir "post_close_watcher_$stamp.err.log"

$premarket = Start-Process -FilePath $Python -ArgumentList @(
    "-u", "-m", "toss_trading_runtime.market_session_run",
    "--symbols", $Symbols,
    "--until-kst", $RegularStartKst,
    "--interval-sec", "$IntervalSec",
    "--minute-count", "120",
    "--daily-count", "20",
    "--max-tokens", "2200"
) -WorkingDirectory $Root -WindowStyle Hidden -RedirectStandardOutput $premarketOut -RedirectStandardError $premarketErr -PassThru

$premarketTick = Start-Process -FilePath $Python -ArgumentList @(
    "-u", "-m", "toss_trading_runtime.tick_collector",
    "--symbols", $Symbols,
    "--iterations", "$premarketTickIterations",
    "--interval-sec", "$IntervalSec",
    "--trade-count", "50"
) -WorkingDirectory $Root -WindowStyle Hidden -RedirectStandardOutput $premarketTickOut -RedirectStandardError $premarketTickErr -PassThru

$premarketGpt = Start-Process -FilePath $Python -ArgumentList @(
    "-u", "-m", "toss_trading_runtime.periodic_gpt_loop",
    "--symbols", $Symbols,
    "--until-kst", $RegularStartKst,
    "--interval-min", "$GptIntervalMin",
    "--minute-count", "120",
    "--daily-count", "20",
    "--max-tokens", "2200",
    "--initial-delay-sec", "180"
) -WorkingDirectory $Root -WindowStyle Hidden -RedirectStandardOutput $premarketGptOut -RedirectStandardError $premarketGptErr -PassThru

$uiExe = if (Test-Path $Pythonw) { $Pythonw } else { $Python }
$ui = Start-Process -FilePath $uiExe -ArgumentList @(
    "-m", "toss_trading_runtime.dashboard_window",
    "--symbols", $Symbols,
    "--refresh-sec", "30"
) -WorkingDirectory $Root -PassThru

$waitCsv = "$($premarket.Id),$($premarketTick.Id),$($premarketGpt.Id)"
$regularWatcher = Start-Process -FilePath "powershell.exe" -ArgumentList @(
    "-NoProfile",
    "-ExecutionPolicy", "Bypass",
    "-File", "`"$RuntimeDir\continue_regular_session.ps1`"",
    "-Symbols", $Symbols,
    "-StartKst", $RegularStartKst,
    "-UntilKst", $RegularUntilKst,
    "-WaitForPidCsv", $waitCsv,
    "-SkipUi"
) -WorkingDirectory $Root -WindowStyle Hidden -RedirectStandardOutput $regularWatcherOut -RedirectStandardError $regularWatcherErr -PassThru

$postClose = Start-Process -FilePath "powershell.exe" -ArgumentList @(
    "-NoProfile",
    "-ExecutionPolicy", "Bypass",
    "-File", "`"$RuntimeDir\post_close_finalize.ps1`"",
    "-StopKst", $PostCloseKst,
    "-Symbols", $Symbols
) -WorkingDirectory $Root -WindowStyle Hidden -RedirectStandardOutput $postCloseOut -RedirectStandardError $postCloseErr -PassThru

@(
    "PREMARKET_PID=$($premarket.Id)",
    "PREMARKET_TICK_PID=$($premarketTick.Id)",
    "PREMARKET_GPT_PID=$($premarketGpt.Id)",
    "UI_PID=$($ui.Id)",
    "REGULAR_WATCHER_PID=$($regularWatcher.Id)",
    "POST_CLOSE_WATCHER_PID=$($postClose.Id)",
    "PREMARKET_STDOUT=$premarketOut",
    "PREMARKET_STDERR=$premarketErr",
    "PREMARKET_TICK_STDOUT=$premarketTickOut",
    "PREMARKET_TICK_STDERR=$premarketTickErr",
    "PREMARKET_GPT_STDOUT=$premarketGptOut",
    "PREMARKET_GPT_STDERR=$premarketGptErr",
    "REGULAR_WATCHER_STDOUT=$regularWatcherOut",
    "REGULAR_WATCHER_STDERR=$regularWatcherErr",
    "POST_CLOSE_STDOUT=$postCloseOut",
    "POST_CLOSE_STDERR=$postCloseErr"
) | Out-File -FilePath $launchLog -Append -Encoding utf8

Get-Content -Path $launchLog
