param(
  [string]$Root = "C:\KOMPAS_AI_BRIDGE",
  [string]$TestModel = ""
)
$ErrorActionPreference = "Stop"
$resolver = Join-Path $Root "common\PythonResolver.ps1"
if (-not (Test-Path $resolver)) { throw "Python resolver missing: $resolver" }
. $resolver
$Python311 = Resolve-Python311
$workerClient = Join-Path $Root "bootstrap\WorkerClient.ps1"
$bootstrapClient = Join-Path $Root "bootstrap\BootstrapClient.ps1"
if (-not (Test-Path $workerClient)) { throw "WorkerClient missing: $workerClient" }

$copyOpened = $false
$report = [ordered]@{
  test = "KOMPAS_AGENT_SELF_TEST"
  started = [DateTimeOffset]::Now.ToString("o")
  root = $Root
  source = $null
  copy = $null
  stages = @()
  pass = $false
}

function Stage([string]$Name, [scriptblock]$Code) {
  try {
    $value = & $Code
    $script:report.stages += [ordered]@{ stage=$Name; ok=$true; result=$value }
    return $value
  } catch {
    $script:report.stages += [ordered]@{ stage=$Name; ok=$false; reason=$_.Exception.Message }
    throw "FAIL $Name $($_.Exception.Message)"
  }
}

function Bridge([string]$Action, $Payload=@{}, [int]$Timeout=60) {
  # Do not pass structured JSON through powershell.exe command-line arguments.
  # Windows PowerShell 5.1 can strip quotes/backslashes from JSON containing
  # Windows paths, spaces and Cyrillic. Use a UTF-8 temp payload file instead.
  $payloadDir = Join-Path $Root "runtime\client_payloads"
  New-Item -ItemType Directory -Force -Path $payloadDir | Out-Null

  $payloadFile = Join-Path $payloadDir (([Guid]::NewGuid().ToString("N")) + ".json")
  $json = $Payload | ConvertTo-Json -Compress -Depth 20
  [System.IO.File]::WriteAllText(
    $payloadFile,
    $json,
    (New-Object System.Text.UTF8Encoding($false))
  )

  $raw = ""
  $code = 999
  try {
    $raw = & powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass `
      -File $workerClient `
      -Action $Action `
      -PayloadFile $payloadFile `
      -Root $Root `
      -TimeoutSec $Timeout 2>&1 | Out-String
    $code = $LASTEXITCODE
  }
  finally {
    Remove-Item -LiteralPath $payloadFile -Force -ErrorAction SilentlyContinue
  }

  $obj = $null
  try {
    $obj = $raw.Trim() | ConvertFrom-Json
  }
  catch {
    throw "invalid bridge response for $Action`: $raw"
  }

  if ($code -ne 0 -or -not $obj.ok) {
    if ($obj.error) {
      throw ($obj.error | ConvertTo-Json -Compress -Depth 10)
    }
    throw "bridge action failed: $Action exit=$code raw=$raw"
  }

  return $obj.result
}

