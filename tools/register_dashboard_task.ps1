<#
.SYNOPSIS
  (Re)registers the Scheduled Task that keeps the Tower Pilot dashboard running.

.DESCRIPTION
  The dashboard supervises the gem collector and nothing else restarts it, so it must come back by itself.
  The task starts at logon for the current user and repeats every 5 minutes. MultipleInstances = IgnoreNew makes
  every repetition a no-op while a dashboard is running; a dead dashboard is started again within 5 minutes.
  A start that loses the port, or that finds backend\logs\_dashboard\HOLD, exits at once (backend\runtime\dashboard_start.py),
  so a repetition can never produce a second dashboard or start a tree that is being landed.

  Re-running replaces the definition in place (Register-ScheduledTask -Force). A running dashboard is not stopped
  by that. This script never starts, stops or ends the task.

.PARAMETER TaskName
  Task to write. Default "TowerPilot Dashboard". Use another name to test the script on a scratch task.

.PARAMETER Execute
.PARAMETER Argument
  The task's action. The defaults run the dashboard as the tray app, under a hidden console: conhost.exe --headless
  "<repo>\.venv\Scripts\python.exe" "<repo>\tools\portable_launcher.py" --background. That is Start Tower Pilot's
  notification-area icon (click it to open the dashboard) without a browser tab or dialog on any repetition; output
  goes to backend\logs\launcher.log. Quit on the icon stops the dashboard until the next repetition. Override both only for a
  scratch task whose action is harmless (for example cmd.exe /c exit 0).

.PARAMETER RepeatMinutes
  Repetition interval. Default 5.

.PARAMETER Remove
  Delete the named task instead of registering it.

.EXAMPLE
  .\tools\register_dashboard_task.ps1 -WhatIf
.EXAMPLE
  .\tools\register_dashboard_task.ps1 -TaskName "TowerPilot Scratch" -Execute cmd.exe -Argument "/c exit 0"
  .\tools\register_dashboard_task.ps1 -TaskName "TowerPilot Scratch" -Remove
#>
[CmdletBinding(SupportsShouldProcess = $true)]
param(
    [string]$TaskName = 'TowerPilot Dashboard',
    [string]$Execute,
    [string]$Argument,
    [ValidateRange(1, 1440)][int]$RepeatMinutes = 5,
    [switch]$Remove
)

$ErrorActionPreference = 'Stop'
$repo = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path

if ($Remove) {
    if ($PSCmdlet.ShouldProcess($TaskName, 'Unregister-ScheduledTask')) {
        Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
        Write-Host "Removed task '$TaskName'."
    }
    return
}

if (-not $Execute) {
    $python = Join-Path $repo '.venv\Scripts\python.exe'
    $launcher = Join-Path $repo 'tools\portable_launcher.py'
    foreach ($path in @($python, $launcher)) {
        if (-not (Test-Path -LiteralPath $path)) { throw "Missing $path - create the .venv first (see README)." }
    }
    $Execute = Join-Path $env:SystemRoot 'System32\conhost.exe'
    $Argument = '--headless "{0}" "{1}" --background' -f $python, $launcher
}

$user = [Security.Principal.WindowsIdentity]::GetCurrent().Name

$action = New-ScheduledTaskAction -Execute $Execute -Argument $Argument -WorkingDirectory $repo

# Two triggers. The logon trigger carries the repetition after each logon; a "once" trigger starting now carries
# the same repetition from registration on, so the watchdog works before the next logon too. No end date.
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $user
$repeat = New-ScheduledTaskTrigger -Once -At (Get-Date) -RepetitionInterval (New-TimeSpan -Minutes $RepeatMinutes)
$trigger.Repetition = $repeat.Repetition
$triggers = @($trigger, $repeat)

$settings = New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew -ExecutionTimeLimit ([TimeSpan]::Zero) `
    -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -Priority 4
$principal = New-ScheduledTaskPrincipal -UserId $user -LogonType Interactive -RunLevel Limited

if ($PSCmdlet.ShouldProcess($TaskName, "Register-ScheduledTask: now and at logon, every $RepeatMinutes min, IgnoreNew, runs: $Execute $Argument")) {
    $null = Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $triggers -Settings $settings `
        -Principal $principal -Description 'Tower Pilot dashboard: starts at logon and every few minutes; a running instance is left alone.' -Force
    $task = Get-ScheduledTask -TaskName $TaskName
    $rep = $task.Triggers[0].Repetition
    Write-Host ("Registered '{0}': {1} {2} (cwd {3})" -f $TaskName, $task.Actions[0].Execute, $task.Actions[0].Arguments, $task.Actions[0].WorkingDirectory)
    Write-Host ("  trigger {0}, repeat every {1}, duration '{2}', MultipleInstances {3}, user {4}" -f `
        $task.Triggers[0].CimClass.CimClassName, $rep.Interval, $rep.Duration, $task.Settings.MultipleInstances, $task.Principal.UserId)
}
