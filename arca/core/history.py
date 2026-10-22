"""One JSON line per run, so the interface can show what happened while it was closed."""
from __future__ import annotations

import json
import os
from pathlib import Path

from arca.core.config import home
from arca.core.jobs import Result

MAX_LINES = 2000


def path() -> Path:
    return home() / "history.jsonl"


def append(result: Result) -> None:
    file = path()
    file.parent.mkdir(parents=True, exist_ok=True)
    with file.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(result.to_record(), ensure_ascii=False) + "\n")
    _trim(file)


def add_past(results: list[Result]) -> int:
    """Merge runs that happened before Arca (an imported log) in time order, skipping any already recorded."""
    current = all_results()
    seen = {(r.job, r.finished) for r in current}
    new = [r for r in results if (r.job, r.finished) not in seen]
    if not new:
        return 0
    merged = sorted(current + new, key=lambda r: r.finished or r.started)
    file = path()
    file.parent.mkdir(parents=True, exist_ok=True)
    tmp = file.with_suffix(".tmp")
    tmp.write_text("".join(json.dumps(r.to_record(), ensure_ascii=False) + "\n" for r in merged[-MAX_LINES:]),
                   encoding="utf-8")
    os.replace(tmp, file)
    return len(new)


def all_results() -> list[Result]:
    file = path()
    if not file.exists():
        return []
    results: list[Result] = []
    for raw in file.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            results.append(Result.from_record(json.loads(raw)))
        except (ValueError, TypeError):
            continue  # a torn or hand-edited line never stops the app
    return results


def last_by_job(dry_run: bool = False) -> dict[str, Result]:
    """Each job's last real backup, or with `dry_run` its last dry run: a dry run never counts as a copy."""
    return {result.job: result for result in all_results() if result.dry_run == dry_run}


def _trim(file: Path) -> None:
    lines = file.read_text(encoding="utf-8", errors="replace").splitlines()
    if len(lines) > MAX_LINES:
        file.write_text("\n".join(lines[-MAX_LINES:]) + "\n", encoding="utf-8")
