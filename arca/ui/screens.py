"""Dialogs: choose a folder, choose where backups go, pick an action, confirm."""
from __future__ import annotations

import stat
from collections.abc import Iterable
from pathlib import Path

from rich.style import Style
from rich.text import Text
from textual import events, on, work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.dom import DOMNode
from textual.message import Message
from textual.screen import ModalScreen
from textual.widgets import Button, DirectoryTree, Input, Label, OptionList, Static
from textual.widgets.option_list import Option

from arca import fmt
from arca.core import rclone, selection
from arca.core.config import Job, join, name_problem
from arca.i18n import t

ACCENT = "#4fd1e8"

# AppData, Cookies, "Datos de programa"... legacy junctions and hidden folders nobody backs up on purpose.
HIDDEN_OR_SYSTEM = getattr(stat, "FILE_ATTRIBUTE_HIDDEN", 2) | getattr(stat, "FILE_ATTRIBUTE_SYSTEM", 4)


def bind(node: DOMNode, keys: str, action: str, label: str = "", show: bool = True) -> None:
    """Bind a key with a translated label. Class-level BINDINGS are frozen at import, before the language is known;
    this is what App.bind does, available to screens too."""
    node._bindings.bind(keys, action, t(label) if label else "", show=show)


def hint_text(hint: str, danger: str = "") -> Text:
    """"↑↓ move · Enter open · Esc back" with each key in the accent colour: the keys are what the eye looks for.
    `danger` is one more "key words" part, last and in red: what deletes never looks like what moves."""
    text = Text()
    for i, part in enumerate(hint.split(" · ")):
        if i:
            text.append("  ·  ", style="grey35")
        key, _, words = part.partition(" ")
        text.append(key, style=f"bold {ACCENT}")
        text.append(f" {words}", style="grey62")
    if danger:
        key, _, words = danger.partition(" ")
        text.append("  ·  ", style="grey35")
        text.append(f"{key} {words}", style="bold #f7768e")
    return text


class LinkRow(Static, can_focus=True):
    """One line outside a panel that does one thing: "+ Add an account", "✗ Delete the configuration"."""

    DEFAULT_CSS = "LinkRow { height: 1; padding: 0 1; }"
    MARK, COLOUR = "+", ACCENT

    class Chosen(Message):
        def __init__(self, row: LinkRow) -> None:
            super().__init__()
            self.row = row

        @property
        def control(self) -> LinkRow:  # lets @on(LinkRow.Chosen, "#id") pick one row
            return self.row

    def __init__(self, label: str, desc: str, mark: str | None = None, **kwargs) -> None:
        super().__init__(**kwargs)
        self.label, self.desc, self.mark = label, desc, mark or self.MARK
        bind(self, "enter", "choose", show=False)
        bind(self, "down", "app.focus_next", show=False)
        bind(self, "up", "app.focus_previous", show=False)

    def render(self) -> Text:
        return Text.assemble((f"{self.mark} {self.label}", f"bold {self.COLOUR}" if self.has_focus else self.COLOUR),
                             (f"   {self.desc}", "grey50"))

    def action_choose(self) -> None:
        self.post_message(self.Chosen(self))

    def on_mouse_down(self, event: events.MouseDown) -> None:
        if event.button == 1:  # on the way down, like the dialog's buttons: some terminals lose the release
            self.focus()
            self.action_choose()


class FolderTree(DirectoryTree):
    """Folders only, no hidden or system ones, and never a crash on a folder Windows will not let us read."""

    def filter_paths(self, paths: Iterable[Path]) -> Iterable[Path]:
        for path in paths:
            try:
                info = path.stat(follow_symlinks=False)
                hidden = getattr(info, "st_file_attributes", 0) & HIDDEN_OR_SYSTEM
                if path.is_dir() and not hidden and not path.name.startswith((".", "$")):
                    yield path
            except OSError:
                continue


def _key(path: Path) -> str:
    """A folder as selection.py keys it: "/" separators, ending in "/"."""
    return path.as_posix().rstrip("/") + "/"


def _above(key: str) -> list[str]:
    parts = key.rstrip("/").split("/")
    return ["/".join(parts[:depth]) + "/" for depth in range(1, len(parts))]


