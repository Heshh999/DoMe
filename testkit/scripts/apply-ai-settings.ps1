# DoMe test kit, step 4 (optional): switch the AI assistant on or off on the running test server after
# creating, changing or deleting testkit\ai-key.txt, without new addresses and without losing the test
# account, the linked PC or the pairing; a running server older than this folder is rebuilt first. Run
# through "4 Apply AI settings.cmd".
# Windows PowerShell 5.1 compatible; ASCII only.
. "$PSScriptRoot\common.ps1"

$exitCode = Invoke-KitMain {
    Write-Banner 'DoMe test server - apply the AI assistant settings'
    Update-ServerAiSettings
}
exit $exitCode
