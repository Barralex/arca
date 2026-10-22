import json
import re
import shutil

import pytest

from arca.core import config, errors, history, rclone
from arca.core.config import Config, Job
from arca.core.jobs import JobRun, Progress, Result, build_args
from arca.core.rclone import parse
from arca.i18n import STRINGS

# Real lines from rclone 1.75, as written to the log on this kind of failure.
LOGIN_EXPIRED = (
    'Failed to create file system for "gdrive:Backups/Documents": couldn\'t find root directory ID: Get '
    '"https://www.googleapis.com/drive/v3/files/root?alt=json": couldn\'t fetch token: invalid_grant: maybe token '
    'expired? - try refreshing with "rclone config reconnect gdrive:"'
)
NOT_CONFIGURED = 'Failed to create file system for "nope:": didn\'t find section in config file ("nope")'
NO_HOST = 'Get "https://www.googleapis.com/drive/v3/files": dial tcp: lookup www.googleapis.com: no such host'
SOURCE_GONE = "error reading source root directory: directory not found"
STATS = (
    '{"time":"2026-10-21T23:11:51.40-03:00","level":"notice","msg":"\\nTransferred: ...","stats":{"bytes":150,'
    '"totalBytes":300,"transfers":1,"totalTransfers":2,"speed":10.5,"eta":4,"elapsedTime":0.5,"deletes":0},'
    '"source":"accounting/stats.go:549"}'
)
COPIED = (
    '{"time":"2026-10-21T23:11:51.34-03:00","level":"info","msg":"Copied (new)","size":2,"object":"a.txt",'
    '"objectType":"*local.Object","source":"operations/copy.go:380"}'
)


@pytest.mark.parametrize(
    ("line", "cause", "action"),
    [
        (LOGIN_EXPIRED, "err.login", "reconnect"),
        (NOT_CONFIGURED, "err.config", "remote"),
        (NO_HOST, "err.network", "retry"),
        (SOURCE_GONE, "err.source", "edit"),
        ("something rclone never said before", "err.unknown", None),
    ],
)
def test_diagnose_known_failures(line, cause, action):
    diagnosis = errors.diagnose([line])
    assert diagnosis is not None
    assert (diagnosis.cause, diagnosis.action) == (cause, action)


def test_diagnose_nothing_when_no_errors():
    assert errors.diagnose([]) is None


def test_parse_stats_and_file_lines():
    stats = parse(STATS)
    assert stats.stats is not None
    progress = Progress.from_stats(stats.stats)
    assert (progress.bytes, progress.total_bytes, progress.fraction, progress.eta) == (150, 300, 0.5, 4.0)
    copied = parse(COPIED)
    assert copied.is_file and not copied.is_error and copied.size == 2


def test_parse_plain_text_flags_errors():
    assert parse("2026/10/14 04:08:09 CRITICAL: Failed to create file system").is_error
    assert not parse("gdrive:").is_error


def test_config_round_trip_keeps_windows_paths(tmp_path):
    path = tmp_path / "config.toml"
    cfg = Config(remote="gdrive:Backups", language="es",
                 jobs=[Job("Fotos", r"C:\Users\me\Pictures", True), Job('Odd "name"', r"D:\x\y", False)])
    config.save(cfg, path)
    assert config.load(path) == cfg


