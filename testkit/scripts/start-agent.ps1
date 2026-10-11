# DoMe test kit, step 2: run the DoMe PC program against the test server started in step 1.
# Run through "2 Start DoMe on this PC.cmd". Windows PowerShell 5.1 compatible; ASCII only.
param(
    [switch]$RebuildExtension,
    [switch]$NoBrowser
)
. "$PSScriptRoot\common.ps1"

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

    # Step 1 records which sources it built the server from (current.json, server_sources). A newer
    # download with a changed server or iPhone app needs step 1 again; one without needs only this step.
    $serverBuiltFrom = ''
    if ($current.PSObject.Properties['server_sources']) { $serverBuiltFrom = [string]$current.server_sources }
    if ($serverBuiltFrom -ne (Get-SourceFingerprint $script:RepoDir $script:ServerSources)) {
        if ($serverBuiltFrom) { Write-Warn 'This folder has a newer DoMe server or iPhone app than the test server that is running.' }
        else { Write-Warn 'The running test server may be older than this folder (it was started by an older test kit).' }
        Write-Note 'To test the new version, close this window, run "1 Start test server.cmd" again, then this step.'
        Write-Note '(With temporary addresses that means new addresses: you link this PC and pair the iPhone again.)'
        Read-Host '   Press Enter to go on with the running test server instead' | Out-Null
    }

    Initialize-BrowserExtension -Rebuild:$RebuildExtension

    # A PC still linked to an earlier test account could never be paired from the phone (common.ps1,
    # Test-LinkedThrough): link again whenever the sign-in address changed or the data was deleted.
    $linked = [bool]$status.identity.linked
    if ($linked -and -not (Test-LinkedThrough ([string]$current.signin_url))) {
        Write-Step 'This PC was linked to an earlier test account'
        Write-Note 'A new test address, or deleted test data, means a new test account. Linking this PC again.'
        # --new-key: the earlier account may still own this PC's key, and DoMe never moves a key between
        # accounts ("already linked to a different account"); a fresh key links cleanly.
        Invoke-Native 'Forgetting the earlier test link' { & $script:AgentExe unlink --new-key }
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
    Write-Host '  The browser extension finds DoMe by itself, usually within 10 seconds. To connect it at once,'
    Write-Host '  in Edge open the DoMe extension (puzzle-piece icon, then DoMe) and press Retry connection.'
    Write-Host ''
    Write-Host '  Keep this window open while testing. Closing it (or Quit DoMe in the tray) stops DoMe.' -ForegroundColor Yellow
    Write-Host ''
    & $script:AgentExe run
    if ($LASTEXITCODE -ne 0) { Stop-Kit ('DoMe stopped with exit code ' + $LASTEXITCODE + '. Logs: %LOCALAPPDATA%\DoMe\logs\agent.log') }
}
exit $exitCode
