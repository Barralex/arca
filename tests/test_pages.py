from datetime import date

import pytest
from textual.widgets import DataTable, Input

from arca.core import config, history, importer, metrics, preflight, rclone
from arca.core.config import Config, Job
from arca.core.errors import diagnose
from arca.core.importer import Candidate, Plan, build_plan, parse_script
from arca.core.jobs import Result
from arca.core.preflight import Health
from arca.ui.app import ArcaApp
from arca.ui.folder import ACTIONS, FolderScreen
from arca.ui.pages import AccountsScreen, Choice, HomeScreen, ImportScreen, MetricsScreen
from arca.ui.screens import AddJobScreen, ConfirmScreen

OLD_SCRIPT = r"""
$Remote = 'gdrive:Backups'
$Jobs = @(
    @{ Name = 'Pictures';  Source = "$env:USERPROFILE\OneDrive\Pictures"  }
    @{ Name = 'Documents'; Source = "$env:USERPROFILE\OneDrive\Documents" }
)
"""


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


def test_the_old_script_is_read_with_its_environment_paths(monkeypatch):
    monkeypatch.setenv("USERPROFILE", "C:\\Users\\me")
    remote, jobs = parse_script(OLD_SCRIPT)
    assert remote == "gdrive:Backups"
    assert jobs == [Job("Pictures", "C:\\Users\\me\\OneDrive\\Pictures"),
                    Job("Documents", "C:\\Users\\me\\OneDrive\\Documents")]


def test_remote_names_win_and_match_across_languages(tmp_path):
    jobs = [Job("Pictures", "C:\\pics"), Job("Documents", "C:\\docs"), Job("Music", "C:\\music")]
    candidates, versions = build_plan(["Fotos", "Documents", "_versiones"], jobs, homes=[tmp_path])
    # Fotos keeps its name, so the next run continues there instead of uploading everything again.
    assert [(c.name, c.source, c.on_remote) for c in candidates] == [
        ("Fotos", "C:\\pics", True), ("Documents", "C:\\docs", True), ("Music", "C:\\music", False)]
    # and each one says where its local folder came from
    assert [(c.origin, c.matched) for c in candidates] == [
        ("script", "Pictures"), ("script", "Documents"), ("script", "Music")]
    assert versions == "_versiones"


def test_a_remote_folder_finds_its_local_twin_by_name(tmp_path):
    (tmp_path / "Descargas").mkdir()
    candidates, versions = build_plan(["Downloads", "Nope"], [], homes=[tmp_path])
    assert candidates[0].source == str(tmp_path / "Descargas")
    assert (candidates[0].origin, candidates[0].matched) == ("name", "Descargas")
    assert (candidates[1].source, candidates[1].origin) == ("", "")  # not found: the user picks it
    assert versions is None


def test_metrics_count_real_backups_and_mark_the_days():
    def result(job, status, when, dry=False):
        return Result(job=job, status=status, dry_run=dry, started=when, finished=when, duration=10,
                      transfers=2, bytes=100)

    results = [result("Docs", "ok", "2026-10-20T21:00:00"), result("Docs", "failed", "2026-10-21T21:00:00"),
               result("Docs", "ok", "2026-10-21T22:00:00"), result("Docs", "ok", "2026-10-22T08:00:00", dry=True),
               result("Gone", "ok", "2026-10-22T08:00:00")]
    (docs,) = metrics.compute(results, ["Docs"], today=date(2026, 10, 22))
    assert (docs.runs, docs.ok, docs.failed, docs.bytes) == (3, 2, 1, 200)
    assert docs.days[-3:] == ["ok", "ok", ""]  # a failure and a later success the same day: the day was saved
    assert docs.last_failed == "2026-10-21T21:00:00"


async def test_home_opens_the_folders_and_esc_comes_back():
    app = ArcaApp(Config(remote="D:\\Backups", jobs=[Job("Docs", "C:\\docs")]))
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        assert isinstance(app.screen, HomeScreen)
        await pilot.press("d")  # folder keys do nothing here: no folder is on screen
        assert isinstance(app.screen, HomeScreen)
        await pilot.press("enter")
        assert app.screen is app.main
        await pilot.press("escape")
        assert isinstance(app.screen, HomeScreen)