try {
  Stage "bootstrap_alive" {
    if (-not (Test-Path $bootstrapClient)) { throw "BootstrapClient missing" }
    $raw = & powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File $bootstrapClient -Action status -Root $Root -TimeoutSec 10 2>&1 | Out-String
    if ($LASTEXITCODE -ne 0) { throw $raw.Trim() }
    $o = $raw.Trim() | ConvertFrom-Json
    if (-not $o.ok) { throw "bootstrap status not ok" }
    $o
  } | Out-Null

  $workerInfo = Stage "worker_alive" { Bridge "ping" }
  $status = Stage "com_connect_and_version" { Bridge "kompas.status" }
  if (-not $status.com_alive) { throw "FAIL com_connect_and_version COM not alive" }

  $active = Stage "active_document" { Bridge "document.active" }
  if (-not $active.present) { throw "FAIL active_document KOMPAS has no active document" }
  if (-not $TestModel) {
    if ($active.path_name) { $TestModel = [string]$active.path_name }
    elseif ($active.file_name) { $TestModel = [string]$active.file_name }
  }
  if (-not $TestModel) {
    throw "FAIL active_document document is open but API7 PathName is empty; use -TestModel only as a diagnostic override"
  }
  if (-not (Test-Path $TestModel)) {
    throw "FAIL active_document API7 returned path but Windows cannot find it: $TestModel"
  }
  $report.source = $TestModel

  $ext = [System.IO.Path]::GetExtension($TestModel)
  $name = "SELF_TEST_" + (Get-Date -Format "yyyyMMdd_HHmmss") + $ext
  $opened = Stage "open_copy" { Bridge "model.open_copy" @{source=$TestModel; name=$name} 120 }
  $report.copy = [string]$opened.copy
  $copyOpened = $true

  $tree = Stage "tree_read" { Bridge "model.tree" @{max_depth=4} 90 }
  Stage "bbox_read" { Bridge "model.bbox" } | Out-Null

  $front = Stage "front_view" { Bridge "view.set" @{view="front"} | Out-Null; Bridge "view.fit" | Out-Null; Bridge "viewport.capture" @{viewport_only=$true;label="selftest_front"} 90 }
  $iso = Stage "iso_view" { Bridge "view.set" @{view="iso"} | Out-Null; Bridge "view.fit" | Out-Null; Bridge "viewport.capture" @{viewport_only=$true;label="selftest_iso"} 90 }
  $rot = Stage "rotate" { Bridge "view.rotate" @{horizontal_steps=2;vertical_steps=1} | Out-Null; Bridge "viewport.capture" @{viewport_only=$true;label="selftest_rotated"} 90 }

  $imageSanity = Join-Path $Root "tests\image_sanity.py"
  Stage "screenshot_sanity" {
    $checks = @()
    foreach ($path in @($front.path,$iso.path,$rot.path)) {
      $pyArgs = @(); $pyArgs += @($Python311.Prefix); $pyArgs += @($imageSanity,'check',$path)
      $raw = & $Python311.Exe @pyArgs 2>&1 | Out-String
      if ($LASTEXITCODE -ne 0) { throw "bad viewport capture $path`: $($raw.Trim())" }
      $checks += ($raw.Trim() | ConvertFrom-Json)
    }
    $checks
  } | Out-Null
  Stage "screenshot_changes" {
    $pyArgs = @(); $pyArgs += @($Python311.Prefix); $pyArgs += @($imageSanity,'compare',$front.path,$iso.path)
    $a = & $Python311.Exe @pyArgs 2>&1 | Out-String
    if ($LASTEXITCODE -ne 0) { throw "front->iso did not change meaningfully: $($a.Trim())" }
    $pyArgs = @(); $pyArgs += @($Python311.Prefix); $pyArgs += @($imageSanity,'compare',$iso.path,$rot.path)
    $b = & $Python311.Exe @pyArgs 2>&1 | Out-String
    if ($LASTEXITCODE -ne 0) { throw "iso->rotated did not change meaningfully; rotation fallback may not have reached KOMPAS: $($b.Trim())" }
    @{front_to_iso=($a.Trim() | ConvertFrom-Json); iso_to_rotated=($b.Trim() | ConvertFrom-Json)}
  } | Out-Null

  $topChildren = $tree.tree.children
  if ($topChildren -and $topChildren.Count -gt 0) {
    $selector = [int]$topChildren[0].index
    Stage "hide_show_component" {
      $hidden = Bridge "view.visibility" @{operation="hide";selector=$selector}
      if (-not $hidden.hidden) { throw "hide did not report hidden=true" }
      $shown = Bridge "view.visibility" @{operation="show";selector=$selector}
      if ($shown.hidden) { throw "show did not report hidden=false" }
      $restored = Bridge "view.visibility" @{operation="restore"}
      @{selector=$selector; hide=$hidden; show=$shown; restore=$restored}
    } | Out-Null
  } else {
    throw "FAIL hide_show_component no top-level component available"
  }

  Stage "save_copy" { Bridge "document.save" } | Out-Null
  Stage "save_reopen_copy" { Bridge "document.reopen" @{save=$true} 120 } | Out-Null
  $refs = Stage "references_read" { Bridge "qa.references" @{max_depth=8} 120 }
  if ($refs.missing_count -gt 0) { throw "FAIL references_read missing references: $($refs.missing_count)" }
  Stage "final_close" { Bridge "document.close" } | Out-Null
  $copyOpened = $false

  $report.pass = $true
  $report.finished = [DateTimeOffset]::Now.ToString("o")
  $outDir = Join-Path $Root "work\self_test"
  New-Item -ItemType Directory -Force -Path $outDir | Out-Null
  $outFile = Join-Path $outDir "KOMPAS_AGENT_SELF_TEST_LAST.json"
  $report | ConvertTo-Json -Depth 30 | Set-Content -Encoding UTF8 $outFile
  $ready = [ordered]@{
    ready = $true
    bridge_version = [string]$workerInfo.bridge_version
    tested_at = $report.finished
    kompas_version = $status.version
    python = ("Python {0} via {1} {2}" -f $Python311.Version, $Python311.Exe, ($Python311.Prefix -join ' ')).Trim()
    passed_tests = @($report.stages | Where-Object {$_.ok} | ForEach-Object {$_.stage})
    available_capabilities = (Bridge "capabilities").capabilities
    known_limitations = @(
      "Arbitrary view rotation uses bounded Ctrl+Arrow UI fallback because prior API5 probe exposed projections/fit but no arbitrary rotate method.",
      "Viewport capture normally uses the API7 active DocumentFrame HWND and needs no hard-coded crop; legacy main-window crop is fallback-only.",
      "Intersection QA, section view and generalized geometry editing are not promoted to stable V1 tools; use bounded task-specific COM jobs when needed."
    )
    self_test_report = $outFile
    screenshots = @($front.path,$iso.path,$rot.path)
  }
  $readyFile = Join-Path $Root "BRIDGE_READY.json"
  $ready | ConvertTo-Json -Depth 30 | Set-Content -Encoding UTF8 $readyFile
  $humanReady = Join-Path (Split-Path -Parent $Root) "KOMPAS_BRIDGE_READY_FOR_MODELING.txt"
  "KOMPAS CODEX ENGINEERING AGENT V1 READY`r`nVersion: $($workerInfo.bridge_version)`r`nTested: $($report.finished)`r`nReport: $outFile" | Set-Content -Encoding UTF8 $humanReady
  Write-Host "PASS KOMPAS_AGENT_SELF_TEST"
  Write-Host $outFile
  Write-Host $readyFile
  exit 0
}
catch {
  if ($copyOpened) {
    try { Bridge "view.visibility" @{operation="restore"} | Out-Null } catch {}
    try { Bridge "document.close" | Out-Null } catch {}
    $copyOpened = $false
  }
  $report.pass = $false
  $report.finished = [DateTimeOffset]::Now.ToString("o")
  $report.failure = $_.Exception.Message
  $outDir = Join-Path $Root "work\self_test"
  New-Item -ItemType Directory -Force -Path $outDir | Out-Null
  $outFile = Join-Path $outDir "KOMPAS_AGENT_SELF_TEST_LAST.json"
  $report | ConvertTo-Json -Depth 30 | Set-Content -Encoding UTF8 $outFile
  Remove-Item -Force (Join-Path $Root "BRIDGE_READY.json") -ErrorAction SilentlyContinue
  Remove-Item -Force (Join-Path (Split-Path -Parent $Root) "KOMPAS_BRIDGE_READY_FOR_MODELING.txt") -ErrorAction SilentlyContinue
  Write-Error $_.Exception.Message
  Write-Host $outFile
  exit 2
}
