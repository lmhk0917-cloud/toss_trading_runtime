param(
    [string]$StopKst = "05:08",
    [string]$Symbols = "MU,NVDA,QQQ"
)

$ErrorActionPreference = "Continue"
$RuntimeDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$Root = Split-Path -Parent $RuntimeDir
$Python = "C:\Users\lmhk2\anaconda3\python.exe"
$ReportsDir = Join-Path $RuntimeDir "reports"
$LogDir = Join-Path $ReportsDir "run_logs"
New-Item -ItemType Directory -Force -Path $ReportsDir | Out-Null
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

$target = Resolve-TargetTime $StopKst
"POST_CLOSE_WATCHER_STARTED=$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') TARGET=$($target.ToString('yyyy-MM-dd HH:mm:ss'))" |
    Out-File -FilePath (Join-Path $LogDir "post_close_finalize_latest.log") -Encoding utf8

while ((Get-Date) -lt $target) {
    Start-Sleep -Seconds 30
}

$stamp = Get-Date -Format "yyyyMMdd_HHmmss"
$postLog = Join-Path $LogDir "post_close_finalize_$stamp.log"
"POST_CLOSE_FINALIZE_STARTED=$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')" | Out-File -FilePath $postLog -Encoding utf8

$runtimeProcesses = Get-CimInstance Win32_Process |
    Where-Object {
        $_.CommandLine -like "*toss_trading_runtime*" -and
        $_.ProcessId -ne $PID -and
        $_.ProcessId -ne $null
    }

foreach ($proc in $runtimeProcesses) {
    "STOPPING_PROCESS pid=$($proc.ProcessId) name=$($proc.Name) cmd=$($proc.CommandLine)" | Out-File -FilePath $postLog -Append -Encoding utf8
    Stop-Process -Id $proc.ProcessId -Force -ErrorAction SilentlyContinue
}

& $Python -m toss_trading_runtime.dashboard --symbols $Symbols --html (Join-Path $ReportsDir "dashboard_latest.html") *>> $postLog
& $Python -m toss_trading_runtime.ops_report --html (Join-Path $ReportsDir "ops_latest.html") *>> $postLog
& $Python -m toss_trading_runtime.post_close_review --symbols $Symbols *>> $postLog
"POST_CLOSE_FINALIZE_FINISHED=$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')" | Out-File -FilePath $postLog -Append -Encoding utf8
