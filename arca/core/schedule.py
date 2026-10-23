"""When Arca backs up by itself: a Windows Task Scheduler task that runs `arca run --headless`.

Two modes, "logon" (a few minutes after signing in to Windows) and "daily" (every day at DAILY_AT; a day the
computer was off at that hour runs as soon as it is on again), and a switch: off keeps the task, disabled, so turning
it back on finds the same mode; no task at all is off too. The task is registered from XML
for the current user, which needs no administrator rights, unlike `schtasks /SC ONLOGON`. It runs pythonw, so no
console window opens; what happened goes to the history, and the interface shows it next time.
"""
from __future__ import annotations

import getpass
import os
import re
import subprocess
import sys
import tempfile
from datetime import date
from pathlib import Path
from xml.sax.saxutils import escape

TASK = "Arca"
OLD_TASKS = ("rclone backup", "rclone-backup-windows")  # backup.ps1's own task: replaced, never run twice
MODES = ("logon", "daily")
DEFAULT_MODE = "daily"  # with no task yet: a missed day still runs, and it never surprises at sign-in
DAILY_AT = "21:00"
LOGON_DELAY = "PT2M"  # after signing in, let the network come up first
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

TRIGGERS = {
    "logon": """<LogonTrigger><Enabled>true</Enabled><UserId>{user}</UserId><Delay>{delay}</Delay></LogonTrigger>""",
    "daily": """<CalendarTrigger><StartBoundary>{day}T{at}:00</StartBoundary><Enabled>true</Enabled>"""
             """<ScheduleByDay><DaysInterval>1</DaysInterval></ScheduleByDay></CalendarTrigger>""",
}

TASK_XML = """<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.2" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo><Description>Arca: backs up the folders chosen in Arca.</Description></RegistrationInfo>
  <Triggers>{trigger}</Triggers>
  <Principals>
    <Principal id="Author"><UserId>{user}</UserId><LogonType>InteractiveToken</LogonType>
    <RunLevel>LeastPrivilege</RunLevel></Principal>
  </Principals>
  <Settings>
    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>
    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>
    <StartWhenAvailable>true</StartWhenAvailable>
    <RunOnlyIfNetworkAvailable>true</RunOnlyIfNetworkAvailable>
    <ExecutionTimeLimit>PT12H</ExecutionTimeLimit>
    <Enabled>{enabled}</Enabled>
  </Settings>
  <Actions Context="Author"><Exec><Command>{command}</Command><Arguments>{arguments}</Arguments></Exec></Actions>
</Task>
"""


def _schtasks(*args: str) -> tuple[int, str]:
    try:
        done = subprocess.run(["schtasks", *args], capture_output=True, text=True, creationflags=NO_WINDOW,
                              stdin=subprocess.DEVNULL, timeout=30)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return -1, str(exc)
    return done.returncode, (done.stdout + done.stderr).strip()


def _user() -> str:
    domain = os.environ.get("USERDOMAIN", "")
    return f"{domain}\\{getpass.getuser()}" if domain else getpass.getuser()


def command() -> tuple[str, str]:
    """The program and arguments the task runs: this same Python, windowless, as `arca run --headless`."""
    python = Path(sys.executable)
    windowless = python.with_name("pythonw.exe")
    return str(windowless if windowless.exists() else python), "-m arca run --headless"


def task_xml(mode: str, on: bool = True, today: date | None = None) -> str:
    user = escape(_user())
    trigger = TRIGGERS[mode].format(user=user, delay=LOGON_DELAY, day=(today or date.today()).isoformat(),
                                    at=DAILY_AT)
    program, arguments = command()
    return TASK_XML.format(trigger=trigger, user=user, command=escape(program), arguments=escape(arguments),
                           enabled=str(on).lower())


def current() -> tuple[str, bool]:
    """(mode, on) as Windows has it registered; no task is (DEFAULT_MODE, False)."""
    code, out = _schtasks("/Query", "/TN", TASK, "/XML", "ONE")
    if code != 0:
        return DEFAULT_MODE, False
    mode = "logon" if "<LogonTrigger" in out else "daily" if "<CalendarTrigger" in out else DEFAULT_MODE
    settings = re.search(r"<Settings>.*?<Enabled>(\w+)</Enabled>", out, re.S)  # the task's own, not a trigger's
    return mode, settings is None or settings.group(1) == "true"


def apply(mode: str, on: bool = True) -> str | None:
    """Register the task for `mode`, enabled or not. Returns Windows' own words when it refused, else None. The old
    script's task goes too, so the same folders are never backed up twice."""
    if mode not in MODES:
        raise ValueError(mode)
    with tempfile.TemporaryDirectory() as folder:
        file = Path(folder) / "arca-task.xml"
        file.write_text(task_xml(mode, on), encoding="utf-16")  # schtasks reads task XML as UTF-16
        code, out = _schtasks("/Create", "/TN", TASK, "/XML", str(file), "/F")
    problem = out if code != 0 else None
    for old in OLD_TASKS if problem is None else ():
        if _schtasks("/Query", "/TN", old)[0] == 0:
            _schtasks("/Delete", "/TN", old, "/F")
    return problem
