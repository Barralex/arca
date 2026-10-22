"""How the backups are doing, from the run history: per folder and for the destination as a whole."""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

from arca.core import rclone
from arca.core.config import is_local, remote_name
from arca.core.jobs import Result

DAYS = 14


@dataclass
class FolderStats:
    name: str
    runs: int = 0
    ok: int = 0
    failed: int = 0
    last_ok: str = ""
    last_failed: str = ""
    duration_total: float = 0.0
    bytes: int = 0
    transfers: int = 0
    days: list[str] = field(default_factory=lambda: [""] * DAYS)  # oldest to today: ok, failed or ""

    @property
    def success(self) -> float | None:
        return self.ok / self.runs if self.runs else None

    @property
    def average(self) -> float | None:
        return self.duration_total / self.runs if self.runs else None


def compute(results: list[Result], names: list[str], today: date | None = None, days: int = DAYS
            ) -> list[FolderStats]:
    """Real backups only: a dry run changes nothing, so it says nothing about how the backups are doing."""
    today = today or date.today()
    first_day = today - timedelta(days=days - 1)
    stats = {name: FolderStats(name, days=[""] * days) for name in names}
    for result in results:
        folder = stats.get(result.job)
        if folder is None or result.dry_run or result.status == "stopped":
            continue
        folder.runs += 1
        folder.duration_total += result.duration
        when = result.finished or result.started
        if result.status == "ok":
            folder.ok += 1
            folder.last_ok = max(folder.last_ok, when)
            folder.bytes += result.bytes
            folder.transfers += result.transfers
        else:
            folder.failed += 1
            folder.last_failed = max(folder.last_failed, when)
        try:
            day = datetime.fromisoformat(when).date()
        except ValueError:
            continue
        if first_day <= day <= today:
            slot = (day - first_day).days
            if result.status == "ok" or folder.days[slot] != "ok":  # one good backup makes the day good
                folder.days[slot] = "ok" if result.status == "ok" else "failed"
    return [stats[name] for name in names]


async def usage(remote: str) -> dict | None:
    """Space on the destination (total, used, free), or None when the backend cannot tell."""
    try:
        code, lines = await rclone.output("about", remote if is_local(remote) else remote_name(remote), "--json",
                                          timeout=45)
    except rclone.RcloneMissing:
        return None
    if code != 0:
        return None
    try:
        data = json.loads("".join(line.msg for line in lines if line.level == "text"))
    except ValueError:
        return None
    return data if isinstance(data, dict) else None
