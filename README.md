<div align="center">

# Arca

[![Python](https://img.shields.io/badge/Python-3.11+-3776AB?style=flat-square&logo=python&logoColor=white)](https://www.python.org/)
[![Textual](https://img.shields.io/badge/TUI-Textual-5A4FCF?style=flat-square)](https://textual.textualize.io/)
[![rclone](https://img.shields.io/badge/rclone-backend--agnostic-3B5?style=flat-square)](https://rclone.org/)
[![Platform](https://img.shields.io/badge/platform-Windows-0078D6?style=flat-square&logo=windows&logoColor=white)](#requirements)
[![License](https://img.shields.io/badge/license-MIT-4A4E6D?style=flat-square)](LICENSE)

### Back up the folders that matter, skip the junk, lose nothing.

A terminal interface for `rclone`: pick folders, watch the backup live, and when
something breaks, read what happened and how to fix it.

</div>

---

## Why

Consumer sync clients back up *everything*. Point one at a folder of code projects
and it happily uploads every `node_modules`, filling a free plan with files that can
be regenerated in seconds. There is no "sync this, but not that" switch.

`rclone` has that switch and talks to every provider, but it is a command line with
no memory and errors buried in a log. Arca is the interface on top.

## What it does

- **A menu, not a manual.** It opens on a home menu: arrows move, Enter opens,
  Esc goes back. Enter on a folder opens its own screen: back up, dry run, pause,
  and its files.
- **Choose folders, don't type paths.** Press `a`, explore with the arrows and
  Enter (they only open and close folders), tick every folder you want with space
  or Tab, then add them all from the line under the tree (or `Ctrl+S`). Each folder
  becomes its own sub-folder on the destination.
- **Brings your old setup along.** Import reads an earlier PowerShell backup script and the
  folders already on the destination (or wherever you paste), matches them to local folders (`Fotos` is
  `Pictures`), and keeps their names so the next run continues instead of
  uploading everything again.
- **Accounts in one place.** Every rclone account on one line with its state and
  an icon: `✕` disconnects, `↻` connects. Adding Google Drive, OneDrive, Dropbox
  or Google Photos is a sign-in in the browser: no `rclone config`, no console.
- **Shows how it's going.** Metrics: space on the destination, success rate per
  folder, last good copy, last error, the last 14 days at a glance.
- **Shows the run live.** Progress and speed per folder; a failed run says why
  and how to fix it.
- **You pick the files.** Each folder lists every file in it, the ones the filters
  leave out too, with a box per line: tick what gets copied, untick what stays,
  and the total (files and size, against the whole folder) follows every tick.
  Each folder shows its size, so the big ones stand out. Save, and each folder keeps
  its own choices.
- **Never fails silently.** The destination is checked before anything runs. An
  expired Google login, a missing remote, a full drive or a moved folder each get a
  plain cause and the fix; `x` reconnects an expired login in place.
- **Never loses a file.** Every backup is an `rclone sync` with `--backup-dir`: the
  destination mirrors the folder, but anything overwritten or deleted is archived to
  `_versions/<timestamp>/` first.
- **Filters like a `.gitignore`.** Dependencies, build output and OS cruft stay
  local. Press `f` to edit the list.
- **Runs itself.** Pick automatic backups under the menu: when you sign in to
  Windows, or every day at 21:00 (a missed day runs when the computer is on).
  No window opens; the interface shows what happened the next time it opens.
- **Any backend.** Google Drive, OneDrive, Backblaze B2 or an external disk.
- **English and Spanish, and themes.** Settings sit under the menu and change
  with the arrows: language, theme, automatic backups.

## Requirements

- Windows, Python 3.11+ and [uv](https://docs.astral.sh/uv/).
- [rclone](https://rclone.org/downloads/) on `PATH` (`winget install Rclone.Rclone`),
  An account can be added from Arca (Accounts), or use a local folder as destination.

## Usage

```powershell
uv run arca                  # open the interface
uv run arca run              # back up every enabled folder, no interface
uv run arca run --dry-run    # show what would change, change nothing
```

Or install the `arca` command once: `uv tool install --editable .`

Everything is in the menu. In the folders view, the keys are shortcuts:

| Key | Action |
|---|---|
| `Esc` | back to the menu |
| `Enter` | what can be done with the selected folder |
| `→` or `↓` / `Space` or `Tab` | into the folder's files / tick or untick one |
| `Ctrl+S` | save the ticks |
| `a` / `Del` or `d` | add / delete a folder from Arca (asks first; its files stay) |
| `r` / `R` | back up the selected folder / all of them |
| `n` | dry run |
| `s` | stop |
| `m` | choose the destination |
| `f` | edit the exclude filters |
| `x` | sign in again (expired or disconnected) |
| `?` | every key |

Settings, filters and run history live in `%APPDATA%\arca\`. Architecture and
working notes: [CLAUDE.md](CLAUDE.md).

## The original script

Arca grew from a PowerShell backup script. That script is a personal setup, so it
is not part of this repository; Import brings a script like it into Arca, and
choosing automatic backups replaces its scheduled task.

## Credits

The interface follows the design of
[Bitácora](https://github.com/baltazarandersson/bitacora) by Baltazar Andersson.

## License

[MIT](LICENSE) &copy; 2026 [Luis Barral](https://barral.dev).

<div align="center">

**[Luis Barral](https://barral.dev)** &middot; MIT

<sub>co-assisted by Claude</sub>

</div>
