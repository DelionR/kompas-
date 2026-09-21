param(
    [Parameter(Mandatory=$true)]
    [string]$Action,
    [string]$PayloadJson = "{}",
    [string]$PayloadFile = "",
    [string]$Root = "C:\KOMPAS_AI_BRIDGE",
    [int]$TimeoutSec = 60
)

$ErrorActionPreference = "Stop"
$cmdDir = Join-Path $Root "runtime\commands"
$rspDir = Join-Path $Root "runtime\responses"
New-Item -ItemType Directory -Force -Path $cmdDir,$rspDir | Out-Null

try {
    if ($PayloadFile) {
        if (-not (Test-Path -LiteralPath $PayloadFile)) {
            throw "PayloadFile does not exist: $PayloadFile"
        }
        $payloadText = [System.IO.File]::ReadAllText($PayloadFile, [System.Text.Encoding]::UTF8)
    } else {
        $payloadText = $PayloadJson
    }

    if ([string]::IsNullOrWhiteSpace($payloadText)) {
        $payloadText = "{}"
    }
    $payload = $payloadText | ConvertFrom-Json
} catch {
    if ($PayloadFile) {
        throw "PayloadFile is not valid JSON: $PayloadFile :: $($_.Exception.Message)"
    }
    throw "PayloadJson is not valid JSON: $PayloadJson"
}

$id = [Guid]::NewGuid().ToString("N")
$req = @{
    id = $id
    action = $Action
    payload = $payload
    sent_at = [DateTimeOffset]::Now.ToString("o")
}
$json = $req | ConvertTo-Json -Depth 20
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
Write-Error "Worker command timed out after $TimeoutSec s: $Action"
exit 4