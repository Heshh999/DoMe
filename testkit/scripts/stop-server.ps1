# DoMe test kit: stop the test service. Run through "3 Stop test server.cmd".
# Windows PowerShell 5.1 compatible; ASCII only.
param(
    [switch]$DeleteData
)
. "$PSScriptRoot\common.ps1"

$exitCode = Invoke-KitMain {
    Write-Banner 'Stop the DoMe test server'
    Assert-Docker
    if (-not $DeleteData) {
        Write-Note 'Your test account, linked PC and paired phones are kept for next time.'
        $answer = Read-Host '   Delete ALL test data instead (start over completely)? [y/N]'
        $DeleteData = [bool]($answer -and $answer.Trim().ToLower().StartsWith('y'))
    }
    $arguments = @('--profile', 'quick-tunnel', 'down')
    if ($DeleteData) { $arguments += '-v' }
    Invoke-Compose 'Stopping DoMe' $arguments
    $current = Get-CurrentPath
    if (Test-Path $current) { Remove-Item -Force $current }
    Write-Ok 'stopped'
    if ($DeleteData) {
        Write-Ok 'test data deleted'
        Write-Note 'This PC still remembers its old link. To start over on the PC too, quit DoMe from the tray'
        Write-Note 'icon and delete the folder %LOCALAPPDATA%\DoMe (README, "Start over").'
    }
    Write-Note 'If DoMe is still running in the tray, right-click its icon and choose Quit DoMe.'
}
exit $exitCode
