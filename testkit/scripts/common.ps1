# Shared helpers for the DoMe test kit scripts. Windows PowerShell 5.1 compatible; keep this file
# ASCII-only (5.1 reads BOM-less scripts in the system code page).

Set-StrictMode -Version 2.0
$ErrorActionPreference = 'Stop'
try { [Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12 } catch { }

$script:KitDir = Split-Path -Parent $PSScriptRoot
$script:RepoDir = Split-Path -Parent $script:KitDir
$script:StateDir = Join-Path $script:KitDir '.state'
$script:ComposeFile = Join-Path $script:KitDir 'docker-compose.yml'
$script:AgentDir = Join-Path $script:RepoDir 'pc-agent'
$script:OnWindows = ($env:OS -eq 'Windows_NT')
if ($script:OnWindows) {
    $script:AgentScripts = Join-Path $script:AgentDir '.venv\Scripts'
    $script:AgentPython = Join-Path $script:AgentScripts 'python.exe'
    $script:AgentExe = Join-Path $script:AgentScripts 'dome-agent.exe'
} else {
    $script:AgentScripts = Join-Path $script:AgentDir '.venv/bin'
    $script:AgentPython = Join-Path $script:AgentScripts 'python'
    $script:AgentExe = Join-Path $script:AgentScripts 'dome-agent'
}

function Write-Banner([string]$Title) {
    Write-Host ''
    Write-Host ('=' * 72) -ForegroundColor DarkCyan
    Write-Host ('  ' + $Title) -ForegroundColor Cyan
    Write-Host ('=' * 72) -ForegroundColor DarkCyan
}

function Write-Step([string]$Text) { Write-Host ''; Write-Host ('>> ' + $Text) -ForegroundColor Cyan }
function Write-Ok([string]$Text) { Write-Host ('   OK  ' + $Text) -ForegroundColor Green }
function Write-Note([string]$Text) { Write-Host ('   ' + $Text) }
function Write-Warn([string]$Text) { Write-Host ('   !   ' + $Text) -ForegroundColor Yellow }
function Stop-Kit([string]$Text) {
    # A failure the user can act on: shown as one plain sentence (see Invoke-KitMain).
    $e = New-Object System.Exception $Text
    $e.Data['DoMeKit'] = $true
    throw $e
}

function Invoke-KitMain([scriptblock]$Body) {
    # Runs a script body and turns failures into one readable message instead of a stack trace.
    try {
        # Out-Host: show program output live instead of returning it as this function's value.
        & $Body | Out-Host
        return 0
    } catch {
        Write-Host ''
        if ($_.Exception.Data.Contains('DoMeKit')) {
            Write-Host ('PROBLEM: ' + $_.Exception.Message) -ForegroundColor Red
            Write-Host 'See testkit\README.md, section "If something goes wrong".' -ForegroundColor Red
            return 1
        }
        Write-Host ('UNEXPECTED ERROR: ' + $_.Exception.Message) -ForegroundColor Red
        Write-Host ($_.ScriptStackTrace) -ForegroundColor DarkGray
        Write-Host 'Please send this text with your test notes.' -ForegroundColor Red
        return 1
    }
}

function Invoke-Native([string]$What, [scriptblock]$Command) {
    # Runs an external program; a non-zero exit code becomes a KitError naming the step.
    & $Command
    if ($LASTEXITCODE -ne 0) { Stop-Kit ("$What failed (exit code $LASTEXITCODE). The lines above say why.") }
}

function Assert-Docker {
    Write-Step 'Checking Docker Desktop'
    if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
        Stop-Kit 'Docker is not installed. Install Docker Desktop from https://www.docker.com/products/docker-desktop/ , start it, wait until it says "Engine running", then run this again.'
    }
    $previous = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try {
        docker info --format '{{.ServerVersion}}' *> $null
        $ok = ($LASTEXITCODE -eq 0)
    } finally { $ErrorActionPreference = $previous }
    if (-not $ok) {
        Stop-Kit 'Docker Desktop is installed but not running. Start Docker Desktop, wait until it says "Engine running", then run this again.'
    }
    Write-Ok 'Docker is running'
}

function Find-Uv {
    $cmd = Get-Command uv -ErrorAction SilentlyContinue
    if ($cmd) { return $cmd.Source }
    foreach ($candidate in @(
            (Join-Path $env:USERPROFILE '.local\bin\uv.exe'),
            (Join-Path $env:USERPROFILE '.cargo\bin\uv.exe'))) {
        if ($env:USERPROFILE -and (Test-Path $candidate)) { return $candidate }
    }
    return $null
}

