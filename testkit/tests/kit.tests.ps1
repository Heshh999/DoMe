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
    Set-Content -Path (Join-Path $script:KitDir 'stable-addresses.txt') -Encoding Ascii -Value @(
        'APP_URL=HTTPS://Dome-Test.Example.com:443', 'SIGNIN_URL=https://Dome-Signin.example.com:8443/realms/dome/')
    $stable = Read-StableAddresses
    Check 'APP_URL lower-cased, :443 dropped' $stable.AppUrl 'https://dome-test.example.com'
    Check 'SIGNIN_URL host lower-cased, other port and path kept' $stable.SigninUrl 'https://dome-signin.example.com:8443/realms/dome'
    $script:KitDir = $saved

    # Get-AgentStatus: JSON from stdout, the program's error output shown (not hidden) when it fails.
    $savedExe = $script:AgentExe
    $fake = Join-Path $script:StateDir 'fake-agent.ps1'
    $pwshExe = (Get-Process -Id $PID).Path
    function Use-FakeAgent([string]$Body) {
        Set-Content -Path $fake -Encoding Ascii -Value $Body
        $wrapper = Join-Path $script:StateDir ('fake-agent' + $(if ($script:OnWindows) { '.cmd' } else { '' }))
        if ($script:OnWindows) {
            Set-Content -Path $wrapper -Encoding Ascii -Value ('@"' + $pwshExe + '" -NoProfile -File "' + $fake + '" %*')
        } else {
            Set-Content -Path $wrapper -Encoding Ascii -Value @('#!/bin/sh', ('exec "' + $pwshExe + '" -NoProfile -File "' + $fake + '" "$@"'))
            & chmod +x $wrapper
        }
        $script:AgentExe = $wrapper
    }
    Use-FakeAgent '[Console]::Out.WriteLine(''{"agent_running": false, "identity": {"linked": true}}''); exit 0'
    $st = Get-AgentStatus
    Check 'status JSON parsed' ([bool]$st.identity.linked) $true
    Use-FakeAgent '[Console]::Error.WriteLine(''a warning on stderr''); [Console]::Out.WriteLine(''{"agent_running": true}''); exit 0'
    Check 'stderr noise does not break the JSON' ([bool](Get-AgentStatus).agent_running) $true
    Use-FakeAgent '[Console]::Error.WriteLine(''ModuleNotFoundError: No module named win32file''); exit 1'
    $caught = @{ e = $null }  # a hashtable: the script block below runs in its own scope
    $shown = & { try { [void](Get-AgentStatus) } catch { $caught.e = $_.Exception } } 6>&1 | Out-String
    Check 'a failing program stops the kit' ([bool]($caught.e -and $caught.e.Data.Contains('DoMeKit'))) $true
    Check 'its error message is shown' ($shown -match 'No module named win32file') $true
    $script:AgentExe = $savedExe

    # Source fingerprints: start-agent.ps1 rebuilds the extension when its sources changed (and warns when
    # the running test server was built from other sources); dependencies, build output, tests and notes
    # do not count.
    $repo = Join-Path $script:StateDir 'repo'
    function Write-RepoFile([string]$Path, [string]$Text) {
        $full = Join-Path $repo $Path
        New-Item -ItemType Directory -Force -Path (Split-Path -Parent $full) | Out-Null
        Set-Content -Path $full -Value $Text -Encoding Ascii -NoNewline
    }
    function Get-ExtensionPrint { return (Get-SourceFingerprint $repo $script:ExtensionSources) }
    Write-RepoFile 'browser-extension/src/background/a.ts' 'one'
    Write-RepoFile 'browser-extension/public/manifest.json' '{}'
    Write-RepoFile 'browser-extension/package.json' '{}'
    Write-RepoFile 'shared/ts/src/index.ts' 'export {};'
    Write-RepoFile 'shared/protocol/schemas/bridge.schema.json' '{}'
    Write-RepoFile 'testkit/extension.Dockerfile' 'FROM scratch'
    $first = Get-ExtensionPrint
    Check 'fingerprint: 64 hex digits' ($first -cmatch '^[0-9a-f]{64}$') $true
    Check 'fingerprint: same sources, same value' (Get-ExtensionPrint) $first
    Push-Location $script:StateDir
    try { Check 'fingerprint: relative root' (Get-SourceFingerprint 'repo' $script:ExtensionSources) $first } finally { Pop-Location }
    foreach ($ignored in @('browser-extension/node_modules/vite/index.js', 'browser-extension/dist/background.js',
            'browser-extension/src/node_modules/x.js', 'shared/ts/node_modules/.pnpm/ajv/a.js', 'browser-extension/.vite/deps.json',
            'browser-extension/test/a.test.ts', 'browser-extension/fixtures/watch.html', 'shared/protocol/fixtures/f.json',
            'browser-extension/README.md', 'shared/ts/tsconfig.tsbuildinfo', 'cloud-api/dome_api/app.py')) {
        Write-RepoFile $ignored 'anything'
        Check ('fingerprint ignores ' + $ignored) (Get-ExtensionPrint) $first
    }
    Write-RepoFile 'browser-extension/src/background/a.ts' 'two'
    $edited = Get-ExtensionPrint
    Check 'fingerprint: an edited source file changes it' ($edited -ne $first) $true
    Write-RepoFile 'browser-extension/src/background/a.ts' 'one'
    Check 'fingerprint: the old content gives the old value' (Get-ExtensionPrint) $first
    Write-RepoFile 'browser-extension/src/background/b.ts' 'one'
    Check 'fingerprint: an added source file changes it' ((Get-ExtensionPrint) -ne $first) $true
    Remove-Item -Force (Join-Path $repo 'browser-extension/src/background/b.ts')
    Move-Item (Join-Path $repo 'browser-extension/src/background/a.ts') (Join-Path $repo 'browser-extension/src/background/c.ts')
    Check 'fingerprint: a renamed source file changes it' ((Get-ExtensionPrint) -ne $first) $true
    Move-Item (Join-Path $repo 'browser-extension/src/background/c.ts') (Join-Path $repo 'browser-extension/src/background/a.ts')
    Check 'fingerprint: back to the old value' (Get-ExtensionPrint) $first
    foreach ($source in @('browser-extension/public/manifest.json', 'browser-extension/package.json', 'browser-extension/pnpm-lock.yaml',
            'browser-extension/vite.config.ts', 'browser-extension/scripts/gen-validators.ts', 'shared/ts/src/index.ts',
            'shared/protocol/schemas/bridge.schema.json', 'shared/protocol/version.json', 'testkit/extension.Dockerfile')) {
        $full = Join-Path $repo $source
        $before = if (Test-Path $full) { [IO.File]::ReadAllText($full) } else { $null }
        Write-RepoFile $source 'changed'
        Check ('fingerprint notices ' + $source) ((Get-ExtensionPrint) -ne $first) $true
        if ($null -eq $before) { Remove-Item -Force $full } else { Write-RepoFile $source $before }
    }
    Check 'fingerprint: unchanged after the round trip' (Get-ExtensionPrint) $first
    $server = Get-SourceFingerprint $repo $script:ServerSources
    Write-RepoFile 'cloud-api/dome_api/app.py' 'changed'
    Check 'server fingerprint notices cloud-api' ((Get-SourceFingerprint $repo $script:ServerSources) -ne $server) $true
    Check 'saved fingerprint: none yet' (Get-SavedFingerprint 'extension-sources.txt') ''
    Save-Fingerprint 'extension-sources.txt' $first
    Check 'saved fingerprint: read back' (Get-SavedFingerprint 'extension-sources.txt') $first
    # Every folder the Docker builds copy is part of the matching fingerprint.
    foreach ($build in @(@('testkit/extension.Dockerfile', $script:ExtensionSources), @('deploy/Dockerfile', $script:ServerSources))) {
        $copied = foreach ($line in (Get-Content -Path (Join-Path $script:RepoDir $build[0]))) {
            if ($line -match '^\s*COPY\s+(.*)$' -and $line -notmatch '--from=') {
                $words = @($Matches[1].Trim() -split '\s+' | Where-Object { $_ -notlike '--*' })
                $words[0..($words.Count - 2)]
            }
        }
        foreach ($path in $copied) {
            $covered = @($build[1] | Where-Object { $path -eq $_ -or $path.StartsWith($_ + '/') }).Count -gt 0
            Check ('fingerprint of ' + $build[0] + ' covers ' + $path) $covered $true
        }
    }
    Check 'fingerprint of this repository' ((Get-SourceFingerprint $script:RepoDir $script:ExtensionSources) -cmatch '^[0-9a-f]{64}$') $true

    # Step 2's browser extension (Initialize-BrowserExtension) with Docker, the PC program, the browser and
    # the owner's answers replaced by the functions below (a function is found before a program or cmdlet
    # of the same name). $kit steers them and records what happened.
    $kit = @{ Builds = 0; FailBuild = $false; HelperFails = $false; CloseAtPrompt = $false; FailMoveFrom = ''
        Prompts = (New-Object System.Collections.Generic.List[string]); Stopped = $null }
    function docker {
        $kit.Builds += 1
        $dest = [string]@($args | Where-Object { ([string]$_).StartsWith('type=local,dest=') })[0]
        $dest = $dest.Substring('type=local,dest='.Length)
        New-Item -ItemType Directory -Force -Path $dest | Out-Null
        '   (fake docker build output)'  # shown on the screen; in a function's value it would read as success
        if ($kit.FailBuild) { $global:LASTEXITCODE = 1; return }
        Set-Content -LiteralPath (Join-Path $dest 'manifest.json') -Value ('build ' + $kit.Builds) -Encoding Ascii -NoNewline
        $global:LASTEXITCODE = 0
    }
    function Invoke-FakeKitHelper {
        if ($kit.HelperFails) { $global:LASTEXITCODE = 1; return }
        $global:LASTEXITCODE = 0
        'abcdefghijklmnopabcdefghijklmnop'
    }
    function Invoke-FakeAgentExe { $global:LASTEXITCODE = 0 }
    function Read-Host([string]$Prompt) {
        $kit.Prompts.Add($Prompt.Trim())
        if ($kit.CloseAtPrompt) { throw 'the window was closed' }
        return ''
    }
    function Set-Clipboard([string]$Value) { }
    function Start-Process { throw 'no browser here' }
    function Move-Item([string]$LiteralPath, [string]$Destination) {
        if ($kit.FailMoveFrom -and $LiteralPath.EndsWith($kit.FailMoveFrom)) { throw 'the folder is in use' }
        Microsoft.PowerShell.Management\Move-Item -LiteralPath $LiteralPath -Destination $Destination
    }
    function Invoke-ExtensionStep([switch]$Rebuild) {
        # Runs the step like start-agent.ps1 does; returns what the window showed.
        $kit.Prompts.Clear()
        $kit.Stopped = $null
        return (& { try { Initialize-BrowserExtension -Rebuild:$Rebuild } catch { $kit.Stopped = $_.Exception } } 6>&1 | Out-String)
    }
    function Get-Build {
        $manifest = Join-Path $extensionDir 'manifest.json'
        if (-not (Test-Path -LiteralPath $manifest)) { return '(no extension)' }
        return [IO.File]::ReadAllText($manifest)
    }
    $savedRepo, $savedPython, $savedExe = $script:RepoDir, $script:AgentPython, $script:AgentExe
    $script:RepoDir, $script:AgentPython, $script:AgentExe = $repo, 'Invoke-FakeKitHelper', 'Invoke-FakeAgentExe'
    $extensionDir = Join-Path $script:StateDir 'extension'
    Remove-Item -Force (Join-Path $script:StateDir 'extension-sources.txt')
    $loaded = 'Press Enter when the extension is loaded'
    $reloaded = 'Press Enter when the extension is reloaded'
    try {
        $shown = Invoke-ExtensionStep
        Check 'extension: first run builds it' (Get-Build) 'build 1'
        Check 'extension: first run asks to load it' ($kit.Prompts -join '|') $loaded
        Check 'extension: no stop' $kit.Stopped $null
        Check 'extension: sources recorded after the owner loaded it' (Get-SavedFingerprint 'extension-sources.txt') (Get-ExtensionPrint)
        Check 'extension: its ID reaches the browser bridge' $env:DOME_AGENT_DEV_EXTENSION_ID 'abcdefghijklmnopabcdefghijklmnop'
        Check 'extension: open YouTube tabs need no reload' ($shown -match 'Reload any YouTube') $false
        Check 'extension: no extension.new left' (Test-Path (Join-Path $script:StateDir 'extension.new')) $false
        Check 'extension: no extension.old left' (Test-Path (Join-Path $script:StateDir 'extension.old')) $false

        $shown = Invoke-ExtensionStep
        Check 'extension: unchanged sources, no build' $kit.Builds 1
        Check 'extension: unchanged sources, no prompt' $kit.Prompts.Count 0
        Check 'extension: says it is up to date' ($shown -match 'the extension is up to date') $true

        # The window is closed at the reload prompt: the next run must build again and ask again.
        Write-RepoFile 'browser-extension/src/background/a.ts' 'two'
        $kit.CloseAtPrompt = $true
        [void](Invoke-ExtensionStep)
        $kit.CloseAtPrompt = $false
        Check 'extension: changed sources are built' (Get-Build) 'build 2'
        Check 'extension: asked to reload it' ($kit.Prompts -join '|') $reloaded
        Check 'extension: not recorded before the owner reloaded it' (Get-SavedFingerprint 'extension-sources.txt') $first
        [void](Invoke-ExtensionStep)
        Check 'extension: an interrupted reload is asked for again' ($kit.Prompts -join '|') $reloaded
        Check 'extension: then recorded' (Get-SavedFingerprint 'extension-sources.txt') (Get-ExtensionPrint)

        # The extension ID cannot be pinned after the swap: the next run asks for the reload again.
        Write-RepoFile 'browser-extension/src/background/a.ts' 'three'
        $kit.HelperFails = $true
        [void](Invoke-ExtensionStep)
        $kit.HelperFails = $false
        Check 'extension: a failing ID helper stops step 2' ([bool]($kit.Stopped -and $kit.Stopped.Data.Contains('DoMeKit'))) $true
        Check 'extension: nothing recorded after that stop' ((Get-SavedFingerprint 'extension-sources.txt') -eq (Get-ExtensionPrint)) $false
        [void](Invoke-ExtensionStep)
        Check 'extension: the run after that stop asks to reload' ($kit.Prompts -join '|') $reloaded

        # A failed rebuild keeps the previous build and DoMe still starts; the next run tries again.
        Write-RepoFile 'browser-extension/src/background/a.ts' 'four'
        $before = Get-Build
        $kit.FailBuild = $true
        $env:DOME_AGENT_DEV_EXTENSION_ID = ''
        $shown = Invoke-ExtensionStep
        $kit.FailBuild = $false
        Check 'extension: a failed rebuild does not stop step 2' $kit.Stopped $null
        Check 'extension: a failed rebuild says so' ($shown -match 'Building the browser extension failed') $true
        Check 'extension: a failed rebuild keeps the previous build' (Get-Build) $before
        Check 'extension: a failed rebuild asks for no reload' $kit.Prompts.Count 0
        Check 'extension: a failed rebuild is not recorded' ((Get-SavedFingerprint 'extension-sources.txt') -eq (Get-ExtensionPrint)) $false
        Check 'extension: the bridge is still registered' $env:DOME_AGENT_DEV_EXTENSION_ID 'abcdefghijklmnopabcdefghijklmnop'
        $builds = $kit.Builds
        [void](Invoke-ExtensionStep)
        Check 'extension: the next run builds again' $kit.Builds ($builds + 1)
        Check 'extension: and asks to reload it' ($kit.Prompts -join '|') $reloaded

        # The new build cannot be moved into place: the previous build is put back.
        Write-RepoFile 'browser-extension/src/background/a.ts' 'five'
        $before = Get-Build
        $kit.FailMoveFrom = 'extension.new'
        $shown = Invoke-ExtensionStep
        $kit.FailMoveFrom = ''
        Check 'extension: a failed swap does not stop step 2' $kit.Stopped $null
        Check 'extension: a failed swap puts the previous build back' (Get-Build) $before
        Check 'extension: a failed swap leaves no extension.old' (Test-Path (Join-Path $script:StateDir 'extension.old')) $false
        Check 'extension: a failed swap says why' ($shown -match 'Could not replace the extension') $true
        Check 'extension: a failed swap is not recorded' ((Get-SavedFingerprint 'extension-sources.txt') -eq (Get-ExtensionPrint)) $false

        # A previous build left in extension.old (its clean-up failed) is replaced, not nested into.
        New-Item -ItemType Directory -Force -Path (Join-Path $script:StateDir 'extension.old') | Out-Null
        Set-Content -LiteralPath (Join-Path $script:StateDir 'extension.old/manifest.json') -Value 'stale' -Encoding Ascii
        [void](Invoke-ExtensionStep)
        Check 'extension: built over a left-over extension.old' (Get-Build) ('build ' + $kit.Builds)
        Check 'extension: the left-over extension.old is gone' (Test-Path (Join-Path $script:StateDir 'extension.old')) $false

        # The first build has nothing to fall back on.
        Remove-Item -Recurse -Force $extensionDir
        $kit.FailBuild = $true
        [void](Invoke-ExtensionStep)
        $kit.FailBuild = $false
        Check 'extension: a failed first build stops step 2' ([bool]($kit.Stopped -and $kit.Stopped.Data.Contains('DoMeKit'))) $true
        Check 'extension: a failed first build installs nothing' (Test-Path $extensionDir) $false
    } finally {
        $script:RepoDir, $script:AgentPython, $script:AgentExe = $savedRepo, $savedPython, $savedExe
        foreach ($name in @('docker', 'Invoke-FakeKitHelper', 'Invoke-FakeAgentExe', 'Read-Host', 'Set-Clipboard', 'Start-Process', 'Move-Item')) {
            Remove-Item -Path ('function:' + $name)
        }
    }

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
