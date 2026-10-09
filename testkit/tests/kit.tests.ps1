# Checks for the test kit scripts that run anywhere PowerShell does (CI uses pwsh on Linux; the kit
# itself targets Windows PowerShell 5.1):
#   pwsh -NoProfile -File testkit/tests/kit.tests.ps1
# Exit code = number of failed checks.
$scriptsDir = Join-Path (Split-Path -Parent $PSScriptRoot) 'scripts'
. (Join-Path $scriptsDir 'common.ps1')
$ErrorActionPreference = 'Stop'
$script:failures = 0

function Check([string]$Name, $Actual, $Expected) {
    if ($Actual -ne $Expected) {
        Write-Host ("FAIL  {0}: got '{1}', expected '{2}'" -f $Name, $Actual, $Expected)
        $script:failures++
    } else {
        Write-Host ('ok    ' + $Name)
    }
}

# Every script parses, and stays ASCII (Windows PowerShell 5.1 reads BOM-less files in the ANSI code page).
foreach ($file in Get-ChildItem -Path $scriptsDir -Filter '*.ps1') {
    $errors = $null
    [void][System.Management.Automation.Language.Parser]::ParseFile($file.FullName, [ref]$null, [ref]$errors)
    Check ('parses: ' + $file.Name) (@($errors).Count) 0
    $bytes = [IO.File]::ReadAllBytes($file.FullName)
    Check ('ASCII only: ' + $file.Name) (@($bytes | Where-Object { $_ -gt 127 }).Count) 0
}

$script:StateDir = Join-Path ([IO.Path]::GetTempPath()) ('dome-kit-test-' + [guid]::NewGuid())
try {
    # Which test account the PC was linked through (start-agent.ps1 links again when it changed).
    Check 'no record: not linked through' (Test-LinkedThrough 'https://a.trycloudflare.com') $false
    Save-LinkRecord 'https://a.trycloudflare.com' 'https://app-a.trycloudflare.com'
    Check 'same sign-in address' (Test-LinkedThrough 'https://a.trycloudflare.com') $true
    Check 'new sign-in address' (Test-LinkedThrough 'https://b.trycloudflare.com') $false
    Set-Content -Path (Get-LinkRecordPath) -Value '{not json' -Encoding Ascii
    Check 'corrupt record' (Test-LinkedThrough 'https://a.trycloudflare.com') $false
    Set-Content -Path (Get-LinkRecordPath) -Value '{"app_url":"x"}' -Encoding Ascii
    Check 'record without signin_url' (Test-LinkedThrough 'https://a.trycloudflare.com') $false
    Set-Content -Path (Get-LinkRecordPath) -Value '' -Encoding Ascii
    Check 'empty record' (Test-LinkedThrough 'https://a.trycloudflare.com') $false
    Remove-LinkRecord
    Remove-LinkRecord
    Check 'removed record' (Test-Path (Get-LinkRecordPath)) $false

    # Persistent secrets survive restarts; an empty file is replaced, not a crash.
    Set-Content -Path (Join-Path $script:StateDir 'session-secret.txt') -Value '' -Encoding Ascii
    $secret = Get-PersistentSecret 'session-secret.txt' 32
    Check 'empty secret file: new 64-hex secret' ($secret -match '^[0-9a-f]{64}$') $true
    Check 'secret is kept' (Get-PersistentSecret 'session-secret.txt' 32) $secret

    # Passphrases: xxxx-xxxx-xxxx from the look-alike-free alphabet.
    $p = New-Passphrase
    Check 'passphrase shape' ($p -cmatch '^[abcdefghjkmnpqrstuvwxyz2-9]{4}-[abcdefghjkmnpqrstuvwxyz2-9]{4}-[abcdefghjkmnpqrstuvwxyz2-9]{4}$') $true

    # stable-addresses.txt parsing.
    $saved = $script:KitDir
    $script:KitDir = $script:StateDir
    Set-Content -Path (Join-Path $script:KitDir 'stable-addresses.txt') -Encoding Ascii -Value @(
        '# comment', 'app_url = https://dome-test.example.com/', 'SIGNIN_URL=https://dome-signin.example.com')
    $stable = Read-StableAddresses
    Check 'stable APP_URL (trailing slash dropped)' $stable.AppUrl 'https://dome-test.example.com'
    Check 'stable SIGNIN_URL' $stable.SigninUrl 'https://dome-signin.example.com'
    Set-Content -Path (Join-Path $script:KitDir 'stable-addresses.txt') -Encoding Ascii -Value @(
        'APP_URL=https://dome-test.example.com/app', 'SIGNIN_URL=https://dome-signin.example.com')
    $refused = $false
    try { [void](Read-StableAddresses) } catch { $refused = [bool]$_.Exception.Data.Contains('DoMeKit') }
    Check 'APP_URL with a path is refused' $refused $true
    $script:KitDir = $saved

    # The quick-tunnel address in cloudflared's banner, and its failure lines (old and 2026.x wording).
    $banner = '2026-10-09T20:00:00Z INF |  https://lucky-orange-river-cat.trycloudflare.com                    |'
    Check 'tunnel URL found' ([regex]::Matches($banner, $script:TunnelUrlPattern)[0].Value) 'https://lucky-orange-river-cat.trycloudflare.com'
    Check 'API host is not a tunnel URL' ([regex]::Matches('Post "https://api.trycloudflare.com/tunnel"', $script:TunnelUrlPattern).Count) 0
    Check 'old failure wording' ('failed to request quick Tunnel: 429 Too Many Requests' -match $script:TunnelFailurePattern) $true
    Check '2026.x failure wording' ('quick tunnel provisioning failed with status 429: rate limited' -match $script:TunnelFailurePattern) $true
    Check 'a normal log line is not a failure' ('INF Requesting new quick Tunnel on trycloudflare.com...' -match $script:TunnelFailurePattern) $false
} finally {
    Remove-Item -Recurse -Force $script:StateDir -ErrorAction SilentlyContinue
}

Write-Host ''
Write-Host ("{0} failed" -f $script:failures)
exit $script:failures
