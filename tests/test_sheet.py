"""clips.xlsx: reading rows, the status flow, and editing captions before the video is made."""

from __future__ import annotations

import datetime as dt
import json

import pytest
from openpyxl import load_workbook

from clipper import sheet
from clipper.captions import CaptionLine, lines_from_srt, read_srt, write_srt
from clipper.transcribe import Word
from clipper.util import ClipperError

from .conftest import make_video, needs_ffmpeg


# ---------- reading a row ----------

def test_clip_times_and_hooks(cfg):
    assert sheet.parse_times("1:20-1:45, 5:10 - 5:40;8:00 to 8:30\n1:02:10-1:02:40, 2:05, 90") == [
        (80, 105), (310, 340), (480, 510), (3730, 3760), (125, None), (90, None)]
    with pytest.raises(ClipperError, match="1:2x"):
        sheet.parse_times("1:2x-1:40")
    job = sheet.parse_row(2, {"link": "https://youtu.be/abcdefghijk", "times": "1:20-1:45, 5:10-5:40",
                              "hooks": "COPS GOT ME; -", "count": 4}, cfg)
    assert job.slices == [(80, 105), (310, 340)] and job.hooks == ["COPS GOT ME", ""]
    assert "ignored" in job.notes[0]
    opts = job.options()
    assert opts.slices == job.slices and opts.prompt is False and opts.review is True and opts.force is False


def test_empty_cells_mean_defaults_and_values_are_forgiving(cfg):
    job = sheet.parse_row(2, {"link": "x"}, cfg)
    assert (job.slices, job.count, job.hooks, job.lang, job.layout, job.music, job.credit) == ([], 1, [], None, None, None, None)
    assert job.check_first is cfg.sheet.check_captions_first
    job = sheet.parse_row(3, {"link": "x", "status": "Redo", "check": "no", "lang": "Hindi", "translate": "Yes",
                              "layout": "Crop", "zoom": "1,5", "music": "None", "credit": "N", "gemini": "no",
                              "game": "GTA6", "count": 3.0}, cfg)
    assert (job.check_first, job.lang, job.translate, job.layout, job.zoom, job.music, job.credit, job.gemini,
            job.game, job.count) == (False, "hi", True, "crop", 1.5, "none", False, False, "gta6", 3)
    assert job.options().force is True
    for bad, message in [({"check": "maybe"}, "Yes or No"), ({"layout": "wide"}, "Blur or Crop"),
                         ({"zoom": "3"}, "between 1 and 2"), ({"game": "fortnite"}, "Game"),
                         ({"count": "20"}, "between 1 and 10"), ({"music": "nope.mp3"}, "not found")]:
        with pytest.raises(ClipperError, match=message):
            sheet.parse_row(4, {"link": "x", **bad}, cfg)


def test_excel_time_of_day_is_read_as_minutes_and_seconds(cfg):
    job = sheet.parse_row(2, {"link": "x", "times": dt.time(1, 20)}, cfg)
    assert job.slices == [(80, None)] and "time of day" in job.notes[0]


# ---------- editable captions ----------

def test_captions_file_round_trip_keeps_word_timing(tmp_path):
    lines = [CaptionLine(1.0, 2.0, [Word(1.0, 1.3, "bhai"), Word(1.4, 1.9, "dekh")]),
             CaptionLine(2.5, 3.5, [Word(2.5, 3.0, "polis"), Word(3.0, 3.4, "aa"), Word(3.4, 3.5, "gayi")])]
    srt = tmp_path / "captions.srt"
    write_srt(lines, srt)
    assert "00:00:01,000 --> 00:00:02,000\nbhai dekh" in srt.read_text(encoding="utf-8")
    # the user fixes a word, moves the second line later and adds a caption, in Notepad (BOM + Windows newlines)
    edited = ("﻿1\r\n00:00:01,000 --> 00:00:02,000\r\nbhai dekh\r\n\r\n2\r\n00:00:03,000 --> 00:00:04,000\r\n"
              "police aa gayi\r\n\r\n00:00:05,000 --> 00:00:06,500\r\nkya scene hai bro\r\n")
    srt.write_bytes(edited.encode("utf-8"))
    entries = read_srt(srt)
    out = lines_from_srt(entries, lines)
    assert [ln.text for ln in out] == ["bhai dekh", "police aa gayi", "kya scene hai bro"]
    assert (out[0].words[1].start, out[0].words[1].end) == pytest.approx((1.4, 1.9))  # untouched timing kept
    assert out[1].words[0].text == "police" and out[1].words[0].start == pytest.approx(3.0)
    assert out[1].words[1].start == pytest.approx(3.5)  # same word layout, shifted with the line
    assert out[2].words[0].start == pytest.approx(5.0) and out[2].words[-1].end == pytest.approx(6.5)
    srt.write_text("1\n00:00:02,000 --> 00:00:01,000\nbackwards\n", encoding="utf-8")
    with pytest.raises(ClipperError, match="end time must be after"):
        read_srt(srt)


# ---------- the whole flow ----------

