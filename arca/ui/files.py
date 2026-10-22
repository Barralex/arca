"""A folder's files with a box on every line: ticked is copied, unticked stays on this computer."""
from __future__ import annotations

from pathlib import Path

from rich.text import Text
from textual.binding import Binding
from textual.message import Message
from textual.widgets import DirectoryTree

from arca import fmt
from arca.core import selection
from arca.ui.screens import ACCENT


class FileTree(DirectoryTree):
    """Every file and folder, the ones the filter leaves out too (.gitignore, node_modules), so the user decides.
    Keyboard first: space or Tab ticks, → opens a folder (or steps into it), ← closes it (or steps out), ↑ at the
    top goes back to the table."""

    BINDINGS = [
        Binding("space,tab", "check", show=False),  # Tab too: ↑↓ already leave the list
        Binding("right", "open", show=False),
        Binding("left", "close", show=False),
    ]

    class Changed(Message):
        pass

    def __init__(self, root: Path, included: set[str], sizes: dict[str, int], **kwargs) -> None:
        # Set before the tree's own __init__, which already renders the root's label.
        self.root_path = root
        self.included = included  # what a backup copies today, by rclone
        self.sizes = sizes  # every file in the folder and its size, for the live total
        self.folder_sizes: dict[str, int] = {}  # each folder's size on disk, shown softly next to its name
        for path, size in sizes.items():
            parts = path.split("/")
            for depth in range(1, len(parts)):
                folder = "/".join(parts[:depth]) + "/"
                self.folder_sizes[folder] = self.folder_sizes.get(folder, 0) + size
        self.changes: dict[str, bool] = {}  # the user's ticks on top of it, not saved yet
        super().__init__(root, **kwargs)
        self.show_root = False

    def on_focus(self) -> None:
        if self.cursor_line < 0:
            self.cursor_line = 0  # arriving from the table lands on the first line, ready for space

    def key(self, path: Path) -> str:
        rel = path.relative_to(self.root_path).as_posix()
        return f"{rel}/" if path.is_dir() else rel

    def render_label(self, node, base_style, style) -> Text:
        label = super().render_label(node, base_style, style)
        if node.data is None or Path(node.data.path) == self.root_path:
            return label
        key = self.key(Path(node.data.path))
        if self.folder_sizes.get(key):
            label = Text.assemble(label, (f"  {fmt.size(self.folder_sizes[key])}", "grey42"))
        if selection.ticked(key, self.included, self.changes):
            return Text.assemble(("[✓] ", f"bold {ACCENT}"), label)
        label.stylize("grey50")
        return Text.assemble(("[ ] ", "grey42"), label)

    def action_check(self) -> None:
        node = self.cursor_node
        if node is None or node.data is None:
            return
        selection.toggle(self.key(Path(node.data.path)), self.included, self.changes)
        self._invalidate()  # a folder repaints everything inside it
        self.post_message(self.Changed())

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
        elif node.parent is not None and node.parent is not self.root:
            self.action_cursor_parent()

    def action_cursor_down(self) -> None:
        if self.cursor_line >= self.last_line:
            self.screen.focus_next()  # on to "save the changes" when it is there
            return
        super().action_cursor_down()

    def action_cursor_up(self) -> None:
        if self.cursor_line <= 0:
            self.screen.focus_previous()  # out of the list, back to the table
            return
        super().action_cursor_up()
