# Register (or re-register) the "Oshinei Task Watchdog" scheduled task.
# (ASCII only: Windows PowerShell 5.1 reads a BOM-less .ps1 as ANSI.)
#
#   powershell -NoProfile -ExecutionPolicy Bypass -File scripts\register_task_watchdog.ps1
#   powershell -NoProfile -ExecutionPolicy Bypass -File scripts\register_task_watchdog.ps1 -Remove
#
# Why a script: the other Oshinei tasks were created by hand in the Task Scheduler UI, so a
# new PC (or a moved folder) has to rebuild them from memory. This one can be rebuilt by
# running one line, and the path comes from where this file sits - nothing is hardcoded.
#
# Runs as the logged-on user (Interactive), like every other Oshinei task: the toast needs a
# desktop session, and no password is stored. 11:15 = after the last morning job (10:45);
# 17:00 = after the 16:00 live sync; at logon = when someone sits down at the PC, which is
# also when the toast will actually be seen.
# 30-minute limit: the watchdog may re-run DB Backup and Feedback Sync (one at a time, up to
# 6 minutes each) before it reports.
param([switch]$Remove)

$name = 'Oshinei Task Watchdog'
$repo = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$bat  = Join-Path $repo 'Task Watchdog.bat'

if ($Remove) {
    Unregister-ScheduledTask -TaskName $name -Confirm:$false -ErrorAction SilentlyContinue
    "removed: $name"
    exit 0
}
if (-not (Test-Path -LiteralPath $bat)) { "ERROR: not found: $bat"; exit 1 }

# same action shape as the hand-made tasks: cmd.exe /c ""<bat>"" auto
$action   = New-ScheduledTaskAction -Execute 'cmd.exe' -Argument ('/c ""' + $bat + '"" auto')
$triggers = @(
    (New-ScheduledTaskTrigger -Daily -At '11:15'),
    (New-ScheduledTaskTrigger -Daily -At '17:00')
)
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 30) -MultipleInstances IgnoreNew
$principal = New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\$env:USERNAME" -LogonType Interactive -RunLevel Limited

Register-ScheduledTask -TaskName $name -Action $action -Trigger $triggers -Settings $settings -Principal $principal `
    -Description 'Flags every Oshinei scheduled task that failed, missed its slot, went stale or is stuck. backend\task_watchdog.py' `
    -Force | Out-Null

# The at-logon trigger needs elevation on some PCs. Add it when allowed; the two daily
# triggers above are enough without it.
try {
    $t = Get-ScheduledTask -TaskName $name
    $logon = New-ScheduledTaskTrigger -AtLogOn -User "$env:USERDOMAIN\$env:USERNAME"
    $logon.Delay = 'PT3M'
    Set-ScheduledTask -TaskName $name -Trigger (@($t.Triggers) + $logon) -ErrorAction Stop | Out-Null
    $logonNote = 'at logon (+3 min)'
} catch {
    $logonNote = 'at logon NOT added (needs an elevated PowerShell) - daily triggers are in place'
}

$t = Get-ScheduledTask -TaskName $name
"registered: $name"
"  runs    : $bat"
"  when    : 11:15 and 17:00 daily; $logonNote"
"  state   : $($t.State)"
