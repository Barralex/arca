"""Talking to the rclone binary: finding it, running it, reading its JSON log line by line."""
from __future__ import annotations

import asyncio
import json
import re
import shutil
import subprocess
from collections.abc import Callable
from dataclasses import dataclass

NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
PLAIN_ERROR = re.compile(r"\b(ERROR|CRITICAL|Failed to)\b")
AUTH_LINK = re.compile(r"https?://127\.0\.0\.1:\d+/auth\S*")
# Backends that sign in through the browser: without a token they are signed out.
OAUTH_TYPES = {"drive", "onedrive", "dropbox", "googlephotos", "box", "pcloud", "yandex", "zoho", "hidrive",
               "jottacloud", "putio", "sharefile", "pikpak", "premiumizeme"}


class RcloneMissing(Exception):
    """rclone is not installed or not on PATH."""


def command() -> list[str] | None:
    exe = shutil.which("rclone")
    return [exe] if exe else None


@dataclass
class Line:
    """One line of rclone output. JSON log lines carry a level; anything else is plain text."""

    level: str
    msg: str
    obj: str = ""
    size: int | None = None
    stats: dict | None = None
    time: str = ""
    kind: str = ""  # rclone's objectType: *local.Object for a file, *drive.Fs for a whole remote

    @property
    def is_error(self) -> bool:
        return self.level in ("error", "critical")

    @property
    def is_file(self) -> bool:
        return bool(self.obj) and self.kind.endswith("Object")


def parse(raw: str) -> Line:
    raw = raw.rstrip("\r\n")
    if raw.startswith("{"):
        try:
            data = json.loads(raw)
        except ValueError:
            data = None
        if isinstance(data, dict):
            size = data.get("size")
            stats = data.get("stats")
            return Line(
                level=str(data.get("level", "info")),
                msg=str(data.get("msg", "")).strip(),
                obj=str(data.get("object") or ""),
                size=size if isinstance(size, int) else None,
                stats=stats if isinstance(stats, dict) else None,
                time=str(data.get("time", "")),
                kind=str(data.get("objectType") or ""),
            )
    return Line("error" if PLAIN_ERROR.search(raw) else "text", raw)


class Process:
    """One rclone run, its output handed over line by line as it arrives."""

    def __init__(self, args: list[str]):
        self.args = args
        self.stopped = False
        self._proc: asyncio.subprocess.Process | None = None

    async def run(self, on_line: Callable[[Line], None]) -> int:
        cmd = command()
        if cmd is None:
            raise RcloneMissing
        self._proc = await asyncio.create_subprocess_exec(
            *cmd,
            *self.args,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            limit=1 << 20,
            creationflags=NO_WINDOW,
        )
        if self.stopped:
            self._proc.terminate()
        assert self._proc.stdout is not None
        async for raw in self._proc.stdout:
            on_line(parse(raw.decode("utf-8", "replace")))
        return await self._proc.wait()

    def stop(self) -> None:
        self.stopped = True
        if self._proc is not None and self._proc.returncode is None:
            self._proc.terminate()


async def output(*args: str, timeout: float = 60) -> tuple[int, list[Line]]:
    """Run a short rclone command and collect everything it printed."""
    lines: list[Line] = []
    proc = Process(list(args))
    try:
        code = await asyncio.wait_for(proc.run(lines.append), timeout)
    except TimeoutError:
        proc.stop()
        return -1, [*lines, Line("error", f"rclone did not answer in {timeout:.0f}s")]
    except asyncio.CancelledError:
        proc.stop()  # the app is closing: never leave rclone running behind it
        raise
    return code, lines


async def list_remotes() -> list[str]:
    try:
        code, lines = await output("listremotes", timeout=20)
    except RcloneMissing:
        return []
    return [line.msg.strip() for line in lines if code == 0 and line.msg.strip().endswith(":")]


