"""Full screens under the masthead: the home menu and the pages it opens. Arrows move, Enter opens, Esc goes back."""
from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from rich.console import Group, RenderableType
from rich.table import Table
from rich.text import Text
from textual import events, on, work
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.message import Message
from textual.screen import Screen
from textual.widget import Widget
from textual.widgets import Input, Label, OptionList, Static
from textual.widgets.option_list import Option

from arca import fmt
from arca.core import config, history, importer, metrics, rclone, schedule
from arca.core.config import Job, is_local, name_problem, remote_name
from arca.core.importer import Candidate, Plan
from arca.i18n import t
from arca.ui.masthead import Masthead
from arca.ui.screens import ACCENT, ActionsScreen, AddJobScreen, LinkRow, bind, hint_text

if TYPE_CHECKING:
    from arca.ui.app import ArcaApp

KINDS = {  # rclone backend type -> what people call it
    "drive": "Google Drive", "onedrive": "OneDrive", "dropbox": "Dropbox", "s3": "S3", "b2": "Backblaze B2",
    "box": "Box", "pcloud": "pCloud", "mega": "MEGA", "sftp": "SFTP", "webdav": "WebDAV", "ftp": "FTP",
    "smb": "SMB", "crypt": "crypt", "alias": "alias", "local": "local", "protondrive": "Proton Drive",
    "googlephotos": "Google Photos",
}
SERVICES = (("drive", "gdrive"), ("onedrive", "onedrive"), ("dropbox", "dropbox"), ("googlephotos", "gphotos"))
THEMES = ("tokyo-night", "nord", "gruvbox", "dracula", "monokai", "catppuccin-mocha", "rose-pine", "solarized-dark")
METRIC_DAYS = (7, 14, 30)
NEEDS_ACCOUNT = ("folders", "run_all", "import", "metrics")  # home lines that lead nowhere without an account


def row(label: str | Text, desc: str | Text = "", right: str | Text = "", label_width: int = 14) -> Table:
    """One menu line: a bold label, a muted description that gives way first, a short value on the right."""
    grid = Table.grid(expand=True, padding=(0, 2))
    grid.add_column(width=label_width, no_wrap=True)
    grid.add_column(ratio=1, no_wrap=True, overflow="ellipsis")
    grid.add_column(justify="right", no_wrap=True)
    grid.add_row(label if isinstance(label, Text) else Text(label, style="bold"),
                 desc if isinstance(desc, Text) else Text(desc, style="grey62"),
                 right if isinstance(right, Text) else Text(right, style="grey62"))
    return grid


class Page(Screen):
    """The masthead, the quiet status lines, one bordered panel and a hint. Esc goes back."""

    TITLE = ""
    HINT = "hint.page"
    DANGER = ""  # a last hint part in red, for the key that deletes
    BACK = True

    def __init__(self) -> None:
        super().__init__()
        if self.BACK:
            bind(self, "escape", "back", show=False)

    @property
    def arca(self) -> ArcaApp:
        return self.app  # type: ignore[return-value]

    def compose(self) -> ComposeResult:
        yield Masthead()
        yield Static(classes="status")
        yield from self.above()
        with Vertical(classes="panel") as panel:
            panel.border_title = t(self.TITLE)
            yield from self.body()
        yield from self.below()
        yield Static(hint_text(t(self.HINT), t(self.DANGER) if self.DANGER else ""), classes="hint-line")

    def above(self) -> ComposeResult:
        yield from ()

    def body(self) -> ComposeResult:
        yield from ()

    def below(self) -> ComposeResult:
        yield from ()

    def on_mount(self) -> None:
        self.arca.render_status()

    def action_back(self) -> None:
        self.app.pop_screen()


class EdgeList(OptionList):
    """An option list that hands the focus on at its ends, so the arrows alone walk the whole screen."""

    def action_cursor_down(self) -> None:
        if self.highlighted is not None and self.highlighted >= self.option_count - 1:
            self.screen.focus_next()
            return
        super().action_cursor_down()

    def action_cursor_up(self) -> None:
        if not self.highlighted:
            self.screen.focus_previous()
            return
        super().action_cursor_up()


class Field(Input):
    """A one-line input the arrows step out of, like every other row on the screen."""

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        bind(self, "down", "app.focus_next", show=False)
        bind(self, "up", "app.focus_previous", show=False)


