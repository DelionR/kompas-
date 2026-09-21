param(
    [string]$Root = "C:\KOMPAS_AI_BRIDGE"
)

$ErrorActionPreference = "Stop"
$Host.UI.RawUI.WindowTitle = "KOMPAS CODEX BOOTSTRAP WATCHDOG"

$bootstrapDir = Join-Path $Root "bootstrap"
$cmdDir       = Join-Path $bootstrapDir "commands"
$rspDir       = Join-Path $bootstrapDir "responses"
$doneDir      = Join-Path $bootstrapDir "processed"
$logDir       = Join-Path $bootstrapDir "logs"
$workerFile   = Join-Path $Root "src\worker.py"
$stateFile    = Join-Path $bootstrapDir "bootstrap_state.json"
$heartbeatFile= Join-Path $Root "runtime\worker_heartbeat.json"
$workerState  = Join-Path $Root "runtime\worker_state.json"
$watchdogLog  = Join-Path $logDir "runtime_watchdog.log"

New-Item -ItemType Directory -Force -Path `
    $cmdDir,$rspDir,$doneDir,$logDir,`
    (Join-Path $Root "runtime\commands"),`
    (Join-Path $Root "runtime\responses"),`
    (Join-Path $Root "runtime\processed"),`
    (Join-Path $Root "runtime\logs"),`
    (Join-Path $Root "work") | Out-Null

$createdNew = $false
$mutex = New-Object System.Threading.Mutex(
    $true,
    "Local\KOMPAS_CODEX_SELF_BUILDING_BOOTSTRAP_V1",
    [ref]$createdNew
)
if (-not $createdNew) {
    Write-Host "Another KOMPAS CODEX bootstrap is already running in this Windows session."
    exit 3
}

$script:WorkerDesired = $true
$script:LastRestartAttempt = [DateTime]::MinValue
$script:RestartCount = 0
$script:LastWorkerError = $null
$script:LastStdout = $null
$script:LastStderr = $null

function NowIso {
    [DateTimeOffset]::Now.ToString("o")
}

function Log([string]$Text) {
    $line = "$(NowIso) $Text"
    try { Add-Content -LiteralPath $watchdogLog -Value $line -Encoding UTF8 } catch {}
}

function Write-JsonAtomic([string]$Path, $Object) {
    $tmp = "$Path.tmp"
    $json = $Object | ConvertTo-Json -Depth 16
    [IO.File]::WriteAllText($tmp,$json,(New-Object Text.UTF8Encoding($false)))
    Move-Item -Force $tmp $Path
}

function Find-Python {
    $resolver = Join-Path $Root "common\PythonResolver.ps1"
    if (-not (Test-Path -LiteralPath $resolver)) { throw "Python resolver missing: $resolver" }
    . $resolver
    Resolve-Python311
}

function Get-WorkerRows {
    @(Get-CimInstance Win32_Process -ErrorAction SilentlyContinue | Where-Object {
        $cmd = [string]$_.CommandLine
        $cmd -and
        $cmd.IndexOf($Root,[StringComparison]::OrdinalIgnoreCase) -ge 0 -and
        $cmd.IndexOf("worker.py",[StringComparison]::OrdinalIgnoreCase) -ge 0
    })
}

function Get-Heartbeat {
    if (-not (Test-Path -LiteralPath $heartbeatFile)) { return $null }
    try {
        $obj = Get-Content -Raw -Encoding UTF8 $heartbeatFile | ConvertFrom-Json
        $item = Get-Item -LiteralPath $heartbeatFile
        $age = ((Get-Date) - $item.LastWriteTime).TotalSeconds
        return [pscustomobject]@{
            pid = [int]$obj.pid
            version = [string]$obj.version
            time = [string]$obj.time
            age_sec = [double]$age
        }
    } catch {
        return $null
    }
}

function Worker-IsRunning {
    $hb = Get-Heartbeat
    if ($null -eq $hb) { return $false }
    if ($hb.age_sec -gt 4.0) { return $false }
    try {
        $null = Get-Process -Id $hb.pid -ErrorAction Stop
        return $true
    } catch {
        return $false
    }
}

function Stop-Worker {
    $rows = Get-WorkerRows
    foreach ($row in $rows) {
        try {
            Log "Stopping worker runtime PID=$($row.ProcessId)"
            Stop-Process -Id ([int]$row.ProcessId) -Force -ErrorAction SilentlyContinue
        } catch {}
    }
    Start-Sleep -Milliseconds 300
    Remove-Item -Force $heartbeatFile,$workerState -ErrorAction SilentlyContinue
}

