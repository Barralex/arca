"""What the user ticks in a folder's Files list, turned into rclone filter rules for that folder.

Paths are relative to the folder, with "/" separators; a folder ends in "/" (as `rclone lsf` writes them). The
listing says what a backup copies today; the changes are the user's ticks on top of it; the rules are what makes
the next backup follow them. Rules are data in config.toml, never code.
"""
from __future__ import annotations

import re

GLOB_SPECIAL = re.compile(r"([*?\[\]{}\\])")


def ticked(path: str, included: set[str], changes: dict[str, bool]) -> bool:
    """Is `path` copied: the user's own tick on it or on the closest folder above it, else what rclone said."""
    if path in changes:
        return changes[path]
    parts = path.rstrip("/").split("/")
    for depth in range(len(parts) - 1, 0, -1):
        folder = "/".join(parts[:depth]) + "/"
        if folder in changes:
            return changes[folder]
    return path in included


def toggle(path: str, included: set[str], changes: dict[str, bool]) -> None:
    """Flip `path`. A folder takes everything inside with it; a tick that only restores what rclone would do
    anyway is dropped, so the rules stay as short as the user's real choices."""
    wanted = not ticked(path, included, changes)
    if path.endswith("/"):
        for inner in [key for key in changes if key.startswith(path) and key != path]:
            del changes[inner]
    changes.pop(path, None)
    if ticked(path, included, changes) != wanted:
        changes[path] = wanted


def total(sizes: dict[str, int], included: set[str], changes: dict[str, bool]) -> tuple[int, int]:
    """How many files, and how many bytes, the ticks put in the backup. `sizes` is every file in the folder."""
    picked = [size for path, size in sizes.items() if ticked(path, included, changes)]
    return len(picked), sum(picked)


def rule(path: str, keep: bool) -> str:
    """"- /drafts/**" for a folder, "+ /notes.log" for a file. Anchored at the folder, glob characters escaped."""
    escaped = GLOB_SPECIAL.sub(r"\\\1", path)
    return f"{'+' if keep else '-'} /{escaped}{'**' if path.endswith('/') else ''}"


def merge(changes: dict[str, bool], old: list[str]) -> list[str]:
    """The folder's new rules: the fresh choices first, deepest first (rclone takes the first rule that matches),
    then the old rules that no fresh choice replaces."""
    fresh = [rule(path, keep) for path, keep in sorted(changes.items(), key=lambda item: -_depth(item[0]))]
    kept = [line for line in old if not any(_covers(path, _rule_path(line)) for path in changes)]
    return fresh + kept


def _depth(path: str) -> int:
    return len(path.rstrip("/").split("/"))


def _rule_path(line: str) -> str:
    path = line[2:].lstrip("/")
    if path.endswith("**"):
        path = path[:-2]
    return re.sub(r"\\(.)", r"\1", path)  # unescape


def _covers(changed: str, path: str) -> bool:
    return path == changed or (changed.endswith("/") and path.startswith(changed))