class Choice(Widget, can_focus=True):
    """A setting changed in place with left and right, after Bitácora's setup screen."""

    DEFAULT_CSS = "Choice { height: 1; padding: 0 1; }"

    class Changed(Message):
        def __init__(self, choice: Choice) -> None:
            super().__init__()
            self.choice = choice

    def __init__(self, label: str, options: list[tuple[object, str]], value: object, **kwargs) -> None:
        super().__init__(**kwargs)
        self.label = label
        self.options = options
        values = [v for v, _ in options]
        self.index = values.index(value) if value in values else 0
        self.label_width = len(label)
        self.value_width = max(len(text) for _, text in options)
        bind(self, "left", "step(-1)", show=False)
        bind(self, "right", "step(1)", show=False)
        bind(self, "down", "app.focus_next", show=False)
        bind(self, "up", "app.focus_previous", show=False)

    @property
    def value(self) -> object:
        return self.options[self.index][0]

    def action_step(self, delta: int) -> None:
        self.index = (self.index + delta) % len(self.options)
        self.refresh()
        self.post_message(self.Changed(self))

    def on_click(self, event: events.Click) -> None:
        self.focus()
        self.action_step(-1 if event.x < self.label_width + 4 else 1)

    def render(self) -> Text:
        focused = self.has_focus
        text = Text(self.label.ljust(self.label_width + 4), style="bold" if focused else "grey62")
        text.append("◂ ", style="grey50")
        text.append(self.options[self.index][1].center(self.value_width), style=f"bold {ACCENT}" if focused else "")
        text.append(" ▸", style="grey50")
        return text


class DangerRow(LinkRow):
    """The one destructive line on the home screen: red, apart from the menu, always behind a confirmation."""

    MARK, COLOUR = "✗", "#f7768e"


class HomeScreen(Page):
    """What Arca does, in the bordered menu; how it behaves, in the settings under it."""

    TITLE = "home.title"
    HINT = "hint.home"
    ITEMS = ("folders", "run_all", "import", "accounts", "metrics", "filters")
    BACK = False  # home is the top: nothing to go back to

    def __init__(self, focus: str | None = None) -> None:
        super().__init__()
        self.start_focus = focus
        bind(self, "enter", "apply", show=False)  # reaches the screen only from a setting: the menu takes its own

    def above(self) -> ComposeResult:
        yield Static(id="callout")  # the one thing to do first, when there is one

    def body(self) -> ComposeResult:
        yield EdgeList(id="menu")

    def below(self) -> ComposeResult:
        cfg = self.arca.cfg
        themes = [name for name in THEMES if name in self.app.available_themes]
        with Vertical(id="settings"):
            yield Label(t("settings.title"), classes="settings-title")
            yield Choice(t("settings.language"), [("auto", t("settings.lang_auto")), ("es", "Español"),
                                                  ("en", "English")], cfg.language, id="set-lang")
            yield Choice(t("settings.theme"), [(name, name) for name in themes], cfg.theme, id="set-theme")
            yield Choice(t("settings.auto"), [(mode, t(f"schedule.{mode}", at=schedule.DAILY_AT))
                                              for mode in schedule.MODES], self.arca.schedule_mode, id="set-auto")
            yield Static(id="settings-note")
            yield DangerRow(t("settings.reset"), t("settings.reset.desc"), id="reset")

    def on_mount(self) -> None:
        super().on_mount()
        choices = list(self.query(Choice))
        label_width = max(choice.label_width for choice in choices)
        value_width = max(choice.value_width for choice in choices)
        for choice in choices:
            choice.label_width, choice.value_width = label_width, value_width
        self.refresh_menu()
        self.query_one(f"#{self.start_focus}" if self.start_focus else "#menu").focus()

    def on_screen_resume(self) -> None:
        self.refresh_menu()

    def refresh_menu(self) -> None:
        menu = self.query_one("#menu", OptionList)
        keep = menu.highlighted or 0
        stats = self.arca.home_stats()
        width = max(len(t(f"home.{item}")) for item in self.ITEMS) + 2
        blocked = NEEDS_ACCOUNT if self.arca.no_account else ()
        menu.set_options(
            # Without a connected account these lead nowhere: greyed out until one is connected (the status
            # line above says why, once).
            Option(row(Text(t(f"home.{item}"), style="grey42"), Text(t(f"home.{item}.desc"), style="grey35"),
                       "", width), id=item, disabled=True)
            if item in blocked else
            Option(row(t(f"home.{item}"), t(f"home.{item}.desc"), stats.get(item, ""), width), id=item)
            for item in self.ITEMS
        )
        menu.highlighted = self.ITEMS.index("accounts") if self.ITEMS[keep] in blocked else keep
        callout = self.query_one("#callout", Static)
        callout.display = bool(blocked)
        callout.update(Text.assemble(("→ ", "bold #e0af68"), (t("callout.connect"), "bold #e0af68"), ("     ", ""),
                                     ("Enter", f"bold {ACCENT}"), (f" {t('callout.on')} ", "grey62"),
                                     (t("home.accounts"), f"bold {ACCENT}")))

    @on(OptionList.OptionSelected, "#menu")
    def _open(self, event: OptionList.OptionSelected) -> None:
        self.arca.open_page(event.option.id or "")

    def on_choice_changed(self, event: Choice.Changed) -> None:
        if event.choice.id == "set-theme":
            self.arca.set_theme(str(event.choice.value))
        self._note_pending()

    def _note_pending(self) -> None:
        """The settings that touch more than Arca wait for Enter, and say so in one amber line."""
        notes = []
        if self.query_one("#set-lang", Choice).value != self.arca.cfg.language:
            notes.append(t("settings.lang_pending"))
        if self.query_one("#set-auto", Choice).value != self.arca.schedule_mode:
            notes.append(t("settings.auto_pending"))
        self.query_one("#settings-note", Static).update(Text("  ".join(notes), style="#e0af68"))

    def show_schedule(self) -> None:
        """Windows answered (on open, or after Enter): the setting shows what is really registered."""
        auto = self.query_one("#set-auto", Choice)
        auto.index = schedule.MODES.index(self.arca.schedule_mode)
        auto.refresh()
        self._note_pending()

    def on_link_row_chosen(self, event: LinkRow.Chosen) -> None:
        if event.row.id == "reset":
            self.arca.ask_reset()

    def action_apply(self) -> None:
        """Enter on a setting. The theme already took effect; the automatic backups change Windows' Task Scheduler
        and the language rebuilds every label with a restart, so both wait for Enter instead of acting at every
        arrow press."""
        lang, auto = self.query_one("#set-lang", Choice), self.query_one("#set-auto", Choice)
        if self.focused is auto and auto.value != self.arca.schedule_mode:
            self.arca.apply_schedule(str(auto.value))
        elif lang.value != self.arca.cfg.language:
            self.arca.apply_language(str(lang.value))
        elif isinstance(self.focused, Choice):
            self.query_one("#menu").focus()