class CheckTree(FolderTree):
    """The folder tree with a box on every line. Keyboard first: space or Tab ticks, Enter opens or closes a folder,
    → opens it (or steps into it), ← closes it (or steps out to its parent). Exploring never saves: ↓ past the last
    line walks to the save line under the tree. The mouse does the same on the way down: a press on the box ticks,
    a press on the name opens or closes.

    A tick takes everything inside the folder with it, as in a folder's Files list (`core/selection.py`, with
    absolute paths): unticking a folder inside a ticked one leaves it out of that backup as a rule, it never makes
    two backups of the same files."""

    BINDINGS = [
        Binding("space,tab", "check", show=False),
        Binding("enter", "toggle", show=False),
        Binding("right", "open", show=False),
        Binding("left", "close", show=False),
    ]

    class Checked(Message):
        pass

    def __init__(self, path: Path, **kwargs) -> None:
        super().__init__(path, **kwargs)
        self.changes: dict[str, bool] = {}  # folder ("C:/Users/me/Docs/") -> ticked, in the order they were ticked

    @property
    def checked(self) -> list[Path]:
        """The folders to back up, each with its own name: the ticks with no ticked folder above them."""
        return [Path(key) for key, keep in self.changes.items() if keep and not any(
            self.changes.get(above) for above in _above(key))]

    def rules(self, folder: Path) -> list[str]:
        """What is unticked (or ticked back) inside `folder`, as its own rclone rules: deepest first."""
        root = _key(folder)
        inside = {key[len(root):]: keep for key, keep in self.changes.items() if key.startswith(root) and key != root}
        return selection.merge(inside, [])

    def left_out(self, folder: Path) -> list[str]:
        """The names unticked right inside `folder`'s backup, for the summary."""
        root = _key(folder)
        return [Path(key).name for key, keep in self.changes.items()
                if not keep and key.startswith(root) and key != root]

    def render_label(self, node, base_style, style) -> Text:
        label = super().render_label(node, base_style, style)
        if node.data is None:
            return label
        box = Style.from_meta({"box": True})  # tells a mouse press on the box from one on the name
        if selection.ticked(_key(Path(node.data.path)), set(), self.changes):
            label.stylize(f"bold {ACCENT}")
            return Text.assemble(("[✓] ", Style.parse(f"bold {ACCENT}") + box), label)
        return Text.assemble(("[ ] ", Style.parse("grey50") + box), label)

    def _on_mouse_down(self, event: events.MouseDown) -> None:
        line = event.style.meta.get("line")
        if event.button != 1 or line is None or line < 0:
            return
        event.stop()
        event.prevent_default()
        self.focus()
        self.cursor_line = line
        if event.style.meta.get("box"):
            self.action_check()
        else:
            self.action_toggle()

    async def _on_click(self, event: events.Click) -> None:
        """The press already acted (some terminals lose the release); Tree's own click would open or close again."""
        event.stop()
        event.prevent_default()

    def action_check(self) -> None:
        node = self.cursor_node
        if node is None or node.data is None:
            return
        selection.toggle(_key(Path(node.data.path)), set(), self.changes)
        self._clear_line_cache()  # the folders inside follow the tick: every line, not just this one
        self._updates += 1
        self.refresh()
        self.post_message(self.Checked())

    def action_toggle(self) -> None:
        node = self.cursor_node
        if node is not None and node.allow_expand:
            node.toggle()

    def action_cursor_down(self) -> None:
        if self.cursor_line >= self.last_line:
            self.screen.focus_next()  # the save line, when something is ticked
            return
        super().action_cursor_down()

    def action_cursor_up(self) -> None:
        if self.cursor_line <= 0:
            self.screen.focus_previous()  # the path field above: Tab ticks here, so ↑ is the way out
            return
        super().action_cursor_up()

    def action_open(self) -> None:
        node = self.cursor_node
        if node is None or not node.allow_expand:
            return
        if not node.is_expanded:
            node.expand()
        elif node.children:
            self.action_cursor_down()

    def action_close(self) -> None:
        node = self.cursor_node
        if node is None:
            return
        if node.is_expanded and node.children:
            node.collapse()
        else:
            self.action_cursor_parent()


class HelpScreen(ModalScreen[None]):
    """Every key, including the ones the footer has no room for."""

    def __init__(self, keys: list[tuple[str, str]]) -> None:
        super().__init__()
        self.keys = keys
        bind(self, "escape,question_mark,q", "close", show=False)

    def compose(self) -> ComposeResult:
        text = Text()
        for key, label in self.keys:
            text.append(f"{key:>7}  ", style="bold #4fd1e8")
            text.append(f"{label}\n")
        with Vertical(id="dialog", classes="help"):
            yield Label(t("help.title"), classes="title")
            yield Static(text)
            yield Label(t("help.close"), classes="hint")

    def action_close(self) -> None:
        self.dismiss(None)


