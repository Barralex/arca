"""Back up one folder: rclone sync with the --backup-dir safety net, read live.

The remote becomes a mirror of the folder, but nothing is lost on the way: whatever the sync would overwrite or
delete is first moved to <remote>/<versions>/<stamp>/<job> (_versions unless an import kept an older folder).
"""
from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from arca.core.config import Job, join
from arca.core.errors import NO_RCLONE, SOURCE_MISSING, Diagnosis, diagnose
from arca.core.rclone import Line, Process, RcloneMissing

FILE_VERBS = ("Copied", "Updated", "Deleted", "Moved", "Skipped copy", "Skipped delete", "Skipped move",
              "Skipped update")
MAX_ERRORS = 200


@dataclass
class Progress:
    bytes: int = 0
    total_bytes: int = 0
    transfers: int = 0
    total_transfers: int = 0
    deletes: int = 0
    errors: int = 0
    speed: float = 0.0
    eta: float | None = None
    elapsed: float = 0.0

    @classmethod
    def from_stats(cls, stats: dict) -> Progress:
        def num(key: str) -> float:
            value = stats.get(key)
            return value if isinstance(value, int | float) else 0

        eta = stats.get("eta")
        return cls(
            bytes=int(num("bytes")),
            total_bytes=int(num("totalBytes")),
            transfers=int(num("transfers")),
            total_transfers=int(num("totalTransfers")),
            deletes=int(num("deletes")),
            errors=int(num("errors")),
            speed=float(num("speed")),
            eta=float(eta) if isinstance(eta, int | float) else None,
            elapsed=float(num("elapsedTime")),
        )

    @property
    def fraction(self) -> float | None:
        if self.total_bytes > 0:
            return min(self.bytes / self.total_bytes, 1.0)
        if self.total_transfers > 0:
            return min(self.transfers / self.total_transfers, 1.0)
        return None


@dataclass
class Result:
    job: str
    started: str
    finished: str = ""
    duration: float = 0.0
    status: str = "ok"  # ok | failed | stopped | skipped
    dry_run: bool = False
    code: int = 0
    transfers: int = 0
    bytes: int = 0
    deletes: int = 0
    errors: list[str] = field(default_factory=list)
    diagnosis: Diagnosis | None = None

    def to_record(self) -> dict:
        record = {k: v for k, v in self.__dict__.items() if k != "diagnosis"}
        record["errors"] = self.errors[:20]
        record["diagnosis"] = self.diagnosis.to_record() if self.diagnosis else None
        return record

    @classmethod
    def from_record(cls, data: dict) -> Result:
        known = {k: v for k, v in data.items() if k in cls.__dataclass_fields__ and k != "diagnosis"}
        return cls(**known, diagnosis=Diagnosis.from_record(data.get("diagnosis")))


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def stamp() -> str:
    return datetime.now().strftime("%Y-%m-%d_%H%M%S")


def filter_args(job: Job, filter_file: Path) -> list[str]:
    """The folder's own rules first, then filter.txt: rclone takes the first rule that matches, so a file the user
    ticked is copied even if the general policy leaves its kind out."""
    args: list[str] = []
    for rule in job.rules:
        args += ["--filter", rule]
    return [*args, "--filter-from", str(filter_file)]


def build_args(job: Job, remote: str, filter_file: Path, run_stamp: str, dry_run: bool,
               versions: str = "_versions") -> list[str]:
    args = [
        "sync", job.source, join(remote, job.name),
        "--backup-dir", join(remote, versions, run_stamp, job.name),
        *filter_args(job, filter_file),
        "--use-json-log", "--log-level", "INFO",
        "--stats", "1s", "--stats-log-level", "NOTICE",
    ]
    if dry_run:
        args.append("--dry-run")
    return args


def failed_before_start(job: Job, dry_run: bool, diagnosis: Diagnosis | None, errors: list[str]) -> Result:
    """The result of a job that never got to run (remote unreachable, rclone missing)."""
    when = now_iso()
    return Result(job=job.name, started=when, finished=when, status="failed", dry_run=dry_run, code=-1,
                  errors=list(errors), diagnosis=diagnosis)


class JobRun:
    def __init__(self, job: Job, remote: str, filter_file: Path, *, dry_run: bool = False,
                 versions: str = "_versions", on_line: Callable[[JobRun, Line], None] | None = None):
        self.job = job
        self.result = Result(job=job.name, started=now_iso(), dry_run=dry_run)
        self.progress = Progress()
        self.files: list[Line] = []
        self._on_line = on_line
        self._proc = Process(build_args(job, remote, filter_file, stamp(), dry_run, versions))

    async def run(self) -> Result:
        if not Path(self.job.source).is_dir():
            return self._finish("skipped", -1, 0.0, SOURCE_MISSING)
        started = time.monotonic()
        try:
            code = await self._proc.run(self._line)
        except RcloneMissing:
            return self._finish("failed", -1, 0.0, NO_RCLONE)
        if self._proc.stopped:
            status = "stopped"
        else:
            status = "ok" if code == 0 else "failed"
        diagnosis = diagnose(self.result.errors) if status == "failed" else None
        return self._finish(status, code, time.monotonic() - started, diagnosis)

    def stop(self) -> None:
        self._proc.stop()

    def _line(self, line: Line) -> None:
        if line.stats:
            self.progress = Progress.from_stats(line.stats)
        elif line.is_error:
            if len(self.result.errors) < MAX_ERRORS:
                self.result.errors.append(f"{line.obj}: {line.msg}" if line.is_file else line.msg)
        elif line.obj and line.msg.startswith(FILE_VERBS):
            self.files.append(line)
        if self._on_line is not None:
            self._on_line(self, line)

    def _finish(self, status: str, code: int, duration: float, diagnosis: Diagnosis | None) -> Result:
        res = self.result
        res.status, res.code, res.duration, res.diagnosis = status, code, duration, diagnosis
        res.finished = now_iso()
        res.transfers, res.bytes, res.deletes = self.progress.transfers, self.progress.bytes, self.progress.deletes
        if res.dry_run:
            # A dry run transfers nothing; report what it would have copied.
            res.transfers = sum(1 for line in self.files if line.msg.startswith("Skipped copy"))
        return res