class ImportScreen(Page):
    """An older setup -- the backup.ps1 script and what is already on the destination -- as ticked folders. Both
    places are editable: paste another script or another remote and Enter searches there. Nothing is written until
    the last line is chosen; then the page says what was saved and where to go next."""

    TITLE = "import.title"
    HINT = "hint.import"

    def __init__(self) -> None:
        super().__init__()
        self.plan: Plan | None = None
        self.chosen: set[int] = set()
        self.done = False
        bind(self, "space", "toggle", show=False)

    def body(self) -> ComposeResult:
        with Horizontal(classes="field", id="field-script"):
            yield Label(t("import.script"), classes="field-label")
            yield Field(id="script", placeholder=t("import.script_ph"))
        with Horizontal(classes="field", id="field-from"):
            yield Label(t("import.remote"), classes="field-label")
            yield Field(id="from", placeholder="gdrive:Backups")
        yield Static(Text(t("import.looking"), style="grey62"), id="import-info")
        yield EdgeList(id="candidates")

    def on_mount(self) -> None:
        super().on_mount()
        self.query_one("#candidates").display = False
        self._find(self.arca.cfg.remote, None, True)

    @on(Input.Submitted, "#script, #from")
    def _search_here(self) -> None:
        raw = self.query_one("#script", Input).value.strip().strip('"')
        script = Path(raw) if raw else None
        if script is not None and script.is_dir():
            script = script / "backup.ps1"  # a pasted folder means the script inside it
        self.query_one("#import-info", Static).update(Text(t("import.looking"), style="grey62"))
        self._find(self.query_one("#from", Input).value.strip().strip('"'), script, False)

    @work(exclusive=True)
    async def _find(self, remote: str, script: Path | None, look_for_script: bool) -> None:
        self.plan = await importer.find(remote, script, look_for_script)
        self.chosen = {i for i, c in enumerate(self.plan.candidates) if self._usable(c)}
        self.query_one("#script", Input).value = str(self.plan.script) if self.plan.script else ""
        self.query_one("#from", Input).value = self.plan.remote
        self._draw()

    def _taken(self, candidate: Candidate) -> bool:
        return any(importer.same(job.name, candidate.name) for job in self.arca.cfg.jobs)

    def _blocked(self, candidate: Candidate) -> bool:
        """Already in Arca, or a name the destination cannot hold: nothing to do with it here."""
        taken = {job.name for job in self.arca.cfg.jobs}
        return self._taken(candidate) or name_problem(candidate.name, taken) is not None

    def _usable(self, candidate: Candidate) -> bool:
        return candidate.exists and not self._blocked(candidate)

    def _draw(self) -> None:
        plan = self.plan
        assert plan is not None
        info = Text(t("import.paste"), style="grey50")
        if plan.missing_script:
            info.append(f"\n{t('import.err.script', path=plan.script)}", style="#e0af68")
        elif plan.script is None:
            info.append(f"\n{t('import.no_script')}", style="grey62")
        if plan.problem:
            values = {"remote": plan.remote, "source": "", "name": ""}
            info.append(f"\n{t(plan.problem.cause, **values)} {t(plan.problem.fix, **values)}", style="#e0af68")
        if plan.versions:
            info.append(f"\n\n{t('import.versions').ljust(18)}", style="grey50")  # lines up with the fields
            info.append(t("import.versions_kept", name=plan.versions), style="grey70")
        if plan.runs:
            info.append(f"\n{'' if plan.versions else chr(10)}{t('import.history').ljust(18)}", style="grey50")
            info.append(t("import.history_found", n=len(plan.runs), since=plan.runs[0].started[:10]),
                        style="grey70")
        if not plan.candidates and not plan.problem:
            info.append(f"\n\n{t('import.nothing')}", style="grey62")
        elif plan.candidates:
            if plan.remote and self.arca.cfg.remote and plan.remote != self.arca.cfg.remote:
                info.append(f"\n\n{t('import.other_remote', remote=self.arca.cfg.remote)}", style="#e0af68")
            info.append(f"\n\n{t('import.where')}", style="grey62")
            info.append(f"\n{t('import.unsaved')}", style="bold")
        self.query_one("#import-info", Static).update(info)

        options = self.query_one("#candidates", OptionList)
        keep = options.highlighted or 0
        options.clear_options()
        width = max((len(c.name) for c in plan.candidates), default=0) + 6
        for i, candidate in enumerate(plan.candidates):
            options.add_option(Option(self._row(candidate, i, width), id=f"c{i}", disabled=self._blocked(candidate)))
        if plan.candidates:
            count = len(self.chosen)
            options.add_option(Option(Text(f"→ {t('import.go', n=count)}", style=f"bold {ACCENT}"), id="go",
                                      disabled=not count))
        options.display = bool(plan.candidates)
        options.highlighted = min(keep, options.option_count - 1) if options.option_count else None
        (options if plan.candidates else self.query_one("#from")).focus()

    def _row(self, candidate: Candidate, index: int, width: int) -> Table:
        if self._taken(candidate):
            mark, note = "   ", t("import.in_arca")
        elif not candidate.exists:
            mark, note = "[ ]", t("import.no_folder")
        else:
            mark = "[x]" if index in self.chosen else "[ ]"
            note = t("import.continues") if candidate.on_remote else t("import.new")
        source = Text(f"← {fmt.path(candidate.source, 44)}" if candidate.source else "←  –", style="grey70")
        why = {"script": t("import.from_script", name=candidate.matched),
               "name": t("import.by_name", name=candidate.matched),
               "picked": t("import.picked")}.get(candidate.origin, "")
        if why and candidate.exists:
            source.append(f"  · {why}", style="grey50")
        return row(f"{mark} {candidate.name}", source, note, width)

    # --- choosing --------------------------------------------------------------------------------------------------

    def _highlighted(self) -> int | None:
        option = self.query_one("#candidates", OptionList).highlighted_option
        return int(option.id[1:]) if option is not None and option.id and option.id.startswith("c") else None

    def action_toggle(self) -> None:
        index = self._highlighted()
        if index is not None and self.plan is not None and self._usable(self.plan.candidates[index]):
            self.chosen ^= {index}
            self._draw()

    @on(OptionList.OptionSelected, "#candidates")
    def _picked(self, event: OptionList.OptionSelected) -> None:
        assert self.plan is not None
        choice = event.option.id or ""
        if self.done:
            self._leave(choice)
        elif choice == "go":
            self._import()
        elif choice.startswith("c"):
            self._ask(int(choice[1:]))

    def _ask(self, index: int) -> None:
        """Enter on a folder: include it or not, or choose its local folder. With no folder yet, straight to the
        picker: there is nothing else to do with it."""
        candidate = self.plan.candidates[index]  # type: ignore[union-attr]
        if not candidate.exists:
            self._choose_folder(index)
            return
        actions = [("skip" if index in self.chosen else "include",
                    t("import.skip" if index in self.chosen else "import.include"), t("key.space")),
                   ("pick", t("import.pick_other"), "")]

        def chosen(action: str | None) -> None:
            if action in ("skip", "include"):
                self.chosen ^= {index}
                self._draw()
            elif action == "pick":
                self._choose_folder(index)

        self.app.push_screen(ActionsScreen(candidate.name, actions), chosen)

    def _choose_folder(self, index: int) -> None:
        candidate = self.plan.candidates[index]  # type: ignore[union-attr]
        start = Path(candidate.source) if candidate.source else None

        def picked(job: Job | None) -> None:
            if job is None:
                return
            candidate.source, candidate.origin, candidate.matched = job.source, "picked", ""
            self.chosen.add(index)
            self._draw()

        self.app.push_screen(AddJobScreen({job.name for job in self.arca.cfg.jobs}, self.arca.cfg.remote,
                                          name=candidate.name, start=start,
                                          title=t("import.pick_title", name=candidate.name)), picked)

    # --- after the import ------------------------------------------------------------------------------------------

    def _import(self) -> None:
        plan = self.plan
        assert plan is not None
        chosen = [plan.candidates[i] for i in sorted(self.chosen)]
        added = self.arca.import_plan(plan, chosen)
        self.done = True
        for field_id in ("#field-script", "#field-from"):
            self.query_one(field_id).display = False
        names = ", ".join(c.name for c in chosen)
        info = Text.assemble((f"✓ {t('import.done', names=names, remote=self.arca.cfg.remote)}", "bold green"))
        if added:
            info.append(f"\n{t('import.done_history', n=added)}", style="grey70")
        info.append(f"\n{t('import.done_where', path=fmt.path(config.config_path()))}", style="grey50")
        self.query_one("#import-info", Static).update(info)
        options = self.query_one("#candidates", OptionList)
        options.clear_options()
        for option_id, key in (("folders", "import.open_folders"), ("metrics", "import.open_metrics"),
                               ("home", "import.back_home")):
            options.add_option(Option(Text(f"→ {t(key)}", style=f"bold {ACCENT}"), id=option_id))
        options.highlighted = 0
        options.focus()
        self.query_one(".hint-line", Static).update(hint_text(t("hint.page")))

    def _leave(self, choice: str) -> None:
        if choice == "folders":
            self.arca.show_folders()
        elif choice == "metrics":
            self.app.switch_screen(MetricsScreen())
        else:
            self.app.pop_screen()


