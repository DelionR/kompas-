param(
    [string]$Root = "C:\KOMPAS_AI_BRIDGE"
)
$ErrorActionPreference = "Stop"

$principal = New-Object Security.Principal.WindowsPrincipal(
    [Security.Principal.WindowsIdentity]::GetCurrent()
)
if ($principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    Write-Error "KOMPAS bridge runtime must be started NON-ADMIN by default. Close this elevated shell and start it from a normal Explorer/PowerShell session so KOMPAS, bootstrap and worker share the same integrity level."
    exit 12
}

$bootstrap = Join-Path $Root "bootstrap\BridgeBootstrap.ps1"
if (-not (Test-Path -LiteralPath $bootstrap)) { throw "BridgeBootstrap missing: $bootstrap" }
& $bootstrap -Root $Root
exit $LASTEXITCODE
