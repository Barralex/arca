"""Settings and jobs, kept in one TOML file that the app writes and a person can still edit by hand."""
from __future__ import annotations

import json
import os
import re
import tomllib
from dataclasses import dataclass, field
from datetime import datetime
from importlib import resources
from pathlib import Path

# Characters a job name cannot hold: it becomes a folder name on the remote.
NAME_FORBIDDEN = re.compile(r'[\\/:*?"<>|]')
RESERVED_NAMES = {"_versions", "_versiones"}  # archive folders, new and from the old script


class ConfigError(Exception):
    """The config file exists but cannot be read."""


@dataclass
class Job:
    name: str
    source: str
    enabled: bool = True
    # This folder's own choices, as rclone filter lines ("- /drafts/**", "+ /notes.log"). They go before filter.txt,
    # so they win over it: what the user ticked or unticked in the Files list.
    rules: list[str] = field(default_factory=list)


@dataclass
class Config:
    remote: str = ""
    language: str = "auto"
    versions: str = "_versions"  # archive folder on the remote; import keeps an older one so history stays together
    theme: str = "tokyo-night"
    metrics_days: int = 14  # how far back the metrics page draws each folder's days
    jobs: list[Job] = field(default_factory=list)

    def job(self, name: str) -> Job | None:
        return next((job for job in self.jobs if job.name == name), None)


def home() -> Path:
    """Where Arca keeps its own files. ARCA_HOME overrides it (tests, portable use)."""
    override = os.environ.get("ARCA_HOME")
    if override:
        return Path(override)
    appdata = os.environ.get("APPDATA")
    return (Path(appdata) if appdata else Path.home() / ".config") / "arca"


def config_path() -> Path:
    return home() / "config.toml"


def filter_path() -> Path:
    """The exclude policy. Created from the bundled default the first time it is needed."""
    path = home() / "filter.txt"
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        default = resources.files("arca").joinpath("default_filter.txt").read_text(encoding="utf-8")
        path.write_text(default, encoding="utf-8")
    return path


def load(path: Path | None = None) -> Config:
    path = path or config_path()
    if not path.exists():
        return Config()
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
        raise ConfigError(f"{path}: {exc}") from exc
    jobs = [
        Job(str(raw["name"]), str(raw["source"]), bool(raw.get("enabled", True)),
            [str(rule) for rule in raw.get("rules", []) if isinstance(rule, str)])
        for raw in data.get("jobs", [])
        if isinstance(raw, dict) and "name" in raw and "source" in raw
    ]
    days = data.get("metrics_days", 14)
    return Config(remote=str(data.get("remote", "")), language=str(data.get("language", "auto")),
                  versions=str(data.get("versions", "_versions")) or "_versions",
                  theme=str(data.get("theme", "tokyo-night")) or "tokyo-night",
                  metrics_days=days if isinstance(days, int) and 1 <= days <= 90 else 14, jobs=jobs)


def save(cfg: Config, path: Path | None = None) -> None:
    path = path or config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "# Arca settings. Written by the app; safe to edit by hand while it is closed.",
        f"remote = {_quote(cfg.remote)}",
        f"language = {_quote(cfg.language)}",
        f"versions = {_quote(cfg.versions)}",
        f"theme = {_quote(cfg.theme)}",
        f"metrics_days = {cfg.metrics_days}",
    ]
    for job in cfg.jobs:
        lines += [
            "",
            "[[jobs]]",
            f"name = {_quote(job.name)}",
            f"source = {_quote(job.source)}",
            f"enabled = {'true' if job.enabled else 'false'}",
        ]
        if job.rules:
            lines.append("rules = [" + ", ".join(_quote(rule) for rule in job.rules) + "]")
    tmp = path.with_suffix(".tmp")
    tmp.write_text("\n".join(lines) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def reset() -> Path | None:
    """Start over without losing anything: settings and history move to previous/<stamp>/, where they can be
    copied back. The filter stays (it is the user's own policy, not state). Returns that folder, or None if there
    was nothing to move."""
    files = [home() / name for name in ("config.toml", "history.jsonl") if (home() / name).exists()]
    if not files:
        return None
    target = previous_dir() / datetime.now().strftime("%Y-%m-%d_%H%M%S")
    target.mkdir(parents=True, exist_ok=True)
    for file in files:
        os.replace(file, target / file.name)
    return target


def previous_dir() -> Path:
    return home() / "previous"


def _quote(value: str) -> str:
    # A JSON string is a valid TOML basic string, escapes included.
    return json.dumps(value, ensure_ascii=False)


def name_problem(name: str, taken: set[str]) -> str | None:
    """The i18n key explaining why a job name is not usable, or None if it is fine."""
    if not name.strip() or NAME_FORBIDDEN.search(name) or name in RESERVED_NAMES:
        return "add.err.name"
    if name in taken:
        return "add.err.dup"
    return None


# --- remotes -------------------------------------------------------------------------------------------------------


def is_local(remote: str) -> bool:
    """True for a plain path (D:\\Backups, \\\\nas\\share); False for an rclone remote (gdrive:Backups)."""
    head, sep, _ = remote.partition(":")
    return not sep or len(head) == 1 or remote.startswith(("\\\\", "/"))


def remote_name(remote: str) -> str:
    """gdrive:Backups -> gdrive:"""
    return remote.partition(":")[0] + ":"


def join(remote: str, *parts: str) -> str:
    """Append path parts to a remote, without the stray slash rclone would read as the root of gdrive:."""
    base = remote.rstrip("/\\") or remote
    for part in parts:
        bare_remote = base.endswith(":") and not is_local(base)
        base = f"{base}{part}" if bare_remote else f"{base}/{part}"
    return base
