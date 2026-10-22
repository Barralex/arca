import shutil
from pathlib import Path

import pytest
from rich.style import Style
from textual.widgets import DataTable

from arca.core import config, history, preflight, rclone
from arca.core.config import Config, Job
from arca.core.jobs import Result
from arca.core.preflight import Health
from arca.ui.app import ArcaApp
from arca.ui.files import FileTree
from arca.ui.folder import FolderScreen
from arca.ui.screens import CheckTree, ConfirmScreen, PickFoldersScreen


@pytest.fixture(autouse=True)
def healthy(every_file, monkeypatch):
    async def ok(remote):
        return Health(True, local=True)

    async def signed_in():
        return {"gdrive:": True}

    async def remotes():
        return ["gdrive:"]

    monkeypatch.setattr(preflight, "check", ok)
    monkeypatch.setattr(rclone, "list_remotes", remotes)
    monkeypatch.setattr(rclone, "account_states", signed_in)


async def test_empty_state_explains_the_next_step():
    app = ArcaApp(Config(remote="D:\\Backups"), home=False)
    async with app.run_test(size=(120, 40)):
        assert app.query_one("#empty").display
        assert not app.query_one(DataTable).display


async def test_ctrl_c_twice_quits_with_a_single_notice():
    app = ArcaApp(Config(remote="D:\\Backups"))
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.press("ctrl+c")
        await pilot.pause()
        assert len(app._notifications) == 1 and app.is_running
        await pilot.press("ctrl+c")
        await pilot.pause()
    assert not app.is_running


async def go_to(pilot, folder: Path) -> None:
    """Keys only, as a person would: ↑ from the top of the tree to the path field, clear it, type, Enter."""
    await pilot.press("up", "end", "ctrl+u", *str(folder), "enter")
    await pilot.pause(0.5)
    assert isinstance(pilot.app.focused, CheckTree)


async def test_folders_are_ticked_and_saved_at_once(tmp_path):
    root = tmp_path / "home"
    for name in ("Music", "Photos", "Work"):
        (root / name).mkdir(parents=True)
    app = ArcaApp(Config(remote=str(tmp_path / "dest"), jobs=[Job("Music", str(tmp_path / "old"))]), home=False)
    async with app.run_test(size=(120, 50)) as pilot:
        await pilot.press("a")
        assert isinstance(app.screen, PickFoldersScreen)
        await go_to(pilot, root)
        # root, Music, Photos, Work: tick Music with space and Work with Tab, then ↓ to the save line and Enter.
        await pilot.press("down", "space", "down", "down", "tab", "down")
        assert app.screen.focused.id == "pick-save"
        await pilot.press("enter")
        await pilot.pause()
        assert app.screen is app.main
    # "Music" is taken by another folder, so the new one is "Music 2"; each keeps its own name otherwise.
    assert [(job.name, job.source) for job in config.load().jobs] == [
        ("Music", str(tmp_path / "old")), ("Music 2", str((root / "Music").resolve())),
        ("Work", str((root / "Work").resolve()))]


@pytest.mark.skipif(shutil.which("rclone") is None, reason="needs rclone on PATH")
async def test_running_a_job_shows_its_result(tmp_path):
    source = tmp_path / "src"
    source.mkdir()
    (source / "a.txt").write_text("a", encoding="utf-8")
    app = ArcaApp(Config(remote=str(tmp_path / "dest"), jobs=[Job("Docs", str(source))]), home=False)
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.press("r")
        for _ in range(100):
            await pilot.pause(0.1)
            if app.views["Docs"].result is not None:
                break
        result = app.views["Docs"].result
        assert result is not None and result.status == "ok", result
    assert (tmp_path / "dest" / "Docs" / "a.txt").exists()


