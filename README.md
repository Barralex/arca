<div align="center">

# rclone-backup-windows

[![PowerShell](https://img.shields.io/badge/PowerShell-5.1+-5391FE?style=flat-square&logo=powershell&logoColor=white)](https://learn.microsoft.com/powershell/)
[![rclone](https://img.shields.io/badge/rclone-backend--agnostic-3B5?style=flat-square)](https://rclone.org/)
[![Platform](https://img.shields.io/badge/platform-Windows-0078D6?style=flat-square&logo=windows&logoColor=white)](#requirements)
[![License](https://img.shields.io/badge/license-MIT-4A4E6D?style=flat-square)](LICENSE)

### Back up the folders that matter, skip the junk, lose nothing.

A scheduled `rclone` wrapper that mirrors chosen local folders to any cloud remote,
honours a `.gitignore`-style exclude list, and keeps a recoverable copy of anything
it overwrites or deletes.

</div>

---

## Why

Consumer sync clients back up *everything*. Point one at a folder of code projects
and it happily uploads every `node_modules`, filling a free plan with files that can
be regenerated in seconds. There is no "sync this, but not that" switch.

`rclone-backup-windows` is that switch. One filter file decides what never leaves the machine;
everything else is a clean, scheduled mirror to the cloud.

## What it does

- **Filters like a `.gitignore`.** Dependencies, build output and OS cruft stay
  local. Excludes live in `filter.txt`, one line each.
- **Never loses a file.** Every run is an `rclone sync` with `--backup-dir`: the
  remote mirrors the source, but anything overwritten or deleted is archived to
  `_versions/<timestamp>/` first.
- **Runs itself.** Built for Task Scheduler: coloured summary when run by hand,
  non-zero exit code and a log when run headless.
- **Backend-agnostic.** Google Drive today, Backblaze B2 or an external disk
  tomorrow, by changing one line. rclone does the talking.

## Requirements

- Windows with PowerShell 5.1+.
- [rclone](https://rclone.org/downloads/) on `PATH`, with a remote configured
  (`rclone config`).

## Usage

```powershell
# edit the config block at the top of backup.ps1, then:
.\backup.ps1            # run the backup
.\backup.ps1 -DryRun    # preview, transfer nothing
```

Schedule it (daily, 21:00):

```powershell
schtasks /Create /TN "rclone-backup-windows" /SC DAILY /ST 21:00 /F ^
  /TR "powershell -NoProfile -ExecutionPolicy Bypass -File \"%CD%\backup.ps1\""
```

## Configuration

Two things, both at the top of `backup.ps1`:

| Setting | What it is |
|---|---|
| `$Remote` | Any rclone remote, `remote:path` form (e.g. `gdrive:Backups`). |
| `$Jobs`   | One entry per folder: `@{ Name = 'Documents'; Source = '...' }`. |

What to skip lives in `filter.txt`. Architecture and working notes: [CLAUDE.md](CLAUDE.md).

## License

[MIT](LICENSE) &copy; 2026 [Luis Barral](https://barral.dev).

<div align="center">

**[Luis Barral](https://barral.dev)** &middot; MIT

<sub>co-assisted by Claude</sub>

</div>