function Initialize-Uv {
    Write-Step 'Checking uv (runs the DoMe PC program from source)'
    $uv = Find-Uv
    if (-not $uv) {
        if (-not $script:OnWindows) { Stop-Kit 'uv is not installed (https://docs.astral.sh/uv/).' }
        Write-Note 'The DoMe PC program needs "uv", a free tool from Astral that installs Python for it.'
        Write-Note 'It installs for your Windows user only (no administrator rights) from https://astral.sh/uv'
        $answer = Read-Host '   Install uv now? [Y/n]'
        if ($answer -and $answer.Trim().ToLower().StartsWith('n')) { Stop-Kit 'uv is required. Install it from https://docs.astral.sh/uv/ and run this again.' }
        Invoke-Native 'Installing uv' { powershell.exe -NoProfile -ExecutionPolicy Bypass -Command 'irm https://astral.sh/uv/install.ps1 | iex' }
        $uv = Find-Uv
        if (-not $uv) { Stop-Kit 'uv was installed but cannot be found. Close this window and run the script again.' }
    }
    $script:Uv = $uv
    $env:PATH = (Split-Path -Parent $uv) + [IO.Path]::PathSeparator + $env:PATH
    Write-Ok ('uv found: ' + $uv)
}

function Sync-AgentEnvironment {
    Write-Step 'Preparing the DoMe PC program (first time: downloads Python and libraries, about 1-2 minutes)'
    Push-Location $script:AgentDir
    try {
        Invoke-Native 'Installing the DoMe PC program' { & $script:Uv sync --locked --python 3.12 --quiet }
    } finally { Pop-Location }
    if (-not (Test-Path $script:AgentExe)) { Stop-Kit ('Expected ' + $script:AgentExe + ' after installing; it is missing.') }
    Write-Ok 'DoMe PC program is ready'
}

function Initialize-StateDir {
    if (-not (Test-Path $script:StateDir)) { New-Item -ItemType Directory -Path $script:StateDir | Out-Null }
}

function New-RandomBytes([int]$Count) {
    $bytes = New-Object byte[] $Count
    $rng = [System.Security.Cryptography.RandomNumberGenerator]::Create()
    try { $rng.GetBytes($bytes) } finally { $rng.Dispose() }
    return , $bytes
}

function New-HexSecret([int]$Bytes) {
    return ((New-RandomBytes $Bytes) | ForEach-Object { $_.ToString('x2') }) -join ''
}

function New-Passphrase {
    # 12 characters from a 31-letter alphabet without look-alikes (about 59 bits), as xxxx-xxxx-xxxx.
    $alphabet = 'abcdefghjkmnpqrstuvwxyz23456789'
    $chars = New-Object System.Collections.Generic.List[char]
    while ($chars.Count -lt 12) {
        foreach ($b in (New-RandomBytes 32)) {
            # Rejection sampling: 248 = 31 * 8, so every letter is equally likely.
            if ($b -lt 248 -and $chars.Count -lt 12) { $chars.Add($alphabet[$b % 31]) }
        }
    }
    $s = -join $chars
    return $s.Substring(0, 4) + '-' + $s.Substring(4, 4) + '-' + $s.Substring(8, 4)
}

function Get-PersistentSecret([string]$Name, [int]$Bytes) {
    Initialize-StateDir
    $path = Join-Path $script:StateDir $Name
    if (Test-Path $path) {
        $value = (Get-Content -Raw -Path $path).Trim()
        if ($value.Length -ge 32) { return $value }
    }
    $value = New-HexSecret $Bytes
    Set-Content -Path $path -Value $value -NoNewline -Encoding Ascii
    return $value
}

function Set-ComposePlaceholders {
    # The compose file refuses to run without these (${...:?}). Commands that do not start the api
    # (stop, logs, tunnels first) still need them set to something.
    foreach ($name in @('DOME_TEST_APP_URL', 'DOME_TEST_SIGNIN_URL')) {
        if (-not [Environment]::GetEnvironmentVariable($name)) { [Environment]::SetEnvironmentVariable($name, 'https://pending.invalid') }
    }
    foreach ($name in @('DOME_TEST_CLIENT_SECRET', 'DOME_TEST_SESSION_SECRET', 'DOME_TEST_PASSPHRASE')) {
        if (-not [Environment]::GetEnvironmentVariable($name)) { [Environment]::SetEnvironmentVariable($name, 'unset') }
    }
}