async def test_enter_on_a_folder_opens_what_can_be_done_with_it():
    app = ArcaApp(Config(remote="D:\\Backups", jobs=[Job("Docs", "C:\\docs")]), home=False)
    async with app.run_test(size=(120, 40)) as pilot:
        app.main.query_one(DataTable).focus()
        await pilot.press("enter")
        await pilot.pause()
        assert isinstance(app.screen, FolderScreen)
        actions = app.screen.query_one("#folder-actions")
        assert [actions.get_option_at_index(i).id for i in range(actions.option_count)] == list(ACTIONS)
        await pilot.press("delete")  # Del: take the folder out of Arca, after asking
        assert isinstance(app.screen, ConfirmScreen)
        await pilot.press("up", "enter")
        await pilot.pause()
        assert app.screen is app.main and not app.cfg.jobs


async def test_import_adds_the_ticked_folders_and_keeps_the_archive(tmp_path, monkeypatch):
    pics = tmp_path / "Pictures"
    pics.mkdir()
    plan = Plan(None, "gdrive:Backups", [Candidate("Fotos", str(pics), True, True),
                                         Candidate("Music", "", False, True)], "_versiones")

    async def find(remote, script=None, look_for_script=True):
        return plan

    monkeypatch.setattr(importer, "find", find)
    app = ArcaApp(Config(), home=False)
    async with app.run_test(size=(120, 40)) as pilot:
        app.open_page("import")
        await pilot.pause()
        await pilot.pause()
        assert isinstance(app.screen, ImportScreen)
        await pilot.press("end", "enter")
        await pilot.pause()
        # It stays to say what was saved, then Enter on the first line goes to the folders.
        assert isinstance(app.screen, ImportScreen) and app.screen.done
        await pilot.press("enter")
        await pilot.pause()
        assert app.screen is app.main
    saved = config.load()
    assert saved.jobs == [Job("Fotos", str(pics))]
    assert (saved.remote, saved.versions) == ("gdrive:Backups", "_versiones")


async def test_esc_leaves_the_import_without_saving(tmp_path, monkeypatch):
    pics = tmp_path / "Pictures"
    pics.mkdir()

    async def find(remote, script=None, look_for_script=True):
        return Plan(None, "gdrive:Backups", [Candidate("Fotos", str(pics), True, True)], None)

    monkeypatch.setattr(importer, "find", find)
    app = ArcaApp(Config(remote="gdrive:Backups"), home=False)
    async with app.run_test(size=(120, 40)) as pilot:
        app.open_page("import")
        await pilot.pause()
        await pilot.press("escape")
        await pilot.pause()
        assert app.screen is app.main
    assert config.load().jobs == []


async def test_a_folder_without_a_local_twin_opens_the_picker(monkeypatch):
    async def find(remote, script=None, look_for_script=True):
        return Plan(None, "gdrive:Backups", [Candidate("Music", "", True, False)], None)

    monkeypatch.setattr(importer, "find", find)
    app = ArcaApp(Config(remote="gdrive:Backups"), home=False)
    async with app.run_test(size=(120, 40)) as pilot:
        app.open_page("import")
        await pilot.pause()
        await pilot.press("home", "enter")
        await pilot.pause()
        assert isinstance(app.screen, AddJobScreen)
        assert app.screen.query_one("#name", Input).value == "Music"


async def test_metrics_page_shows_the_history(monkeypatch):
    async def usage(remote):
        return {"total": 1000, "used": 100, "free": 800}

    monkeypatch.setattr(metrics, "usage", usage)
    history.append(Result(job="Docs", status="ok", dry_run=False, started="2026-10-22T08:00:00",
                          finished="2026-10-22T08:00:10", duration=10, transfers=1, bytes=5))
    app = ArcaApp(Config(remote="gdrive:Backups", jobs=[Job("Docs", "C:\\docs")]), home=False)
    async with app.run_test(size=(120, 40)) as pilot:
        app.open_page("metrics")
        await pilot.pause()
        await pilot.pause()
        assert isinstance(app.screen, MetricsScreen) and app.screen.usage_done


async def test_a_pasted_script_that_is_not_there_is_reported(tmp_path):
    plan = await importer.find("", tmp_path / "nope" / "backup.ps1", look_for_script=False)
    assert plan.missing_script and plan.candidates == []