class AccountList(EdgeList):
    """The accounts, each line ending in its icon: ✕ disconnects, ↻ connects. Keyboard first: → moves the cursor
    onto the icon and ← back to the line, Enter does what the cursor is on, Del disconnects like deleting from any
    list. A mouse press on the icon does the same."""

    ICON_CELLS = 6  # the chip (4 cells) and the padding around it

    class Toggled(Message):
        def __init__(self, account: str) -> None:
            super().__init__()
            self.account = account

    class IconMoved(Message):
        pass

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self._icon_pressed = False
        self.on_icon = False  # the cursor is on the icon of the highlighted line, not on the line
        bind(self, "right", "icon(True)", show=False)
        bind(self, "left", "icon(False)", show=False)
        bind(self, "delete,backspace", "screen.disconnect", show=False)

    def action_icon(self, on: bool) -> None:
        if on != self.on_icon:
            self.on_icon = on
            self.post_message(self.IconMoved())

    def _on_mouse_down(self, event: events.MouseDown) -> None:
        index = event.style.meta.get("option")
        if event.button == 1 and index is not None and event.x >= self.region.width - self.ICON_CELLS:
            event.stop()
            event.prevent_default()
            self._icon_pressed = True
            self.highlighted = index
            self.post_message(self.Toggled(self.get_option_at_index(index).id or ""))

    async def _on_click(self, event: events.Click) -> None:
        if self._icon_pressed:  # the icon already acted on the way down; the line is not chosen too
            self._icon_pressed = False
            event.stop()
            event.prevent_default()


