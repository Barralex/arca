"""rclone failures people actually hit, turned into a cause, a fix and the action that applies it.

Every rule here is matched against rclone's own error lines. An error no rule knows is still reported, verbatim,
as "unknown": nothing is ever swallowed.
"""
from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass


@dataclass(frozen=True)
class Diagnosis:
    cause: str  # i18n key
    fix: str  # i18n key
    action: str | None = None  # app action that applies the fix: reconnect, remote, retry, edit

    def to_record(self) -> dict:
        return {"cause": self.cause, "fix": self.fix, "action": self.action}

    @classmethod
    def from_record(cls, data: dict | None) -> Diagnosis | None:
        if not data or "cause" not in data:
            return None
        return cls(data["cause"], data.get("fix", "fix.unknown"), data.get("action"))


RULES: list[tuple[re.Pattern[str], Diagnosis]] = [
    (
        re.compile(r"empty token found", re.I),  # signed out on purpose, from Accounts
        Diagnosis("err.disconnected", "fix.disconnected", "reconnect"),
    ),
    (
        re.compile(r"invalid_grant|token expired|couldn't fetch token|cannot fetch token", re.I),
        Diagnosis("err.login", "fix.login", "reconnect"),
    ),
    (
        re.compile(r"didn't find section in config file|not found in config file", re.I),
        Diagnosis("err.config", "fix.config", "remote"),
    ),
    (
        re.compile(r"storageQuotaExceeded|quota exceeded|insufficient storage|no space left|not enough space", re.I),
        Diagnosis("err.quota", "fix.quota", "remote"),
    ),
    (
        re.compile(r"no such host|dial tcp|connection refused|i/o timeout|handshake timeout|network is unreachable",
                   re.I),
        Diagnosis("err.network", "fix.network", "retry"),
    ),
    (
        re.compile(r"error reading source root directory: directory not found", re.I),
        Diagnosis("err.source", "fix.source", "edit"),
    ),
]

UNKNOWN = Diagnosis("err.unknown", "fix.unknown")
SOURCE_MISSING = Diagnosis("err.source", "fix.source", "edit")
NO_RCLONE = Diagnosis("err.norclone", "fix.norclone")
NO_REMOTE = Diagnosis("err.noremote", "fix.noremote", "remote")
DRIVE_MISSING = Diagnosis("err.drive", "fix.drive", "remote")


def diagnose(errors: Iterable[str]) -> Diagnosis | None:
    text = "\n".join(errors)
    if not text.strip():
        return None
    for pattern, diagnosis in RULES:
        if pattern.search(text):
            return diagnosis
    return UNKNOWN
