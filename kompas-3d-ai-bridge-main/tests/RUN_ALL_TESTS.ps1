param(
  [string]$Root = "C:\KOMPAS_AI_BRIDGE",
  [string]$TestModel = ""
)
$ErrorActionPreference = "Stop"
& powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File (Join-Path $Root "tests\RUN_STATIC_TESTS.ps1") -Root $Root
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
$testArgs = @("-NoLogo","-NoProfile","-ExecutionPolicy","Bypass","-File",(Join-Path $Root "tests\KOMPAS_AGENT_SELF_TEST.ps1"),"-Root",$Root)
if ($TestModel) { $testArgs += @("-TestModel",$TestModel) }
& powershell.exe @testArgs
exit $LASTEXITCODE