async def test_a_pasted_script_brings_its_jobs(tmp_path, monkeypatch):
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    (tmp_path / "OneDrive" / "Pictures").mkdir(parents=True)
    (tmp_path / "old.ps1").write_text(OLD_SCRIPT.replace("gdrive:Backups", ""), encoding="utf-8")
    plan = await importer.find("", tmp_path / "old.ps1", look_for_script=False)
    assert [(c.name, c.exists, c.on_remote) for c in plan.candidates] == [
        ("Pictures", True, False), ("Documents", False, False)]


async def test_enter_in_a_field_searches_where_it_points(monkeypatch):
    asked = []

    async def find(remote, script=None, look_for_script=True):
        asked.append((remote, look_for_script))
        return Plan(script, remote, [], None)

    monkeypatch.setattr(importer, "find", find)
    app = ArcaApp(Config(remote="gdrive:Backups"), home=False)
    async with app.run_test(size=(120, 40)) as pilot:
        app.open_page("import")
        await pilot.pause()
        field = app.screen.query_one("#from", Input)
        field.value = "onedrive:Old"
        field.focus()
        await pilot.press("enter")
        await pilot.pause()
    assert asked == [("gdrive:Backups", True), ("onedrive:Old", False)]


async def test_settings_sit_under_the_menu_and_change_with_the_arrows():
    app = ArcaApp(Config(remote="D:\\Backups"))
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        app.screen.query_one("#set-theme", Choice).focus()
        await pilot.press("right")
        assert app.theme == app.cfg.theme != "tokyo-night"
    assert config.load().theme == app.cfg.theme


async def test_automatic_backups_are_scheduled_on_enter(task_scheduler):
    app = ArcaApp(Config(remote="D:\\Backups"))
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        app.screen.query_one("#set-auto", Choice).focus()
        await pilot.press("right")  # Manual -> When I sign in: nothing happens to Windows yet
        assert task_scheduler["mode"] == "manual"
        assert "Enter schedules it" in str(app.screen.query_one("#settings-note").render())
        await pilot.press("right", "enter")  # Every day at 21:00
        await pilot.pause(0.3)
        assert task_scheduler["mode"] == "daily" and app.schedule_mode == "daily"
        assert str(app.screen.query_one("#settings-note").render()).strip() == ""


async def test_the_metrics_change_their_days_with_the_arrows():
    app = ArcaApp(Config(remote="D:\\Backups"), home=False)
    async with app.run_test(size=(120, 40)) as pilot:
        app.open_page("metrics")
        await pilot.pause()
        await pilot.press("right")  # 14 -> 30
        assert app.cfg.metrics_days == 30 and config.load().metrics_days == 30


async def test_the_language_waits_for_enter_then_reopens_on_it():
    app = ArcaApp(Config(remote="D:\\Backups", language="en"))
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        app.screen.query_one("#set-lang", Choice).focus()
        await pilot.press("right")
        assert app.is_running and app.cfg.language == "en"
        await pilot.press("enter")
        await pilot.pause()
    assert app.return_value == "restart:set-lang"
    assert config.load().language != "en"


OLD_LOG = """2026/09/21 09:50:14 INFO  : a.jpg: Copied (new)
2026/09/21 09:50:14 INFO  :
Transferred:   \t        520 B / 520 B, 100%, 129 B/s, ETA 0s
Transferred:            1 / 1, 100%
Elapsed time:         6.0s

2026/09/21 09:51:14 INFO  :
Transferred:   \t   17.467 MiB / 38.665 MiB, 45%, 326.370 KiB/s, ETA 1m6s
Transferred:           52 / 644, 8%
Elapsed time:        59.7s

2026/10/21 23:07:08 ERROR : preflight: CRITICAL: Failed to create file system for "gdrive:": invalid_grant
"""


def test_the_old_log_becomes_history_job_by_job():
    runs = importer.parse_log(OLD_LOG, ["Pictures", "Documents"])
    assert [(r.job, r.status, r.transfers) for r in runs] == [
        ("Pictures", "ok", 1), ("Documents", "stopped", 52), ("Pictures", "failed", 0), ("Documents", "failed", 0)]
    assert runs[0].bytes == 520 and runs[1].duration == 59.7
    assert runs[1].finished.startswith("2026-09-21T09:51:14")  # its own stamp, though the line ends at the colon
    assert runs[2].diagnosis is not None and runs[2].diagnosis.cause == "err.login"


