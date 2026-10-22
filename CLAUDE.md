# CLAUDE.md

Working guide for this repository. Read it before making any change.

> **Maintenance rule — non-negotiable.** This file is part of the deliverable, not a
> side note. Any change that touches the behaviour, the interface, the configuration
> surface, the filter policy, the scheduling story or the phase table below must
> update this file **in the same change** as the code. A change that alters
> behaviour and leaves `CLAUDE.md` stale is incomplete. When you finish a task,
> your last step is to re-read this file and reconcile it with what you just shipped.

---

## 1. Project

**Arca** — a terminal interface for `rclone` on Windows. It organises your cloud
backups from one screen: connect accounts, choose folders, decide what to skip,
run, watch, read what went wrong, restore an old version, schedule. Everything
that today means typing `rclone` commands by hand becomes a key press.

The name: an *arca* is the ark that carries everything through the flood, and the
strongbox where valuables are kept. The tool does both.

Why it exists: consumer sync clients (OneDrive, Google Drive for Desktop) sync
*everything* and offer no "back this up but not its `node_modules`". `rclone`
can do it and talks to every provider, but it is a command line with no memory,
no overview and errors buried in a log. Arca is the missing interface on top.

Four properties define it, in priority order:

1. **Never fails silently.** Every failure reaches the user with its cause *and*
   the fix, on screen and, when unattended, as a Windows notification. An expired
   login says "reconnect" and offers the button; it never says "exit 1, see log".
2. **Non-destructive.** Every backup is an `rclone sync` with `--backup-dir`: the
   remote is a clean mirror, but anything overwritten or deleted is moved to
   `_versions/<timestamp>/` first. A wrong local delete is always recoverable.
3. **Filtered.** `filter.txt` decides what never leaves the machine. The policy
   is data, editable from the interface, never code.
4. **Unattended.** The same engine runs headless from Task Scheduler, records
   what happened, and the interface shows it the next time it opens.

### Design model

The interface follows **Bitácora** (<https://github.com/baltazarandersson/bitacora>,
Baltazar Andersson, MIT), used with the author's permission: Python + Textual,
a masthead and setup screen, a dense live table, a detail pane with numbered
tabs, a footer that lists every key, i18n in English and Spanish, themes. Any
file or substantial snippet copied from it keeps its MIT notice and is listed in
`LICENSE` as third-party code in the same change.

### Audience and license

The tool backs up the author's machine; the repository is shown to prospective
employers. Code quality, commit history and documentation are part of the product.

**Arca is open source under MIT, by the author's explicit choice** (2026-10-21).
This overrides the default "All rights reserved" rule for client and portfolio
work: do not flag the MIT license or offer to switch it.

---

## 2. Status and phases

Arca is being built in phases on top of the original script. The MVP (phases 1-3)
runs, and schedules itself (phase 4); `backup.ps1` stays until it is retired
(phase 4b) and keeps its own rules (§8) while it exists. It is the author's own setup, so it
lives only on this machine: `*.ps1` is in `.gitignore` and the file left the
repository on 2026-10-22 (it is in the history up to then).

| Phase | Deliverable | Status |
|---|---|---|
| 0 | `backup.ps1` checks the login first, prints cause + fix of every failure, waits for a key when double-clicked | done |
| 1 | `arca/core`: config, preflight, rclone runner (JSON log), error contract, run history; `arca run` | done |
| 2 | Interface: masthead, quiet header, folders list, each folder's own screen (actions, cause + fix, Files), help | done |
| 3 | Folder picker, destination picker, on/off and remove; filters open in the default editor | done |
| 3b | Home menu with arrows + Enter and settings under it (language, theme, metrics days); Import (old `backup.ps1` + folders already on the remote, both editable), Accounts, Metrics; Enter on a folder opens its screen | done |
| 4 | Automatic backups from the home settings (Task Scheduler, no window) | done |
| 4b | Windows notifications on headless failure; retire `backup.ps1` | next |
| 5 | Remote manager: add/reconnect/remove accounts (Google Drive, OneDrive, Dropbox, Google Photos, B2/S3, local disk) without `rclone config` | |
| 6 | Versions browser: list `_versions/`, restore a file or folder | |

i18n (English/Spanish) and themes shipped early, as settings on the home screen.
Most of phase 5 is in: the Accounts page lists the rclone remotes with their
state, switches the destination, connects, disconnects and adds Google Drive,
OneDrive, Dropbox and Google Photos, all without leaving Arca. Missing: removing an
account for good, and backends that need more than a browser sign-in (B2/S3 keys,
a local disk as a named remote).