function Start-Worker {
    if (Worker-IsRunning) {
        $hb = Get-Heartbeat
        return @{
            ok = $true
            already_running = $true
            worker_pid = $hb.pid
            worker_version = $hb.version
            stdout = $script:LastStdout
            stderr = $script:LastStderr
        }
    }

    # Kill stale/orphan worker processes before starting a single fresh one.
    Stop-Worker

    if (-not (Test-Path -LiteralPath $workerFile)) {
        throw "Worker source not found: $workerFile"
    }

    $python = Find-Python
    $stamp = Get-Date -Format "yyyyMMdd_HHmmss_fff"
    $stdout = Join-Path $logDir "worker_${stamp}_stdout.log"
    $stderr = Join-Path $logDir "worker_${stamp}_stderr.log"

    $workerArgs = @()
    $workerArgs += @($python.Prefix)
    $workerArgs += @("-u",$workerFile,"--root",$Root)

    $startedAt = Get-Date
    $launcher = Start-Process -FilePath $python.Exe `
        -ArgumentList $workerArgs `
        -WorkingDirectory $Root `
        -PassThru `
        -WindowStyle Hidden `
        -RedirectStandardOutput $stdout `
        -RedirectStandardError $stderr

    $script:LastStdout = $stdout
    $script:LastStderr = $stderr

    $deadline = [DateTime]::UtcNow.AddSeconds(8)
    while ([DateTime]::UtcNow -lt $deadline) {
        $hb = Get-Heartbeat
        if ($null -ne $hb -and $hb.age_sec -lt 2.0) {
            try {
                $null = Get-Process -Id $hb.pid -ErrorAction Stop
                $script:RestartCount++
                $script:LastWorkerError = $null
                Log "Worker started PID=$($hb.pid) version=$($hb.version) launcherPID=$($launcher.Id)"
                return @{
                    ok = $true
                    already_running = $false
                    worker_pid = $hb.pid
                    worker_version = $hb.version
                    launcher_pid = $launcher.Id
                    stdout = $stdout
                    stderr = $stderr
                    python = $python.Exe
                    worker_source = $workerFile
                }
            } catch {}
        }
        Start-Sleep -Milliseconds 150
    }

    $tail = ""
    if (Test-Path -LiteralPath $stderr) {
        try { $tail = (Get-Content -Tail 30 -LiteralPath $stderr) -join "`n" } catch {}
    }
    throw "Worker failed to produce a fresh heartbeat within 8 seconds. stderr=$tail"
}

function Watchdog-Tick {
    if (-not $script:WorkerDesired) { return }
    if (Worker-IsRunning) { return }

    $now = Get-Date
    if (($now - $script:LastRestartAttempt).TotalSeconds -lt 2.0) { return }

    $script:LastRestartAttempt = $now
    try {
        Log "Watchdog detected missing/stale worker; restarting."
        $null = Start-Worker
    } catch {
        $script:LastWorkerError = $_.Exception.Message
        Log "Watchdog restart failed: $($script:LastWorkerError)"
    }
}

function Get-BootstrapStatus {
    $hb = Get-Heartbeat
    @{
        ok = $true
        action = "status"
        bootstrap_pid = $PID
        bootstrap_session_id = (Get-Process -Id $PID).SessionId
        root = $Root
        worker_desired = $script:WorkerDesired
        worker_running = (Worker-IsRunning)
        worker_pid = if ($hb) { $hb.pid } else { $null }
        worker_version = if ($hb) { $hb.version } else { $null }
        worker_heartbeat_age_sec = if ($hb) { [math]::Round($hb.age_sec,2) } else { $null }
        restart_count = $script:RestartCount
        last_worker_error = $script:LastWorkerError
        worker_stdout = $script:LastStdout
        worker_stderr = $script:LastStderr
        time = NowIso
    }
}

function Process-Command([IO.FileInfo]$File) {
    $id = $File.BaseName
    try {
        $req = Get-Content -Raw -Encoding UTF8 $File.FullName | ConvertFrom-Json
        if ($req.id) { $id = [string]$req.id }
        $action = [string]$req.action

        switch ($action) {
            "status" {
                $resp = Get-BootstrapStatus
            }
            "start_worker" {
                $script:WorkerDesired = $true
                $r = Start-Worker
                $resp = @{ok=[bool]$r.ok;action=$action;result=$r;time=NowIso}
            }
            "restart_worker" {
                $script:WorkerDesired = $true
                Stop-Worker
                $r = Start-Worker
                $resp = @{ok=[bool]$r.ok;action=$action;result=$r;time=NowIso}
            }
            "stop_worker" {
                $script:WorkerDesired = $false
                Stop-Worker
                $resp = @{ok=$true;action=$action;worker_running=$false;time=NowIso}
            }
            default {
                $resp = @{
                    ok=$false
                    action=$action
                    error="unsupported_bootstrap_action"
                    allowed=@("status","start_worker","restart_worker","stop_worker")
                    time=NowIso
                }
            }
        }
    } catch {
        $resp = @{
            ok=$false
            error=$_.Exception.GetType().FullName
            message=$_.Exception.Message
            time=NowIso
        }
    }

    $resp.id = $id
    Write-JsonAtomic (Join-Path $rspDir "$id.json") $resp
    try {
        Move-Item -Force $File.FullName (Join-Path $doneDir $File.Name)
    } catch {
        Remove-Item -Force $File.FullName -ErrorAction SilentlyContinue
    }
}

try {
    Log "Bootstrap watchdog starting PID=$PID session=$((Get-Process -Id $PID).SessionId)"

    try {
        $null = Start-Worker
    } catch {
        $script:LastWorkerError = $_.Exception.Message
        Log "Initial worker start failed: $($script:LastWorkerError)"
    }

    while ($true) {
        Watchdog-Tick
        Write-JsonAtomic $stateFile (Get-BootstrapStatus)

        $files = Get-ChildItem -Path $cmdDir -Filter "*.json" -File -ErrorAction SilentlyContinue |
            Sort-Object LastWriteTime

        foreach ($file in $files) {
            Process-Command $file
        }

        Start-Sleep -Milliseconds 200
    }
}
finally {
    $script:WorkerDesired = $false
    Stop-Worker
    try { $mutex.ReleaseMutex() } catch {}
    try { $mutex.Dispose() } catch {}
    Log "Bootstrap watchdog stopped."
}