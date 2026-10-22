"""When Arca backs up by itself: a Windows Task Scheduler task that runs `arca run --headless`.

Three modes: "manual" (no task), "logon" (a few minutes after signing in to Windows) and "daily" (every day at
DAILY_AT; a day the computer was off at that hour runs as soon as it is on again). The task is registered from XML
for the current user, which needs no administrator rights, unlike `schtasks /SC ONLOGON`. It runs pythonw, so no
console window opens; what happened goes to the history, and the interface shows it next time.
"""
from __future__ import annotations

import getpass
import os
import subprocess
import sys
import tempfile
from datetime import date
from pathlib import Path
from xml.sax.saxutils import escape

TASK = "Arca"
OLD_TASK = "rclone-backup-windows"  # what backup.ps1's instructions registered: replaced, never run twice
MODES = ("manual", "logon", "daily")
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
    <Enabled>true</Enabled>
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


def task_xml(mode: str, today: date | None = None) -> str:
    user = escape(_user())
    trigger = TRIGGERS[mode].format(user=user, delay=LOGON_DELAY, day=(today or date.today()).isoformat(),
                                    at=DAILY_AT)
    program, arguments = command()
    return TASK_XML.format(trigger=trigger, user=user, command=escape(program), arguments=escape(arguments))


def current() -> str:
    """The mode the registered task is in, read back from Windows; "manual" when there is none."""
    code, out = _schtasks("/Query", "/TN", TASK, "/XML", "ONE")
    if code != 0:
        return "manual"
    if "<LogonTrigger" in out:
        return "logon"
    return "daily" if "<CalendarTrigger" in out else "manual"


def apply(mode: str) -> str | None:
    """Register (or remove) the task for `mode`. Returns Windows' own words when it refused, else None. The old
    script's task goes too, so the same folders are never backed up twice."""
    if mode not in MODES:
        raise ValueError(mode)
    if mode == "manual":
        code, out = _schtasks("/Delete", "/TN", TASK, "/F")
        problem = out if code != 0 and current() != "manual" else None
    else:
        with tempfile.TemporaryDirectory() as folder:
            file = Path(folder) / "arca-task.xml"
            file.write_text(task_xml(mode), encoding="utf-16")  # schtasks reads task XML as UTF-16
            code, out = _schtasks("/Create", "/TN", TASK, "/XML", str(file), "/F")
        problem = out if code != 0 else None
    if problem is None and _schtasks("/Query", "/TN", OLD_TASK)[0] == 0:
        _schtasks("/Delete", "/TN", OLD_TASK, "/F")
    return problem
