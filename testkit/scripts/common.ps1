# Shared helpers for the DoMe test kit scripts. Windows PowerShell 5.1 compatible; keep this file
# ASCII-only (5.1 reads BOM-less scripts in the system code page).

Set-StrictMode -Version 2.0
$ErrorActionPreference = 'Stop'
# Docker's animated progress display needs the console window itself; the kit passes program output
# through PowerShell, and on Windows that fails with "failed to get console: The handle is invalid".
# Plain text progress works everywhere (also passed as --progress plain below).
$env:BUILDKIT_PROGRESS = 'plain'
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
        # [string] turns the $null that Get-Content -Raw returns for an empty file into ''.
        $value = ([string](Get-Content -Raw -Path $path)).Trim()
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
    $all = @('compose', '-f', $script:ComposeFile, '--progress', 'plain') + $Arguments
    Invoke-Native $What { docker @all }
}

function Get-ComposeOutput([string[]]$Arguments) {
    Set-ComposePlaceholders
    $all = @('compose', '-f', $script:ComposeFile) + $Arguments
    $previous = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try { return (docker @all 2>$null | Out-String) } finally { $ErrorActionPreference = $previous }
}

# The address in cloudflared's "Your quick Tunnel has been created" banner (never api.trycloudflare.com),
# and its failure lines: older releases say "failed to request quick Tunnel", 2026.x says
# "quick tunnel provisioning failed with status 429: ...". Checked by testkit/tests/kit.tests.ps1.
$script:TunnelUrlPattern = 'https://[a-z0-9]+(-[a-z0-9]+)+\.trycloudflare\.com'
$script:TunnelFailurePattern = 'failed to (request|unmarshal) quick Tunnel|quick tunnel provisioning failed|429 Too Many Requests'

function Stop-Tunnels {
    # Both tunnel containers restart on failure; leaving them would keep asking Cloudflare for addresses
    # (and prolong a rate limit). Best effort: we are already reporting a problem.
    [void](Get-ComposeOutput @('--profile', 'quick-tunnel', 'rm', '--stop', '--force', 'tunnel-app', 'tunnel-signin'))
}

function Wait-TunnelUrl([string]$Service, [int]$TimeoutSeconds = 90) {
    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    while ((Get-Date) -lt $deadline) {
        $logs = Get-ComposeOutput @('--profile', 'quick-tunnel', 'logs', '--no-color', $Service)
        $found = [regex]::Matches($logs, $script:TunnelUrlPattern)
        if ($found.Count -gt 0) { return $found[$found.Count - 1].Value }
        if ($logs -match $script:TunnelFailurePattern) {
            $said = @($logs -split "`n" | Where-Object { $_ -match $script:TunnelFailurePattern } | Select-Object -Last 1)
            Stop-Tunnels
            Stop-Kit ("Cloudflare did not create a temporary address (it limits how often they can be made). Wait a minute or two and run this again. Cloudflare said: " + ([string]($said -join '')).Trim())
        }
        Start-Sleep -Seconds 2
    }
    Stop-Tunnels
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
    $app = ConvertTo-NormalHttpsUrl $values['APP_URL']
    $signin = ConvertTo-NormalHttpsUrl $values['SIGNIN_URL']
    if (-not $app -or -not $signin) { Stop-Kit "testkit\stable-addresses.txt: APP_URL and SIGNIN_URL must look like https://host (see README, 'Stable address')." }
    if ($app -notmatch '^https://[^/]+$') { Stop-Kit 'APP_URL must be just https://host[:port] with no path.' }
    return [pscustomobject]@{ AppUrl = $app; SigninUrl = $signin }
}

function ConvertTo-NormalHttpsUrl([string]$Url) {
    # The api lower-cases its public origin and browsers drop ':443', while the sign-in page compares the
    # redirect address exactly: hand every component the same spelling.
    $m = [regex]::Match($Url, '^(?i:https)://([^/:?#\s]+)(?::(\d+))?(/[^?#\s]*)?$')
    if (-not $m.Success) { return $null }
    $normal = 'https://' + $m.Groups[1].Value.ToLowerInvariant()
    if ($m.Groups[2].Success -and $m.Groups[2].Value -ne '443') { $normal += ':' + $m.Groups[2].Value }
    return ($normal + $m.Groups[3].Value).TrimEnd('/')
}

function Get-AgentStatus {
    # Asks the DoMe PC program for its status as JSON. Its error output is kept apart from the JSON and,
    # when it fails, shown to the user: hiding it made a failure on Windows impossible to diagnose.
    $previous = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try { $all = @(& $script:AgentExe status --json 2>&1) } finally { $ErrorActionPreference = $previous }
    $code = $LASTEXITCODE
    $stdout = ($all | Where-Object { $_ -isnot [System.Management.Automation.ErrorRecord] } | ForEach-Object { [string]$_ }) -join "`n"
    $stderr = @($all | Where-Object { $_ -is [System.Management.Automation.ErrorRecord] } | ForEach-Object { $_.ToString() })
    $status = $null
    if ($code -eq 0 -and $stdout.Trim()) {
        try { $status = $stdout | ConvertFrom-Json } catch { $status = $null }
    }
    if ($null -eq $status) {
        Write-Host ''
        Write-Host ('   The DoMe PC program answered (exit code ' + $code + '):') -ForegroundColor Yellow
        foreach ($line in (@($stderr) + @($stdout -split "`n") | Where-Object { $_ -and $_.Trim() } | Select-Object -Last 30)) {
            Write-Host ('     ' + $line)
        }
        Stop-Kit 'Could not read the DoMe PC program status (its own message is above). Please send a screenshot of this window.'
    }
    return $status
}

function Get-CurrentPath { return (Join-Path $script:StateDir 'current.json') }

# DoMe identifies an account by sign-in address + email, so a new quick-tunnel address (or deleted test
# data) is a NEW test account. agent-link.json remembers which sign-in address this PC was linked through.
function Get-LinkRecordPath { return (Join-Path $script:StateDir 'agent-link.json') }

function Test-LinkedThrough([string]$SigninUrl) {
    $path = Get-LinkRecordPath
    if (-not (Test-Path $path)) { return $false }
    try { $record = Get-Content -Raw -Path $path | ConvertFrom-Json } catch { return $false }
    if (-not $record -or -not $record.PSObject.Properties['signin_url']) { return $false }
    return ([string]$record.signin_url -eq $SigninUrl)
}

function Save-LinkRecord([string]$SigninUrl, [string]$AppUrl) {
    Initialize-StateDir
    [ordered]@{ signin_url = $SigninUrl; app_url = $AppUrl; linked_at = (Get-Date).ToString('s') } |
        ConvertTo-Json | Set-Content -Path (Get-LinkRecordPath) -Encoding Ascii
}

function Remove-LinkRecord {
    $path = Get-LinkRecordPath
    if (Test-Path $path) { Remove-Item -Force $path }
}

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
