# DoMe test kit, step 2: run the DoMe PC program against the test server started in step 1.
# Run through "2 Start DoMe on this PC.cmd". Windows PowerShell 5.1 compatible; ASCII only.
param(
    [switch]$RebuildExtension,
    [switch]$NoBrowser
)
. "$PSScriptRoot\common.ps1"

function Get-AgentStatus {
    $previous = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try { $text = (& $script:AgentExe status --json 2>$null | Out-String) } finally { $ErrorActionPreference = $previous }
    if ($LASTEXITCODE -ne 0 -or -not $text.Trim()) { Stop-Kit 'Could not read the DoMe PC program status.' }
    return ($text | ConvertFrom-Json)
}

function Open-ExtensionsPage {
    foreach ($browser in @(@('chrome', 'chrome://extensions'), @('msedge', 'edge://extensions'))) {
        try {
            Start-Process -FilePath $browser[0] -ArgumentList $browser[1] -ErrorAction Stop
            return $browser[0]
        } catch { }
    }
    return $null
}

$exitCode = Invoke-KitMain {
    Write-Banner 'DoMe on this PC - step 2 of 2'
    if (-not $script:OnWindows) { Stop-Kit 'This step runs on the Windows PC you want to control.' }

    $current = Read-Current
    if (-not $current) { Stop-Kit 'The test server is not running. Double-click "1 Start test server.cmd" first.' }
    Assert-Docker
    Initialize-Uv
    Sync-AgentEnvironment

    $appUrl = [string]$current.app_url
    $relayUrl = ($appUrl -replace '^https://', 'wss://') + '/ws/agent'
    Write-Step ('Checking the test server at ' + $appUrl)
    if (-not (Wait-Url ($appUrl + '/healthz') 60)) {
        Stop-Kit 'The test server does not answer at its address. Run "1 Start test server.cmd" again (it will make new addresses).'
    }
    Write-Ok 'test server answers'

    # The agent normally remembers the addresses it was linked with; the test kit's addresses change
    # on every start, so they are passed in on every run instead.
    $env:DOME_AGENT_API_URL = $appUrl
    $env:DOME_AGENT_RELAY_URL = $relayUrl

    $status = Get-AgentStatus
    if ($status.agent_running) {
        Stop-Kit 'DoMe is already running on this PC. Right-click the DoMe icon near the clock, choose "Quit DoMe", then run this again.'
    }

    Write-Step 'Preparing the DoMe browser extension (YouTube control)'
    $extensionDir = Join-Path $script:StateDir 'extension'
    $keyPath = Join-Path $script:StateDir 'extension-key.pem'
    $firstExtension = -not (Test-Path (Join-Path $extensionDir 'manifest.json'))
    if ($firstExtension -or $RebuildExtension) {
        Write-Note 'Building it with Docker (first time: about 2-4 minutes)'
        if (Test-Path $extensionDir) { Remove-Item -Recurse -Force $extensionDir }
        Invoke-Native 'Building the browser extension' {
            docker build -f (Join-Path $script:KitDir 'extension.Dockerfile') --output ('type=local,dest=' + $extensionDir) $script:RepoDir
        }
    }
    $previous = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try { $extensionId = (& $script:AgentPython (Join-Path $PSScriptRoot 'kit_helper.py') extension $extensionDir $keyPath | Out-String).Trim() }
    finally { $ErrorActionPreference = $previous }
    if ($LASTEXITCODE -ne 0 -or $extensionId -notmatch '^[a-p]{32}$') { Stop-Kit 'Could not prepare the browser extension.' }
    Write-Ok ('extension ID ' + $extensionId)

    $env:DOME_AGENT_DEV_EXTENSION_ID = $extensionId
    Invoke-Native 'Registering the browser bridge' { & $script:AgentExe install-native-host }
    Write-Ok 'Chrome and Edge can now talk to DoMe on this PC'

    if ($firstExtension -or $RebuildExtension) {
        try { Set-Clipboard -Value $extensionDir } catch { }
        Write-Host ''
        Write-Host '  Load the extension in Chrome or Edge (one time):' -ForegroundColor White
        Write-Host '    1. On the Extensions page that opens, switch on "Developer mode".'
        Write-Host '    2. Click "Load unpacked" and choose this folder (already copied, paste with Ctrl+V):'
        Write-Host ('         ' + $extensionDir) -ForegroundColor Green
        Write-Host '    3. Reload any YouTube tabs that were already open.'
        if ($RebuildExtension -and -not $firstExtension) { Write-Host '    (Already loaded? Click the reload arrow on the DoMe card instead.)' }
        $opened = Open-ExtensionsPage
        if (-not $opened) { Write-Note 'Open chrome://extensions (or edge://extensions) yourself.' }
        Read-Host '  Press Enter when the extension is loaded' | Out-Null
    }

    # A PC still linked to an earlier test account could never be paired from the phone (common.ps1,
    # Test-LinkedThrough): link again whenever the sign-in address changed or the data was deleted.
    $linked = [bool]$status.identity.linked
    if ($linked -and -not (Test-LinkedThrough ([string]$current.signin_url))) {
        Write-Step 'This PC was linked to an earlier test account'
        Write-Note 'A new test address, or deleted test data, means a new test account. Linking this PC again.'
        Invoke-Native 'Forgetting the earlier test link' { & $script:AgentExe unlink }
        $linked = $false
    }

    if (-not $linked) {
        Write-Step 'Linking this PC to your DoMe test account'
        Write-Note 'A browser window opens. Sign in, using this passphrase on the sign-in page:'
        Write-Host ('      ' + [string]$current.passphrase) -ForegroundColor Green
        Write-Note 'and the SAME email you use on the iPhone (for example alice@example.test),'
        Write-Note 'then check the PC name and click "Link this PC".'
        $linkArgs = @('link', '--name', $env:COMPUTERNAME)
        if ($NoBrowser) { $linkArgs += '--no-browser' }
        Invoke-Native 'Linking this PC' { & $script:AgentExe @linkArgs }
        Save-LinkRecord ([string]$current.signin_url) $appUrl
        Write-Ok 'PC linked'
    } else {
        Write-Ok ('This PC is already linked as "' + [string]$status.identity.pc_name + '"')
    }

    Write-Banner 'DoMe is starting on this PC'
    Write-Host ''
    Write-Host '  Look for the DoMe icon near the clock (it may be under the ^ arrow).'
    Write-Host '  To pair your iPhone: right-click the icon -> "Pair a phone...", then in the DoMe app on'
    Write-Host '  the iPhone: More -> Devices -> Pair with a PC. Scan the code IN THE APP (or type it),'
    Write-Host '  compare the 6-digit numbers and approve on this PC.'
    Write-Host ''
    Write-Host '  Keep this window open while testing. Closing it (or Quit DoMe in the tray) stops DoMe.' -ForegroundColor Yellow
    Write-Host ''
    & $script:AgentExe run
    if ($LASTEXITCODE -ne 0) { Stop-Kit ('DoMe stopped with exit code ' + $LASTEXITCODE + '. Logs: %LOCALAPPDATA%\DoMe\logs\agent.log') }
}
exit $exitCode