async def test_import_brings_the_old_history_under_the_new_names(tmp_path, monkeypatch):
    pics = tmp_path / "Pictures"
    pics.mkdir()
    runs = importer.parse_log(OLD_LOG, ["Pictures", "Documents"])
    plan = Plan(None, "gdrive:Backups", [Candidate("Fotos", str(pics), True, True)], None, runs=runs)
    app = ArcaApp(Config(remote="gdrive:Backups"), home=False)
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        app.import_plan(plan, plan.candidates)
        app.import_plan(plan, [])  # a second import adds nothing twice
        await pilot.pause()
    assert [(r.job, r.status) for r in history.all_results()] == [("Fotos", "ok"), ("Fotos", "failed")]


async def test_deleting_the_configuration_moves_it_aside_and_reopens():
    config.save(Config(remote="gdrive:Backups", jobs=[Job("Docs", "C:\\docs")]))
    history.append(Result(job="Docs", started="2026-10-22T08:00:00", finished="2026-10-22T08:00:10"))
    app = ArcaApp(config.load())
    async with app.run_test(size=(120, 44)) as pilot:
        await pilot.pause()
        app.screen.query_one("#reset").focus()
        await pilot.press("enter")
        await pilot.pause()
        assert isinstance(app.screen, ConfirmScreen)  # never without asking
        await pilot.press("y")
        await pilot.pause()
    assert app.return_value == "restart:reset"
    assert not config.config_path().exists() and history.all_results() == []
    (kept,) = config.previous_dir().iterdir()
    assert config.load(kept / "config.toml").jobs == [Job("Docs", "C:\\docs")]


async def _confirm(app: ArcaApp, pilot) -> None:
    app.screen.query_one("#reset").focus()
    await pilot.press("enter")
    await pilot.pause()
    assert isinstance(app.screen, ConfirmScreen)


@pytest.mark.parametrize("answer", ["mouse_down", "arrows", "s"])
async def test_the_confirmation_answers_without_a_click(answer):
    # VS Code's terminal on Windows can lose the mouse release, so a press alone must answer; so must the keys.
    config.save(Config(remote="gdrive:Backups"))
    app = ArcaApp(config.load())
    async with app.run_test(size=(120, 44)) as pilot:
        await pilot.pause()
        await _confirm(app, pilot)
        if answer == "mouse_down":
            await pilot.mouse_down("#answers", offset=(4, 0))  # the first line: yes
        elif answer == "arrows":
            await pilot.press("up", "enter")
        else:
            await pilot.press("s")
        await pilot.pause()
    assert app.return_value == "restart:reset"


class FakeSignIn:
    """rclone waiting for the browser: prints the link, then finishes."""

    stopped = False

    def __init__(self, name, kind):
        self.name, self.kind = name, kind

    async def run(self, on_line):
        on_line(rclone.parse("NOTICE: go to the following link: http://127.0.0.1:53682/auth?state=abc"))
        return 0

    def stop(self):
        self.stopped = True


@pytest.fixture
def accounts(monkeypatch):
    """Two accounts in a fake rclone: gdrive (the destination) and work, both signed in."""
    states = {"gdrive:": True, "work:": True}
    calls = []

    async def long():
        return [("gdrive:", "drive"), ("work:", "onedrive")]

    async def account_states():
        return dict(states)

    async def disconnect(name):
        calls.append(("disconnect", name))
        states[name] = False

    def sign_in(name, kind=None):
        calls.append(("sign_in", name, kind))
        states[f"{name}:"] = True
        return FakeSignIn(name, kind)

    monkeypatch.setattr(rclone, "list_remotes_long", long)
    monkeypatch.setattr(rclone, "account_states", account_states)
    monkeypatch.setattr(rclone, "disconnect", disconnect)
    monkeypatch.setattr(rclone, "sign_in", sign_in)
    return calls


