# CLAUDE.md

Working guide for this repository. Read it before making any change.

> **Maintenance rule — non-negotiable.** This file is part of the deliverable, not a
> side note. Any change that touches the behaviour, the configuration surface, the
> filter policy, or the scheduling story must update this file **in the same commit**
> as the code. A change that alters behaviour and leaves `CLAUDE.md` stale is
> incomplete. When you finish a task, your last step is to re-read this file and
> reconcile it with what you just shipped.

---

## 1. Project

**drive-backup** — a small Windows tool that mirrors chosen local folders to a
cloud remote on a schedule, skipping the noise, and never silently loses a file.

It exists because the consumer sync clients (OneDrive, Google Drive for Desktop)
sync *everything*: they have no way to say "back up this project but not its
`node_modules`", so a few repos fill a free plan with regenerable junk. This tool
gives that one missing knob — an exclude list — on top of `rclone`.

Three properties define it:

1. **Filtered.** One `filter.txt` decides what never leaves the machine. Editing
   the policy is editing one file, not the script.
2. **Non-destructive.** Every run is an `rclone sync` with `--backup-dir`: the
   remote becomes a clean mirror, but anything overwritten or deleted is moved to
   `_versions/<timestamp>/` first. A wrong local delete is always recoverable.
3. **Unattended.** It prints for a human when run by hand and exits non-zero when a
   job fails, so Task Scheduler can run it and something can tell when it broke.

### Audience of this repository

The tool backs up the author's machine; the repository is shown to prospective
employers. Code quality, commit history and documentation are part of the product.

---

## 2. How it works

No dependencies to install beyond `rclone` itself. One script, one filter, one log.

```
backup.ps1     The tool. Config block at the top, everything below it is generic.
filter.txt     rclone filter rules: what to exclude. The only file most edits touch.
backup.log     Per-run rclone log (git-ignored).
```

`backup.ps1` walks `$Jobs`, and for each one runs:

```
rclone sync <Source> <Remote>/<Name> \
    --backup-dir <Remote>/_versions/<stamp>/<Name> \
    --filter-from filter.txt --log-file backup.log -P
```

Paths resolve from `$PSScriptRoot`, so the folder works wherever it is cloned. The
script pre-checks that `rclone` is on `PATH` and that each source exists (a missing
source is skipped, not fatal), and returns the count of failed jobs as its exit
code.

---

## 3. Configuration

Everything a user changes lives in the marked block at the top of `backup.ps1`:

- **`$Remote`** — any rclone remote, `remote:path` form. Set it up once with
  `rclone config`. Nothing else in the tool is provider-specific: point it at
  Google Drive, Backblaze B2, S3 or a second disk and the rest is unchanged.
- **`$Jobs`** — one entry per folder, `@{ Name = ...; Source = ... }`. `Name` is
  both the console label and the destination sub-folder under `$Remote`.

The exclude policy lives entirely in **`filter.txt`** and is intentionally kept out
of the script, so tuning what gets skipped never means touching code.

---

## 4. Conventions

- **Everything in the repository is English** — code, identifiers, comments, docs,
  filenames. Console output and log are English too. Dates are ISO 8601
  `YYYY-MM-DD`, never `MM/DD/YYYY`.
- **`backup.ps1` stays a single self-contained file.** No modules, no external
  PowerShell dependencies. The only runtime dependency is `rclone`.
- **The file stays pure ASCII.** Colour is built at runtime from `[char]27`; no
  literal escape bytes and no box-drawing characters, so it survives being written
  by any editor or encoding.
- **Filter rules are data, not code.** A new folder to exclude is a line in
  `filter.txt`, never an `--exclude` flag hard-coded in the script.
- **Never widen a delete.** The `--backup-dir` safety net is the point of the tool;
  a change that removes it, or that switches `sync` for something that deletes
  without archiving, is a regression.

---

## 5. Scheduling

The tool is meant to run from **Windows Task Scheduler**, daily. Register it once:

```powershell
schtasks /Create /TN "drive-backup" /SC DAILY /ST 21:00 /F ^
  /TR "powershell -NoProfile -ExecutionPolicy Bypass -File \"%CD%\backup.ps1\""
```

The scheduled context has no console, so the script guards `Clear-Host` and relies
on the log rather than the coloured output. A failing run leaves a non-zero exit
code and an `[ERROR]` line in `backup.log`.

---

## 6. Definition of done

A change is finished when all of the following are true:

- [ ] `.\backup.ps1 -DryRun` runs clean against a real remote.
- [ ] The file is still pure ASCII and still a single self-contained script.
- [ ] No exclude rule was hard-coded into the script instead of `filter.txt`.
- [ ] The `--backup-dir` safety net is intact.
- [ ] **`CLAUDE.md` and `README.md` updated if anything in §1-§5 moved.**
- [ ] Commit message follows the convention below.

---

## 7. Commits

`type(scope): short message`, in English. Types: `feat`, `fix`, `docs`, `style`,
`refactor`, `chore`. For example `feat(filter): exclude .gradle`,
`fix(script): guard Clear-Host when headless`, `docs(readme): document -DryRun`.

- **Subject line only, never a body.** One line, 10 words, 15 at the limit. If it
  will not fit, split the commit; detail that needs recording belongs in this file.
- **No co-author trailers.** The history stays in one name.

---

<p align="center"><sub>co-assisted by <b>Claude</b></sub></p>