async def test_enter_explores_and_never_adds_a_folder(tmp_path):
    """Keyboard only: Enter opens Documents, Tab ticks a folder inside it, and nothing is added until the save line.
    Esc with ticks asks before they are lost."""
    (tmp_path / "home" / "Documents" / "Taxes").mkdir(parents=True)
    (tmp_path / "home" / "Documents" / "Games").mkdir()
    app = ArcaApp(Config(remote=str(tmp_path / "dest")), home=False)
    async with app.run_test(size=(120, 50)) as pilot:
        await pilot.press("a")
        tree = app.screen.query_one(CheckTree)
        await go_to(pilot, tmp_path / "home")
        await pilot.press("up")  # ↑ at the top reaches the path field, ↓ comes back
        assert app.focused.id == "path"
        await pilot.press("down")
        assert app.focused is tree
        await pilot.press("down", "enter")  # Documents opens: nothing added
        await pilot.pause(0.5)
        assert isinstance(app.screen, PickFoldersScreen) and tree.cursor_node.is_expanded
        assert not app.screen.query_one("#pick-save").display  # no save line while nothing is ticked
        await pilot.press("ctrl+s")  # nor does Ctrl+S add the folder under the cursor
        assert isinstance(app.screen, PickFoldersScreen) and config.load().jobs == []
        await pilot.press("down", "down", "tab")  # Games, Taxes: Taxes ticked
        assert [path.name for path in tree.checked] == ["Taxes"]
        assert app.screen.query_one("#pick-save").display
        await pilot.press("enter")  # Enter on a ticked folder still only explores
        assert isinstance(app.screen, PickFoldersScreen)
        await pilot.press("escape")
        await pilot.pause()
        assert isinstance(app.screen, ConfirmScreen)  # the ticks are not dropped silently
        await pilot.press("enter")  # "Yes, add them" holds the cursor
        await pilot.pause()
        assert app.screen is app.main
    assert [job.name for job in config.load().jobs] == ["Taxes"]


async def test_the_arrows_open_and_close_folders(tmp_path):
    (tmp_path / "home" / "Projects" / "Web").mkdir(parents=True)
    app = ArcaApp(Config(remote=str(tmp_path / "dest")), home=False)
    async with app.run_test(size=(120, 50)) as pilot:
        await pilot.press("a")
        tree = app.screen.query_one(CheckTree)
        await go_to(pilot, tmp_path / "home")
        await pilot.press("down", "right")  # Projects opens
        await pilot.pause(0.5)
        assert tree.cursor_node.is_expanded and tree.cursor_node.children
        await pilot.press("right")  # and a second → steps into it
        assert tree.cursor_node.data.path.name == "Web"
        await pilot.press("left", "left")  # out to Projects, then it closes
        assert tree.cursor_node.data.path.name == "Projects" and not tree.cursor_node.is_expanded


async def test_del_removes_a_folder_after_asking(tmp_path):
    app = ArcaApp(Config(remote=str(tmp_path / "dest"), jobs=[Job("Music", str(tmp_path))]), home=False)
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.press("delete")
        await pilot.pause()
        assert isinstance(app.screen, ConfirmScreen) and app.cfg.jobs  # never without asking
        await pilot.press("s")
        await pilot.pause()
    assert config.load().jobs == []


def _folder(tmp_path: Path, name: str) -> Path:
    root = tmp_path / name
    (root / "drafts").mkdir(parents=True)
    (root / "a.txt").write_text("a", encoding="utf-8")
    (root / "drafts" / "d.txt").write_text("d", encoding="utf-8")
    return root


