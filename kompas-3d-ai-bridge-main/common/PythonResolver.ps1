# KOMPAS CODEX ENGINEERING AGENT - Python resolver
#
# IMPORTANT: the original proven bridge used `py.exe -3.11` whenever the
# Windows Python Launcher was present. Keep that proven behaviour first and
# do not reject a working launcher because of an over-strict preflight probe.

function Get-PythonCommandPath {
    param([Parameter(Mandatory=$true)][string]$Name)
    try {
        $cmd = Get-Command $Name -ErrorAction SilentlyContinue
        if (-not $cmd) { return $null }
        if ($cmd.Path) { return [string]$cmd.Path }
        if ($cmd.Source) { return [string]$cmd.Source }
        return [string]$cmd.Name
    } catch {
        return $null
    }
}

function Get-BridgePythonVersion {
    param([Parameter(Mandatory=$true)]$Python)
    # Do not use `python -c` here. Windows PowerShell 5.1 can mangle quotes in
    # inline native-command arguments. `--version` needs no nested quoting.
    $invokeArgs = @()
    $invokeArgs += @($Python.Prefix)
    $invokeArgs += @('--version')
    $oldEap = $ErrorActionPreference
    try {
        $ErrorActionPreference = 'Continue'
        $raw = & $Python.Exe @invokeArgs 2>&1 | Out-String
        $code = [int]$LASTEXITCODE
        if ($code -eq 0) {
            $v = $raw.Trim()
            if ($v -match 'Python\s+([0-9]+\.[0-9]+\.[0-9]+)') { return $Matches[1] }
            if ($v) { return $v }
        }
    } catch {
    } finally {
        $ErrorActionPreference = $oldEap
    }
    return 'unverified'
}

function Resolve-BridgePython {
    # Explicit override for unusual/custom installations.
    if ($env:KOMPAS_PYTHON_EXE) {
        $explicit = [Environment]::ExpandEnvironmentVariables([string]$env:KOMPAS_PYTHON_EXE)
        if (Test-Path $explicit) {
            $spec = [pscustomobject]@{ Exe=$explicit; Prefix=@(); Source='KOMPAS_PYTHON_EXE'; Version='unverified' }
            $spec.Version = Get-BridgePythonVersion -Python $spec
            return $spec
        }
    }

    # 1) Preserve the exact behaviour of the original proven bridge.
    $py = Get-PythonCommandPath 'py.exe'
    if (-not $py) { $py = Get-PythonCommandPath 'py' }
    if ($py) {
        $spec = [pscustomobject]@{ Exe=$py; Prefix=@('-3.11'); Source='Windows Python Launcher'; Version='unverified' }
        $spec.Version = Get-BridgePythonVersion -Python $spec
        # Keep the proven launcher path when it can actually resolve 3.11.
        # A stale Windows launcher can remain on PATH after its registered
        # Python installation is removed; in that case continue to the direct
        # python.exe/registry/folder fallbacks below.
        if ($spec.Version -ne 'unverified') { return $spec }
    }

    # 2) Normal python.exe/python/python3 on PATH.
    foreach ($name in @('python.exe','python','python3.exe','python3')) {
        $exe = Get-PythonCommandPath $name
        if ($exe) {
            $spec = [pscustomobject]@{ Exe=$exe; Prefix=@(); Source=('PATH:' + $name); Version='unverified' }
            $spec.Version = Get-BridgePythonVersion -Python $spec
            return $spec
        }
    }

    # 3) Registry fallback. Enumerate all PythonCore versions rather than
    # assuming only one registry layout or exact patch installation.
    $registryRoots = @(
        'HKCU:\Software\Python\PythonCore',
        'HKLM:\Software\Python\PythonCore',
        'HKLM:\Software\WOW6432Node\Python\PythonCore'
    )
    foreach ($root in $registryRoots) {
        if (-not (Test-Path $root)) { continue }
        try {
            $versions = Get-ChildItem $root -ErrorAction SilentlyContinue | Sort-Object PSChildName -Descending
            foreach ($versionKey in $versions) {
                $installKey = Join-Path $versionKey.PSPath 'InstallPath'
                if (-not (Test-Path $installKey)) { continue }
                $item = Get-Item -Path $installKey -ErrorAction SilentlyContinue
                if (-not $item) { continue }
                $base = [string]$item.GetValue('')
                if (-not $base) { continue }
                $exe = Join-Path $base 'python.exe'
                if (Test-Path $exe) {
                    $spec = [pscustomobject]@{ Exe=$exe; Prefix=@(); Source=('registry:' + $versionKey.PSChildName); Version='unverified' }
                    $spec.Version = Get-BridgePythonVersion -Python $spec
                    return $spec
                }
            }
        } catch {}
    }

    # 4) Common install folders, including version-independent scans.
    $roots = @()
    if ($env:LOCALAPPDATA) { $roots += (Join-Path $env:LOCALAPPDATA 'Programs\Python') }
    if ($env:USERPROFILE) { $roots += (Join-Path $env:USERPROFILE 'AppData\Local\Programs\Python') }
    if ($env:ProgramFiles) { $roots += $env:ProgramFiles }
    $pf86 = [Environment]::GetEnvironmentVariable('ProgramFiles(x86)')
    if ($pf86) { $roots += $pf86 }

    foreach ($base in ($roots | Select-Object -Unique)) {
        if (-not (Test-Path $base)) { continue }
        $patterns = @('Python311\python.exe','Python3*\python.exe')
        foreach ($pattern in $patterns) {
            try {
                $matches = Get-ChildItem -Path (Join-Path $base $pattern) -File -ErrorAction SilentlyContinue | Sort-Object FullName -Descending
                foreach ($m in $matches) {
                    $spec = [pscustomobject]@{ Exe=$m.FullName; Prefix=@(); Source='standard-folder'; Version='unverified' }
                    $spec.Version = Get-BridgePythonVersion -Python $spec
                    return $spec
                }
            } catch {}
        }
    }

    throw @"
Python was not found by the bridge.
The original bridge normally uses the Windows Python Launcher as: py.exe -3.11.
No py/python command, registry installation, or standard Python folder was found.
If Python is installed in a custom folder, set KOMPAS_PYTHON_EXE to its full python.exe path and rerun the installer.
"@
}

# Compatibility name retained so all V1 scripts can use the resolver without
# another migration. It no longer performs an over-strict version rejection.
function Resolve-Python311 {
    return Resolve-BridgePython
}

function Invoke-Python311 {
    param(
        [Parameter(Mandatory=$true)]$Python,
        [Parameter(Mandatory=$true)][string[]]$Arguments,
        [switch]$QuietStderr
    )
    $invokeArgs = @()
    $invokeArgs += @($Python.Prefix)
    $invokeArgs += @($Arguments)
    $oldEap = $ErrorActionPreference
    try {
        $ErrorActionPreference = 'Continue'
        if ($QuietStderr) {
            & $Python.Exe @invokeArgs 2>$null | Out-Host
        } else {
            & $Python.Exe @invokeArgs 2>&1 | Out-Host
        }
        return [int]$LASTEXITCODE
    } catch {
        Write-Host ("Python invocation failed: " + $_.Exception.Message)
        return 9009
    } finally {
        $ErrorActionPreference = $oldEap
    }
}
