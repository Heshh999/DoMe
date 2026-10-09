# DoMe test kit, step 1: start the DoMe service on this PC and give it a temporary HTTPS address.
# Run through "1 Start test server.cmd". Windows PowerShell 5.1 compatible; ASCII only.
param(
    [switch]$NoQr
)
. "$PSScriptRoot\common.ps1"

$exitCode = Invoke-KitMain {
    Write-Banner 'DoMe test server - step 1 of 2'
    Assert-Docker
    Initialize-Uv
    Sync-AgentEnvironment
    Initialize-StateDir

    Write-Step 'Creating fresh secrets for this run'
    # The session secret is kept so a stable address keeps you signed in across restarts; the
    # sign-in passphrase and the sign-in client secret are new on every start.
    $env:DOME_TEST_SESSION_SECRET = Get-PersistentSecret 'session-secret.txt' 32
    $env:DOME_TEST_CLIENT_SECRET = New-HexSecret 24
    $env:DOME_TEST_PASSPHRASE = New-Passphrase
    Write-Ok 'done'

    $stable = Read-StableAddresses
    if ($stable) {
        Write-Step 'Using your own addresses from testkit\stable-addresses.txt'
        Invoke-Compose 'Stopping temporary tunnels' @('--profile', 'quick-tunnel', 'rm', '--stop', '--force', 'tunnel-app', 'tunnel-signin')
        $appUrl = $stable.AppUrl
        $signinUrl = $stable.SigninUrl
        $mode = 'stable'
    } else {
        # A new sign-in address is a new test account, and accounts from earlier addresses can never be
        # signed into again: start from an empty database instead of piling them up.
        Write-Step 'Starting from empty test data (new addresses mean a new test account)'
        Invoke-Compose 'Clearing earlier test data' @('--profile', 'quick-tunnel', 'down', '-v')
        Remove-LinkRecord
        Write-Ok 'done'
        Write-Step 'Opening two temporary HTTPS addresses (Cloudflare quick tunnels, no account needed)'
        Invoke-Compose 'Starting the tunnels' @('--profile', 'quick-tunnel', 'up', '-d', '--force-recreate', 'tunnel-app', 'tunnel-signin')
        $appUrl = Wait-TunnelUrl 'tunnel-app'
        $signinUrl = Wait-TunnelUrl 'tunnel-signin'
        $mode = 'quick-tunnel'
    }
    Write-Ok ('app address:     ' + $appUrl)
    Write-Ok ('sign-in address: ' + $signinUrl)
    $env:DOME_TEST_APP_URL = $appUrl
    $env:DOME_TEST_SIGNIN_URL = $signinUrl

    Write-Step 'Building and starting DoMe (first time: 5-15 minutes while Docker downloads and builds)'
    Invoke-Compose 'Starting DoMe' @('up', '-d', '--build', 'postgres', 'dev-idp', 'api')

    Write-Step 'Waiting for DoMe to answer on this PC'
    if (-not (Wait-Url 'http://127.0.0.1:18080/healthz' 240)) {
        Stop-Kit 'DoMe did not start. To see why, run in a Command Prompt: docker logs dome-test-api-1'
    }
    Write-Ok 'DoMe is running'

    Write-Current ([ordered]@{
            app_url    = $appUrl
            signin_url = $signinUrl
            passphrase = $env:DOME_TEST_PASSPHRASE
            mode       = $mode
            started_at = (Get-Date).ToString('s')
        })

    Write-Step 'Checking the public address (new addresses can take up to a minute to work)'
    Start-Sleep -Seconds 10
    $publicOk = (Wait-Url ($appUrl + '/healthz') 120) -and (Wait-Url ($signinUrl + '/.well-known/openid-configuration') 60)
    if ($publicOk) { Write-Ok 'reachable from the internet' }
    else { Write-Warn 'Not reachable yet from this PC. Give it a minute; if your iPhone cannot open it either, see README "If something goes wrong".' }

    # /app goes straight to sign-in (the bare address is the public website).
    $phoneUrl = $appUrl + '/app'
    $qrPath = Join-Path $script:StateDir 'open-on-iphone.png'
    if (-not $NoQr) {
        Invoke-Native 'Making the QR code' { & $script:AgentPython (Join-Path $PSScriptRoot 'kit_helper.py') qr $phoneUrl $qrPath }
        Show-Image $qrPath
    }

    Write-Banner 'DoMe test server is running'
    Write-Host ''
    Write-Host '  Open this on your iPhone (Safari):' -ForegroundColor White
    Write-Host ('      ' + $phoneUrl) -ForegroundColor Green
    if (-not $NoQr) { Write-Host '      (or point the iPhone camera at the QR code that just opened)' }
    Write-Host ''
    Write-Host '  Sign-in passphrase (the sign-in page asks for it):' -ForegroundColor White
    Write-Host ('      ' + $env:DOME_TEST_PASSPHRASE) -ForegroundColor Green
    Write-Host ''
    Write-Host '  Next: double-click "2 Start DoMe on this PC.cmd".'
    if ($mode -eq 'quick-tunnel') {
        Write-Host '  These addresses stop working when you run "3 Stop test server.cmd", restart Docker or'
        Write-Host '  restart the PC. Starting again gives NEW addresses (see README, "Every start is a new address").'
        Write-Host '  A new address is a new, empty test account: step 2 links this PC again, and the iPhone'
        Write-Host '  opens the new address, signs in and pairs again.'
    }
    Write-Host '  You can close this window; DoMe keeps running in Docker.'
}
exit $exitCode