When a phase ships: mark it done here, update §3-§7 to match, retire what it
replaces.

Out of scope: **Gmail** is not an rclone backend (rclone does Drive and Photos,
not mail). Mail backup would need another tool and is not planned.

---

## 3. Stack and architecture

- **Python 3.11+** (developed on 3.14), managed with **uv**. Run it with
  `uv run arca`, or install the command with `uv tool install --editable .`.
- **Textual** (8.x) for the interface. Terminal only: no desktop window, no web view.
- **rclone** is an external binary on `PATH`, never vendored. Arca shells out to
  it; it does not reimplement any provider.
- Runtime dependencies beyond Textual need a reason written here.

```
arca/
  core/        No Textual imports. Usable headless and testable alone.
    config.py    Settings and jobs (TOML), remote helpers (is_local, join).
    rclone.py    Spawn rclone, parse --use-json-log lines into typed Lines.
    preflight.py rclone on PATH, remote configured, login valid.
    errors.py    Known rclone failures -> Diagnosis(cause, fix, action). See §5.
    jobs.py      Run one job: sync + backup-dir + filter, live progress, Result.
    selection.py Ticks in a folder's Files list -> that folder's filter rules.
    history.py   One JSON line per run.
    importer.py  An older setup -> candidate folders (backup.ps1 + remote folders).
    metrics.py   Per-folder record from the history; space via `rclone about`.
    schedule.py  The Task Scheduler task for automatic backups (manual/logon/daily).
  ui/
    app.py       The app and the folders list (table, failure note, add folders, keys).
    folder.py    FolderScreen: one folder's actions and its files, opened with Enter.
    files.py     FileTree: a folder's files with a box per line (copied or not).
    pages.py     Home menu and the pages it opens: Import, Accounts, Metrics.
    screens.py   Dialogs: pick folders (ticks), destination, import row actions, confirm, help.
    masthead.py  The still night scene; one line on small terminals.
    arca.tcss    Layout and colours.
  i18n.py      Every user-facing string, English and Spanish.
  fmt.py       Sizes, durations, "3h ago", ~-shortened paths.
  cli.py       `arca` (interface), `arca run [--dry-run] [--job NAME] [--headless]`.
  default_filter.txt  Exclude policy copied to the settings folder on first use.
tests/         pytest; core, the real rclone safety net, and the app via Pilot.
```

Key labels are translated, so bindings are registered at runtime with
`screens.bind()` instead of class-level `BINDINGS` (which are frozen at import).
A language change exits the app with `"restart"` (or `"restart:<id>"` to land back
on a widget of the home screen) and `cli.main` reopens it.

The folders list is the bottom screen (`app.main`); the home menu, the pages and
a folder's own screen are pushed over it. The app always queries `app.main`, never the active screen,
so a running backup keeps updating its row while a page or dialog is on top. The
folder keys are bound to that screen, not the app, so they do nothing on the
menu. Never name a method `_render` on a widget or screen: it overrides Textual's.

**rclone integration.** Jobs run as
`rclone sync <src> <remote>/<name> --backup-dir <remote>/<versions>/<stamp>/<name>
[--filter <rule>]... --filter-from filter.txt --use-json-log --stats 1s
--stats-log-level NOTICE`. The `--filter` rules are the folder's own ticks (§6);
they come first because rclone obeys the first rule that matches. stderr is read
line by line: stats feed the live progress, `level: error` lines feed the result's
cause and fix. Nothing that matters lives only in a log file. What a folder copies
is listed with `rclone lsf -R --files-only --format sp <src>` and the same filter
arguments (`rclone.listing`: path -> size), once more with no filters for every
file in the folder (both at once); `rclone.with_folders` adds the folders that
hold a copied file (a folder whose files are all excluded is not copied: sync
never creates it). Accounts are
signed in with `rclone config create|reconnect --auto-confirm` run hidden (see §4);
`--non-interactive`, which answers with JSON questions, is the path for backends
that need more than the browser.

---

## 4. Interface

Modelled on Bitácora. **Keyboard first. Arrows move, Enter opens, Esc goes back,
Del deletes**: everything is reachable without knowing a single key; the keys are
shortcuts on top. An action on a line (an icon) is reached with `→`, never only
with the mouse or a letter.

