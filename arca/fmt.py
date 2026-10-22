"""Short human formats for sizes, durations and moments."""
from __future__ import annotations

from datetime import datetime
from pathlib import Path

from arca.i18n import t


def path(value: str | Path, width: int = 0) -> str:
    """C:\\Users\\me\\OneDrive\\Docs -> ~\\OneDrive\\Docs, cut from the left when wider than width."""
    text = str(value)
    home = str(Path.home())
    if text.lower().startswith(home.lower()):
        text = "~" + text[len(home):]
    if width and len(text) > width:
        text = "…" + text[-(width - 1):]
    return text


def size(n: float | None) -> str:
    if n is None:
        return "–"
    value = float(n)
    for unit in ("B", "KB", "MB", "GB"):
        if value < 1024:
            return f"{value:.0f} {unit}" if unit == "B" else f"{value:.1f} {unit}"
        value /= 1024
    return f"{value:.1f} TB"


def duration(seconds: float | None) -> str:
    if seconds is None:
        return "–"
    s = int(round(seconds))
    if s < 60:
        return f"{s}s"
    if s < 3600:
        return f"{s // 60}m{s % 60:02d}s"
    return f"{s // 3600}h{s % 3600 // 60:02d}m"


def ago(iso: str, now: datetime | None = None) -> str:
    try:
        then = datetime.fromisoformat(iso)
    except ValueError:
        return "–"
    now = now or datetime.now(then.tzinfo)
    seconds = (now - then).total_seconds()
    if seconds < 60:
        return t("now")
    if seconds < 3600:
        span = f"{int(seconds // 60)}m"
    elif seconds < 86400:
        span = f"{int(seconds // 3600)}h"
    else:
        span = f"{int(seconds // 86400)}d"
    return t("ago", t=span)


def clock(iso: str) -> str:
    """2026-10-21T21:00:05-03:00 -> 2026-10-21 21:00"""
    return iso[:16].replace("T", " ") if len(iso) >= 16 else iso
