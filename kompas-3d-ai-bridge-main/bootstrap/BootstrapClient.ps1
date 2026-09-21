param(
    [Parameter(Mandatory=$true)]
    [ValidateSet("status","start_worker","restart_worker","stop_worker")]
    [string]$Action,
    [string]$Root = "C:\KOMPAS_AI_BRIDGE",
    [int]$TimeoutSec = 20
)

$ErrorActionPreference = "Stop"
$cmdDir = Join-Path $Root "bootstrap\commands"
$rspDir = Join-Path $Root "bootstrap\responses"
New-Item -ItemType Directory -Force -Path $cmdDir,$rspDir | Out-Null

$id = [Guid]::NewGuid().ToString("N")
$req = @{
    id = $id
    action = $Action
    sent_at = [DateTimeOffset]::Now.ToString("o")
}
$json = $req | ConvertTo-Json -Depth 6
$tmp = Join-Path $cmdDir "$id.json.tmp"
$dst = Join-Path $cmdDir "$id.json"
[System.IO.File]::WriteAllText($tmp, $json, (New-Object System.Text.UTF8Encoding($false)))
Move-Item -Force $tmp $dst

$resp = Join-Path $rspDir "$id.json"
$deadline = [DateTime]::UtcNow.AddSeconds($TimeoutSec)
while ([DateTime]::UtcNow -lt $deadline) {
    if (Test-Path $resp) {
        $raw = Get-Content -Raw -Encoding UTF8 $resp
        Write-Output $raw
        try {
            $obj = $raw | ConvertFrom-Json
            if ($obj.ok) { exit 0 } else { exit 2 }
        } catch {
            exit 3
        }
    }
    Start-Sleep -Milliseconds 100
}
Write-Error "Bootstrap command timed out after $TimeoutSec s: $Action"
exit 4
