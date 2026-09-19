#Requires -Version 5.1
<#
.SYNOPSIS
    Filtered backup of local folders to an rclone remote, built to run unattended.
.DESCRIPTION
    Mirrors each folder in $Jobs to a cloud remote with rclone, skipping anything
    matched by filter.txt (dependencies, build output, OS cruft). Overwritten and
    deleted files are moved to a dated _versions folder on the remote instead of
    being lost, so a bad local delete is always recoverable. Prints a console
    summary and exits non-zero if any job fails, so a scheduler can detect it.
.PARAMETER DryRun
    Show what would change without transferring anything (rclone --dry-run).
.EXAMPLE
    .\backup.ps1
.EXAMPLE
    .\backup.ps1 -DryRun
#>
param([switch]$DryRun)

# =================== configuration ===================
# Edit these two. $Remote is any rclone remote (run `rclone config` once).
$Remote = 'gdrive:Backups'

$Jobs = @(
    @{ Name = 'Pictures';  Source = "$env:USERPROFILE\OneDrive\Pictures"  }
    @{ Name = 'Documents'; Source = "$env:USERPROFILE\OneDrive\Documents" }
)
# =====================================================

$here   = $PSScriptRoot
$filter = Join-Path $here 'filter.txt'
$log    = Join-Path $here 'backup.log'
$stamp  = Get-Date -Format 'yyyy-MM-dd_HHmm'

# ANSI palette (built at runtime, so the file stays pure ASCII)
$e = [char]27
$R = "$e[0m"; $B = "$e[1m"
$cyan = "$e[96m"; $grn = "$e[92m"; $red = "$e[91m"; $gry = "$e[90m"; $mag = "$e[95m"; $yel = "$e[93m"

try { Clear-Host } catch { }
Write-Host ""
Write-Host "$cyan$B  +==============================================+$R"
Write-Host "$cyan$B  |            D R I V E   B A C K U P            |$R"
Write-Host "$cyan$B  +==============================================+$R"
$mode = if ($DryRun) { " $yel(dry run)$R" } else { "" }
Write-Host "$gry   $(Get-Date -Format 'ddd dd MMM yyyy  HH:mm')   ->  $Remote$R$mode"
Write-Host ""

if (-not (Get-Command rclone -ErrorAction SilentlyContinue)) {
    Write-Host "$red  rclone not found on PATH. Install it and run 'rclone config' first.$R"
    exit 1
}

$common = @('--filter-from', $filter, '--log-file', $log, '--log-level', 'INFO', '-P')
if ($DryRun) { $common += '--dry-run' }

$fail = 0
foreach ($j in $Jobs) {
    Write-Host "$mag$B  >>> $($j.Name)$R $gry$($j.Source)$R"
    if (-not (Test-Path $j.Source)) {
        Write-Host "$yel  [SKIP]  source not found$R`n"
        continue
    }
    $dest    = "$Remote/$($j.Name)"
    $archive = "$Remote/_versions/$stamp/$($j.Name)"
    rclone sync $j.Source $dest --backup-dir $archive @common
    if ($LASTEXITCODE -eq 0) {
        Write-Host "$grn  [ OK ]  $($j.Name) up to date$R`n"
    } else {
        Write-Host "$red  [FAIL]  $($j.Name)  (exit $LASTEXITCODE, see log)$R`n"
        $fail++
    }
}

Write-Host "$cyan$B  --  summary  ----------------------------------$R"
$fc = if ($fail) { $red } else { $grn }
Write-Host ("   {0} job(s)  |  {1}{2} failed{3}" -f $Jobs.Count, $fc, $fail, $R)
Write-Host "$gry   log: $log$R`n"
exit $fail