async def test_accounts_disconnect_in_place_and_stay_listed(accounts):
    app = ArcaApp(Config(remote="gdrive:Backups"), home=False)
    async with app.run_test(size=(120, 44)) as pilot:
        app.open_page("accounts")
        await pilot.pause()
        page = app.screen
        assert isinstance(page, AccountsScreen)
        listed = page.query_one("#accounts")
        assert [listed.get_option_at_index(i).id for i in range(listed.option_count)] == ["gdrive:", "work:"]
        await pilot.press("down", "delete")  # no dialog, no window: Del just disconnects
        await pilot.pause(0.3)
        assert accounts == [("disconnect", "work:")]
        assert app.screen is page and page.states["work:"] is False
        assert listed.option_count == 2  # still there, now "disconnected"
        await pilot.press("enter")  # Enter on a disconnected account connects it, in the browser
        await pilot.pause(0.3)
        assert accounts[-1] == ("sign_in", "work", None)
        assert page.states["work:"] is True and app.signing is None


async def test_the_icon_at_the_end_of_the_line_disconnects(accounts):
    app = ArcaApp(Config(remote="gdrive:Backups"), home=False)
    async with app.run_test(size=(120, 44)) as pilot:
        app.open_page("accounts")
        await pilot.pause()
        listed = app.screen.query_one("#accounts")
        await pilot.mouse_down(listed, offset=(listed.region.width - 3, 1))  # the ✕ of the second line
        await pilot.pause(0.3)
    assert accounts == [("disconnect", "work:")]


async def test_adding_an_account_signs_in_without_a_console(accounts):
    app = ArcaApp(Config(remote="gdrive:Backups"), home=False)
    async with app.run_test(size=(120, 44)) as pilot:
        app.open_page("accounts")
        await pilot.pause()
        await pilot.press("down", "down", "enter")  # past both accounts: "+ Add an account" opens the services
        await pilot.pause()
        assert app.screen.query_one("#services").display
        await pilot.press("down", "enter")  # OneDrive
        await pilot.pause(0.3)
    assert accounts == [("sign_in", "onedrive", "onedrive")]


def test_a_signed_out_account_says_so():
    line = ('drive: failed when making oauth client: failed to create oauth client: empty token found - please run '
            '"rclone config reconnect probe:"')
    assert diagnose([line]).cause == "err.disconnected" and diagnose([line]).action == "reconnect"


async def test_the_icon_is_reached_with_the_arrows(accounts):
    app = ArcaApp(Config(remote="gdrive:Backups"), home=False)
    async with app.run_test(size=(120, 44)) as pilot:
        app.open_page("accounts")
        await pilot.pause()
        page = app.screen
        await pilot.press("down", "right", "enter")  # onto work's ✕, Enter: disconnected
        await pilot.pause(0.3)
        assert accounts == [("disconnect", "work:")] and page.states["work:"] is False
        await pilot.press("enter")  # the cursor stays on the icon, now ↻: connects again
        await pilot.pause(0.3)
        assert accounts[-1] == ("sign_in", "work", None)
        await pilot.press("left", "up", "enter")  # back on the line: Enter on the destination only says so
        await pilot.pause(0.3)
        assert len(accounts) == 2


async def test_without_a_connected_account_only_accounts_opens(monkeypatch):
    async def signed_out():
        return {"gdrive:": False}

    monkeypatch.setattr(rclone, "account_states", signed_out)
    app = ArcaApp(Config(remote="gdrive:Backups"))
    async with app.run_test(size=(120, 44)) as pilot:
        await pilot.pause(0.3)
        menu = app.screen.query_one("#menu")
        assert app.no_account and menu.get_option("accounts").id == "accounts"
        assert [menu.get_option(item).disabled for item in ("folders", "run_all", "import", "metrics")] == [True] * 4
        assert menu.highlighted == HomeScreen.ITEMS.index("accounts")  # the one thing to do, under the cursor
        status = str(app.screen.query_one(".status").render())
        assert "gdrive" not in status  # no account named when none is connected


async def test_the_destination_line_follows_the_check(accounts, monkeypatch):
    app = ArcaApp(Config(remote="gdrive:Backups"), home=False)
    async with app.run_test(size=(120, 44)) as pilot:
        app.open_page("accounts")
        await pilot.pause(0.3)
        app.action_check()  # as after a sign-in: the page was drawn while the check ran
        await pilot.pause(0.3)
        line = str(app.screen.query_one("#accounts").get_option_at_index(0).prompt.columns[2]._cells[0])
        assert "conectad" in line or "connected" in line  # never left on "checking" once the check is done