def test_config_broken_file_is_reported(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text("remote = = broken", encoding="utf-8")
    with pytest.raises(config.ConfigError):
        config.load(path)


@pytest.mark.parametrize(
    ("remote", "local", "joined"),
    [
        ("gdrive:Backups", False, "gdrive:Backups/Docs"),
        ("gdrive:", False, "gdrive:Docs"),
        ("D:\\Backups", True, "D:\\Backups/Docs"),
        ("D:\\", True, "D:/Docs"),
        ("\\\\nas\\share", True, "\\\\nas\\share/Docs"),
    ],
)
def test_remote_helpers(remote, local, joined):
    assert config.is_local(remote) is local
    assert config.join(remote, "Docs") == joined


def test_name_rules():
    assert config.name_problem("Docs", set()) is None
    assert config.name_problem("Docs", {"Docs"}) == "add.err.dup"
    for bad in ("", "  ", "a/b", "a:b", "_versions"):
        assert config.name_problem(bad, set()) == "add.err.name"


def test_sync_always_keeps_the_backup_dir_safety_net(tmp_path):
    args = build_args(Job("Docs", "C:\\Docs"), "gdrive:Backups", tmp_path / "filter.txt", "2026-10-21_2100", False)
    assert args[:3] == ["sync", "C:\\Docs", "gdrive:Backups/Docs"]
    assert args[args.index("--backup-dir") + 1] == "gdrive:Backups/_versions/2026-10-21_2100/Docs"
    assert "--filter-from" in args and "--dry-run" not in args


def test_history_keeps_the_last_result_per_job():
    history.append(Result(job="Docs", started="2026-10-20T21:00:00", status="failed"))
    history.append(Result(job="Docs", started="2026-10-21T21:00:00", status="ok"))
    history.path().open("a", encoding="utf-8").write("not json\n")
    latest = history.last_by_job()
    assert latest["Docs"].status == "ok"
    assert json.loads(history.path().read_text(encoding="utf-8").splitlines()[0])["job"] == "Docs"


def test_every_string_has_both_languages_with_matching_placeholders():
    for key, (en, es) in STRINGS.items():
        assert en and es, key
        assert set(re.findall(r"{(\w+)}", en)) == set(re.findall(r"{(\w+)}", es)), key


async def test_missing_source_is_skipped_with_a_fix(tmp_path):
    job = Job("Gone", str(tmp_path / "does-not-exist"))
    result = await JobRun(job, str(tmp_path / "dest"), tmp_path / "filter.txt").run()
    assert result.status == "skipped"
    assert result.diagnosis == errors.SOURCE_MISSING


@pytest.mark.skipif(shutil.which("rclone") is None, reason="needs rclone on PATH")
async def test_real_sync_moves_deleted_files_to_versions(tmp_path):
    source, dest = tmp_path / "src", tmp_path / "dest"
    (source / "sub").mkdir(parents=True)
    (source / "keep.txt").write_text("keep", encoding="utf-8")
    (source / "gone.txt").write_text("gone", encoding="utf-8")
    (source / "sub" / "node_modules").mkdir()
    (source / "sub" / "node_modules" / "junk.js").write_text("x", encoding="utf-8")
    filter_file = config.filter_path()
    job = Job("Docs", str(source))

    first = await JobRun(job, str(dest), filter_file).run()
    assert first.status == "ok", first.errors
    assert (dest / "Docs" / "gone.txt").exists()
    assert not (dest / "Docs" / "sub" / "node_modules").exists(), "the filter must keep node_modules local"

    (source / "gone.txt").unlink()
    second = await JobRun(job, str(dest), filter_file).run()
    assert second.status == "ok", second.errors
    assert not (dest / "Docs" / "gone.txt").exists()
    archived = list((dest / "_versions").rglob("gone.txt"))
    assert len(archived) == 1, "a file deleted locally must be archived, never lost"


def test_ticks_become_the_shortest_rules():
    from arca.core import selection

    included = {"a.txt", "drafts/", "drafts/d.txt", "node_modules/"}
    changes: dict[str, bool] = {}
    selection.toggle("drafts/", included, changes)  # untick a folder: everything inside follows
    assert not selection.ticked("drafts/d.txt", included, changes)
    selection.toggle("drafts/d.txt", included, changes)  # tick one file back inside it
    selection.toggle("build.log", included, changes)  # tick a file the filter leaves out
    assert selection.merge(changes, ["- /old/**", "+ /drafts/keep.txt"]) == [
        "+ /drafts/d.txt", "- /drafts/**", "+ /build.log", "- /old/**"]  # deepest first; replaced rules dropped
    selection.toggle("build.log", included, changes)  # back to what rclone does anyway: no rule at all
    assert "build.log" not in changes
    sizes = {"a.txt": 10, "drafts/d.txt": 5, "drafts/e.txt": 7, "build.log": 3}
    assert selection.total(sizes, included, changes) == (2, 15)  # a.txt and the file ticked back in drafts/
    assert selection.rule("notes [v2].txt", True) == "+ /notes \\[v2\\].txt"  # glob characters escaped


def test_a_folders_rules_are_saved_and_go_before_the_filter(tmp_path):
    job = Job("Docs", "C:\\Docs", rules=["- /drafts/**", "+ /build.log"])
    config.save(Config(remote="gdrive:Backups", jobs=[job]), tmp_path / "c.toml")
    assert config.load(tmp_path / "c.toml").jobs == [job]
    args = build_args(job, "gdrive:Backups", tmp_path / "filter.txt", "2026-10-22_2100", False)
    first_rule, filter_from = args.index("--filter"), args.index("--filter-from")
    assert first_rule < filter_from  # rclone takes the first match: the folder's choice wins


@pytest.mark.skipif(shutil.which("rclone") is None, reason="needs rclone on PATH")
async def test_rclone_lists_what_a_folder_copies_with_its_rules(tmp_path):
    from arca.core.jobs import filter_args

    src = tmp_path / "src"
    (src / "node_modules" / "x").mkdir(parents=True)
    (src / "docs").mkdir()
    (src / "keep").mkdir()
    for name in ("a.txt", "b.log", "docs/d.txt", "node_modules/x/m.js", "keep/k.txt"):
        (src / name).write_text("x", encoding="utf-8")
    policy = tmp_path / "filter.txt"
    policy.write_text("- node_modules/**\n- *.log\n", encoding="utf-8")
    job = Job("Src", str(src), rules=["+ /b.log", "- /docs/**"])
    # node_modules/ is listed by lsf but holds nothing that is copied: it is not shown as copied
    assert await rclone.included(job.source, filter_args(job, policy)) == {"a.txt", "b.log", "keep/", "keep/k.txt"}


def test_the_task_runs_arca_windowless_for_this_user(monkeypatch):
    from datetime import date

    from arca.core import schedule

    monkeypatch.setenv("USERDOMAIN", "PC")
    monkeypatch.setattr(schedule.getpass, "getuser", lambda: "me")
    daily = schedule.task_xml("daily", today=date(2026, 10, 22))
    assert "<StartBoundary>2026-10-22T21:00:00</StartBoundary>" in daily
    assert "<StartWhenAvailable>true</StartWhenAvailable>" in daily  # a day the PC was off runs when it is on
    assert "<Arguments>-m arca run --headless</Arguments>" in daily
    logon = schedule.task_xml("logon")
    assert "<LogonTrigger><Enabled>true</Enabled><UserId>PC\\me</UserId>" in logon  # this user: no admin rights
