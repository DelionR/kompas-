param(
  [string]$Root = "C:\KOMPAS_AI_BRIDGE",
  [string]$ServerName = "kompas_engineering"
)
$ErrorActionPreference = "Stop"
$server = Join-Path $Root "mcp\kompas_mcp_server.py"
if (-not (Test-Path $server)) { throw "MCP server not found: $server" }
$resolver = Join-Path $Root "common\PythonResolver.ps1"
if (-not (Test-Path $resolver)) { throw "Python resolver missing: $resolver" }
. $resolver
$Python311 = Resolve-Python311

$codex = Get-Command codex.exe -ErrorAction SilentlyContinue
if (-not $codex) { $codex = Get-Command codex -ErrorAction SilentlyContinue }
if ($codex) {
  $oldEap = $ErrorActionPreference
  try {
    $ErrorActionPreference = "Continue"
    & $codex.Source mcp remove $ServerName 2>$null | Out-Null
  } finally {
    $ErrorActionPreference = $oldEap
  }

  $codexArgs = @('mcp','add',$ServerName,'--',$Python311.Exe)
  $codexArgs += @($Python311.Prefix)
  $codexArgs += @($server,'--root',$Root)
  & $codex.Source @codexArgs
  if ($LASTEXITCODE -ne 0) { throw "codex mcp add failed with exit code $LASTEXITCODE" }
  & $codex.Source mcp list
  Write-Host "Registered MCP server '$ServerName'. Restart/reload Codex if it was already open."
} else {
  Write-Warning "Codex CLI command was not found. The bridge is installed, but MCP registration was not automated."
  $prefixText = ($Python311.Prefix -join ' ')
  Write-Host "Add this STDIO server in Codex MCP settings:"
  Write-Host ("  command: {0}" -f $Python311.Exe)
  Write-Host ("  args: {0} `"{1}`" --root `"{2}`"" -f $prefixText,$server,$Root)
}
