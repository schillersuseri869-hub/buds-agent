# print_client/install_task.ps1
# Run once as Administrator to register BUDS Print Client as a Windows scheduled task.
# The task starts automatically when any user logs in.

$ErrorActionPreference = "Stop"

$pythonExe = (Get-Command pythonw.exe -ErrorAction SilentlyContinue)
if (-not $pythonExe) {
    $pythonExe = (Get-Command python.exe -ErrorAction Stop)
}
$pythonPath = $pythonExe.Path

$scriptDir  = Split-Path -Parent $MyInvocation.MyCommand.Path
$scriptPath = Join-Path $scriptDir "print_client.py"
$envFile    = Join-Path $scriptDir ".env"

if (-not (Test-Path $envFile)) {
    Write-Error ".env file not found at $envFile. Copy .env.example to .env and fill in values."
    exit 1
}

$action = New-ScheduledTaskAction `
    -Execute $pythonPath `
    -Argument "`"$scriptPath`"" `
    -WorkingDirectory $scriptDir

$trigger = New-ScheduledTaskTrigger -AtLogOn

$settings = New-ScheduledTaskSettingsSet `
    -MultipleInstances IgnoreNew `
    -ExecutionTimeLimit (New-TimeSpan -Seconds 0) `
    -RestartCount 3 `
    -RestartInterval (New-TimeSpan -Minutes 1)

$principal = New-ScheduledTaskPrincipal `
    -GroupId "BUILTIN\Administrators" `
    -RunLevel Highest

Register-ScheduledTask `
    -TaskName "BUDS Print Client" `
    -Action $action `
    -Trigger $trigger `
    -Settings $settings `
    -Principal $principal `
    -Force | Out-Null

Write-Host "Task 'BUDS Print Client' registered successfully."
Write-Host "It will start automatically when any administrator logs in."
Write-Host "To start it now: Start-ScheduledTask -TaskName 'BUDS Print Client'"
