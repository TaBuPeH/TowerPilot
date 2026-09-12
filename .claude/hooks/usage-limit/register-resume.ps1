# Usage-limit guard "alarm" (OB-736): registers (or with -Cleanup removes) a
# one-shot Windows scheduled task that resumes a paused Claude Code session
# after the usage window resets.
#
# What this script ACTUALLY does:
#   - Reads the snapshot statusline.js maintains under ~/.claude/usage-limit/
#     (state-<SessionId>.json, falling back to state.json) and picks the LATEST
#     resets_at among windows currently >= 95% — resuming after the 5h reset is
#     pointless if the 7d window is also spent. Falls back to the five-hour
#     reset, then to "now", so the task always lands DelayMinutes after the
#     chosen anchor.
#   - Registers task "ClaudeUsageResume-<sid8>" (current user, no elevation)
#     whose action is a cmd.exe wrapper (so the claude .cmd/.exe shim resolves)
#     running: claude --resume <SessionId> -p "<resume prompt>" from -ProjectDir,
#     appending output to resume-<sid8>.log in the state dir. The resume prompt
#     tells the session to read its resume brief, restart tasking, and run this
#     script with -Cleanup to remove the task.
param(
  [Parameter(Mandatory = $true)][string]$SessionId,
  [Parameter(Mandatory = $true)][string]$ProjectDir,
  [int]$DelayMinutes = 5,
  [switch]$Cleanup
)
$ErrorActionPreference = 'Stop'

$stateDir = Join-Path $env:USERPROFILE '.claude\usage-limit'
$sid8 = $SessionId.Substring(0, [Math]::Min(8, $SessionId.Length))
$taskName = "ClaudeUsageResume-$sid8"

if ($Cleanup) {
  try { Unregister-ScheduledTask -TaskName $taskName -Confirm:$false -ErrorAction Stop } catch {}
  Write-Host "Removed scheduled task $taskName (if it existed)."
  exit 0
}

# Resolve the reset anchor from the per-session state file (global fallback).
$stateFile = Join-Path $stateDir "state-$SessionId.json"
if (-not (Test-Path $stateFile)) { $stateFile = Join-Path $stateDir 'state.json' }
$resetUnix = 0
if (Test-Path $stateFile) {
  $s = Get-Content $stateFile -Raw | ConvertFrom-Json
  $candidates = @()
  if ($s.fiveHourPct -ge 95 -and $s.fiveHourResetsAt) { $candidates += [long]$s.fiveHourResetsAt }
  if ($s.sevenDayPct -ge 95 -and $s.sevenDayResetsAt) { $candidates += [long]$s.sevenDayResetsAt }
  if ($candidates.Count -eq 0 -and $s.fiveHourResetsAt) { $candidates += [long]$s.fiveHourResetsAt }
  if ($candidates.Count -gt 0) { $resetUnix = ($candidates | Measure-Object -Maximum).Maximum }
}
$nowUnix = [DateTimeOffset]::UtcNow.ToUnixTimeSeconds()
if ($resetUnix -le $nowUnix) { $resetUnix = $nowUnix }
$resumeAt = [DateTimeOffset]::FromUnixTimeSeconds($resetUnix).LocalDateTime.AddMinutes($DelayMinutes)

$brief = Join-Path $stateDir "resume-brief-$SessionId.md"
$resumeLog = Join-Path $stateDir "resume-$sid8.log"
$selfPath = $MyInvocation.MyCommand.Path
$prompt = "Usage window has reset. Resume orchestration: read the resume brief at $brief if it exists, collect finished agent outputs from their files, and restart tasking where you left off. Also run: powershell -ExecutionPolicy Bypass -File '$selfPath' -SessionId $SessionId -ProjectDir '$ProjectDir' -Cleanup"
$cmdLine = "/c claude --resume $SessionId -p `"$prompt`" >> `"$resumeLog`" 2>&1"
$action = New-ScheduledTaskAction -Execute 'cmd.exe' -Argument $cmdLine -WorkingDirectory $ProjectDir
$trigger = New-ScheduledTaskTrigger -Once -At $resumeAt
Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger -Force | Out-Null
Write-Host "Registered $taskName -> resumes session $sid8... at $resumeAt (window reset + $DelayMinutes min). Log: $resumeLog"
