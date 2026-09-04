# Archive the old OneDrive working copy once nothing holds it open.
#
# The repo moved to C:\Users\ASUS\Oshinei on 2026-07-20 (docs/MIGRATE_OFF_ONEDRIVE.md);
# the last step, renaming the old folder so it stops looking live, fails with
# "being used by another process" as long as a Claude session that was OPENED
# there is still running (its harness holds the folder). This retries in the
# background until the rename succeeds or the deadline passes, and logs either way.
#
#   powershell -NoProfile -ExecutionPolicy Bypass -File scripts\archive_onedrive_copy.ps1
#
param(
    [string]$Old = "C:\Users\ASUS\OneDrive\Daiki\2026\Cost Analysis",
    [string]$NewName = "_ARCHIVED_Cost Analysis",
    [int]$Hours = 12,
    [int]$EverySeconds = 60
)
$log = Join-Path $PSScriptRoot "..\backend\logs\archive_onedrive_copy.log"
$deadline = (Get-Date).AddHours($Hours)
"$(Get-Date -Format s) start: '$Old' -> '$NewName' (until $deadline)" | Out-File -Append -Encoding utf8 $log
while ((Get-Date) -lt $deadline) {
    if (-not (Test-Path -LiteralPath $Old)) {
        "$(Get-Date -Format s) already gone — nothing to do" | Out-File -Append -Encoding utf8 $log
        exit 0
    }
    try {
        Rename-Item -LiteralPath $Old -NewName $NewName -ErrorAction Stop
        "$(Get-Date -Format s) RENAMED -> $NewName" | Out-File -Append -Encoding utf8 $log
        exit 0
    } catch {
        Start-Sleep -Seconds $EverySeconds
    }
}
"$(Get-Date -Format s) gave up after $Hours h — rename it from File Explorer (close Claude sessions opened in the old folder first)" | Out-File -Append -Encoding utf8 $log
exit 1