def cell(ws, row, header):
    columns = {str(ws.cell(row=1, column=c).value): c for c in range(1, ws.max_column + 1)}
    return ws.cell(row=row, column=columns[header])


@needs_ffmpeg
def test_sheet_flow_new_rows_then_edit_captions_then_ready(cfg, fake_transcribe, tmp_path):
    cfg.render.keep_work_files = True
    path = tmp_path / "clips.xlsx"
    summary = sheet.run(cfg, path=path)  # first run creates the sheet
    assert path.exists() and summary.prepared == 0
    first = make_video(tmp_path / "first.mp4", duration=80, burst_at=30.0)
    second = make_video(tmp_path / "second.mp4", duration=120, burst_at=70.0)

    wb = load_workbook(path)
    ws = wb["Videos"]
    for header, value in {"Video link": str(first), "Clip times": "0:10-0:32, 0:40-1:02", "Hook text": "FIRST HOOK; -",
                          "Music": "none"}.items():
        cell(ws, 4, header).value = value
    for header, value in {"Video link": str(second), "How many clips": 1, "Check captions first": "No",
                          "Music": "none"}.items():
        cell(ws, 5, header).value = value
    cell(ws, 6, "Video link").value = str(tmp_path / "missing.mp4")
    wb.save(path)

    summary = sheet.run(cfg, path=path)
    wb = load_workbook(path)
    ws = wb["Videos"]
    assert cell(ws, 2, "Status").value == "Example"  # example rows untouched
    assert cell(ws, 4, "Status").value == "Edit captions"
    assert cell(ws, 5, "Status").value == "Done"
    assert cell(ws, 6, "Status").value.startswith("Failed: That's not a link")
    assert (summary.prepared, summary.rendered, summary.failed, summary.waiting) == (3, 1, 1, 1)
    folders = [p for p in cell(ws, 4, "Clip folders").value.splitlines()]
    assert len(folders) == 2 and all("first" in f and "clip 0.1" in f or "clip 0.4" in f for f in folders)
    assert cell(ws, 4, "Hooks used").value == "1) FIRST HOOK\n2) (none)"
    assert cell(ws, 5, "Videos").value.endswith("clip.mp4")
    assert "your clip times" in cell(ws, 4, "Notes").value and "loudest" in cell(ws, 5, "Notes").value

    # edit the captions of the first clip, then mark the row Ready
    from pathlib import Path

    clip = Path(folders[0])
    assert not (clip / "clip.mp4").exists() and (clip / "captions.srt").exists()
    srt = clip / "captions.srt"
    srt.write_text(srt.read_text(encoding="utf-8").replace("cops", "kops", 1), encoding="utf-8")
    (clip / "hook.txt").write_text("EDITED HOOK\nSECOND LINE\n", encoding="utf-8")
    cell(ws, 4, "Status").value = "Ready"
    wb.save(path)

    sheet.run(cfg, path=path)
    wb = load_workbook(path)
    ws = wb["Videos"]
    assert cell(ws, 4, "Status").value == "Done"
    videos = cell(ws, 4, "Videos").value.splitlines()
    assert len(videos) == 2 and all(Path(v).exists() for v in videos)
    job = json.loads((clip / "job.json").read_text(encoding="utf-8"))
    assert job["state"] == "rendered" and job["hook"] == "EDITED HOOK | SECOND LINE"
    work_ass = [p for p in (cfg.root / "cache" / "work").rglob("clip.ass")]
    assert any("KOPS" in p.read_text(encoding="utf-8") and r"EDITED HOOK\NSECOND LINE" in p.read_text(encoding="utf-8")
               for p in work_ass)

    # finished rows are left alone; Redo makes them again
    before = summary.prepared
    summary = sheet.run(cfg, path=path)
    assert summary.prepared == 0 and summary.rendered == 0


def test_sheet_results_go_to_a_copy_when_excel_holds_the_file(cfg, tmp_path, monkeypatch):
    path = tmp_path / "clips.xlsx"
    sheet.create_sheet(path, cfg)
    wb = load_workbook(path)
    calls = []

    def locked(target):
        calls.append(target)
        if target == path:
            raise PermissionError("locked")
        return original(target)

    original = wb.save
    monkeypatch.setattr(sheet.time, "sleep", lambda s: None)
    wb.save = locked
    saver = sheet._Saver(wb, path, sheet.Reporter())
    saver.save()
    assert saver.fallback is not None and saver.fallback.exists() and "results" in saver.fallback.name


def test_older_sheets_get_new_columns(cfg, tmp_path):
    from openpyxl import Workbook

    path = tmp_path / "old.xlsx"
    wb = Workbook()
    wb.active.title = "Videos"
    wb.active.append(["Video link", "Status"])
    wb.active.append(["https://youtu.be/abcdefghijk", ""])
    wb.save(path)
    _, ws, columns = sheet.open_sheet(path, cfg)
    assert set(columns) == {c.key for c in sheet.COLUMNS}
    assert ws.cell(row=2, column=columns["link"]).value == "https://youtu.be/abcdefghijk"