class AccountsScreen(Page):
    """The rclone accounts on this machine: one per line with its state and the icon that connects or disconnects
    it. Adding one is a line under the panel. Nothing opens a console: rclone runs hidden and the browser signs in."""

    TITLE = "accounts.title"
    HINT = "hint.accounts"

    def __init__(self) -> None:
        super().__init__()
        self.remotes: list[tuple[str, str]] = []
        self.states: dict[str, bool | None] = {}

    def body(self) -> ComposeResult:
        yield Static(Text(t("remote.loading"), style="grey62"), id="accounts-info")
        yield AccountList(id="accounts")
        yield Static(id="accounts-note")

    def below(self) -> ComposeResult:
        with Vertical(id="account-links"):
            yield LinkRow(t("accounts.add"), t("accounts.add.desc"), id="add")
            yield EdgeList(id="services")

    def on_mount(self) -> None:
        super().on_mount()
        services = self.query_one("#services", OptionList)
        services.set_options(Option(Text(f"  {KINDS[kind]}"), id=kind) for kind, _ in SERVICES)
        services.display = False
        self._load()

    def on_screen_resume(self) -> None:
        if self.remotes:
            self._draw()

    def reload(self) -> None:
        self._load()

    @work(exclusive=True)
    async def _load(self) -> None:
        self.remotes = await rclone.list_remotes_long()
        self.states = await rclone.account_states()
        self._draw()

    def _destination(self) -> str:
        remote = self.arca.cfg.remote
        return remote_name(remote) if remote and not is_local(remote) else ""

    def _state(self, name: str) -> Text:
        connected = self.states.get(name)
        if connected is False:
            state = Text(t("accounts.off"), style="#e0af68")
        elif name == self._destination() and not (self.arca.health and self.arca.health.ok):
            state = self.arca.health_text()  # checking, expired, offline: the destination's own words
        else:
            state = Text(t("accounts.on"), style="green") if connected else Text("")
        if name == self._destination():
            return Text.assemble((f"{t('status.remote')} · ", "grey50"), state)
        return state

    def _icon(self, name: str, cursor: bool = False) -> Text:
        signing = self.arca.signing
        if signing and f"{signing[0]}:" == name:
            return Text("  …  ", style=f"bold {ACCENT}")
        connected = self.states.get(name)
        if connected is None:
            return Text("     ")
        # A small chip, not a bare glyph against the border: the cursor lights it in what it does, red to
        # disconnect, the accent to connect.
        icon, colour = ("  ✕  ", "#f7768e") if connected else ("  ↻  ", ACCENT)
        if cursor:
            return Text(icon, style=f"bold #1a1b26 on {colour}")
        return Text(icon, style=f"{'grey62' if connected else f'bold {ACCENT}'} on #2f344a")

    def _line(self, name: str, kind: str, width: int, cursor: bool) -> RenderableType:
        # One account per line: name, what it is, its state and its icon, all on the same line.
        mark = "●" if name == self._destination() else "○"
        return row(f"{mark} {name.rstrip(':')}", KINDS.get(kind, kind),
                   Text.assemble(self._state(name), "  ", self._icon(name, cursor)), width)

    def _draw_lines(self) -> None:
        """Repaint the lines in place, keeping the cursor where it is."""
        options = self.query_one("#accounts", AccountList)
        width = max((len(name) for name, _ in self.remotes), default=0) + 4
        if self.states.get(self._selected() or "") is None:
            options.on_icon = False  # a disk or S3 keys: no icon to stand on
        for index, (name, kind) in enumerate(self.remotes):
            cursor = options.on_icon and index == options.highlighted
            options.replace_option_prompt_at_index(index, self._line(name, kind, width, cursor))

    def on_account_list_icon_moved(self) -> None:
        self._draw_lines()

    @on(OptionList.OptionHighlighted, "#accounts")
    def _moved(self) -> None:
        if self.remotes and self.query_one("#accounts", AccountList).on_icon:
            self._draw_lines()  # the icon cursor follows the line

    def _draw(self) -> None:
        cfg = self.arca.cfg
        info = Text.assemble((f"{t('status.remote')}  ", "grey50"), (cfg.remote or "–", f"bold {ACCENT}"))
        if not self.remotes:
            info.append(f"\n\n{t('accounts.none')}", style="grey62")
        elif all(self.states.get(name) is False for name, _ in self.remotes):
            info = Text(t("accounts.none_connected"), style="#e0af68")
        self.query_one("#accounts-info", Static).update(info)
        options = self.query_one("#accounts", OptionList)
        options.display = bool(self.remotes)
        names = [name for name, _ in self.remotes]
        if [options.get_option_at_index(i).id for i in range(options.option_count)] != names:
            keep = options.highlighted or 0
            options.set_options(Option("", id=name) for name in names)
            if names:
                options.highlighted = min(keep, len(names) - 1)
        self._draw_lines()
        self._draw_note()
        if not isinstance(self.focused, (LinkRow, AccountList)) or not self.focused.display:
            (options if self.remotes else self.query_one("#add")).focus()

    def _draw_note(self) -> None:
        note = self.query_one("#accounts-note", Static)
        signing = self.arca.signing
        note.display = signing is not None
        if signing is None:
            return
        name, link = signing
        text = Text.assemble(("● ", ACCENT), t("accounts.signing", name=name), ("   ", ""),
                             (t("accounts.cancel"), "grey50"))
        if link:
            text.append(f"\n{t('accounts.link')} ", style="grey62")
            text.append(link, style=f"underline {ACCENT}")
        note.update(text)

    def _selected(self) -> str | None:
        options = self.query_one("#accounts", OptionList)
        if not self.remotes or options.highlighted is None:
            return None
        return options.get_option_at_index(options.highlighted).id

    @on(OptionList.OptionSelected, "#accounts")
    def _picked(self, event: OptionList.OptionSelected) -> None:
        name = event.option.id or ""
        if self.query_one("#accounts", AccountList).on_icon:
            self._toggle(name)
        elif self.states.get(name) is False:
            self.arca.sign_in(name)
        elif name == self._destination():
            self.notify(t("accounts.already", remote=self.arca.cfg.remote))
        else:
            self.arca.set_remote(f"{name}Backups")
            self._draw()

    def on_account_list_toggled(self, event: AccountList.Toggled) -> None:
        self._toggle(event.account)

    def _toggle(self, name: str) -> None:
        if self.states.get(name):
            self.arca.disconnect(name)
        elif self.states.get(name) is False:
            self.arca.sign_in(name)

    def action_disconnect(self) -> None:
        if (name := self._selected()) and self.states.get(name):
            self.arca.disconnect(name)

    def on_link_row_chosen(self, event: LinkRow.Chosen) -> None:
        services = self.query_one("#services", OptionList)
        services.display = not services.display
        if services.display:
            services.highlighted = 0
            services.focus()

    @on(OptionList.OptionSelected, "#services")
    def _service(self, event: OptionList.OptionSelected) -> None:
        kind = event.option.id or ""
        self.query_one("#services").display = False
        self.query_one("#add").focus()
        self.arca.sign_in(self._free_name(kind), kind)

    def _free_name(self, kind: str) -> str:
        base = dict(SERVICES)[kind]
        taken = {name.rstrip(":") for name, _ in self.remotes}
        name, n = base, 2
        while name in taken:
            name, n = f"{base}{n}", n + 1
        return name

    def action_back(self) -> None:
        if self.arca.signing:
            self.arca.cancel_sign_in()
        elif self.query_one("#services").display:
            self.query_one("#services").display = False
            self.query_one("#add").focus()
        else:
            super().action_back()


