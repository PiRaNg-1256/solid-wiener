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
