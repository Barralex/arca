"""The Arca interface. It opens on the home menu; the folders list (masthead, status, the folders table, "add
folders") is the screen underneath, and every other page is pushed over it: Enter on a folder opens its own screen."""
from __future__ import annotations

import asyncio
import os
import sys
import time
from collections import deque
from dataclasses import dataclass, replace

from rich.text import Text
from textual import on, work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import Screen
from textual.widgets import DataTable, Footer, OptionList, Static
from textual.widgets.option_list import Option

from arca import __version__, fmt
from arca.core import config, history, importer, preflight, rclone, schedule
from arca.core.config import Config, Job, is_local, remote_name
from arca.core.importer import Candidate, Plan
from arca.core.jobs import JobRun, Progress, Result, failed_before_start
from arca.core.preflight import Health
from arca.core.rclone import Line
from arca.i18n import STRINGS, language, t
from arca.ui.folder import FolderScreen
from arca.ui.masthead import Masthead
from arca.ui.pages import AccountsScreen, HomeScreen, ImportScreen, LinkRow, MetricsScreen, row
from arca.ui.screens import (
    ConfirmScreen,
    HelpScreen,
    PickFoldersScreen,
    RemoteScreen,
    bind,
    hint_text,
)

COLUMNS = ("status", "name", "source", "last", "duration", "files", "size", "errors", "now")
STATES = {  # state -> (symbol, colour); the word comes from i18n, so colour is never the only signal
    "idle": ("○", "grey50"),
    "queued": ("◌", "yellow"),
    "running": ("●", "#22b8d6"),
    "ok": ("✓", "green"),
    "failed": ("✗", "red"),
    "skipped": ("!", "yellow"),
    "stopped": ("■", "yellow"),
    "off": ("–", "grey35"),
}
# (keys, action, label, shown in the footer) of the folders list. The footer is quiet: only the way out (Esc to the
# menu, q to quit). Enter on a folder opens everything it can do; the hint line and ? list the keys.
KEYS = (
    ("escape", "home", "key.menu", True),
    ("r", "run", "key.run", False),
    ("R", "run_all", "key.run_all", False),
    ("n", "dry", "key.dry", False),
    ("s", "stop", "key.stop", False),
    ("a", "add", "key.add", False),
    ("d", "delete", "key.delete", False),
    ("delete,backspace", "delete", "key.delete", False),  # Del removes, as in any list; red in the hint
    ("space", "toggle", "key.toggle", False),
    ("m", "remote", "key.remote", False),
    ("f", "filters", "key.filters", False),
    ("x", "reconnect", "key.reconnect", False),
    ("c", "check", "key.check", False),
    ("l", "language", "key.lang", False),
    ("question_mark", "help", "key.help", False),
    ("q", "quit", "key.quit", True),
)
FOLDER_ACTIONS = ("run", "run_all", "dry", "delete", "toggle")  # need a folder: out of the footer while there is none
EMPTY_STEPS = (("add", "a"), ("import", ""), ("home", "esc"))  # what the empty folders view offers, with its key
APP_KEYS = ("question_mark", "q")  # these work on every screen, the home menu included
SOURCE_WIDTH = 34
CTRL_C_WINDOW = 2.0  # seconds within which a second ctrl+c quits
SIGN_IN_TIMEOUT = 300.0  # seconds rclone waits for the browser sign-in before giving up
HINTS = ("reconnect", "remote", "retry")  # diagnosis actions with a one-key hint in the header


@dataclass
class View:
    """What the interface remembers about one job between runs."""

    state: str = "idle"
    result: Result | None = None  # the last real backup: the table, the summary and the metrics count only these
    trial: Result | None = None  # the last dry run after it, shown apart as what a backup would do
    progress: Progress | None = None


class JobsTable(DataTable):
    """The folders. Enter or → opens one; ↓ on the last walks on to "add folders": every list in Arca hands the
    focus on at its ends."""

    BINDINGS = [Binding("right", "select_cursor", show=False)]

    def action_cursor_down(self) -> None:
        if self.cursor_row >= self.row_count - 1:
            self.screen.focus_next()
            return
        super().action_cursor_down()


