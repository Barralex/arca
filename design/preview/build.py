#!/usr/bin/env python3
"""Render the README screenshot: the home menu and a folder that failed, from the app itself.

    uv run python design/preview/build.py

Writes assets/brand/preview.png (1860x1050). Needs Chrome or Edge (CHROME overrides the path).
The app runs headless with demo data only: a throwaway ARCA_HOME, a made-up history, and rclone,
the health check and the Task Scheduler replaced, so nothing on this machine is read or shown.
Each screen is saved as Textual's SVG and preview.html lays the two windows out.
"""
import asyncio
import os
import pathlib
import shutil
import sys
import tempfile
from datetime import datetime, timedelta

ROOT = pathlib.Path(__file__).resolve().parents[2]
PAGE = ROOT / "design" / "preview" / "preview.html"
OUT = ROOT / "assets" / "brand" / "preview.png"
WIDTH, HEIGHT = 1860, 1050
SIZE = (118, 40)  # terminal cells of each window

sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "design" / "banner"))
from build import shoot  # noqa: E402  the Chrome runner lives with the banner

HOME = pathlib.Path.home()  # shown as ~, never the user's name
FOLDERS = [  # name, source, hours ago, files, bytes, seconds
    ("Documents", HOME / "Documents", 3, 1840, 412_000_000, 95),
    ("Pictures", HOME / "Pictures", 3, 12_406, 21_300_000_000, 1310),
    ("Projects", HOME / "Projects", 3, 3_517, 980_000_000, 240),
    ("Music", pathlib.Path("E:/Music"), 27, 0, 0, 2),  # the external drive is unplugged
]


def demo():
    """A config and a history that look lived in, and every outside call answered from here."""
    from arca.core import config, history, preflight, rclone, schedule
    from arca.core.config import Config, Job
    from arca.core.errors import SOURCE_MISSING
    from arca.core.jobs import Result

    async def remotes():
        return ["gdrive"]

    async def accounts():
        return {"gdrive": True}

    async def healthy(remote):
        return preflight.Health(True)

    rclone.list_remotes, rclone.account_states, preflight.check = remotes, accounts, healthy
    schedule.current, schedule.apply = (lambda: "daily"), (lambda mode: None)

    config.save(Config(remote="gdrive:Backups", jobs=[Job(name, str(source)) for name, source, *_ in FOLDERS]))
    now = datetime.now().replace(microsecond=0)
    for name, _, hours, files, size, took in FOLDERS:
        end = now - timedelta(hours=hours)
        failed = name == "Music"
        history.append(Result(job=name, started=(end - timedelta(seconds=took)).isoformat(),
                              finished=end.isoformat(), duration=took, status="failed" if failed else "ok",
                              code=3 if failed else 0, transfers=files, bytes=size,
                              errors=["directory not found: E:\\Music"] if failed else [],
                              diagnosis=SOURCE_MISSING if failed else None))


async def screens(folder):
    from arca.core import config
    from arca.i18n import set_language
    from arca.ui.app import ArcaApp

    set_language("en")
    app = ArcaApp(config.load())
    async with app.run_test(size=SIZE) as pilot:
        await pilot.pause(1.5)
        app.save_screenshot(str(folder / "home.svg"))
        await pilot.press("enter")  # Folders
        await pilot.pause(0.5)
        await pilot.press("down", "down", "down")  # Music: its cause and fix show under the row
        await pilot.pause(0.5)
        app.save_screenshot(str(folder / "folders.svg"))


def main():
    with tempfile.TemporaryDirectory() as tmp:
        folder = pathlib.Path(tmp)
        os.environ["ARCA_HOME"] = str(folder / "arca")
        demo()
        asyncio.run(screens(folder))
        shutil.copy(PAGE, folder / PAGE.name)
        shoot((folder / PAGE.name).as_uri(), str(OUT), WIDTH, HEIGHT)
    print("wrote", OUT.relative_to(ROOT).as_posix(), f"{OUT.stat().st_size // 1024} KB")


if __name__ == "__main__":
    main()
