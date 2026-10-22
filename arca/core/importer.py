"""Find an earlier setup and turn it into Arca folders.

Two places hold it: the old backup.ps1 (its $Remote and $Jobs) and the destination itself, whose top-level folders
are what was actually backed up. Names already on the remote win, so a folder picks up where it left off instead of
being uploaded again; the script supplies the local path, matched across languages (Fotos is Pictures).
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path

import arca
from arca.core import rclone
from arca.core.config import RESERVED_NAMES, Job
from arca.core.errors import UNKNOWN, Diagnosis, diagnose
from arca.core.jobs import Result

SYNONYMS = (
    {"pictures", "photos", "fotos", "imágenes", "imagenes"},
    {"documents", "documentos", "docs"},
    {"music", "música", "musica"},
    {"videos", "vídeos", "video"},
    {"desktop", "escritorio"},
    {"downloads", "descargas"},
)
REMOTE_LINE = re.compile(r"^\s*\$Remote\s*=\s*['\"]([^'\"]+)['\"]", re.MULTILINE)
JOB_LINE = re.compile(r"Name\s*=\s*['\"]([^'\"]+)['\"]\s*;\s*Source\s*=\s*['\"]([^'\"]+)['\"]")
# The old script's rclone log: plain lines, a stats block at the end of every job.
LOG_STAMP = re.compile(r"^(\d{4}/\d{2}/\d{2} \d{2}:\d{2}:\d{2}) (\w+)\s*:\s?(.*)")
LOG_BYTES = re.compile(r"^Transferred:\s+([\d.]+ \w+) / ([\d.]+ \w+), (\d+%|-)")
LOG_FILES = re.compile(r"^Transferred:\s+(\d+) / (\d+), (\d+)%")
LOG_ERRORS = re.compile(r"^Errors:\s+(\d+)")
LOG_ELAPSED = re.compile(r"^Elapsed time:\s+(.+)$")
UNITS = {"B": 1, "KiB": 1 << 10, "MiB": 1 << 20, "GiB": 1 << 30, "TiB": 1 << 40}
SAME_RUN = 10  # seconds: a job that starts this soon after the previous one ended belongs to the same script run


@dataclass
class Candidate:
    name: str
    source: str  # "" when no local folder matches
    on_remote: bool  # already backed up there: the next run continues, it does not start over
    from_script: bool
    origin: str = ""  # where source came from: "script", "name" (found by name), "picked" (by the user) or ""
    matched: str = ""  # the script job or the folder name it was matched to (Pictures for Fotos)

    @property
    def exists(self) -> bool:
        return bool(self.source) and Path(self.source).is_dir()


@dataclass
class Plan:
    script: Path | None
    remote: str
    candidates: list[Candidate]
    versions: str | None  # the archive folder already on the remote, if any
    problem: Diagnosis | None = None  # the remote could not be listed
    missing_script: bool = False  # a script path was given and is not there
    runs: list[Result] = field(default_factory=list)  # the script's past runs, read from its backup.log


def expand(path: str) -> str:
    """$env:USERPROFILE\\x and %USERPROFILE%\\x -> C:\\Users\\me\\x"""
    path = re.sub(r"\$env:(\w+)", lambda m: os.environ.get(m.group(1), m.group(0)), path, flags=re.IGNORECASE)
    return os.path.expandvars(path)


def parse_script(text: str) -> tuple[str, list[Job]]:
    remote = REMOTE_LINE.search(text)
    jobs = [Job(name, expand(source)) for name, source in JOB_LINE.findall(text)]
    return (remote.group(1) if remote else ""), jobs


def _seconds(text: str) -> float:
    """rclone's elapsed time: 1.6s, 59.9s, 1m22.2s, 1h2m3s."""
    total = 0.0
    for value, unit in re.findall(r"([\d.]+)(h|m|s)", text):
        total += float(value) * {"h": 3600, "m": 60, "s": 1}[unit]
    return total


def _bytes(text: str) -> int:
    value, _, unit = text.partition(" ")
    return round(float(value) * UNITS.get(unit, 1))


def _iso(stamp: str) -> datetime:
    return datetime.strptime(stamp, "%Y/%m/%d %H:%M:%S").astimezone()


