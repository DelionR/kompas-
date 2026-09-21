param([string]$Root = (Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)))
$ErrorActionPreference = "Stop"
$resolver = Join-Path $Root "common\PythonResolver.ps1"
if (-not (Test-Path $resolver)) { throw "Python resolver missing: $resolver" }
. $resolver
$Python311 = Resolve-Python311
Write-Host ("Python 3.11: {0} {1}" -f $Python311.Exe, ($Python311.Prefix -join ' '))

Write-Host "[1/10] Python compileall"
$code = Invoke-Python311 -Python $Python311 -Arguments @('-m','compileall','-q',(Join-Path $Root 'src'),(Join-Path $Root 'mcp'),(Join-Path $Root 'tests'),(Join-Path $Root 'jobs'))
if ($code -ne 0) { throw "compileall failed" }
Write-Host "[2/10] MCP stdio contract"
$code = Invoke-Python311 -Python $Python311 -Arguments @((Join-Path $Root 'tests\test_mcp_stdio.py'))
if ($code -ne 0) { throw "MCP stdio test failed" }
Write-Host "[3/10] MCP file-routing + image attachment contract"
$code = Invoke-Python311 -Python $Python311 -Arguments @((Join-Path $Root 'tests\test_mcp_bridge_routing.py'))
if ($code -ne 0) { throw "MCP routing test failed" }
Write-Host "[4/10] Wave 1: catalog, config policy, error surface, compaction, job allow-list"
Push-Location $Root
try {
  $code = Invoke-Python311 -Python $Python311 -Arguments @('-m','unittest','discover','-s','tests','-p','test_wave1.py')
} finally { Pop-Location }
if ($code -ne 0) { throw "Wave 1 tests failed" }
Write-Host "[5/10] Wave 2: assembly mates, artifact verification"
Push-Location $Root
try {
  $code = Invoke-Python311 -Python $Python311 -Arguments @('-m','unittest','discover','-s','tests','-p','test_wave2.py')
} finally { Pop-Location }
if ($code -ne 0) { throw "Wave 2 tests failed" }
Write-Host "[6/10] Wave 3: modal dialog watchdog, version support matrix"
Push-Location $Root
try {
  $code = Invoke-Python311 -Python $Python311 -Arguments @('-m','unittest','discover','-s','tests','-p','test_wave3.py')
} finally { Pop-Location }
if ($code -ne 0) { throw "Wave 3 tests failed" }
Write-Host "[7/10] Wave 4: verified export, checkpoints"
Push-Location $Root
try {
  $code = Invoke-Python311 -Python $Python311 -Arguments @('-m','unittest','discover','-s','tests','-p','test_wave4.py')
} finally { Pop-Location }
if ($code -ne 0) { throw "Wave 4 tests failed" }
Write-Host "[8/10] Wave 5: drawings, stamp, specification"
Push-Location $Root
try {
  $code = Invoke-Python311 -Python $Python311 -Arguments @('-m','unittest','discover','-s','tests','-p','test_wave5.py')
} finally { Pop-Location }
if ($code -ne 0) { throw "Wave 5 tests failed" }
Write-Host "[9/10] Wave 6: dry run for mutating tools"
Push-Location $Root
try {
  $code = Invoke-Python311 -Python $Python311 -Arguments @('-m','unittest','discover','-s','tests','-p','test_wave6.py')
} finally { Pop-Location }
if ($code -ne 0) { throw "Wave 6 tests failed" }
Write-Host "[10/10] Required files"
$required = @("src\worker.py","src\settings.py","src\errors.py","src\compact.py","src\mate_tools.py","src\artifact_check.py","src\dialogs.py","src\version_matrix.py","src\checkpoint.py","src\export_tools.py","src\drawing_tools.py","src\spec_tools.py","src\dryrun.py","src\engineering_capabilities.py","src\model_tools.py","src\view_tools.py","src\screenshot.py","mcp\kompas_mcp_server.py","mcp\tools_catalog.py","jobs\allowlist.json","tests\test_wave2.py","tests\test_wave3.py","tests\test_wave4.py","tests\KOMPAS_AGENT_SELF_TEST.ps1","tests\image_sanity.py","skills\kompas-engineering\SKILL.md","common\PythonResolver.ps1","common\dependency_check.py")
foreach ($rel in $required) { if (-not (Test-Path (Join-Path $Root $rel))) { throw "Missing $rel" } }
Write-Host "PASS STATIC TESTS"