def sign_in(name: str, kind: str | None = None) -> Process:
    """Connect `name` again, or create it as a new account of type `kind`. rclone opens the browser and waits on
    its local port for the sign-in. No console: every question takes its default (--auto-confirm) and stdin is
    closed, so a question without a default fails instead of hanging."""
    args = ["config", "create", name, kind] if kind else ["config", "reconnect", f"{name.rstrip(':')}:"]
    return Process([*args, "--auto-confirm"])


def auth_link(text: str) -> str | None:
    """The local sign-in link rclone prints in case the browser did not open."""
    found = AUTH_LINK.search(text)
    return found.group(0) if found else None


async def disconnect(name: str) -> str | None:
    """Sign out on this machine: the account stays in rclone, without its token, until it is connected again.
    (Drive has no `rclone config disconnect` to revoke the token.) Returns rclone's words when it fails."""
    try:
        code, lines = await output("config", "update", name.rstrip(":"), "token=", "--non-interactive", timeout=20)
    except RcloneMissing as exc:
        return str(exc)
    return None if code == 0 else "\n".join(line.msg for line in lines if line.is_error) or f"exit {code}"


async def listing(source: str, filters: list[str]) -> dict[str, int]:
    """Every file under `source` that `filters` let through (none: every file), relative to it, with its size in
    bytes. `filters` are the arguments a backup uses (jobs.filter_args). Raises RcloneMissing, or RuntimeError
    with rclone's words when the listing fails; a folder it could not read is left out, the rest still listed."""
    code, lines = await output("lsf", "-R", "--files-only", "--format", "sp", source, *filters, "--use-json-log",
                               timeout=600)
    files: dict[str, int] = {}
    for line in lines:
        if line.level != "text" or not line.msg:  # rclone's own logs come as JSON
            continue
        size, _, path = line.msg.partition(";")  # size first: a ";" in a file name stays in the path
        files[path] = int(size) if size.isdigit() else 0
    if code != 0 and not files:
        raise RuntimeError("\n".join(line.msg for line in lines if line.is_error) or f"exit {code}")
    return files


def with_folders(files) -> set[str]:
    """The files plus the folders that hold one, ending in "/". A folder whose files are all excluded (like
    node_modules) is not among them: sync never creates it."""
    folders = {"/".join(parts[:depth]) + "/" for parts in (path.split("/") for path in files)
               for depth in range(1, len(parts))}
    return set(files) | folders


async def included(source: str, filters: list[str]) -> set[str]:
    """What a backup of `source` copies: files and the folders that hold them (see listing, with_folders)."""
    return with_folders(await listing(source, filters))


async def account_states() -> dict[str, bool | None]:
    """Each remote -> True signed in, False signed out, None nothing to sign in to (a disk, S3 keys). Read from
    `rclone config dump`: the tokens pass through memory to be checked for presence and are never kept."""
    try:
        code, lines = await output("config", "dump", timeout=20)
    except RcloneMissing:
        return {}
    try:
        data = json.loads("\n".join(line.msg for line in lines)) if code == 0 else {}
    except ValueError:
        return {}
    states: dict[str, bool | None] = {}
    for name, section in data.items():
        if not isinstance(section, dict):
            continue
        if section.get("service_account_file") or section.get("service_account_credentials"):
            states[f"{name}:"] = None
        elif "token" in section:
            states[f"{name}:"] = bool(section["token"])
        else:
            states[f"{name}:"] = False if section.get("type") in OAUTH_TYPES else None
    return states


async def list_remotes_long() -> list[tuple[str, str]]:
    """Each remote with its backend type: [("gdrive:", "drive"), ...]."""
    try:
        code, lines = await output("listremotes", "--long", timeout=20)
    except RcloneMissing:
        return []
    remotes = []
    for line in lines:
        name, _, kind = line.msg.strip().partition(" ")
        if code == 0 and name.endswith(":"):
            remotes.append((name, kind.strip()))
    return remotes