class Dialog(ModalScreen):
    def __init__(self) -> None:
        super().__init__()
        bind(self, "escape", "cancel", "key.cancel")

    def action_cancel(self) -> None:
        self.dismiss(None)

    @on(Button.Pressed, "#cancel")
    def _cancel_pressed(self) -> None:
        self.action_cancel()


class AddJobScreen(Dialog):
    """Pick a folder and its name. With name given, only the folder is chosen (the import uses it that way)."""

    def __init__(self, taken: set[str], remote: str, name: str | None = None, start: Path | None = None,
                 title: str | None = None) -> None:
        super().__init__()
        self.taken = taken
        self.remote = remote
        self.fixed_name = name
        self.start = start if start is not None and start.is_dir() else Path.home()
        self.title_text = title or t("add.title")
        self._auto_name = ""
        bind(self, "ctrl+s", "save", "key.save")

    def compose(self) -> ComposeResult:
        start = self.start
        with Vertical(id="dialog"):
            yield Label(self.title_text, classes="title")
            yield Label(t("add.hint"), classes="hint")
            yield Input(str(start), id="path", placeholder=t("add.path"))
            yield FolderTree(start, id="tree")
            yield Input(self.fixed_name or "", id="name", placeholder=t("add.name"),
                        disabled=self.fixed_name is not None)
            yield Label("", id="dest", classes="hint")
            yield Label("", id="error", classes="error")
            with Horizontal(classes="buttons"):
                yield Button(t("key.save"), variant="primary", id="save")
                yield Button(t("key.cancel"), id="cancel")

    def on_mount(self) -> None:
        self.query_one(FolderTree).focus()
        self._pick(self.start)

    def _pick(self, folder: Path) -> None:
        self.query_one("#path", Input).value = str(folder)
        name_input = self.query_one("#name", Input)
        if name_input.value in ("", self._auto_name):
            self._auto_name = folder.name or str(folder).rstrip(":\\")
            name_input.value = self._auto_name
        self._show_dest()

    def _show_dest(self) -> None:
        name = self.query_one("#name", Input).value.strip()
        dest = t("add.dest", dest=fmt.path(join(self.remote, name))) if self.remote and name else ""
        self.query_one("#dest", Label).update(dest)

    @on(DirectoryTree.NodeHighlighted)
    def _highlighted(self, event: DirectoryTree.NodeHighlighted) -> None:
        entry = event.node.data
        if entry is not None:
            self._pick(Path(entry.path))

    @on(Input.Submitted, "#path")
    def _jump(self, event: Input.Submitted) -> None:
        folder = Path(event.value.strip().strip('"'))
        if folder.is_dir():
            self.query_one(FolderTree).path = folder
            self._pick(folder)
            self.query_one("#error", Label).update("")
        else:
            self.query_one("#error", Label).update(t("add.err.path"))

    @on(Input.Changed, "#name")
    def _name_changed(self) -> None:
        self._show_dest()

    @on(Input.Submitted, "#name")
    @on(Button.Pressed, "#save")
    def action_save(self) -> None:
        folder = Path(self.query_one("#path", Input).value.strip().strip('"'))
        name = self.query_one("#name", Input).value.strip()
        error = self.query_one("#error", Label)
        if not folder.is_dir():
            error.update(t("add.err.path"))
            return
        problem = name_problem(name, self.taken)
        if problem:
            error.update(t(problem, name=name))
            return
        self.dismiss(Job(name=name, source=str(folder.resolve())))