class ArcaApp(App[str]):
    TITLE = "Arca"
    CSS_PATH = "arca.tcss"
    ALLOW_SELECT = False  # a click is a click, not the start of a purple text selection
    ENABLE_COMMAND_PALETTE = False  # every action is a key or a menu line; ^p would be one more thing to explain

    def __init__(self, cfg: Config, home: bool = True, focus: str | None = None):
        super().__init__()
        self.cfg = cfg
        self.start_home = home
        self.start_focus = focus  # a widget on the home screen to focus first, after a restart from a setting
        self.filter_file = config.filter_path()
        self.views: dict[str, View] = {job.name: View() for job in cfg.jobs}
        self._load_results()
        self.health: Health | None = None
        self.remotes: list[str] | None = None  # None until rclone has been asked
        self.accounts: dict[str, bool | None] | None = None  # remote -> signed in; None until asked
        self.queue: deque[tuple[str, bool]] = deque()
        self.current: JobRun | None = None
        self._runner_busy = False
        self._ctrl_c_at = -CTRL_C_WINDOW
        self.schedule_mode = "manual"  # what Windows has registered; read on open
        self.signing: tuple[str, str] | None = None  # (account, link) while rclone waits for a browser sign-in
        self._sign_in_proc: rclone.Process | None = None
        for keys, action, label, show in KEYS:
            if keys in APP_KEYS:
                bind(self, keys, action, label, show)

    # --- layout -----------------------------------------------------------------------------------------------------

    def compose(self) -> ComposeResult:
        yield Masthead(id="masthead")
        yield Static(classes="status")
        yield Static(id="summary")
        with Vertical(id="empty", classes="panel"):
            yield Static(id="empty-text")
            yield OptionList(id="empty-menu")
        yield Static(hint_text(t("hint.empty")), id="empty-hint", classes="hint-line")
        yield JobsTable(id="jobs", cursor_type="row", zebra_stripes=True)
        yield Static(id="row-note")  # the highlighted folder's last failure: cause and fix, never silent
        yield LinkRow(t("folders.add"), t("folders.add.desc"), id="add-folders")
        yield Static(hint_text(t("hint.folders"), t("hint.files.delete")), id="folders-hint", classes="hint-line")
        yield Footer()

    def on_mount(self) -> None:
        self.theme = self.cfg.theme if self.cfg.theme in self.available_themes else "tokyo-night"
        # The folder keys belong to the folders view, so the home menu and the pages never act on a folder
        # nobody can see.
        for keys, action, label, show in KEYS:
            if keys not in APP_KEYS:
                bind(self.main, keys, f"app.{action}", label, show)
        table = self.main.query_one(DataTable)
        for key in COLUMNS:
            table.add_column(t(f"col.{key}"), key=key, width=SOURCE_WIDTH if key == "source" else None)
        self._fill_table()
        self.render_status()
        self._focus_folders()
        self.action_check()
        self._read_schedule()
        self.set_interval(30, self._refresh_times)
        if self.start_focus == "reset":  # reopened after "delete the configuration"
            self.start_focus = None
            self.notify(t("msg.reset", path=fmt.path(config.previous_dir())), timeout=12)
        if self.start_home:
            self.push_screen(HomeScreen(self.start_focus))

    @property
    def main(self) -> Screen:
        """The folders view. Queries go through it, not the active screen: a backup keeps updating its rows while
        the home menu or a dialog is on top."""
        return self.screen_stack[0]

    # --- selection and table ----------------------------------------------------------------------------------------

    @property
    def selected(self) -> Job | None:
        table = self.main.query_one(DataTable)
        if not self.cfg.jobs or table.row_count == 0:
            return None
        return self.cfg.jobs[min(table.cursor_row, len(self.cfg.jobs) - 1)]

    def _state(self, job: Job) -> str:
        view = self.views[job.name]
        if view.state in ("queued", "running"):
            return view.state
        if not job.enabled:
            return "off"
        result = view.result
        if result is None:
            return "idle"
        return result.status

    def _cells(self, job: Job) -> list[object]:
        view = self.views[job.name]
        state = self._state(job)
        symbol, colour = STATES[state]
        result = view.result
        errors = len(result.errors) if result else 0
        return [
            Text(f"{symbol} {t('st.' + state)}", style=colour),
            Text(job.name, style="bold"),
            Text(fmt.path(job.source, SOURCE_WIDTH), style="grey70"),
            fmt.ago(result.finished) if result and result.finished else "–",
            fmt.duration(result.duration) if result else "–",
            str(result.transfers) if result else "–",
            fmt.size(result.bytes) if result else "–",
            Text(str(errors), style="red bold" if errors else "grey50") if result else "–",
            self._now_cell(view),
        ]

    @staticmethod
    def _now_cell(view: View) -> Text | str:
        if view.state != "running" or view.progress is None:
            return ""
        fraction = view.progress.fraction
        percent = f"{fraction * 100:3.0f}% " if fraction is not None else ""
        return Text(f"{percent}{fmt.size(view.progress.speed)}/s", style="#22b8d6")

    def _fill_table(self) -> None:
        table = self.main.query_one(DataTable)
        row = table.cursor_row
        table.clear()
        for job in self.cfg.jobs:
            table.add_row(*self._cells(job), key=job.name)
        has_jobs = bool(self.cfg.jobs)
        table.display = has_jobs
        for widget_id in ("#empty", "#empty-hint"):
            self.main.query_one(widget_id).display = not has_jobs
        for widget_id in ("#add-folders", "#folders-hint"):
            self.main.query_one(widget_id).display = has_jobs
        if has_jobs:
            table.move_cursor(row=min(row, len(self.cfg.jobs) - 1))
        else:
            self._fill_empty()
        if self.main.focused is None or not self.main.focused.display:
            self._focus_folders()
        self._render_summary()
        self._render_row_note()

    def _fill_empty(self) -> None:
        """No folders: not a sentence in an empty screen, but the panel of next steps, like the home menu."""
        self.main.query_one("#empty").border_title = t("home.folders")
        self.main.query_one("#empty-text", Static).update(Text(t("empty", remote=self.cfg.remote or "–"),
                                                               style="grey62"))
        menu = self.main.query_one("#empty-menu", OptionList)
        width = max(len(t(f"empty.{step}")) for step, _ in EMPTY_STEPS) + 2
        menu.set_options(Option(row(t(f"empty.{step}"), t(f"empty.{step}.desc"), key, width), id=step)
                         for step, key in EMPTY_STEPS)
        menu.highlighted = 0

    def _focus_folders(self) -> None:
        self.main.query_one("#jobs" if self.cfg.jobs else "#empty-menu").focus()

    @on(OptionList.OptionSelected, "#empty-menu")
    def _empty_step(self, event: OptionList.OptionSelected) -> None:
        step = event.option.id
        if step == "add":
            self.action_add()
        elif step == "import":
            self.open_page("import")
        else:
            self.action_home()

    def check_action(self, action: str, parameters: tuple[object, ...]) -> bool | None:
        # A key with nothing to act on leaves the footer instead of answering "no folder" when pressed.
        if action in FOLDER_ACTIONS and not self.cfg.jobs:
            return False
        if action == "stop" and self.current is None and not self.queue:
            return False
        return True

    def _update_row(self, name: str) -> None:
        job = self.cfg.job(name)
        if job is None:
            return
        table = self.main.query_one(DataTable)
        for key, value in zip(COLUMNS, self._cells(job), strict=True):
            table.update_cell(name, key, value)
        self._render_summary()

    def _refresh_times(self) -> None:
        for job in self.cfg.jobs:
            self._update_row(job.name)

    @on(DataTable.RowHighlighted)
    def _row_changed(self) -> None:
        self._render_row_note()

    @on(DataTable.RowSelected)
    def _row_chosen(self) -> None:
        if (job := self.selected) is not None:
            self.push_screen(FolderScreen(job))

    @on(LinkRow.Chosen, "#add-folders")
    def _add_chosen(self) -> None:
        self.action_add()

    # --- status, summary and detail ---------------------------------------------------------------------------------

    @property
    def no_account(self) -> bool:
        """Nothing to back up to: the destination is not a local disk and no account on this machine is signed in.
        Until one is, the home menu only opens Accounts."""
        if self.cfg.remote and is_local(self.cfg.remote) or self.accounts is None:
            return False
        return all(state is False for state in self.accounts.values())

    def render_status(self) -> None:
        # Quiet, like Bitácora's "Found:" line: say what is there; a problem is one soft line with the key that
        # fixes it. The full explanation lives in the Errors tab.
        if self.no_account:  # no account names nor the old destination: there is nothing connected to show
            line = Text.assemble((f"{t('status.remote')}  ", "bold"), ("–", "bold #4fd1e8"),
                                 (f"      {t('status.no_account')}", "grey50"),
                                 (f"\n{t('tagline')} · v{__version__}", "grey50"))
            self._show_status(line)
            return
        line = Text.assemble((f"{t('status.remote')}  ", "bold"),
                             (fmt.path(self.cfg.remote, 50) or "–", "bold #4fd1e8"))
        health = self.health
        if health is not None and health.ok:
            line.append(f"  {t('health.local') if health.local else t('health.ok')}", style="grey50")
        if self.remotes:
            line.append(f"      {t('status.found')} ", style="grey50")
            line.append(", ".join(name.rstrip(":") for name in self.remotes))
        elif self.remotes is not None:
            line.append(f"      {t('status.no_remotes')}", style="grey50")
        line.append(f"\n{t('tagline')} · v{__version__}", style="grey50")
        if health is None:
            line.append(f"\n{t('health.checking')}", style="grey50")
        elif not health.ok and health.diagnosis:
            hint = f" · {t('hint.' + health.diagnosis.action)}" if health.diagnosis.action in HINTS else ""
            line.append(f"\n{self._say(health.diagnosis.cause)}{hint}", style="#e0af68")
        self._show_status(line)

    def _show_status(self, line: Text) -> None:
        for screen in self.screen_stack:
            for widget in screen.query(".status"):
                widget.update(line)
        self._refresh_home()
        for screen in self._accounts():  # the destination's line says what the check found, not "checking…"
            if screen.is_mounted and screen.remotes:
                screen._draw_lines()

    def _render_summary(self) -> None:
        states = [self._state(job) for job in self.cfg.jobs]
        parts: list[tuple[str, str]] = [(t("sum.jobs", n=len(states)), "bold")]
        for state, key, colour in (("ok", "sum.ok", "green"), ("failed", "sum.failed", "red"),
                                   ("running", "sum.running", "#22b8d6"), ("queued", "sum.queued", "yellow"),
                                   ("off", "sum.off", "grey50")):
            count = states.count(state)
            if count:
                parts.append((f"{STATES[state][0]} {t(key, n=count)}", colour))
        finished = [v.result.finished for v in self.views.values() if v.result and v.result.finished]
        parts.append((t("sum.last", when=fmt.ago(max(finished))) if finished else t("sum.never"), "grey50"))
        line = Text()
        for i, (text, style) in enumerate(parts):
            if i:
                line.append("  ·  ", style="grey35")
            line.append(text, style=style)
        self.main.query_one("#summary", Static).update(line)
        self._refresh_home()

    def _say(self, key: str, job: Job | None = None) -> str:
        source = fmt.path(job.source) if job else ""
        return t(key, remote=self.cfg.remote, source=source, name=job.name if job else "")

    def failure_text(self, job: Job) -> Text | None:
        """What went wrong in the folder's last run and the fix; rclone's own words when nothing matched. None when
        it did not fail. A failed dry run counts too: it found a real problem."""
        view = self.views.get(job.name)
        result = view and (view.trial or view.result)
        if view is None or view.state in ("queued", "running") or not result or \
                result.status not in ("failed", "skipped"):
            return None
        text = Text()
        if result.diagnosis:
            text.append(f"✗ {self._say(result.diagnosis.cause, job)}", style="bold red")
            text.append(f"\n{self._say(result.diagnosis.fix, job)}", style="yellow")
        if result.errors:
            text.append(f"{chr(10) if text else ''}{t('rclone.said')} {result.errors[0]}", style="grey50")
        return text

    def _render_row_note(self) -> None:
        note = self.main.query_one("#row-note", Static)
        job = self.selected
        failure = self.failure_text(job) if job is not None else None
        note.display = failure is not None
        if failure is not None:
            note.update(failure)

    def _refresh_folder(self, job: Job) -> None:
        """The table row, the note under it and, if it is open, the folder's own screen."""
        self._update_row(job.name)
        self._render_row_note()
        for screen in self.screen_stack:
            if isinstance(screen, FolderScreen) and screen.job is job and screen.is_mounted:
                screen.refresh_head()

    # --- running ----------------------------------------------------------------------------------------------------

    def _enqueue(self, items: list[tuple[str, bool]]) -> None:
        waiting = {name for name, _ in self.queue}
        running = self.current.job.name if self.current else None
        for name, dry in items:
            if name in waiting or name == running:
                continue
            self.queue.append((name, dry))
            self.views[name].state = "queued"
            self._update_row(name)
        self._render_row_note()
        if not self._runner_busy:
            self._runner_busy = True
            self._run_queue()

    @work(group="runner")
    async def _run_queue(self) -> None:
        try:
            # One check per batch: if the remote is unreachable, every job would fail the same way.
            self.health = None
            self.render_status()
            self.health = await preflight.check(self.cfg.remote)
            self.render_status()
            while self.queue:
                name, dry = self.queue.popleft()
                job = self.cfg.job(name)
                if job is None:
                    continue
                if not self.health.ok:
                    self._finish(job, failed_before_start(job, dry, self.health.diagnosis, self.health.errors))
                    continue
                await self._run_one(job, dry)
        finally:
            self._runner_busy = False
            self.current = None

    async def _run_one(self, job: Job, dry: bool) -> None:
        view = self.views[job.name]
        view.state, view.progress = "running", Progress()
        self._refresh_folder(job)
        self.current = JobRun(job, self.cfg.remote, self.filter_file, dry_run=dry, versions=self.cfg.versions,
                              on_line=self._on_line)
        result = await self.current.run()
        self.current = None
        self._finish(job, result)

    def _on_line(self, run: JobRun, line: Line) -> None:
        job, view = run.job, self.views[run.job.name]
        if line.stats:
            view.progress = run.progress
            self._update_row(job.name)

    def _finish(self, job: Job, result: Result) -> None:
        view = self.views.get(job.name)
        if view is None:
            return
        view.state, view.progress = "idle", None
        if result.dry_run:
            view.trial = result
        else:
            view.result, view.trial = result, None
        history.append(result)
        self._refresh_folder(job)
        if result.status == "ok":
            self.notify(t("msg.done_dry" if result.dry_run else "msg.done", name=job.name, n=result.transfers,
                          size=fmt.size(result.bytes)))
        elif result.status == "stopped":
            self.notify(t("msg.stopped", name=job.name), severity="warning")
        else:
            cause = self._say(result.diagnosis.cause, job) if result.diagnosis else ""
            self.notify(t("msg.failed", name=job.name, cause=cause), severity="error", timeout=10)

    def _load_results(self) -> None:
        real, trials = history.last_by_job(), history.last_by_job(dry_run=True)
        for name, view in self.views.items():
            if view.state != "idle":
                continue
            view.result = real.get(name)
            trial = trials.get(name)
            newer = trial is not None and (view.result is None or trial.started > view.result.started)
            view.trial = trial if newer else None

    # --- actions ----------------------------------------------------------------------------------------------------

    def run_job(self, job: Job | None, dry: bool = False) -> bool:
        """Queue one folder's backup (or dry run). False, with a notice saying why, when it cannot run."""
        if job is None:
            self.notify(t("msg.no_job"), severity="warning")
            return False
        if not job.enabled:
            self.notify(t("msg.off", name=job.name), severity="warning")
            return False
        self._enqueue([(job.name, dry)])
        return True

    def action_run(self) -> None:
        self.run_job(self.selected)

    def action_dry(self) -> None:
        self.run_job(self.selected, dry=True)

    def action_run_all(self) -> None:
        jobs = [job for job in self.cfg.jobs if job.enabled]
        if not jobs:
            self.notify(t("msg.no_job"), severity="warning")
            return
        self._enqueue([(job.name, False) for job in jobs])

    def action_stop(self) -> None:
        if self.current is None and not self.queue:
            self.notify(t("msg.nothing_running"))
            return
        for name, _ in self.queue:
            self.views[name].state = "idle"
            self._update_row(name)
        self.queue.clear()
        if self.current is not None:
            self.current.stop()
        self._render_row_note()

    def action_help(self) -> None:
        lines: dict[str, tuple[str, str]] = {}  # one line per action: "d / Del  Remove"
        for keys, action, label, _ in KEYS:
            shown = keys.replace("question_mark", "?").replace("delete,backspace", t("key.del"))
            first, said = lines.get(action, ("", t(label)))
            lines[action] = (f"{first} / {shown}" if first else shown, said)
        self.push_screen(HelpScreen(list(lines.values())))

    def action_add(self) -> None:
        def added(jobs: list[Job] | None) -> None:
            if not jobs:
                return
            self.cfg.jobs.extend(jobs)
            for job in jobs:
                self.views[job.name] = View()
            config.save(self.cfg)
            self._fill_table()
            self.main.query_one(DataTable).move_cursor(row=len(self.cfg.jobs) - 1)
            self.notify(t("msg.added", name=jobs[0].name) if len(jobs) == 1 else
                        t("msg.added_many", n=len(jobs), names=", ".join(job.name for job in jobs)))

        self.push_screen(PickFoldersScreen({job.name for job in self.cfg.jobs}, {job.source for job in self.cfg.jobs},
                                           self.cfg.remote), added)

    def action_delete(self) -> None:
        if (job := self.selected) is not None:
            self.delete_job(job)

    def delete_job(self, job: Job, then=None) -> None:
        """Take a folder out of Arca, after a yes/no. Nothing is deleted from the disk or the destination."""
        if self._busy(job):
            self.notify(t("msg.busy", name=job.name), severity="warning")
            return

        def confirmed(yes: bool | None) -> None:
            if not yes:
                return
            self.cfg.jobs.remove(job)
            self.views.pop(job.name, None)
            config.save(self.cfg)
            if then is not None:
                then()
            self._fill_table()
            self.notify(t("msg.removed", name=job.name))

        self.push_screen(ConfirmScreen(t("confirm.delete", name=job.name), t("yes.remove"), t("no.keep")), confirmed)

    def action_toggle(self) -> None:
        if (job := self.selected) is not None:
            self.toggle_job(job)

    def toggle_job(self, job: Job) -> None:
        if self._busy(job):
            return
        job.enabled = not job.enabled
        config.save(self.cfg)
        self._refresh_folder(job)

    def _busy(self, job: Job) -> bool:
        return self.views[job.name].state in ("queued", "running")

    def action_remote(self) -> None:
        def chosen(remote: str | None) -> None:
            if remote and remote != self.cfg.remote:
                self.set_remote(remote)

        self.push_screen(RemoteScreen(self.cfg.remote), chosen)

    def set_remote(self, remote: str) -> None:
        self.cfg.remote = remote
        config.save(self.cfg)
        self.notify(t("msg.remote_set", remote=remote))
        self.action_check()

    def open_folder(self, job: Job) -> None:
        if sys.platform == "win32" and os.path.isdir(job.source):
            os.startfile(job.source)  # noqa: S606 - the user's own folder, in Explorer
        else:
            self.notify(self._say("err.source", job), severity="warning")

    def action_filters(self) -> None:
        if sys.platform == "win32":
            os.startfile(self.filter_file)  # noqa: S606 - opens the user's own text file in their editor
        self.notify(t("msg.filters", path=self.filter_file), timeout=8)

    @work(group="health", exclusive=True)
    async def action_check(self) -> None:
        self.health = None
        self.render_status()
        self.remotes = await rclone.list_remotes()
        self.accounts = await rclone.account_states()
        self.health = await preflight.check(self.cfg.remote)
        self.render_status()

    def action_reconnect(self) -> None:
        remote = self.cfg.remote
        if not remote:
            self.notify(self._say("fix.noremote"), severity="warning")
            return
        if is_local(remote):
            self.notify(t("msg.reconnect_local"))
            return
        self.sign_in(remote_name(remote))

    def sign_in(self, name: str, kind: str | None = None) -> None:
        """Connect an account again, or add a new one of type `kind`, without a console: rclone runs hidden, the
        browser asks for the sign-in, and the Accounts page shows the progress and the link."""
        if self.signing:
            self.notify(t("msg.sign_in_busy"), severity="warning")
            return
        self.signing = (name.rstrip(":"), "")
        self._sign_in(name.rstrip(":"), kind)

    def cancel_sign_in(self) -> None:
        if self._sign_in_proc is not None:
            self._sign_in_proc.stop()

    @work(group="sign-in", exclusive=True)
    async def _sign_in(self, name: str, kind: str | None) -> None:
        proc = self._sign_in_proc = rclone.sign_in(name, kind)
        errors: list[str] = []

        def heard(line: rclone.Line) -> None:
            if link := rclone.auth_link(line.msg):
                self.signing = (name, link)
                self._redraw_accounts()
            elif line.is_error:
                errors.append(line.msg)  # never the other lines: `config create` prints the new token

        self.notify(t("msg.sign_in", name=name), timeout=10)
        self._redraw_accounts()
        try:
            code = await asyncio.wait_for(proc.run(heard), SIGN_IN_TIMEOUT)
        except TimeoutError:
            proc.stop()
            code, errors = -1, [*errors, t("msg.sign_in_timeout")]
        except rclone.RcloneMissing:
            code, errors = -1, [self._say("fix.norclone")]
        except asyncio.CancelledError:
            proc.stop()  # the app is closing: never leave rclone waiting on its port
            raise
        finally:
            self.signing = None
            self._sign_in_proc = None
        if proc.stopped and code != 0:
            self.notify(t("msg.sign_in_cancelled", name=name))
        elif code == 0:
            self.notify(t("msg.connected", name=name))
        else:
            self.notify(t("msg.sign_in_failed", name=name, why=errors[-1] if errors else f"exit {code}"),
                        severity="error", timeout=15)
        self._accounts_changed()

    def disconnect(self, name: str) -> None:
        if self.current is not None or self.queue:
            self.notify(t("msg.disconnect_busy"), severity="warning")
            return
        self._disconnect(name)

    @work(group="disconnect", exclusive=True)
    async def _disconnect(self, name: str) -> None:
        problem = await rclone.disconnect(name)
        if problem:
            self.notify(t("msg.disconnect_failed", name=name.rstrip(":"), why=problem), severity="error", timeout=15)
        else:
            self.notify(t("msg.disconnected", name=name.rstrip(":")))
        self._accounts_changed()

    def _accounts(self) -> list[AccountsScreen]:
        return [screen for screen in self.screen_stack if isinstance(screen, AccountsScreen)]

    def _redraw_accounts(self) -> None:
        for screen in self._accounts():
            screen._draw()

    def _accounts_changed(self) -> None:
        self.action_check()
        for screen in self._accounts():
            screen.reload()

    def action_language(self) -> None:
        self.cfg.language = "en" if language() == "es" else "es"
        config.save(self.cfg)
        if self.current is None:
            self.exit("restart")

    # --- home menu and pages ----------------------------------------------------------------------------------------

    def action_home(self) -> None:
        if not any(isinstance(screen, HomeScreen) for screen in self.screen_stack):
            self.push_screen(HomeScreen())

    def open_page(self, page: str) -> None:
        if page == "folders":
            self.show_folders()
        elif page == "run_all":
            self.pop_screen()
            self.action_run_all()
        elif page in PAGES:
            self.push_screen(PAGES[page]())
        elif page == "filters":
            self.action_filters()

    def set_theme(self, name: str) -> None:
        self.theme = self.cfg.theme = name
        config.save(self.cfg)

    @work(group="schedule", exclusive=True, thread=True)
    def _read_schedule(self) -> None:
        mode = schedule.current()
        self.call_from_thread(self._schedule_known, mode)

    def apply_schedule(self, mode: str) -> None:
        if self.current is not None or self.queue:
            self.notify(t("msg.schedule_busy"), severity="warning")
            return
        self._apply_schedule(mode)

    @work(group="schedule", exclusive=True, thread=True)
    def _apply_schedule(self, mode: str) -> None:
        problem = schedule.apply(mode)
        known = schedule.current()
        if problem:
            self.call_from_thread(self.notify, t("msg.schedule_failed", why=problem), severity="error", timeout=15)
        else:
            self.call_from_thread(self.notify, t("msg.scheduled", mode=t(f"schedule.{known}", at=schedule.DAILY_AT)))
        self.call_from_thread(self._schedule_known, known)

    def _schedule_known(self, mode: str) -> None:
        self.schedule_mode = mode
        for screen in self.screen_stack:
            if isinstance(screen, HomeScreen) and screen.is_mounted:
                screen.show_schedule()

    def set_metrics_days(self, days: int) -> None:
        self.cfg.metrics_days = days
        config.save(self.cfg)

    def ask_reset(self) -> None:
        if self.current is not None or self.queue:
            self.notify(t("msg.reset_busy"), severity="warning")
            return

        def answered(yes: bool | None) -> None:
            if yes:
                config.reset()
                self.exit("restart:reset")

        self.push_screen(ConfirmScreen(t("confirm.reset", path=fmt.path(config.previous_dir())), t("yes.reset"),
                                       t("no.back")), answered)

    def apply_language(self, code: str) -> None:
        if self.current is not None:
            self.notify(t("msg.lang_busy", name=self.current.job.name), severity="warning")
            return
        self.cfg.language = code
        config.save(self.cfg)
        self.exit("restart:set-lang")

    def _refresh_home(self) -> None:
        for screen in self.screen_stack:
            if isinstance(screen, HomeScreen) and screen.is_mounted:
                screen.refresh_menu()

    def health_text(self) -> Text:
        """The destination's state in a few words, for the menu and the accounts page."""
        health = self.health
        if health is None:
            return Text(t("health.checking"), style="grey50")
        if health.ok:
            return Text(t("health.local") if health.local else t("health.ok"), style="green")
        diagnosis = health.diagnosis
        cause = "disconnected" if diagnosis and diagnosis.cause == "err.disconnected" else (
            diagnosis.action if diagnosis else "unknown")
        return Text(t(f"health.short.{cause}") if f"health.short.{cause}" in STRINGS else t("health.problem"),
                    style="#e0af68")

    def home_stats(self) -> dict[str, str | Text]:
        """The short value at the right of each menu line."""
        states = [self._state(job) for job in self.cfg.jobs]
        folders = Text(t("sum.jobs", n=len(states)), style="grey62")
        if states.count("failed"):
            folders.append(f"  ✗ {states.count('failed')}", style="red")
        elif states.count("running") or states.count("queued"):
            folders.append(f"  ● {t('sum.running', n=states.count('running') + states.count('queued'))}",
                           style="#22b8d6")
        elif states and states.count("ok") == len(states):
            folders.append("  ✓", style="green")
        finished = [v.result.finished for v in self.views.values() if v.result and v.result.finished]
        results = [r for r in history.all_results() if not r.dry_run and r.status != "stopped"]
        rules = sum(1 for line in self.filter_file.read_text(encoding="utf-8", errors="replace").splitlines()
                    if line.strip() and not line.lstrip().startswith(("#", ";")))
        accounts = Text.assemble((remote_name(self.cfg.remote).rstrip(":") + "  " if self.cfg.remote and not
                                  is_local(self.cfg.remote) else "", "grey62"), self.health_text())
        return {
            "folders": folders,
            "run_all": t("sum.last", when=fmt.ago(max(finished))) if finished else t("sum.never"),
            "import": t("home.import.found") if importer.script_paths() else "",
            "accounts": Text(t("status.no_account"), style="#e0af68") if self.no_account
            else accounts if self.cfg.remote else Text(t("err.noremote"), style="#e0af68"),
            "metrics": t("metrics.success", pct=round(sum(r.status == "ok" for r in results) * 100 / len(results)))
            if results else "",
            "filters": t("home.rules", n=rules),
        }

    def import_plan(self, plan: Plan, chosen: list[Candidate]) -> int:
        """Save the chosen folders and the old history. Returns how many past runs reached the history."""
        if not self.cfg.remote and plan.remote:
            self.cfg.remote = plan.remote
        if plan.versions and plan.remote == self.cfg.remote:
            self.cfg.versions = plan.versions  # keep the old archive: one history, not two
        for candidate in chosen:
            self.cfg.jobs.append(Job(candidate.name, candidate.source))
            self.views[candidate.name] = View()
        config.save(self.cfg)
        # The script's past runs come along under the imported names (Pictures ran as Fotos), so the metrics
        # start with the real history instead of empty.
        past = []
        for run in plan.runs:
            target = next((c.name for c in chosen if importer.same(c.matched or c.name, run.job)
                           or importer.same(c.name, run.job)), None)
            if target is not None:
                past.append(replace(run, job=target))
        added = history.add_past(past)
        self._load_results()
        self._fill_table()
        self.render_status()
        return added

    def show_folders(self) -> None:
        while len(self.screen_stack) > 1:
            self.pop_screen()
        self._focus_folders()

    async def action_quit(self) -> None:
        folder = next((screen for screen in self.screen_stack if isinstance(screen, FolderScreen)), None)
        if folder is not None:
            folder.settle(self._quit)  # ticks not saved yet are asked about, as when leaving the folder
        else:
            self._quit()

    def _quit(self) -> None:
        if self.current is None:
            self.exit()
            return

        def answered(yes: bool | None) -> None:
            if yes:
                self._stop_and_exit()

        self.push_screen(ConfirmScreen(t("confirm.quit", name=self.current.job.name), t("yes.quit"), t("no.back")),
                         answered)

    def action_help_quit(self) -> None:
        """ctrl+c twice quits, like any terminal program. Textual's default only points at another key, once per
        press, and the notices pile up."""
        now = time.monotonic()
        if now - self._ctrl_c_at < CTRL_C_WINDOW:
            self._stop_and_exit()
            return
        self._ctrl_c_at = now
        self.clear_notifications()
        self.notify(t("msg.ctrl_c"), timeout=CTRL_C_WINDOW)

    def _stop_and_exit(self) -> None:
        self.queue.clear()
        if self.current is not None:
            self.current.stop()
        self.exit()


PAGES = {"import": ImportScreen, "accounts": AccountsScreen, "metrics": MetricsScreen}

