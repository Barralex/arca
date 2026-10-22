"""One folder's own screen, opened with Enter from the folders list: what can be done with it, and every file in it
with a box, ticked is copied. Esc goes back to the list; ticks not saved yet are asked about first."""
from __future__ import annotations

import asyncio
from pathlib import Path

from rich.text import Text
from textual import events, on, work
from textual.app import ComposeResult
from textual.containers import Vertical
from textual.widgets import OptionList, Static
from textual.widgets.option_list import Option

from arca import fmt
from arca.core import config, rclone, selection
from arca.core.config import Job, join
from arca.core.jobs import filter_args
from arca.i18n import t
from arca.ui.files import FileTree
from arca.ui.pages import EdgeList, LinkRow, Page, row
from arca.ui.screens import ACCENT, ConfirmScreen, bind

ACTIONS = ("run", "dry", "toggle", "open")


class FolderScreen(Page):
    """Where from and where to, what went wrong last time (if anything), the actions, then the files."""

    HINT = "hint.folder"
    DANGER = "hint.files.delete"

    def __init__(self, job: Job) -> None:
        self.job = job
        super().__init__()
        bind(self, "ctrl+s", "save", show=False)
        bind(self, "delete", "delete", show=False)
        self._touched = False  # a key pressed here: the list loading late never pulls the cursor away

    def body(self) -> ComposeResult:
        yield Static(id="folder-head")
        yield EdgeList(id="folder-actions")
        yield Static(id="files-note")
        yield Vertical(id="files-holder")
        yield LinkRow("", "", id="files-save")

    def on_mount(self) -> None:
        super().on_mount()
        self.query_one(".panel").border_title = self.job.name
        self.refresh_head()
        self._draw_actions()
        self.query_one("#folder-actions").focus()
        self._show_save(0)
        self._load_files()

    def on_key(self, event: events.Key) -> None:
        self._touched = True

    # --- head and actions -------------------------------------------------------------------------------------------

    def refresh_head(self) -> None:
        labels = (t("detail.from"), t("detail.to"))
        width = max(len(label) for label in labels) + 2
        remote = self.arca.cfg.remote
        dest = fmt.path(join(remote, self.job.name), 100) if remote else "–"
        head = Text.assemble((labels[0].ljust(width), "grey50"), (fmt.path(self.job.source, 100), "grey70"), "\n",
                             (labels[1].ljust(width), "grey50"), (dest, "grey70"))
        failure = self.arca.failure_text(self.job)
        if failure is not None:  # never silent: what went wrong last time and the fix, before anything else
            head.append("\n\n")
            head.append_text(failure)
        self.query_one("#folder-head", Static).update(head)

    def _draw_actions(self) -> None:
        actions = self.query_one("#folder-actions", EdgeList)
        highlighted = actions.highlighted
        dest = fmt.path(join(self.arca.cfg.remote, self.job.name), 60) if self.arca.cfg.remote else "–"
        lines = {
            "run": (t("act.run"), t("folder.run.desc", dest=dest)),
            "dry": (t("act.dry"), t("folder.dry.desc")),
            "toggle": (t("act.off"), t("folder.off.desc")) if self.job.enabled else
                      (t("act.on"), t("folder.on.desc")),
            "open": (t("act.open"), t("folder.open.desc")),
        }
        width = max(len(label) for label, _ in lines.values()) + 2
        actions.set_options(Option(row(label, desc, label_width=width), id=action)
                            for action, (label, desc) in lines.items())
        actions.highlighted = highlighted or 0

    @on(OptionList.OptionSelected, "#folder-actions")
    def _act(self, event: OptionList.OptionSelected) -> None:
        action = event.option.id
        if action in ("run", "dry"):  # the backup follows what is saved: settle the ticks, then watch it in the list
            self.settle(lambda: self._run(dry=action == "dry"))
        elif action == "toggle":
            self.arca.toggle_job(self.job)
            self._draw_actions()
        elif action == "open":
            self.arca.open_folder(self.job)

    def _run(self, dry: bool) -> None:
        if self.arca.run_job(self.job, dry):
            self.app.pop_screen()

    def action_delete(self) -> None:
        self.arca.delete_job(self.job, then=self.app.pop_screen)

    def action_back(self) -> None:
        self.settle(self.app.pop_screen)

    # --- files ------------------------------------------------------------------------------------------------------

    def _tree(self) -> FileTree | None:
        trees = self.query(FileTree)
        return trees.first() if trees else None

    @work(group="files", exclusive=True)
    async def _load_files(self) -> None:
        job, note = self.job, self.query_one("#files-note", Static)
        keep = isinstance(self.focused, FileTree)  # a reload after a save keeps the keys in the list
        await self.query_one("#files-holder").remove_children()
        self._show_save(0)
        note.update(Text(t("files.loading", name=job.name), style="grey62"))
        if not Path(job.source).is_dir():
            note.update(Text(f"{self.arca._say('err.source', job)} {self.arca._say('fix.source', job)}",
                             style="#e0af68"))
            return
        # Listed fresh every time the folder is opened: what was added or moved since shows up as it is now.
        try:
            copied, sizes = await asyncio.gather(rclone.listing(job.source, filter_args(job, self.arca.filter_file)),
                                                 rclone.listing(job.source, []))
        except rclone.RcloneMissing:
            note.update(Text(self.arca._say("fix.norclone"), style="#e0af68"))
            return
        except RuntimeError as exc:
            note.update(Text(t("files.failed", why=str(exc)), style="#e0af68"))
            return
        tree = FileTree(Path(job.source), rclone.with_folders(copied), sizes, id="files")
        self._count(tree)
        await self.query_one("#files-holder").mount(tree)
        if keep or not self._touched:
            tree.focus()

    def _count(self, tree: FileTree) -> None:
        """The total on top of the list, recounted on every tick: what this folder's backup takes."""
        n, size = selection.total(tree.sizes, tree.included, tree.changes)
        self.query_one("#files-note", Static).update(Text.assemble(
            (t("files.count", n=n, size=fmt.size(size)), "bold"),
            (f"  {t('files.of', size=fmt.size(sum(tree.sizes.values())))}", "grey62"), ("     [✓] ", f"bold {ACCENT}"),
            (t("files.in"), "grey62"), ("   [ ] ", "grey42"), (t("files.out"), "grey62")))

    def on_file_tree_changed(self) -> None:
        tree = self._tree()
        if tree is not None:
            self._count(tree)
            self._show_save(len(tree.changes))

    def _show_save(self, changes: int) -> None:
        save = self.query_one("#files-save", LinkRow)
        save.display = changes > 0
        save.label, save.desc = t("files.save", n=changes), t("files.save.desc")
        save.refresh()

    @on(LinkRow.Chosen, "#files-save")
    def _save_chosen(self) -> None:
        self.action_save()

    def action_save(self, reload: bool = True) -> None:
        tree = self._tree()
        if tree is None or not tree.changes:
            return
        self.job.rules = selection.merge(tree.changes, self.job.rules)
        config.save(self.arca.cfg)
        tree.changes.clear()
        self.app.notify(t("msg.files_saved", name=self.job.name))
        if reload:
            self._load_files()  # recount from rclone, with the new rules

    def settle(self, then) -> None:
        """Unsaved ticks never vanish: before leaving them, ask. Esc stays here with them."""
        tree = self._tree()
        if tree is None or not tree.changes:
            then()
            return

        def answered(yes: bool | None) -> None:
            if yes is None:
                return
            if yes:
                self.action_save(reload=False)  # on the way out: nothing left to recount
            else:
                tree.changes.clear()
            then()

        self.app.push_screen(ConfirmScreen(t("confirm.files", name=self.job.name, n=len(tree.changes)),
                                           t("yes.save"), t("no.discard"), default_yes=True), answered)