class PickFoldersScreen(Dialog):
    """Tick every folder to back up and add them at once. Exploring (arrows, Enter) never adds anything: only the
    save line under the tree does, or Ctrl+S. Each one goes to the destination under its own name (Documents,
    Documents 2 if that name is taken); a folder already backed up is skipped."""

    def __init__(self, taken: set[str], sources: set[str], remote: str, start: Path | None = None) -> None:
        super().__init__()
        self.taken = taken
        self.sources = {source.lower() for source in sources}
        self.remote = remote
        self.start = start if start is not None and start.is_dir() else Path.home()
        bind(self, "ctrl+s", "save", show=False)

    def compose(self) -> ComposeResult:
        with Vertical(id="dialog", classes="pick"):
            yield Label(t("pick.title"), classes="title")
            yield Input(str(self.start), id="path", placeholder=t("add.path"))
            yield Label(t("pick.jump"), classes="hint")
            yield CheckTree(self.start, id="tree")
            yield Static(id="picked")
            yield LinkRow("", "", id="pick-save")
            yield Label("", id="error", classes="error")
            yield Static(hint_text(t("hint.pick")), classes="hint")

    def on_mount(self) -> None:
        bind(self.query_one("#path", Input), "down", "app.focus_next", show=False)  # ↓ back into the tree
        self.query_one(CheckTree).focus()
        self._summary()

    def _summary(self) -> None:
        tree = self.query_one(CheckTree)
        checked = tree.checked
        if not checked:
            text = Text(t("pick.none"), style="grey62")
        else:
            text = Text.assemble((f"✓ {t('pick.some', n=len(checked))}  ", f"bold {ACCENT}"))
            for i, path in enumerate(checked):
                # with the folder that holds it: two "Varios" in different places read apart
                left_out = tree.left_out(path)
                held = [path.parent.name] if path.name and path.parent.name else []
                if left_out:
                    held.append(t("pick.without", names=", ".join(left_out)))
                text.append_text(Text.assemble((", " if i else "", ""), (path.name or str(path), ""),
                                               (f" ({', '.join(held)})" if held else "", "grey50")))
            if self.remote:
                text.append(f"\n{t('pick.dest', dest=fmt.path(join(self.remote, '…')))}", style="grey50")
        self.query_one("#picked", Static).update(text)
        save = self.query_one("#pick-save", LinkRow)
        save.display = bool(checked)
        save.label, save.desc = t("pick.save", n=len(checked)), t("pick.save.desc")
        save.refresh()

    @on(LinkRow.Chosen, "#pick-save")
    def _save_chosen(self) -> None:
        self.action_save()

    def action_cancel(self) -> None:
        """Ticks never vanish without asking: Esc with folders ticked offers to add them first."""
        checked = self.query_one(CheckTree).checked
        if not checked:
            self.dismiss(None)
            return

        def answered(yes: bool | None) -> None:
            if yes:
                self.action_save()
            elif yes is False:
                self.dismiss(None)

        self.app.push_screen(ConfirmScreen(t("confirm.pick", n=len(checked)), t("yes.add"), t("no.drop"),
                                           default_yes=True), answered)

    def on_check_tree_checked(self) -> None:
        self.query_one("#error", Label).update("")
        self._summary()

    @on(Input.Submitted, "#path")
    def _jump(self, event: Input.Submitted) -> None:
        folder = Path(event.value.strip().strip('"'))
        if folder.is_dir():
            tree = self.query_one(CheckTree)
            tree.path = folder
            tree.focus()
            self.query_one("#error", Label).update("")
        else:
            self.query_one("#error", Label).update(t("add.err.path"))

    def action_save(self) -> None:
        tree = self.query_one(CheckTree)
        folders = tree.checked
        if not folders:
            return  # nothing ticked, nothing added: the cursor alone never chooses
        taken = set(self.taken)
        jobs: list[Job] = []
        for folder in folders:
            source = str(folder.resolve())
            if source.lower() in self.sources:
                continue  # already backed up
            base = folder.name or str(folder).rstrip(":\\") or "Disk"
            name, n = base, 2
            while name_problem(name, taken):
                name, n = f"{base} {n}", n + 1
            taken.add(name)
            jobs.append(Job(name=name, source=source, rules=tree.rules(folder)))
        if not jobs:
            self.query_one("#error", Label).update(t("pick.err.none"))
            return
        self.dismiss(jobs)


class RemoteScreen(Dialog):
    def __init__(self, current: str) -> None:
        super().__init__()
        self.current = current
        bind(self, "ctrl+s", "save", "key.save")

    def compose(self) -> ComposeResult:
        with Vertical(id="dialog"):
            yield Label(t("remote.title"), classes="title")
            yield Label(t("remote.hint"), classes="hint")
            yield Input(self.current, id="remote", placeholder="gdrive:Backups")
            yield Label(t("remote.loading"), id="remotes-label", classes="hint")
            yield OptionList(id="remotes")
            yield Label("", id="error", classes="error")
            with Horizontal(classes="buttons"):
                yield Button(t("key.save"), variant="primary", id="save")
                yield Button(t("key.cancel"), id="cancel")

    def on_mount(self) -> None:
        self.query_one("#remote", Input).focus()
        self._load_remotes()

    @work(exclusive=True)
    async def _load_remotes(self) -> None:
        remotes = await rclone.list_remotes()
        options = self.query_one("#remotes", OptionList)
        options.add_options(remotes)
        options.display = bool(remotes)
        self.query_one("#remotes-label", Label).update(t("remote.list") if remotes else t("remote.none"))

    @on(OptionList.OptionSelected)
    def _chosen(self, event: OptionList.OptionSelected) -> None:
        field = self.query_one("#remote", Input)
        field.value = f"{event.option.prompt}Backups"
        field.focus()

    @on(Input.Submitted, "#remote")
    @on(Button.Pressed, "#save")
    def action_save(self) -> None:
        value = self.query_one("#remote", Input).value.strip().strip('"')
        if not value:
            self.query_one("#error", Label).update(t("remote.err.empty"))
            return
        self.dismiss(value)


