"""The commands themselves start and finish without crashing (catches mistakes the other tests can't see)."""

from __future__ import annotations

from typer.testing import CliRunner

from clipper import cli


def test_every_command_has_working_help():
    runner = CliRunner()
    for args in ([], ["make", "--help"], ["run", "--help"], ["render", "--help"], ["peaks", "--help"],
                 ["doctor", "--help"], ["gemini-test", "--help"], ["update", "--help"]):
        result = runner.invoke(cli.app, args, terminal_width=120)
        assert result.exception is None or isinstance(result.exception, SystemExit), (args, result.output)
        assert "Usage" in result.output


def test_doctor_runs_to_the_end(tmp_path):
    config = tmp_path / "config.yaml"
    config.write_text("output_dir: out\ncache_dir: cache\n", encoding="utf-8")
    result = CliRunner().invoke(cli.app, ["doctor", "--config", str(config)], terminal_width=160)
    assert not isinstance(result.exception, Exception) or isinstance(result.exception, SystemExit), result.output
    for row in ("FFmpeg", "Speech model", "Video encoding", "Facecam detector", "Excel sheet", "Gemini"):
        assert row in result.output, (row, result.output)


def test_menu_quits_and_opens_the_output_folder(tmp_path, monkeypatch):
    config = tmp_path / "config.yaml"
    config.write_text("output_dir: out\ncache_dir: cache\n", encoding="utf-8")
    opened = []
    monkeypatch.setattr(cli, "_open", lambda path: opened.append(path))
    result = CliRunner().invoke(cli.app, ["menu", "--config", str(config)], input="4\n0\n", terminal_width=120)
    assert result.exit_code == 0, result.output
    assert opened and opened[0].name == "out" and opened[0].is_dir()
    assert "Make clips from one YouTube link now" in result.output


def test_update_pulls_new_versions_and_installs_what_they_need(tmp_path, monkeypatch):
    import subprocess

    def git(*args, cwd):
        subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)

    remote, local = tmp_path / "remote", tmp_path / "local"
    remote.mkdir()
    git("init", "-q", "-b", "main", cwd=remote)
    git("config", "user.email", "t@t", cwd=remote)
    git("config", "user.name", "t", cwd=remote)
    (remote / "a.txt").write_text("1")
    git("add", ".", cwd=remote)
    git("commit", "-qm", "first", cwd=remote)
    git("clone", "-q", str(remote), str(local), cwd=tmp_path)
    monkeypatch.setattr(cli, "PROJECT_DIR", local)
    calls = []
    monkeypatch.setattr(cli.subprocess, "call", lambda cmd, **kw: calls.append(cmd) or 0)

    assert cli.update_tool() and calls == []  # nothing new: nothing installed
    (remote / "a.txt").write_text("2")
    git("commit", "-qam", "Add a better feature", cwd=remote)
    assert cli.update_tool()
    assert (local / "a.txt").read_text() == "2"
    assert len(calls) == 1 and calls[0][-2] == "-e" and calls[0][-1] in (str(local), f"{local}[gpu]")

    monkeypatch.setattr(cli, "PROJECT_DIR", tmp_path / "zip-copy")  # not a git folder: explains instead
    (tmp_path / "zip-copy").mkdir()
    assert cli.update_tool()
