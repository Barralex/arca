from pathlib import Path

import pytest

from arca.core import rclone, schedule
from arca.i18n import set_language


@pytest.fixture(autouse=True)
def arca_home(tmp_path, monkeypatch):
    """Every test gets its own settings folder; nothing touches the real %APPDATA%\\arca."""
    home = tmp_path / "arca-home"
    monkeypatch.setenv("ARCA_HOME", str(home))
    set_language("en")
    return home


@pytest.fixture(autouse=True)
def task_scheduler(monkeypatch):
    """No test ever touches this machine's Task Scheduler: a stand-in remembers the mode instead."""
    registered = {"mode": "manual"}

    def apply(mode):
        registered["mode"] = mode

    monkeypatch.setattr(schedule, "current", lambda: registered["mode"])
    monkeypatch.setattr(schedule, "apply", apply)
    return registered


@pytest.fixture
def every_file(monkeypatch):
    """A stand-in for `rclone lsf -R`: a folder's Files list shows everything as copied, without spawning rclone."""
    async def listing(source, filters):
        root = Path(source)
        return {p.relative_to(root).as_posix(): p.stat().st_size for p in root.rglob("*") if p.is_file()}

    monkeypatch.setattr(rclone, "listing", listing)
