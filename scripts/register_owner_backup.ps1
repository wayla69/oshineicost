# Register (or re-register) the "Oshinei Owner Data Backup" scheduled task.
# (ASCII only: Windows PowerShell 5.1 reads a BOM-less .ps1 as ANSI.)
#
#   powershell -NoProfile -ExecutionPolicy Bypass -File scripts\register_owner_backup.ps1
#   powershell -NoProfile -ExecutionPolicy Bypass -File scripts\register_owner_backup.ps1 -Remove
#
# Runs "Backup Owner Data.bat auto" every day at 02:30 as the logged-on user (Interactive), like
# every other Oshinei task: no password is stored. StartWhenAvailable = if the PC was off at
# 02:30 it runs when the PC is next on. The task name starts with "Oshinei", so the task
# watchdog (backend\task_watchdog.py) already flags it when it fails, misses its slot or goes stale.
# 30-minute limit: a normal run takes well under a minute.
param([switch]$Remove)

$name = 'Oshinei Owner Data Backup'
$repo = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$bat  = Join-Path $repo 'Backup Owner Data.bat'

if ($Remove) {
    Unregister-ScheduledTask -TaskName $name -Confirm:$false -ErrorAction SilentlyContinue
    "removed: $name"
    exit 0
}
if (-not (Test-Path -LiteralPath $bat)) { "ERROR: not found: $bat"; exit 1 }

$action   = New-ScheduledTaskAction -Execute 'cmd.exe' -Argument ('/c ""' + $bat + '"" auto')
$trigger  = New-ScheduledTaskTrigger -Daily -At '02:30'
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 30) -MultipleInstances IgnoreNew
$principal = New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\$env:USERNAME" -LogonType Interactive -RunLevel Limited

Register-ScheduledTask -TaskName $name -Action $action -Trigger $trigger -Settings $settings -Principal $principal `
    -Description 'Nightly off-site copy of the live app owner-data tables (users, BoM Center, reconciliation, feedback ...) to Oshinei_backups. scripts\backup_owner_tables.py' `
    -Force | Out-Null

$t = Get-ScheduledTask -TaskName $name
"registered: $name"
"  runs    : $bat"
"  when    : 02:30 daily (catches up when the PC was off)"
"  state   : $($t.State)"