function Invoke-Compose([string]$What, [string[]]$Arguments) {
    Set-ComposePlaceholders
    $all = @('compose', '-f', $script:ComposeFile) + $Arguments
    Invoke-Native $What { docker @all }
}

function Get-ComposeOutput([string[]]$Arguments) {
    Set-ComposePlaceholders
    $all = @('compose', '-f', $script:ComposeFile) + $Arguments
    $previous = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try { return (docker @all 2>$null | Out-String) } finally { $ErrorActionPreference = $previous }
}

function Wait-TunnelUrl([string]$Service, [int]$TimeoutSeconds = 90) {
    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    while ((Get-Date) -lt $deadline) {
        $logs = Get-ComposeOutput @('--profile', 'quick-tunnel', 'logs', '--no-color', $Service)
        $found = [regex]::Matches($logs, 'https://[a-z0-9]+(-[a-z0-9]+)+\.trycloudflare\.com')
        if ($found.Count -gt 0) { return $found[$found.Count - 1].Value }
        # Older cloudflared: "failed to request quick Tunnel"; 2026.x: "quick tunnel provisioning failed with status 429: ..."
        if ($logs -match 'failed to (request|unmarshal) quick Tunnel|quick tunnel provisioning failed|429 Too Many Requests') {
            Stop-Kit ("Cloudflare did not create the temporary address for $Service. Wait a minute and run this again. Details: docker compose -f testkit\docker-compose.yml --profile quick-tunnel logs $Service")
        }
        Start-Sleep -Seconds 2
    }
    Stop-Kit ("No temporary address from $Service after $TimeoutSeconds seconds. Check that this PC can reach the internet (a VPN or company firewall can block Cloudflare tunnels).")
}

function Test-Url([string]$Url) {
    try {
        $r = Invoke-WebRequest -Uri $Url -UseBasicParsing -TimeoutSec 10 -MaximumRedirection 0
        return ($r.StatusCode -eq 200)
    } catch { return $false }
}

function Wait-Url([string]$Url, [int]$TimeoutSeconds) {
    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    while ((Get-Date) -lt $deadline) {
        if (Test-Url $Url) { return $true }
        Start-Sleep -Seconds 3
    }
    return $false
}

function Read-StableAddresses {
    # Optional testkit\stable-addresses.txt with two lines, for your own HTTPS forwarding:
    #   APP_URL=https://...       (forwards to http://127.0.0.1:18080)
    #   SIGNIN_URL=https://...    (forwards to http://127.0.0.1:18081)
    $path = Join-Path $script:KitDir 'stable-addresses.txt'
    if (-not (Test-Path $path)) { return $null }
    $values = @{}
    foreach ($line in (Get-Content -Path $path)) {
        $t = $line.Trim()
        if (-not $t -or $t.StartsWith('#')) { continue }
        $parts = $t.Split('=', 2)
        if ($parts.Count -eq 2) { $values[$parts[0].Trim().ToUpper()] = $parts[1].Trim().TrimEnd('/') }
    }
    foreach ($key in @('APP_URL', 'SIGNIN_URL')) {
        if (-not $values.ContainsKey($key) -or -not ($values[$key] -match '^https://[^/?#\s]+(/[^?#\s]*)?$')) {
            Stop-Kit "testkit\stable-addresses.txt must contain $key=https://... (see README, 'Stable address')."
        }
    }
    if ($values['APP_URL'] -notmatch '^https://[^/]+$') { Stop-Kit 'APP_URL must be just https://host[:port] with no path.' }
    return [pscustomobject]@{ AppUrl = $values['APP_URL']; SigninUrl = $values['SIGNIN_URL'] }
}

function Get-CurrentPath { return (Join-Path $script:StateDir 'current.json') }

function Read-Current {
    $path = Get-CurrentPath
    if (-not (Test-Path $path)) { return $null }
    return (Get-Content -Raw -Path $path | ConvertFrom-Json)
}

function Write-Current($Object) {
    Initialize-StateDir
    $Object | ConvertTo-Json | Set-Content -Path (Get-CurrentPath) -Encoding Ascii
}

function Show-Image([string]$Path) {
    if ($script:OnWindows) {
        try { Start-Process -FilePath $Path } catch { Write-Warn ('Could not open ' + $Path) }
    }
}
