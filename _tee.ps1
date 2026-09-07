<#
  Oshinei - run a .bat so its output goes to BOTH the console and a log file.

  WHY: cmd cannot split a stream. Plain ">> log" made the scheduled task's
  window blank, and on 2026-07-29 that blank window got closed mid-run - the
  task died with 0xC000013A (STATUS_CONTROL_C_EXIT) right at the last step of
  the Fly push. Logging must not cost us the on-screen progress.

  The bat re-enters itself through this script with OSHINEI_LOGGED=1 set, so
  the child skips the wrapper and just runs.

  Two details that matter:
  * "2>&1" lives INSIDE the cmd /c string, not on the PowerShell side. In
    PS 5.1, redirecting a native command's stderr wraps every line in an
    ErrorRecord (NativeCommandError) and flips $?, which would corrupt the log.
  * exit $LASTEXITCODE - ForEach-Object/Add-Content are cmdlets, not native
    commands, so $LASTEXITCODE still carries cmd's real code. Task Scheduler's exit code is
    the only failure signal we get; a pipeline must not swallow it.
#>
param(
    [Parameter(Mandatory = $true)][string] $Bat,
    [Parameter(Mandatory = $true)][string] $Log,
    [string] $ArgLine = ''
)

$dir = Split-Path -Parent $Log
if (-not (Test-Path $dir)) { New-Item -ItemType Directory -Path $dir -Force | Out-Null }

# The sync prints UTF-8 (Thai branch names, → and · glyphs). PowerShell decodes a
# native command's output with the console codepage - OEM 437/874 under Task
# Scheduler - so Thai reached the screen and the log as "α╕¬α╕▓..." mojibake
# (2026-09-07). Decode as UTF-8; the log is written as UTF-8 too, so one
# encoding covers the whole file instead of the old UTF-8/UTF-16 mix.
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$OutputEncoding = [System.Text.Encoding]::UTF8

& cmd.exe /c "`"$Bat`" $ArgLine 2>&1" | ForEach-Object {
    $_                                       # screen
    Add-Content -LiteralPath $Log -Value $_ -Encoding UTF8   # log, line by line
}

if ($null -eq $LASTEXITCODE) { exit 1 }
exit $LASTEXITCODE