async def test_enter_opens_a_folder_where_its_files_are_ticked_and_saved(tmp_path):
    root = _folder(tmp_path, "Docs")
    app = ArcaApp(Config(remote=str(tmp_path / "dest"), jobs=[Job("Docs", str(root))]), home=False)
    async with app.run_test(size=(120, 50)) as pilot:
        await pilot.pause(0.3)
        assert not app.main.query(FileTree)  # the list is only the folders
        await pilot.press("enter")  # the folder's own screen
        await pilot.pause(0.5)
        screen = app.screen
        assert isinstance(screen, FolderScreen)
        tree = screen.query_one(FileTree)
        assert app.focused is tree  # the keys land in the files once they are listed
        note = screen.query_one("#files-note")
        assert "2 files · 2 B in the backup  of 2 B in the folder" in str(note.render())
        assert "drafts  1 B" in str(tree.render_label(tree.root.children[0], Style(), Style()))  # size, softly
        await pilot.press("tab")  # drafts/ (folders first): unticked, Tab as well as space
        await pilot.pause()
        assert app.focused is tree and screen.query_one("#files-save").display  # the save line shows up
        assert "1 files" in str(note.render())  # the total follows the ticks
        await pilot.press("ctrl+s")
        await pilot.pause(0.3)
        assert not screen.query_one("#files-save").display
        assert isinstance(app.focused, FileTree)  # the reloaded list keeps the keys
        await pilot.press("up")  # ↑ at the top walks to the folder's actions
        assert app.focused is screen.query_one("#folder-actions")
        await pilot.press("escape")  # back to the folders
        await pilot.pause()
        assert app.screen is app.main
    assert config.load().jobs[0].rules == ["- /drafts/**"]


async def test_leaving_a_folder_with_unsaved_ticks_asks_first(tmp_path):
    app = ArcaApp(Config(remote=str(tmp_path / "dest"), jobs=[Job("Docs", str(_folder(tmp_path, "Docs")))]),
                  home=False)
    async with app.run_test(size=(120, 50)) as pilot:
        await pilot.press("enter")
        await pilot.pause(0.5)
        await pilot.press("space")  # untick drafts/
        await pilot.press("escape")  # the ticks would be lost
        await pilot.pause()
        assert isinstance(app.screen, ConfirmScreen)
        await pilot.press("enter")  # the cursor starts on "Yes, save them"
        await pilot.pause(0.3)
        assert app.screen is app.main
    assert config.load().jobs[0].rules == ["- /drafts/**"]


async def test_the_list_ends_with_add_folders_and_a_failure_shows_under_it(tmp_path):
    history.append(Result(job="Docs", status="failed", started="2026-10-22T02:42:00", errors=["boom"]))
    app = ArcaApp(Config(remote=str(tmp_path / "dest"), jobs=[Job("Docs", str(_folder(tmp_path, "Docs")))]),
                  home=False)
    async with app.run_test(size=(120, 50)) as pilot:
        await pilot.pause(0.3)
        assert "boom" in str(app.main.query_one("#row-note").render())  # never silent, right under the row
        await pilot.press("down")  # ↓ past the last folder: "add folders"
        assert app.focused is app.main.query_one("#add-folders")
        opened = []
        app.action_add = lambda: opened.append("picker")  # the picker itself is tested above; not the real home
        await pilot.press("enter")
        assert opened == ["picker"]


async def test_a_dry_run_never_looks_like_a_backup(tmp_path):
    history.append(Result(job="Docs", status="ok", dry_run=True, started="2026-10-22T02:42:00",
                          finished="2026-10-22T02:42:04", duration=4, transfers=2847, bytes=31_800_000_000))
    app = ArcaApp(Config(remote=str(tmp_path / "dest"), jobs=[Job("Docs", str(_folder(tmp_path, "Docs")))]),
                  home=False)
    async with app.run_test(size=(120, 50)) as pilot:
        await pilot.pause(0.3)
        assert app._state(app.cfg.jobs[0]) == "idle"  # "not backed up yet", not "ok"
        assert "no backup yet" in str(app.main.query_one("#summary").render())


async def test_opening_a_folder_again_lists_its_files_again(tmp_path):
    root = _folder(tmp_path, "Docs")
    app = ArcaApp(Config(remote=str(tmp_path / "dest"), jobs=[Job("Docs", str(root))]), home=False)
    async with app.run_test(size=(120, 50)) as pilot:
        await pilot.press("enter")
        await pilot.pause(0.5)
        (root / "new.txt").write_text("new", encoding="utf-8")  # added while Arca is open
        await pilot.press("escape", "enter")  # back to the list, and in again
        await pilot.pause(0.5)
        assert "new.txt" in app.screen.query_one(FileTree).sizes
