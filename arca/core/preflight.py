"""Before any job runs: is rclone there, is the remote configured, is the login still valid."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from arca.core import rclone
from arca.core.config import is_local, remote_name
from arca.core.errors import DRIVE_MISSING, NO_RCLONE, NO_REMOTE, Diagnosis, diagnose


@dataclass
class Health:
    ok: bool
    local: bool = False
    diagnosis: Diagnosis | None = None
    errors: list[str] = field(default_factory=list)


async def check(remote: str) -> Health:
    if not remote:
        return Health(False, diagnosis=NO_REMOTE)
    if rclone.command() is None:
        return Health(False, diagnosis=NO_RCLONE)
    if is_local(remote):
        anchor = Path(remote).anchor
        if anchor and not Path(anchor).exists():
            return Health(False, local=True, diagnosis=DRIVE_MISSING)
        return Health(True, local=True)
    # One cheap listing of the remote root proves the config and the login in a single call.
    code, lines = await rclone.output("lsf", remote_name(remote), "--max-depth", "1", "--dirs-only", "--use-json-log",
                                      timeout=45)
    if code == 0:
        return Health(True)
    errors = [line.msg for line in lines if line.is_error] or [line.msg for line in lines[-3:]]
    return Health(False, diagnosis=diagnose(errors), errors=errors)
