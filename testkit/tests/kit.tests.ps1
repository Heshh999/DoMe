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

    # Every double-click file keeps CRLF line endings (cmd.exe needs them) and runs a script that exists.
    foreach ($cmd in Get-ChildItem -Path $script:KitDir -Filter '*.cmd') {
        $text = [IO.File]::ReadAllText($cmd.FullName)
        Check ('CRLF only: ' + $cmd.Name) ([regex]::Matches($text, '(?<!\r)\n').Count) 0
        $m = [regex]::Match($text, 'scripts\\([A-Za-z0-9-]+\.ps1)')
        Check ('runs an existing script: ' + $cmd.Name) ($m.Success -and (Test-Path (Join-Path $scriptsDir $m.Groups[1].Value))) $true
    }
    Check 'step 4 runs apply-ai-settings.ps1' ([IO.File]::ReadAllText((Join-Path $script:KitDir '4 Apply AI settings.cmd')) -match 'scripts\\apply-ai-settings\.ps1') $true

    # The optional AI assistant: the owner's key in testkit\ai-key.txt, DoMe's monthly limit in
    # testkit\ai-monthly-limit-usd.txt (Read-AiSettings). The key must never be shown or written by the kit.
    $realKitDir = $script:KitDir
    $script:KitDir = $script:StateDir
    $keyFile = Join-Path $script:KitDir 'ai-key.txt'
    $limitFile = Join-Path $script:KitDir 'ai-monthly-limit-usd.txt'
    $sentinel = 'sk-ant-api03-KITTEST' + [guid]::NewGuid().ToString('N')
    $script:allShown = New-Object System.Text.StringBuilder
    function Write-KitFile([string]$Path, [string]$Text, [bool]$Bom = $false) {
        [IO.File]::WriteAllText($Path, $Text, (New-Object System.Text.UTF8Encoding $Bom))
    }
    function Invoke-ReadAi {
        # Runs Read-AiSettings like the scripts do; returns its value, what the window showed and how it stopped.
        $r = @{ Ai = $null; Stopped = $null; Shown = '' }
        $r.Shown = & { try { $r.Ai = Read-AiSettings } catch { $r.Stopped = $_.Exception } } *>&1 | Out-String
        [void]$script:allShown.Append($r.Shown)
        if ($r.Stopped) { [void]$script:allShown.Append($r.Stopped.Message) }
        return $r
    }
    function Test-KitStop($Result) { return [bool]($Result.Stopped -and $Result.Stopped.Data.Contains('DoMeKit')) }
    try {
        $r = Invoke-ReadAi
        Check 'AI: no key file, off' $r.Ai.Enabled $false
        Check 'AI: no key file, no key' $r.Ai.Key ''
        Check 'AI: no key file, default limit 500 cents' $r.Ai.CeilingCents 500
        Check 'AI: no key file, status line' (Get-AiStatusLine $r.Ai) "AI assistant: off (no testkit\ai-key.txt; see README, 'Talk to DoMe')"

        Write-KitFile $keyFile ("  " + $sentinel + "  `r`n") $true
        $r = Invoke-ReadAi
        Check 'AI: key with BOM, spaces and a trailing newline is on' $r.Ai.Enabled $true
        Check 'AI: that key is trimmed' ($r.Ai.Key -ceq $sentinel) $true
        Check 'AI: an sk-ant- key gives no warning' ($r.Shown -match 'does not look like') $false
        Check 'AI: on, status line' (Get-AiStatusLine $r.Ai) 'AI assistant: on (key file found, monthly limit US$5)'
        Write-KitFile $keyFile ($sentinel + "`n")
        Check 'AI: key with an LF line end' ((Invoke-ReadAi).Ai.Key -ceq $sentinel) $true

        foreach ($bad in @(@('a space inside', ($sentinel.Substring(0, 12) + ' ' + $sentinel.Substring(12))),
                @('two lines', ($sentinel + "`r`nsecond line")), @('a tab inside', ($sentinel + "`tx")),
                @('a typographic quote', ($sentinel + [char]0x2019)), @('a non-breaking space inside', ('sk-ant-' + [char]0x00A0 + 'x')))) {
            Write-KitFile $keyFile $bad[1]
            $r = Invoke-ReadAi
            Check ('AI: key with ' + $bad[0] + ' is refused') (Test-KitStop $r) $true
            Check ('AI: that refusal says why') ([bool]($r.Stopped -and $r.Stopped.Message -match 'must contain only the key')) $true
        }

        Write-KitFile $keyFile 'not-an-anthropic-key-123'
        $r = Invoke-ReadAi
        Check 'AI: a non-sk-ant- value is still used' $r.Ai.Enabled $true
        Check 'AI: with a warning' ($r.Shown -match 'does not look like an Anthropic API key') $true
        Check 'AI: and without showing it' ($r.Shown -match 'not-an-anthropic-key-123') $false

        Write-KitFile $keyFile "  `r`n" $true
        $r = Invoke-ReadAi
        Check 'AI: an empty key file is off' $r.Ai.Enabled $false
        Check 'AI: an empty key file is no stop' $r.Stopped $null
        Check 'AI: empty file, status line' (Get-AiStatusLine $r.Ai) "AI assistant: off (testkit\ai-key.txt is empty; see README, 'Talk to DoMe')"

        Write-KitFile $keyFile $sentinel
        foreach ($case in @(@('invalid', 'five'), @('0', '0'), @('101', '101'), @('negative', '-3'), @('a fraction', '5.5'),
                @('a currency sign', '$12'), @('empty', ''))) {
            Write-KitFile $limitFile $case[1]
            $r = Invoke-ReadAi
            Check ('AI limit ' + $case[0] + ': default 500 cents') $r.Ai.CeilingCents 500
            Check ('AI limit ' + $case[0] + ': with a warning') ($r.Shown -match 'whole number of US dollars from 1 to 100') $true
        }
        foreach ($case in @(@('12', "12`r`n", 1200), @('1', '1', 100), @('100', '100', 10000), @('with a BOM', ' 7 ', 700))) {
            Write-KitFile $limitFile $case[1] ($case[0] -eq 'with a BOM')
            $r = Invoke-ReadAi
            Check ('AI limit ' + $case[0] + ': cents') $r.Ai.CeilingCents $case[2]
            Check ('AI limit ' + $case[0] + ': no warning') ($r.Shown -match 'whole number') $false
        }
        Check 'AI: the status line names the limit' (Get-AiStatusLine $r.Ai) 'AI assistant: on (key file found, monthly limit US$7)'
        Remove-Item -Force $limitFile

        # The compose variables: the key only ever travels in this process's environment.
        Set-AiComposeVariables (Invoke-ReadAi).Ai
        Check 'AI compose: enabled' $env:DOME_TEST_AI_ENABLED 'true'
        Check 'AI compose: key' ($env:DOME_TEST_AI_KEY -ceq $sentinel) $true
        Check 'AI compose: ceiling' $env:DOME_TEST_AI_CEILING_CENTS '500'
        Remove-Item -Force $keyFile
        Set-AiComposeVariables (Invoke-ReadAi).Ai
        Check 'AI compose: off' $env:DOME_TEST_AI_ENABLED 'false'
        Check 'AI compose: an earlier key is removed' ([string]$env:DOME_TEST_AI_KEY) ''

        # docker-compose.yml passes exactly these seven settings to the api service (docs/design/assistant.md 5.1).
        $composeLines = @(Get-Content -Path $script:ComposeFile | ForEach-Object { $_.Trim() })
        $apiAt = [array]::IndexOf($composeLines, 'api:')
        $nextServiceAt = [array]::IndexOf($composeLines, 'tunnel-app:')
        $expectedAi = @('DOME_AI_ENABLED: ${DOME_TEST_AI_ENABLED:-false}', 'DOME_AI_PROVIDER: anthropic',
            'DOME_AI_ANTHROPIC_API_KEY: ${DOME_TEST_AI_KEY:-}', 'DOME_AI_MONTHLY_CEILING_CENTS: ${DOME_TEST_AI_CEILING_CENTS:-500}',
            'DOME_AI_CACHE_TTL: 1h', 'DOME_AI_DEV_GRANT_INTERPRETATIONS: "300"',
            'DOME_AI_DEV_GRANT_COST_CAP_CENTS: ${DOME_TEST_AI_CEILING_CENTS:-500}')
        foreach ($line in $expectedAi) {
            $at = [array]::IndexOf($composeLines, $line)
            Check ('compose api service has: ' + $line) ($at -gt $apiAt -and $at -lt $nextServiceAt) $true
        }
        Check 'compose has no other DOME_AI_ setting' (@($composeLines | Where-Object { $_ -like 'DOME_AI_*' }).Count) $expectedAi.Count
        Check 'compose default ceiling is the kit default limit' ('${DOME_TEST_AI_CEILING_CENTS:-' + ($script:AiDefaultLimitUsd * 100) + '}' -in
            @($composeLines | ForEach-Object { if ($_ -match '^DOME_AI_MONTHLY_CEILING_CENTS: (.*)$') { $Matches[1] } })) $true
        # Step 1 reads the key file before its first compose call (a refused file stops before test data is
        # cleared or tunnels open) and sets the compose variables before starting DoMe; step 4 runs the function.
        $startServer = [IO.File]::ReadAllText((Join-Path $scriptsDir 'start-server.ps1'))
        $readAt = $startServer.IndexOf('$ai = Read-AiSettings')
        Check 'step 1 reads the AI settings' ($readAt -gt 0) $true
        Check 'step 1 reads them before any compose call' ($readAt -lt $startServer.IndexOf('Invoke-Compose')) $true
        Check 'step 1 sets the AI compose variables before starting DoMe' (
            $startServer.IndexOf('Set-AiComposeVariables $ai') -in ($readAt + 1)..($startServer.IndexOf("Invoke-Compose 'Starting DoMe'"))) $true
        Check 'step 1 repeats the AI line in its summary' ($startServer.IndexOf('Get-AiStatusLine $ai') -gt $startServer.IndexOf("Write-Banner 'DoMe test server is running'")) $true
        Check 'step 4 runs Update-ServerAiSettings' ([IO.File]::ReadAllText((Join-Path $scriptsDir 'apply-ai-settings.ps1')) -match
            '(?s)Invoke-KitMain \{.*Update-ServerAiSettings.*\}') $true
        $ignored = @(Get-Content -Path (Join-Path $realKitDir '.gitignore') | ForEach-Object { $_.Trim() })
        Check 'git ignores ai-key.txt' ('ai-key.txt' -in $ignored) $true
        Check 'git ignores ai-monthly-limit-usd.txt' ('ai-monthly-limit-usd.txt' -in $ignored) $true

        # Step 4 (Update-ServerAiSettings) with Docker and the network replaced by the functions below, defined in
        # the child scope that runs it. $step4 steers them and records what happened. The server's sources are
        # the small repository of the fingerprint checks above.
        $realRepoDir = $script:RepoDir
        $script:RepoDir = $repo
        $serverPrint = Get-SourceFingerprint $repo $script:ServerSources
        $step4 = @{ Invoke = (New-Object System.Collections.Generic.List[string]); Output = (New-Object System.Collections.Generic.List[string])
            Running = "postgres`ndev-idp`napi`n"; Healthy = $true; Public = $true; FailCompose = $false; Env = $null; Stopped = $null }
        function Invoke-Step4 {
            $step4.Invoke.Clear()
            $step4.Output.Clear()
            $step4.Env = $null
            $step4.Stopped = $null
            $shown = & {
                function Assert-Docker { }
                function Get-ComposeOutput([string[]]$Arguments) { $step4.Output.Add($Arguments -join ' '); return $step4.Running }
                function Invoke-Compose([string]$What, [string[]]$Arguments) {
                    $step4.Invoke.Add($Arguments -join ' ')
                    # As Invoke-Native reports a failing docker compose (a build that could not download, say).
                    if ($step4.FailCompose) { Stop-Kit ("$What failed (exit code 1). The lines above say why.") }
                    $step4.Env = @{ App = $env:DOME_TEST_APP_URL; Signin = $env:DOME_TEST_SIGNIN_URL; Client = $env:DOME_TEST_CLIENT_SECRET
                        Session = $env:DOME_TEST_SESSION_SECRET; Passphrase = $env:DOME_TEST_PASSPHRASE; AiEnabled = $env:DOME_TEST_AI_ENABLED
                        AiKey = [string]$env:DOME_TEST_AI_KEY; Ceiling = $env:DOME_TEST_AI_CEILING_CENTS }
                }
                function Wait-Url([string]$Url, [int]$TimeoutSeconds) {
                    if ($Url.StartsWith('http://127.0.0.1:18080/')) { return $step4.Healthy }
                    return $step4.Public
                }
                try { Update-ServerAiSettings } catch { $step4.Stopped = $_.Exception }
            } *>&1 | Out-String
            [void]$script:allShown.Append($shown)
            if ($step4.Stopped) { [void]$script:allShown.Append($step4.Stopped.Message) }
            return $shown
        }
        function Test-Step4Stop { return [bool]($step4.Stopped -and $step4.Stopped.Data.Contains('DoMeKit')) }
        function Get-RecordedSources {
            # current.json's server_sources, or '' when it has none (strict mode refuses a missing property).
            $c = Read-Current
            if ($c -and $c.PSObject.Properties['server_sources']) { return [string]$c.server_sources }
            return ''
        }
        $currentJson = @{ app_url = 'https://app-one.trycloudflare.com'; signin_url = 'https://signin-one.trycloudflare.com'
            passphrase = 'abcd-efgh-jkmn'; mode = 'quick-tunnel'; started_at = '2026-10-11T10:00:00'; server_sources = $serverPrint }
        Remove-Item -Force (Get-CurrentPath) -ErrorAction SilentlyContinue

        [void](Invoke-Step4)
        Check 'step 4 without current.json: stops' (Test-Step4Stop) $true
        Check 'step 4 without current.json: says to run step 1' ($step4.Stopped.Message -match '1 Start test server\.cmd') $true
        Check 'step 4 without current.json: no compose change' $step4.Invoke.Count 0

        Write-Current $currentJson
        $step4.Running = "postgres`ndev-idp`n"
        [void](Invoke-Step4)
        Check 'step 4 with the api stopped: stops' (Test-Step4Stop) $true
        Check 'step 4 with the api stopped: says to run step 1' ($step4.Stopped.Message -match '1 Start test server\.cmd') $true
        Check 'step 4 with the api stopped: no compose change' $step4.Invoke.Count 0
        $step4.Running = "postgres`r`ndev-idp`r`napi`r`n"

        Write-KitFile $keyFile ($sentinel + "`r`n")
        Write-KitFile $limitFile '12'
        $currentBefore = [IO.File]::ReadAllText((Get-CurrentPath))
        $shown = Invoke-Step4
        Check 'step 4: no stop' $step4.Stopped $null
        Check 'step 4: exactly one compose change' ($step4.Invoke -join ' | ') 'up -d --no-deps --force-recreate dev-idp api'
        $allCompose = (@($step4.Invoke) + @($step4.Output)) -join ' | '
        Check 'step 4: never compose down' ($allCompose -match '\bdown\b') $false
        Check 'step 4: never removes volumes' ($allCompose -match '(^|\s)-v(\s|$)|--volumes') $false
        Check 'step 4: never touches a tunnel' ($allCompose -match 'tunnel') $false
        Check 'step 4: the app address from current.json' $step4.Env.App $currentJson.app_url
        Check 'step 4: the sign-in address from current.json' $step4.Env.Signin $currentJson.signin_url
        Check 'step 4: the passphrase stays' $step4.Env.Passphrase $currentJson.passphrase
        Check 'step 4: the persistent session secret' $step4.Env.Session (Get-PersistentSecret 'session-secret.txt' 32)
        Check 'step 4: a new sign-in client secret' ($step4.Env.Client -cmatch '^[0-9a-f]{48}$') $true
        Check 'step 4: AI on' $step4.Env.AiEnabled 'true'
        Check 'step 4: the key reaches compose' ($step4.Env.AiKey -ceq $sentinel) $true
        Check 'step 4: the monthly limit reaches compose' $step4.Env.Ceiling '1200'
        Check 'step 4: says AI is on' ($shown -match [regex]::Escape('AI assistant: on (key file found, monthly limit US$12)')) $true
        Check 'step 4: shows the passphrase' ($shown -match $currentJson.passphrase) $true
        Check 'step 4: the key is not in a compose argument' ($allCompose.Contains($sentinel)) $false
        Check 'step 4, same sources: no rebuild' ($shown -match 'rebuilding|updated too') $false
        Check 'step 4, same sources: current.json unchanged' ([IO.File]::ReadAllText((Get-CurrentPath)) -ceq $currentBefore) $true
        $firstClient = $step4.Env.Client
        [void](Invoke-Step4)
        Check 'step 4: the client secret is new on every run' ($step4.Env.Client -ne $firstClient) $true

        # An older current.json without a passphrase gets a new one, recorded with every other field kept.
        $old = [ordered]@{ app_url = $currentJson.app_url; signin_url = $currentJson.signin_url; mode = 'stable'; server_sources = $serverPrint }
        Write-Current $old
        $shown = Invoke-Step4
        $saved = Read-Current
        Check 'step 4, no passphrase yet: a new one' ([string]$saved.passphrase -cmatch '^[a-z2-9]{4}-[a-z2-9]{4}-[a-z2-9]{4}$') $true
        Check 'step 4, no passphrase yet: compose gets it' $step4.Env.Passphrase ([string]$saved.passphrase)
        Check 'step 4, no passphrase yet: it is shown' ($shown -match [string]$saved.passphrase) $true
        Check 'step 4, no passphrase yet: other fields kept' ('{0}|{1}|{2}|{3}' -f $saved.app_url, $saved.signin_url, $saved.mode, $saved.server_sources) (
            '{0}|{1}|stable|{2}' -f $currentJson.app_url, $currentJson.signin_url, $serverPrint)
        Check 'step 4, no passphrase yet: only recreates' ($step4.Invoke -join ' | ') 'up -d --no-deps --force-recreate dev-idp api'
        Check 'step 4: the key is not in current.json' ([IO.File]::ReadAllText((Get-CurrentPath)).Contains($sentinel)) $false

        # A test server built from other sources than this folder's (started before a newer download was
        # extracted here) is rebuilt, never just recreated from its old image: that would say "AI assistant: on"
        # while the server and the iPhone app have no assistant. The account, pairing and addresses stay.
        $rebuildCall = 'up -d --build --no-deps --force-recreate dev-idp api'
        $older = [ordered]@{ app_url = $currentJson.app_url; signin_url = $currentJson.signin_url; passphrase = $currentJson.passphrase
            mode = 'quick-tunnel'; started_at = $currentJson.started_at; server_sources = 'f00d' }
        Write-Current $older
        $shown = Invoke-Step4
        $saved = Read-Current
        Check 'step 4, newer sources: no stop' $step4.Stopped $null
        Check 'step 4, newer sources: rebuilds dev-idp and api' ($step4.Invoke -join ' | ') $rebuildCall
        Check 'step 4, newer sources: says so' ($shown -match 'This folder has a newer DoMe server; rebuilding it') $true
        Check 'step 4, newer sources: says the account stays' ($shown -match 'your test account and pairing stay') $true
        Check 'step 4, newer sources: says to reopen the iPhone app' ($shown -match 'The iPhone app was updated too') $true
        Check 'step 4, newer sources: recorded' (Get-RecordedSources) $serverPrint
        Check 'step 4, newer sources: other fields kept' ('{0}|{1}|{2}|{3}' -f $saved.app_url, $saved.signin_url, $saved.passphrase, $saved.mode) (
            '{0}|{1}|{2}|quick-tunnel' -f $currentJson.app_url, $currentJson.signin_url, $currentJson.passphrase)
        # Read from the file: PowerShell 7's ConvertFrom-Json turns the date into a DateTime (5.1 keeps the text).
        Check 'step 4, newer sources: started_at kept' ([IO.File]::ReadAllText((Get-CurrentPath)) -match '"started_at":\s*"2026-10-11T10:00:00"') $true
        Check 'step 4, newer sources: the passphrase stays' $step4.Env.Passphrase $currentJson.passphrase
        Check 'step 4, newer sources: the addresses stay' ('{0}|{1}' -f $step4.Env.App, $step4.Env.Signin) ('{0}|{1}' -f $currentJson.app_url, $currentJson.signin_url)
        Check 'step 4, newer sources: AI on' $step4.Env.AiEnabled 'true'
        Check 'step 4, newer sources: the key reaches compose' ($step4.Env.AiKey -ceq $sentinel) $true
        $allCompose = (@($step4.Invoke) + @($step4.Output)) -join ' | '
        Check 'step 4, newer sources: never compose down, volumes or a tunnel' ($allCompose -match '\bdown\b|(^|\s)-v(\s|$)|--volumes|tunnel') $false
        $shown = Invoke-Step4
        Check 'step 4 after a rebuild: only recreates' ($step4.Invoke -join ' | ') 'up -d --no-deps --force-recreate dev-idp api'
        Check 'step 4 after a rebuild: says nothing of a rebuild' ($shown -match 'rebuilding|updated too') $false

        # A current.json from a kit that did not record server_sources yet: rebuilt as well.
        $older.Remove('server_sources')
        Write-Current $older
        $shown = Invoke-Step4
        Check 'step 4, sources unknown: rebuilds' ($step4.Invoke -join ' | ') $rebuildCall
        Check 'step 4, sources unknown: says so' ($shown -match 'started by an older test kit; rebuilding it') $true
        Check 'step 4, sources unknown: recorded' (Get-RecordedSources) $serverPrint

        # The real fingerprint: an edited server source rebuilds; a failed build stops with the running server
        # unchanged and records nothing, so the next run builds again.
        Write-RepoFile 'cloud-api/dome_api/app.py' 'changed again'
        $editedPrint = Get-SourceFingerprint $repo $script:ServerSources
        [void](Invoke-Step4)
        Check 'step 4, edited server source: rebuilds' ($step4.Invoke -join ' | ') $rebuildCall
        Check 'step 4, edited server source: recorded' (Get-RecordedSources) $editedPrint
        Write-RepoFile 'mobile-app/src/lib/assistant.ts' 'export {};'
        $step4.FailCompose = $true
        [void](Invoke-Step4)
        $step4.FailCompose = $false
        Check 'step 4, failed rebuild: stops' (Test-Step4Stop) $true
        Check 'step 4, failed rebuild: says what failed' ($step4.Stopped.Message -match '^Rebuilding DoMe failed') $true
        Check 'step 4, failed rebuild: nothing recorded' (Get-RecordedSources) $editedPrint
        [void](Invoke-Step4)
        Check 'step 4 after a failed rebuild: rebuilds again' ($step4.Invoke -join ' | ') $rebuildCall
        Check 'step 4 after a failed rebuild: then recorded' (Get-RecordedSources) (Get-SourceFingerprint $repo $script:ServerSources)

        # Both at once: no passphrase yet and newer sources.
        Write-Current ([ordered]@{ app_url = $currentJson.app_url; signin_url = $currentJson.signin_url; mode = 'stable'; server_sources = 'f00d' })
        [void](Invoke-Step4)
        $saved = Read-Current
        Check 'step 4, no passphrase and newer sources: rebuilds' ($step4.Invoke -join ' | ') $rebuildCall
        Check 'step 4, no passphrase and newer sources: both recorded' ('{0}|{1}|{2}' -f ([string]$saved.passphrase -cmatch '^[a-z2-9]{4}-[a-z2-9]{4}-[a-z2-9]{4}$'),
            $saved.server_sources, $saved.mode) ('True|{0}|stable' -f (Get-SourceFingerprint $repo $script:ServerSources))

        Remove-Item -Force $keyFile
        $shown = Invoke-Step4
        Check 'step 4 without a key file: AI off' $step4.Env.AiEnabled 'false'
        Check 'step 4 without a key file: no key reaches compose' $step4.Env.AiKey ''
        Check 'step 4 without a key file: says AI is off' ($shown -match [regex]::Escape("AI assistant: off (no testkit\ai-key.txt")) $true

        Write-KitFile $keyFile ('two words ' + $sentinel)
        [void](Invoke-Step4)
        Check 'step 4 with a refused key file: stops' (Test-Step4Stop) $true
        Check 'step 4 with a refused key file: no compose change' $step4.Invoke.Count 0
        Write-KitFile $keyFile $sentinel

        $step4.Public = $false
        $shown = Invoke-Step4
        Check 'step 4, public address down: no stop' $step4.Stopped $null
        Check 'step 4, public address down: says so' ($shown -match 'Not reachable at') $true
        $step4.Public = $true
        $step4.Healthy = $false
        [void](Invoke-Step4)
        Check 'step 4, DoMe does not start: stops' (Test-Step4Stop) $true
        Check 'step 4, DoMe does not start: points to the log' ($step4.Stopped.Message -match 'docker logs dome-test-api-1') $true
        $step4.Healthy = $true

        # Nothing the kit showed or wrote holds the key.
        Check 'AI: the key was never shown' ($script:allShown.ToString().Contains($sentinel)) $false
        $written = @(Get-ChildItem -Path $script:StateDir -Recurse -File | Where-Object { $_.FullName -ne $keyFile } |
                Where-Object { [IO.File]::ReadAllText($_.FullName).Contains($sentinel) } | ForEach-Object { $_.Name })
        Check 'AI: the key was never written to a file' ($written -join ', ') ''
    } finally {
        $script:KitDir = $realKitDir
        if (Get-Variable -Name realRepoDir -ErrorAction SilentlyContinue) { $script:RepoDir = $realRepoDir }
        foreach ($name in @('DOME_TEST_APP_URL', 'DOME_TEST_SIGNIN_URL', 'DOME_TEST_CLIENT_SECRET', 'DOME_TEST_SESSION_SECRET',
                'DOME_TEST_PASSPHRASE', 'DOME_TEST_AI_ENABLED', 'DOME_TEST_AI_KEY', 'DOME_TEST_AI_CEILING_CENTS')) {
            [Environment]::SetEnvironmentVariable($name, $null)
        }
    }
} finally {
    Remove-Item -Recurse -Force $script:StateDir -ErrorAction SilentlyContinue
}

Write-Host ''
Write-Host ("{0} failed" -f $script:failures)
exit $script:failures