- **Masthead.** A still night scene in half-block pixels: the ark with lit
  windows, the name, the dove bringing the olive branch, a crescent moon with its
  path on the water, gusts of wind. Built once per width; a single line under 96
  columns or 30 rows. Nothing animates.
- **Home menu**, shown on open: one panel with Bitácora's rounded cyan border,
  one line per entry (name, what it does, a live value on the right), only for
  what Arca *does*: Folders, Back up all, Import, Accounts, Metrics, Filters.
- **Settings**, under the menu and outside its border, as in Bitácora: one row
  each, `◂ value ▸`, changed with ←→. Language (System, Español, English), theme,
  **automatic backups** (Manual, When I sign in, Every day at 21:00; see §7). ↓
  past the last menu line walks into them. The theme applies and saves at once;
  the language (it restarts the app) and the automatic backups (they change
  Windows' Task Scheduler) wait for Enter and say so in one amber line. The
  automatic backups show what Windows really has registered, read on open and
  after every change. There is no "at shutdown": Windows gives a shutdown task
  seconds, and a backup of gigabytes would be cut. Last, in red and apart
  from everything: **delete the configuration**, behind a yes/no that says what
  happens. It moves `config.toml` and `history.jsonl` to `previous/<stamp>/`
  (recoverable, the filter stays) and reopens Arca as on first launch.
- **Import.** Two editable fields at the top say where it looks: the old script
  (found next to the checkout or in the working folder) and the destination.
  Paste another path, folder or remote and Enter searches there. It reads the
  script (`$Remote`, `$Jobs`, `$env:` paths) and lists the folders already on the
  destination; a remote that cannot be listed shows its cause and fix. The
  script's `backup.log` (next to it) becomes history, so the metrics start with
  the real past: the log never names the job, so a stats block that starts
  within 10 s of the previous one's end is the next job of the same run; a run
  without 100% is `stopped`; a `preflight:` error fails every job. Imported
  runs are merged by time and never added twice (`history.add_past`).
  Every row says where its local folder came from ("from the script, as
  Pictures", "found by name: Imágenes", "chosen by you"); Enter on a row offers
  include/leave out and another local folder, and a row with none found goes
  straight to the folder picker. The page states that nothing is saved until the
  last line (Esc leaves without changes), and after importing it stays to say
  what was saved and where, with "see the folders / the metrics / the menu".
  Hints show their keys in the accent colour; the settings title says they save
  as they change. Remote names win (`Fotos` stays
  `Fotos`, matched to the script's `Pictures` across languages), so the next run
  continues the existing copy instead of uploading it again; an existing
  `_versiones` archive is kept as `versions`. Enter ticks, the last line imports.
- **Accounts.** One rclone remote per line: `●` the destination, name, type,
  state ("destination · connected", "disconnected") and an icon at the end: `✕`
  disconnects, `↻` connects. Keyboard first: `→` moves the cursor onto the icon
  (it lights up, red for `✕`), `←` back to the line, Enter does what the cursor is
  on, `Del` disconnects as deleting from any list; no letter shortcuts to learn.
  The icon is a small chip (dark slate; red or accent under the cursor) kept one
  cell off the border; the lines align with the text above them. A line never
  stays on "checking": every finished check repaints the Accounts page too.
  Enter on the line backs up there, or connects a disconnected one. The mouse
  (a press on the icon) does the same. **No popups and no console windows**: disconnect
  empties the token (`rclone config update <name> token=`; Drive has no
  `rclone config disconnect`), so the account stays listed and reversible, and needs
  no confirmation. Connect and add run rclone hidden (`config reconnect|create
  --auto-confirm`, stdin closed so an unanswerable question fails instead of
  hanging); rclone opens the browser, the panel shows "finish in the browser" and
  the local link in case it did not open; Esc cancels; 5 minutes time it out. The
  state comes from `rclone config dump` (token present or not; tokens are never
  kept or logged, and `config create` output is dropped except its errors).
  `+ Add an account` sits under the panel and unfolds the services in place.
  Another destination folder stays on `m` in the folders view.
- **No account connected** (every remote signed out, destination not a local
  disk): the header names no account ("destination – · no account connected")
  and a callout of its own between the header and the menu (amber rounded border,
  bold, padded, `#callout`) says "→ Connect an account to start · Enter on
  Accounts". The
  home lines that need an account (Folders, Back up all, Import, Metrics) are
  greyed out and cannot be chosen; the cursor starts on Accounts. Filters and the
  settings stay open. Connecting one unblocks everything at once.
- **Folders, empty.** No sentence in a blank screen: a panel like the menu with
  the next steps (add a folder, import, back to the menu). Footer keys that need a
  folder (`r R n d space`) or a run (`s`) are hidden until they can act
  (`App.check_action`); the command palette is off.
- **Metrics.** Space on the destination (`rclone about`), then per folder: real
  backups (dry runs do not count), success rate, last good, last error, average
  time, uploaded, and the last 7/14/30 days as squares (←→ on the page changes
  how many, saved at once).
- **Header**, quiet like Bitácora's "Found:" line: the destination, the rclone
  remotes found, the tagline and version. Health is checked on open, before every
  batch and on `c`. A problem is **one soft amber line**, cause plus the key that
  fixes it (`x to reconnect`): no red ✗, no banner. The full cause and fix live
  under the folder's row and on its screen. No remotes is a short message, not an error.
- **Summary line.** `4 folders · ✓ 3 ok · ✗ 1 failed · last backup 3h ago`.
- **A dry run is never a backup.** The table, the summary, the menu and the
  metrics count only real backups (`history.last_by_job()`); a folder with only a
  dry run still reads "not backed up yet". Its notice says what a backup would
  upload ("a backup would upload 2847 files, 31.8 GB; nothing was changed"); if it
  failed, its cause and fix show as for a backup.
- **Folders list** (`app.main`): the table (status, name, folder, last run, took,
  files, size, errors, now with live percent and speed), under it **only for a
  failed folder** its cause (red), fix (amber) and rclone's own words
  (`#row-note`, never silent), then `+ Add folders` (`LinkRow`, reached with `↓`
  past the last row) and the hint line, which ends with `Del delete folder` in red.
  Enter or `→` on a row opens the folder's screen. Empty: the panel of next steps.
- **A folder's screen** (`FolderScreen`, `ui/folder.py`) is a page over the list:
  Esc goes back (the standard back key in terminal interfaces, and Arca's
  everywhere). In its panel, titled with the folder's name: origin and
  destination; the last failure's cause and fix; the actions as menu lines
  (back up now, dry run, pause/turn on, open in the Explorer; a backup or a dry
  run settles unsaved ticks first and goes back to the list, where its progress
  shows); then **its Files**: every file and folder in it, the ones `filter.txt`
  leaves out too (`.gitignore`, `node_modules`), as a tree with a box per line
  (`FileTree`): `[✓]` copied, `[ ]` stays on this computer (greyed). "Loading the
  files…" while rclone lists them, fresh every time the screen opens, so what was
  added or moved shows as it is now; the keys land in the list once it is there,
  unless a key was already pressed. On top the total the ticks put in the backup
  against the whole folder, `2856 files · 31.8 GB in the backup  of 32.0 GB in the
  folder`, recounted on every tick (`selection.total`); each folder line shows its
  size on disk softly after its name (grey, none when empty). `↑` at the top of the
  list walks to the actions; while the keys are in the list the actions show no
  cursor (one cursor on screen). Space or Tab ticks (a folder takes everything in
  it), `→`/`←` open and close. Ticks are not saved until **"+ Save the changes
  (n)"** appears under the list (Enter on it, or `Ctrl+S`); Esc or quitting with
  unsaved ticks asks first (save, discard, or Esc to stay). Saved ticks become the
  folder's `rules` (`core/selection.py`: `- /dir/**`, `+ /file`, deepest first,
  glob characters escaped; a tick that only restores what rclone does anyway adds
  no rule). `Del` removes the folder from Arca after a yes/no and goes back.
  `Versions` comes in phase 6.
- **Adding folders** is a tree with a box on every line (`PickFoldersScreen`,
  `CheckTree`): space or Tab ticks, Enter opens or closes a folder, `→` opens it or
  steps into it, `←` closes it or steps out. **Exploring never adds anything**: the
  ticked folders are added only from **"+ Add the n ticked folders"** under the tree
  (reached with `↓` past the last line, shown once something is ticked) or with
  `Ctrl+S`; the cursor alone never chooses. Esc with ticks asks first (add them,
  drop them, or Esc to stay). No name field: each goes to the destination under its own folder
  name, `Name 2` when taken; a folder already backed up is skipped. A path typed
  at the top (`D:\`) moves the tree there; since Tab ticks in the tree, the field
  is reached with `↑` from the tree's first line, and `↓` goes back. The import's "pick another folder"
  still uses the single-folder `AddJobScreen`, where the name is fixed.
- **Import** is described generically ("bring in your previous setup"): the old
  script can have any name, so no screen names `backup.ps1` as the thing to look for.
- **Keys.** The footer stays quiet: `esc` menu and `q` quit, nothing else (a row
  of letters is noise). Each screen's hint line says its keys, and ends with
  `Del delete folder` in red and bold where it applies, as every destructive thing
  (`hint_text(..., danger=)`, `Page.DANGER`): it removes the folder from Arca after
  a yes/no; its files stay where they are. A hint that does not fit wraps to a
  second line instead of being cut. The list's shortcuts are in help (`?`): `r`
  back up, `R` all, `n` dry run, `s` stop, `a` add, `d` (or `Del`) remove, `m`
  destination, `f` filters, `x` reconnect, `space` on/off, `c` check, `l` language;
  `Ctrl+S` saves the ticks on a folder's screen.
  `ctrl+c` twice within 2 s quits (stopping any run), like any terminal program;
  the first press shows one notice, never a stack of them.
  Lines outside a panel that do one thing (add an account, delete the
  configuration) are `LinkRow`s: Enter or a click, on mouse down.
- **Confirmations** are a short list, never two look-alike buttons: the question
  in bold, what it means under it, then the answers one under the other with the
  chosen one marked (`❯`, bold, teal wash). The yes says what it does in red
  ("Sí, quitarla"), the no is safe ("No, dejarla") and holds the cursor at first.
  A safe yes ("Sí, guardarlos", "Sí, agregarlas": `default_yes`) holds the cursor
  instead and stays in the accent colour: red is only for what deletes or stops.
  `↑↓` choose, `Enter` answers, `Esc` cancels; `s`/`y` and `n` still work. Answers
  (and the red delete row) act on mouse *down*: VS Code's terminal on Windows can
  lose the release, and a Textual click needs both on the same widget.

Jobs run one at a time from a queue; `R` queues every enabled folder.

Rules: every state has a colour *and* a word (never colour alone); the header
adapts to width; no screen is ever blank — empty states explain the next step;
a failing row always shows its fix right under it. Tone is calm: say what is
there, hint at what to press, keep alarms for the failed folder itself. Every block shares
a 2-column side margin and is separated by one blank line. One highlight colour,
a teal wash that keeps each column's own colour readable (never the theme's
purple block). Text selection is off: a click is a click.

---

## 5. Error contract

`core/errors.py` maps rclone output to a cause, a fix and, when possible, an
action the interface can run. Known cases:

| rclone says | Cause shown | Action |
|---|---|---|
| `empty token found` | Account disconnected (from Accounts) | Connect (`x`, or `↻` in Accounts) |
| `invalid_grant`, `token expired` | Login expired | Reconnect (`rclone config reconnect`) |
| `didn't find section in config file` | Remote not configured | Open remote manager |
| `storageQuotaExceeded`, `quota` | Remote full | — |
| `no such host`, `dial tcp`, timeout | Network down | Retry |
| source path missing | Folder moved or unplugged | Remove and add again |
| no remote set / drive letter missing | Nothing to back up to | Choose destination (`m`) |
| rclone not on PATH | rclone missing | `winget install Rclone.Rclone` |

An unknown error is shown verbatim, never swallowed. Adding a case means adding
a test with the real rclone line.

Exit codes for `arca run`: `0` all ok, otherwise the number of failed jobs. A
preflight failure counts every job as failed. A config file that cannot be
parsed exits `1` with the parser's message.

---

## 6. Configuration and data

Everything lives in `%APPDATA%\arca\` (`ARCA_HOME` overrides it; tests use it):

- **`config.toml`** — `remote`, `language` (`auto`/`en`/`es`), `versions` (the
  archive folder, `_versions` unless an import kept an older one), `theme` (a
  Textual theme name), `metrics_days` (7/14/30, from the Metrics page) and `[[jobs]]`
  with `name`, `source`, `enabled` and `rules` (the folder's own rclone filter
  lines from its Files ticks, applied before `filter.txt`; absent when none).
  Written by the interface, editable by hand. On first
  launch `remote` defaults to the first rclone remote plus `Backups`.
- **`filter.txt`** — the exclude policy, rclone filter syntax, copied from
  `arca/default_filter.txt` on first use and opened with `f`. The repository's
  root `filter.txt` belongs to `backup.ps1` until it is retired.
- **`history.jsonl`** — one line per run, trimmed to the last 2000.
- **`previous/<YYYY-MM-DD_HHMMSS>/`** — what "delete the configuration" moved
  aside. Copy a file back (with Arca closed) to undo it.
- Each job syncs to `<remote>/<name>`; archives go to
  `<remote>/<versions>/<YYYY-MM-DD_HHMMSS>/<name>`. `_versions` and the old
  script's `_versiones` are reserved names.
- `rclone.conf` belongs to rclone. Arca changes it only through `rclone config`
  commands, never by editing the file.

---

## 7. Scheduling

**Automatic backups** is a setting under the home menu, applied with Enter, and
`core/schedule.py` turns it into one **Windows Task Scheduler** task named
`Arca`:

- **Manual**: no task (it is deleted).
- **When I sign in**: a logon trigger for this user, 2 minutes later so the
  network is up.
- **Every day at 21:00**: a daily trigger with "run as soon as possible after a
  missed start", so a day the computer was off at 21:00 runs when it is on.

The task is registered from XML for the current user (`schtasks /Create /XML`),
which needs no administrator rights; `/SC ONLOGON` would. It runs this same
Python's `pythonw.exe -m arca run --headless`: no console window, nothing
printed, the history records the result and the interface shows it next time.
It only starts with the network up, never twice at once, and on battery too.
Applying a mode also removes the old script's task (`rclone-backup-windows`) if it
is there, so the folders are never backed up twice. Tests replace
`schedule.current` and `schedule.apply` (`tests/conftest.py`): no test touches
the machine's Task Scheduler.

---

## 8. Conventions

- **Everything in the repository is English** — code, identifiers, comments,
  docs, filenames. Interface strings go through `i18n.t()` with both English and
  Spanish; a test checks every key has both and that placeholders match. Dates
  are ISO 8601 `YYYY-MM-DD`.
- **Never widen a delete.** The `--backup-dir` safety net is the point of the
  tool; a change that removes it, or switches `sync` for something that deletes
  without archiving, is a regression. Restore never deletes on the remote.
- **No `.ps1` in the repository.** The old script is the author's own setup and
  stays on this machine (`*.ps1` in `.gitignore`); tests use an inline sample.
- **Filter rules are data, not code.** A new exclude is a line in `filter.txt`
  (or a folder's `rules` in `config.toml`), never a hard-coded `--exclude`.
- **core/ never imports Textual; ui/ never spawns rclone.**
- **Tests use a fake rclone** (a script that replays recorded JSON lines). Never
  commit real paths, account names, tokens or logs from this machine.
- `ruff check .` and `pytest` pass before any commit.
- **While it exists, `backup.ps1`** stays a single self-contained file, pure
  ASCII (colour built from `[char]27`), no PowerShell modules.

---

## 9. Definition of done

- [ ] `ruff check .` and `pytest` pass.
- [ ] A dry run against the real remote runs clean (`arca run --dry-run`).
- [ ] Interface changes checked by eye: `App.save_screenshot()` from a
      `run_test()` session, rendered to PNG.
- [ ] Keyboard first, proven: a Pilot test drives the change with `pilot.press()`
      only, and Enter while exploring never saves, adds, runs or deletes. The
      project's Stop hook (`.claude/hooks/keyboard_check.py`, set in
      `.claude/settings.json`) holds Claude to this checklist once every time
      `arca/ui/` or `arca/i18n.py` changed since its last check.
- [ ] Every new failure path shows a cause and a fix (§5).
- [ ] No exclude rule hard-coded; the `--backup-dir` safety net is intact.
- [ ] New interface strings exist in English and Spanish.
- [ ] **`CLAUDE.md`, `README.md` and the phase table updated if anything moved.**
- [ ] `LICENSE` lists any third-party code or asset added.
- [ ] Commit message follows the convention below.

---

## 10. Commits

`type(scope): short message`, in English. Types: `feat`, `fix`, `docs`, `style`,
`refactor`, `test`, `chore`. Scopes: `core`, `ui`, `cli`, `filter`, `script`,
`docs`. For example `feat(ui): live progress in jobs table`,
`fix(script): show rclone cause on failure`.

- **Subject line only, never a body.** One line, 10 words, 15 at the limit. If it
  will not fit, split the commit; detail that needs recording belongs in this file.
- **No co-author trailers.** The history stays in one name.

---

<p align="center"><sub>co-assisted by <b>Claude</b></sub></p>