def parse_log(text: str, job_names: list[str]) -> list[Result]:
    """The old script's history, one Result per job per run.

    The log never names the job, so it comes from the order: the script ran its jobs one after the other, so a
    stats block that starts right after the previous one ended is the next job of the same run. A run cut short
    (no 100%) is kept as stopped, which the metrics leave out. A failed preflight fails every job at once.
    """
    if not job_names:
        return []
    results: list[Result] = []
    last_stamp, errors = "", []
    files = moved = 0
    size, complete = 0, True
    chain_end: datetime | None = None
    index = 0
    for raw in text.splitlines():
        line = raw.strip()
        if match := LOG_STAMP.match(line):
            last_stamp = match.group(1)
            if match.group(2) in ("ERROR", "CRITICAL"):
                if match.group(3).startswith("preflight:"):
                    when = _iso(last_stamp).isoformat(timespec="seconds")
                    for name in job_names:
                        results.append(Result(job=name, started=when, finished=when, status="failed",
                                              errors=[match.group(3)], diagnosis=diagnose([match.group(3)])))
                    chain_end = None
                else:
                    errors.append(match.group(3))
        elif match := LOG_BYTES.match(line):
            size = _bytes(match.group(1))
            complete = match.group(3) in ("100%", "-")
        elif match := LOG_FILES.match(line):
            files, moved = int(match.group(1)), int(match.group(2))
            complete = complete and files == moved
        elif match := LOG_ERRORS.match(line):
            complete = complete and int(match.group(1)) == 0
        elif (match := LOG_ELAPSED.match(line)) and last_stamp:
            end = _iso(last_stamp)
            took = _seconds(match.group(1))
            start = end - timedelta(seconds=took)
            same_run = chain_end is not None and (start - chain_end).total_seconds() < SAME_RUN
            index = index + 1 if same_run else 0
            chain_end = end
            if index < len(job_names):
                status = "ok" if complete and not errors else ("failed" if errors else "stopped")
                results.append(Result(job=job_names[index], started=start.isoformat(timespec="seconds"),
                                      finished=end.isoformat(timespec="seconds"), duration=took, status=status,
                                      transfers=files, bytes=size, errors=errors[-20:],
                                      diagnosis=diagnose(errors) if errors else None))
            errors, files, moved, size, complete = [], 0, 0, 0, True
    return results


def script_paths() -> list[Path]:
    """backup.ps1 next to this checkout of Arca, or in the folder Arca was started from."""
    seen: list[Path] = []
    for root in (Path(arca.__file__).resolve().parents[1], Path.cwd()):
        path = (root / "backup.ps1").resolve()
        if path.is_file() and path not in seen:
            seen.append(path)
    return seen


def same(a: str, b: str) -> bool:
    a, b = a.lower(), b.lower()
    return a == b or any(a in group and b in group for group in SYNONYMS)


def guess_source(name: str, script_jobs: list[Job], homes: list[Path] | None = None) -> tuple[str, str, str]:
    """(source, origin, matched): the script's job of the same name first, else a folder with that name (in any
    language) in ~\\OneDrive or ~. Nothing found is ("", "", ""): the user picks it."""
    for job in script_jobs:
        if same(job.name, name):
            return job.source, "script", job.name
    for base in homes if homes is not None else [Path.home() / "OneDrive", Path.home()]:
        try:
            for child in base.iterdir():
                if child.is_dir() and same(child.name, name):
                    return str(child), "name", child.name
        except OSError:
            continue
    return "", "", ""


def build_plan(remote_dirs: list[str], script_jobs: list[Job], homes: list[Path] | None = None
               ) -> tuple[list[Candidate], str | None]:
    """Remote folders first (they carry the history), then script jobs that never reached the remote."""
    versions = "_versions" if "_versions" in remote_dirs else ("_versiones" if "_versiones" in remote_dirs else None)
    candidates = []
    for name in remote_dirs:
        if name not in RESERVED_NAMES:
            source, origin, matched = guess_source(name, script_jobs, homes)
            candidates.append(Candidate(name, source, True, origin == "script", origin, matched))
    for job in script_jobs:
        if not any(same(c.name, job.name) for c in candidates):
            candidates.append(Candidate(job.name, job.source, False, True, "script", job.name))
    return candidates, versions


async def find(remote_hint: str, script: Path | None = None, look_for_script: bool = True) -> Plan:
    """Search where the user points (a pasted script path, a pasted remote), else where an old setup usually is."""
    if script is None and look_for_script:
        scripts = script_paths()
        script = scripts[0] if scripts else None
    missing = script is not None and not script.is_file()
    script_remote, script_jobs = ("", [])
    runs: list[Result] = []
    if script is not None and not missing:
        script_remote, script_jobs = parse_script(script.read_text(encoding="utf-8", errors="replace"))
        log = script.parent / "backup.log"  # where the script wrote it: next to itself
        if log.is_file():
            runs = parse_log(log.read_text(encoding="utf-8", errors="replace"), [job.name for job in script_jobs])
    remote = remote_hint or script_remote
    dirs: list[str] = []
    problem = None
    if remote:
        try:
            code, lines = await rclone.output("lsf", remote, "--dirs-only", "--max-depth", "1", timeout=60)
        except rclone.RcloneMissing:
            code, lines = 1, [rclone.Line("error", "rclone not found")]
        if code == 0:
            dirs = [line.msg.rstrip("/") for line in lines if line.level == "text" and line.msg.endswith("/")]
        else:
            problem = diagnose(line.msg for line in lines) or UNKNOWN
    candidates, versions = build_plan(dirs, script_jobs)
    return Plan(script, remote, candidates, versions, problem, missing, runs)
