"""Entry point. `arca` opens the interface; `arca run` backs up headless, for Task Scheduler."""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
from datetime import datetime

from arca import __version__, fmt
from arca.core import config, history, preflight, rclone
from arca.core.config import Config, ConfigError, remote_name
from arca.core.errors import Diagnosis
from arca.core.jobs import JobRun, failed_before_start
from arca.i18n import set_language, t


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="arca", description="A terminal interface for rclone backups.")
    parser.add_argument("--version", action="version", version=f"arca {__version__}")
    sub = parser.add_subparsers(dest="command")
    run = sub.add_parser("run", help="back up every enabled folder without the interface")
    run.add_argument("--dry-run", action="store_true", help="show what would change, change nothing")
    run.add_argument("--job", action="append", metavar="NAME", help="only this folder (repeatable)")
    run.add_argument("--headless", action="store_true",
                     help="print nothing: the scheduled task, under pythonw with no window (the history keeps it)")
    args = parser.parse_args(argv)

    if args.command == "run" and args.headless or sys.stdout is None:  # pythonw has no console to print to
        sys.stdout = sys.stderr = open(os.devnull, "w", encoding="utf-8")  # noqa: SIM115 - lives as long as the run

    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")  # Task Scheduler redirects to ANSI files
    try:
        cfg = _load()
    except ConfigError as exc:
        print(t("cli.config_error"), exc, file=sys.stderr)
        return 1

    if args.command == "run":
        return asyncio.run(headless(cfg, dry_run=args.dry_run, only=args.job))

    from arca.ui.app import ArcaApp

    focus = None
    # A language change rebuilds every label: the app exits with "restart" (or "restart:<widget to focus>") and
    # opens again.
    while (result := ArcaApp(cfg, focus=focus).run() or "").startswith("restart"):
        cfg = _load()
        focus = result.partition(":")[2] or None
    return 0


def _load() -> Config:
    cfg = config.load()
    set_language(cfg.language)
    if not config.config_path().exists():
        cfg.remote = _first_remote()
        config.save(cfg)
    return cfg


def _first_remote() -> str:
    """A sensible default on first launch: the first rclone remote, in a Backups folder."""
    remotes = asyncio.run(rclone.list_remotes())
    return f"{remotes[0]}Backups" if remotes else ""


def _out(text: str = "") -> None:
    print(text, flush=True)


def _explain(diagnosis: Diagnosis | None, errors: list[str], cfg: Config, source: str = "") -> None:
    if diagnosis:
        values = {"remote": cfg.remote, "source": source, "name": ""}
        _out(f"       {t(diagnosis.cause, **values)}")
        _out(f"  {t('fix')}: {t(diagnosis.fix, **values)}")
        if diagnosis.action == "reconnect":
            _out(f"       rclone config reconnect {remote_name(cfg.remote)}")
    for error in errors[-5:]:
        # rclone puts the cause at the end, so cut long lines from the middle.
        _out(f"       {error if len(error) <= 240 else error[:100] + ' ... ' + error[-135:]}")


async def headless(cfg: Config, *, dry_run: bool = False, only: list[str] | None = None) -> int:
    """Back up every enabled folder. Exit code: the number of folders that failed."""
    filter_file = config.filter_path()
    jobs = [job for job in cfg.jobs if job.enabled and (not only or job.name in only)]
    mode = f" ({t('cli.dry')})" if dry_run else ""
    _out(f"Arca {__version__}   {datetime.now():%Y-%m-%d %H:%M}   -> {cfg.remote or '-'}{mode}")
    if not jobs:
        _out(t("cli.nojobs"))
        return 0

    health = await preflight.check(cfg.remote)
    if not health.ok:
        _out(f"[FAIL] {t('cli.preflight', remote=cfg.remote or '-')}")
        _explain(health.diagnosis, health.errors, cfg)
        for job in jobs:
            history.append(failed_before_start(job, dry_run, health.diagnosis, health.errors))
        return len(jobs)

    failed = 0
    for job in jobs:
        _out(f">>> {job.name}   {job.source}")
        result = await JobRun(job, cfg.remote, filter_file, dry_run=dry_run, versions=cfg.versions).run()
        history.append(result)
        summary = t("cli.done", name=job.name, files=result.transfers, size=fmt.size(result.bytes),
                    took=fmt.duration(result.duration))
        if result.status == "ok":
            _out(f"[ OK ] {summary}")
        else:
            failed += 1
            _out(f"[FAIL] {summary}")
            _explain(result.diagnosis, result.errors, cfg, job.source)
    _out(t("cli.summary", n=len(jobs), failed=failed))
    return failed


if __name__ == "__main__":
    sys.exit(main())