class AnswerList(OptionList):
    """Answers one under the other. Chosen on mouse down: a click is a press and a release on the same widget, and
    some Windows terminals (VS Code's among them) lose the release."""

    def _on_mouse_down(self, event: events.MouseDown) -> None:
        index = event.style.meta.get("option")
        if event.button == 1 and index is not None:
            event.stop()
            event.prevent_default()  # Textual calls the handler of every class in the MRO otherwise
            self.highlighted = index
            self.action_select()

    async def _on_click(self, event: events.Click) -> None:
        event.stop()  # already answered on the way down
        event.prevent_default()


class ConfirmScreen(ModalScreen[bool]):
    """A yes/no as a short list, like a terminal prompt: the question in bold, what it means under it, and the two
    answers one under the other with the chosen one marked (❯ and the teal wash), never two look-alike buttons.
    The cursor starts on No, and the yes is red: it deletes or stops something. A safe yes (`default_yes`, such as
    "save them") holds the cursor and keeps the accent colour."""

    def __init__(self, message: str, yes: str | None = None, no: str | None = None,
                 default_yes: bool = False) -> None:
        super().__init__()
        question, mark, detail = message.partition("?")
        self.question, self.detail = (question + mark, detail.strip()) if mark else (message, "")
        self.answers = [("yes", yes or t("yes")), ("no", no or t("no"))]
        self.default_yes = default_yes
        # Both letters for yes: "s" is what a Spanish speaker reaches for, "y" what everyone else does.
        bind(self, "y,s", "answer(True)", show=False)
        bind(self, "n", "answer(False)", show=False)
        bind(self, "escape", "cancel", show=False)  # neither answer: callers read None as "leave things as they are"

    def compose(self) -> ComposeResult:
        with Vertical(id="dialog", classes="confirm"):
            yield Label(self.question, classes="question")
            if self.detail:
                yield Label(self.detail, classes="detail")
            yield AnswerList(id="answers")
            yield Static(hint_text(t("hint.confirm")), classes="hint")

    def on_mount(self) -> None:
        answers = self.query_one("#answers", AnswerList)
        answers.set_options(Option("", id=answer) for answer, _ in self.answers)
        answers.highlighted = 0 if self.default_yes else 1
        answers.focus()
        self._draw()

    def _draw(self) -> None:
        answers = self.query_one("#answers", AnswerList)
        for index, (answer, label) in enumerate(self.answers):
            here = index == answers.highlighted
            colour = ("#f7768e" if not self.default_yes else ACCENT) if answer == "yes" else ""
            answers.replace_option_prompt_at_index(index, Text.assemble(
                ("❯ " if here else "  ", f"bold {ACCENT}"),
                (label, f"bold {colour}".strip() if here else colour or "grey70")))

    @on(OptionList.OptionHighlighted, "#answers")
    def _moved(self) -> None:
        self._draw()

    @on(OptionList.OptionSelected, "#answers")
    def _chosen(self, event: OptionList.OptionSelected) -> None:
        self.dismiss(event.option.id == "yes")

    def action_answer(self, value: bool) -> None:
        self.dismiss(value)

    def action_cancel(self) -> None:
        self.dismiss(None)


class ActionsScreen(ModalScreen[str | None]):
    """What can be done with one folder, for Enter: the same actions as the keys, without knowing the keys."""

    def __init__(self, title: str, actions: list[tuple[str, str, str]]) -> None:
        super().__init__()
        self.title_text = title
        self.actions = actions  # (action, label, key)
        bind(self, "escape", "cancel", show=False)

    def compose(self) -> ComposeResult:
        options = []
        for action, label, key in self.actions:
            line = Text(f"{label:<28}")
            line.append(key, style="grey50")
            options.append(Option(line, id=action))
        with Vertical(id="dialog", classes="actions"):
            yield Label(self.title_text, classes="title")
            yield OptionList(*options, id="actions")
            yield Label(t("hint.actions"), classes="hint")

    def on_mount(self) -> None:
        self.query_one("#actions").focus()

    def action_cancel(self) -> None:
        self.dismiss(None)

    @on(OptionList.OptionSelected, "#actions")
    def _chosen(self, event: OptionList.OptionSelected) -> None:
        self.dismiss(event.option.id)