class MetricsScreen(Page):
    """How the backups are doing: space on the destination, then each folder's record."""

    TITLE = "metrics.title"
    HINT = "hint.metrics"

    def __init__(self) -> None:
        super().__init__()
        bind(self, "left", "days(-1)", show=False)
        bind(self, "right", "days(1)", show=False)

    def action_days(self, step: int) -> None:
        """←→ change how many days the squares draw (7, 14, 30), saved at once."""
        days = METRIC_DAYS[(METRIC_DAYS.index(self.arca.cfg.metrics_days) + step) % len(METRIC_DAYS)] \
            if self.arca.cfg.metrics_days in METRIC_DAYS else METRIC_DAYS[1]
        self.arca.set_metrics_days(days)
        self._draw()

    def body(self) -> ComposeResult:
        with VerticalScroll(id="metrics-scroll"):
            yield Static(id="metrics")

    def on_mount(self) -> None:
        super().on_mount()
        self.usage: dict | None = None
        self.usage_done = False
        self._draw()
        self.query_one("#metrics-scroll").focus()
        self._load_usage()

    @work(exclusive=True)
    async def _load_usage(self) -> None:
        if self.arca.cfg.remote:
            self.usage = await metrics.usage(self.arca.cfg.remote)
        self.usage_done = True
        self._draw()

    def _draw(self) -> None:
        cfg = self.arca.cfg
        folders = metrics.compute(history.all_results(), [job.name for job in cfg.jobs], days=cfg.metrics_days)
        parts: list[RenderableType] = [self._space(), Text()]
        runs = sum(f.runs for f in folders)
        if not runs:
            parts.append(Text(t("metrics.none"), style="grey62"))
        else:
            ok = sum(f.ok for f in folders)
            last = max((f.last_ok for f in folders), default="")
            totals = Text()
            totals.append(t("metrics.runs", n=runs), style="bold")
            totals.append("  ·  ", style="grey35")
            totals.append(t("metrics.success", pct=round(ok * 100 / runs)), style="green" if ok == runs else "#e0af68")
            totals.append("  ·  ", style="grey35")
            totals.append(t("metrics.uploaded", size=fmt.size(sum(f.bytes for f in folders))))
            if last:
                totals.append("  ·  ", style="grey35")
                totals.append(t("metrics.last_ok", when=fmt.ago(last)), style="grey62")
            parts += [totals, Text(), self._table(folders)]
        self.query_one("#metrics", Static).update(Group(*parts))

    def _space(self) -> Text:
        line = Text.assemble((f"{t('metrics.space')}  ", "bold"))
        if not self.usage_done:
            line.append(t("metrics.space_loading"), style="grey50")
            return line
        usage = self.usage or {}
        total, used, free = usage.get("total"), usage.get("used"), usage.get("free")
        if not isinstance(used, int):
            line.append(t("metrics.space_unknown"), style="grey50")
            return line
        if isinstance(total, int) and total > 0:
            taken = total - free if isinstance(free, int) else used
            width = 30
            filled = max(1 if taken else 0, round(taken / total * width))
            colour = "#e0af68" if taken / total > 0.85 else ACCENT
            line.append("█" * filled, style=colour)
            line.append("░" * (width - filled), style="grey30")
            line.append(f"  {t('metrics.space_of', used=fmt.size(taken), total=fmt.size(total))}")
            if isinstance(free, int):
                line.append(f"  ·  {t('metrics.free', size=fmt.size(free))}", style="grey62")
        line.append(f"\n{' ' * (len(t('metrics.space')) + 2)}", style="grey50")
        line.append(t("metrics.arca_uses", size=fmt.size(used)), style="grey50")
        return line

    @staticmethod
    def _table(folders: list[metrics.FolderStats]) -> Table:
        table = Table(box=None, padding=(0, 2, 0, 0), header_style="grey50", show_edge=False)
        for key in ("name", "runs", "success", "last_ok", "last_failed", "average", "uploaded", "days"):
            table.add_column(t(f"metrics.col.{key}", n=len(folders[0].days) if folders else 0), no_wrap=True,
                             justify="right" if key in ("runs", "success", "average", "uploaded") else "left")
        for folder in folders:
            days = Text()
            gap = " " if len(folder.days) <= 14 else ""  # a month of squares fits only without the spaces
            for day in folder.days:
                days.append(("■" if day else "·") + gap, style={"ok": "green", "failed": "red"}.get(day, "grey30"))
            success = folder.success
            table.add_row(
                Text(folder.name, style="bold"),
                str(folder.runs) if folder.runs else "–",
                Text(f"{success * 100:.0f}%", style="green" if success == 1 else "#e0af68")
                if success is not None else "–",
                fmt.ago(folder.last_ok) if folder.last_ok else "–",
                Text(fmt.ago(folder.last_failed), style="red") if folder.last_failed else "–",
                fmt.duration(folder.average),
                fmt.size(folder.bytes) if folder.runs else "–",
                days,
            )
        return table

