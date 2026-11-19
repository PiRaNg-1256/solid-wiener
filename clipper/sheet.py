"""clips.xlsx: one row per video. `clipper run` (or run.cmd) works through the rows that need work."""

from __future__ import annotations

import datetime as dt
import re
import time
import traceback
from dataclasses import dataclass, field
from pathlib import Path

from openpyxl import Workbook, load_workbook
from openpyxl.comments import Comment
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.worksheet.datavalidation import DataValidation

from .config import Config
from .facecam import parse_hint
from .pipeline import JOB_FILE, MakeOptions, Reporter, prepare, render_clip
from .util import ClipperError, parse_time

SHEET = "Videos"
GREY = PatternFill("solid", fgColor="EDEDED")
HEADER_IN = PatternFill("solid", fgColor="1F4E78")
HEADER_OUT = PatternFill("solid", fgColor="7F7F7F")
STATUS_FILLS = {
    "edit captions": PatternFill("solid", fgColor="FFF2CC"),
    "ready": PatternFill("solid", fgColor="DDEBF7"),
    "done": PatternFill("solid", fgColor="E2EFDA"),
    "failed": PatternFill("solid", fgColor="F8CBAD"),
}


@dataclass
class Column:
    key: str
    header: str
    width: int
    help: str
    output: bool = False
    choices: str = ""  # dropdown list
    text: bool = False  # keep what's typed as text (stops Excel turning 1:20 into a time of day)


COLUMNS = [
    Column("status", "Status", 16, "Leave EMPTY for new videos. The tool writes 'Edit captions' when the captions are "
           "ready to check, then you set 'Ready' to make the video. 'Done' when finished. Set 'Redo' to make it "
           "again, 'Skip' to leave the row alone.", choices="Ready,Redo,Skip"),
    Column("link", "Video link", 45, "YouTube link (or the full path of a video file on your PC).", text=True),
    Column("times", "Clip times", 34, "Optional. Your own clip times, start-end, several separated by commas: "
           "1:20-1:45, 5:10-5:40. Only a start (2:05) = 30 seconds from there. Empty = the tool finds the best "
           "moments.", text=True),
    Column("count", "How many clips", 10, "Optional, used only when Clip times is empty: how many of the best "
           "moments to clip. Empty = 1."),
    Column("hooks", "Hook text", 34, "Optional. The text at the top of the Short. For several clips, separate the "
           "hooks with ; in the same order as the clip times. Missing hooks are written by Gemini. Type - for no "
           "hook. Use | for a line break.", text=True),
    Column("check", "Check captions first", 12, "Yes = stop after making the captions so you can fix them, then set "
           "Status to Ready. No = make the video straight away. Empty = the setting in config.yaml.", choices="Yes,No"),
    Column("lang", "Caption language", 12, "Auto, English or Hindi. Hindi is written in English letters. Empty = Auto.",
           choices="Auto,English,Hindi"),
    Column("translate", "Translate Hindi to English", 12, "Yes = Hindi speech gets English captions. Empty = No.",
           choices="Yes,No"),
    Column("layout", "Layout", 9, "Blur = whole gameplay on a blurred background, Crop = fill the screen. "
           "Empty = Blur.", choices="Blur,Crop"),
    Column("zoom", "Zoom", 7, "Blur layout only: 1 = whole game frame, up to 2 = bigger gameplay. Empty = 1.25."),
    Column("facecam", "Facecam", 14, "For streams with a webcam: the webcam goes big on the top half, the gameplay "
           "on the bottom half (the webcam's corner is left out of the gameplay). Auto = find the webcam by its face, "
           "or say where it is: Bottom right, Bottom left, Top right, Top left. Exact box: x,y,w,h in percent of "
           "the frame (75,70,25,30). Off = normal layout. Empty = normal layout.",
           choices="Auto,Bottom right,Bottom left,Top right,Top left,Off"),
    Column("music", "Music", 16, "Random = a random track from assets\\music, None = no music, or the name of a file "
           "in assets\\music. Empty = Random.", choices="Random,None"),
    Column("credit", "Show credit", 9, "Show 'footage: @creator' on the video. Empty = Yes.", choices="Yes,No"),
    Column("gemini", "Use Gemini", 9, "Yes = Gemini checks the caption words and writes the hook and titles "
           "(needs the key in config.yaml). Empty = Yes when a key is set.", choices="Yes,No"),
    Column("game", "Game", 9, "For hashtags: gta6, gta5, rdr2 (or your own from config.yaml). "
           "Empty = detected from the video title."),
    Column("title", "Video title", 30, "Filled in by the tool.", output=True),
    Column("folders", "Clip folders", 45, "Filled in by the tool: one folder per clip. Edit captions.srt and "
           "hook.txt in these folders before setting Status to Ready.", output=True),
    Column("videos", "Videos", 45, "Filled in by the tool: the finished videos.", output=True),
    Column("hooks_used", "Hooks used", 30, "Filled in by the tool.", output=True),
    Column("titles", "Titles", 40, "Filled in by the tool: the first title idea for each clip (more in post.txt).",
           output=True),
    Column("notes", "Notes", 45, "Filled in by the tool: where the moments came from, problems.", output=True),
    Column("last_run", "Last run", 17, "Filled in by the tool.", output=True),
]
BY_KEY = {c.key: c for c in COLUMNS}
EXAMPLES = [
    {"status": "Example", "link": "https://www.youtube.com/watch?v=VIDEO_ID", "count": 3,
     "notes": "Example row (skipped). 3 clips of the best moments."},
    {"status": "Example", "link": "https://www.youtube.com/watch?v=VIDEO_ID",
     "times": "1:20-1:45, 5:10-5:40, 8:00-8:30", "hooks": "COPS GOT ME; THIS JUMP; NO WAY",
     "check": "Yes", "notes": "Example row (skipped). 3 clips at these times, with your own hooks."},
]

HELP_ROWS = [
    ("HOW TO USE clips.xlsx", ""),
    ("", ""),
    ("1", "Put one video link per row in the 'Video link' column (sheet 'Videos'). Leave Status empty."),
    ("2", "Fill in only what you want to change. Every empty cell uses the default."),
    ("3", "Save and CLOSE this file (the tool can't write to it while Excel has it open)."),
    ("4", "Double-click run.cmd (or type: .\\clipper run)."),
    ("5", "Rows show 'Edit captions'. Open the folder listed in 'Clip folders'. In each clip folder, fix the words "
          "in captions.srt (Notepad is fine; keep the time lines as they are, or adjust them) and the hook in hook.txt."),
    ("6", "Set those rows' Status to 'Ready', save, close, and run again. The videos are made: Status becomes "
          "'Done' and 'Videos' shows where they are. Titles and description to paste: post.txt in each clip folder."),
    ("", ""),
    ("EXAMPLES", ""),
    ("What you want", "What to type"),
    ("1 clip of the most-replayed moment", "Only the link."),
    ("The best 3 moments", "How many clips: 3"),
    ("5 clips at your own times", "Clip times: 1:20-1:45, 5:10-5:40, 8:00-8:30, 12:00-12:25, 15:30-16:00"),
    ("Your own hook for each clip", "Clip times: 1:20-1:45, 5:10-5:40   Hook text: COPS GOT ME; THIS JUMP THO"),
    ("Hook on two lines", "Hook text: GTA 6 COPS | DON'T PLAY"),
    ("No hook at all", "Hook text: -"),
    ("A clip from 2:05 (30 seconds)", "Clip times: 2:05"),
    ("Times after the first hour", "Clip times: 1:02:10-1:02:40"),
    ("Webcam big on top, gameplay below", "Facecam: Auto (or the corner the webcam is in, like Bottom right). "
                                            "Check preview.jpg in the clip folder before setting Ready."),
    ("Different settings for clips of one video", "Use one row per clip, with the same link."),
    ("Make a finished row again", "Set Status to Redo."),
    ("", ""),
    ("STATUS", ""),
    ("(empty)", "New: the tool works on it at the next run."),
    ("Edit captions", "Captions made; waiting for you to check them. Set Ready when done."),
    ("Ready", "You checked the captions: the next run makes the videos."),
    ("Done", "Finished. The tool leaves it alone."),
    ("Failed: ...", "Something went wrong (the reason is shown). Fix it, clear the Status (or set Redo), run again."),
    ("Redo / Skip", "Make it again from scratch / leave the row alone."),
    ("", ""),
    ("TIMES", "Write minutes:seconds (1:20) or hours:minutes:seconds (1:02:10). Clips longer than 59 seconds get "
              "blocked as Shorts if anyone claims copyright."),
]


# ---------- creating / opening the workbook ----------

def create_sheet(path: Path, cfg: Config) -> None:
    wb = Workbook()
    ws = wb.active
    ws.title = SHEET
    _style_columns(ws, cfg, start=1)
    for r, example in enumerate(EXAMPLES, 2):
        for c, column in enumerate(COLUMNS, 1):
            if column.key in example:
                cell = ws.cell(row=r, column=c, value=example[column.key])
                cell.font = Font(italic=True, color="808080")
    help_ws = wb.create_sheet("How to use")
    help_ws.column_dimensions["A"].width = 42
    help_ws.column_dimensions["B"].width = 110
    for r, (a, b) in enumerate(HELP_ROWS, 1):
        help_ws.cell(row=r, column=1, value=a)
        help_ws.cell(row=r, column=2, value=b).alignment = Alignment(wrap_text=True, vertical="top")
        if a.isupper() and a:
            help_ws.cell(row=r, column=1).font = Font(bold=True, size=12)
        if a == "What you want":
            help_ws.cell(row=r, column=1).font = help_ws.cell(row=r, column=2).font = Font(bold=True)
    path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(path)


def _style_columns(ws, cfg: Config, start: int) -> None:
    """Headers, widths, help notes and dropdowns for COLUMNS[start-1:]."""
    for index in range(start, len(COLUMNS) + 1):
        _style_column(ws, index, COLUMNS[index - 1], cfg)
    ws.row_dimensions[1].height = 32
    ws.freeze_panes = "C2"


def _style_column(ws, index: int, column: Column, cfg: Config) -> None:
    """Header, width, help note, dropdown and cell format for one column."""
    cell = ws.cell(row=1, column=index, value=column.header)
    cell.font = Font(bold=True, color="FFFFFF")
    cell.fill = HEADER_OUT if column.output else HEADER_IN
    cell.alignment = Alignment(wrap_text=True, vertical="center")
    cell.comment = Comment(column.help, "clipper", width=320, height=140)
    letter = cell.column_letter
    ws.column_dimensions[letter].width = column.width
    choices = column.choices or (",".join(list(cfg.games)[:20]) if column.key == "game" else "")
    if choices:
        dv = DataValidation(type="list", formula1=f'"{choices}"', allow_blank=True, showErrorMessage=False)
        ws.add_data_validation(dv)
        dv.add(f"{letter}2:{letter}1000")
    for row in range(2, 1001):
        target = ws.cell(row=row, column=index)
        if column.text:
            target.number_format = "@"
        if column.output:
            target.fill = GREY
            target.alignment = Alignment(wrap_text=True, vertical="top")


def open_sheet(path: Path, cfg: Config):
    """Load the workbook; add any columns a newer version of the tool needs. Returns (workbook, sheet, column map)."""
    try:
        wb = load_workbook(path)
    except PermissionError:
        raise ClipperError(f"{path.name} is open in Excel. Close it, then run again.") from None
    except Exception as exc:
        raise ClipperError(f"Can't open {path.name}: {exc}") from None
    ws = wb[SHEET] if SHEET in wb.sheetnames else wb.worksheets[0]
    headers = {str(ws.cell(row=1, column=c).value or "").strip().lower(): c for c in range(1, ws.max_column + 1)}
    columns = {}
    for column in COLUMNS:
        if column.header.lower() in headers:
            columns[column.key] = headers[column.header.lower()]
    missing = [c for c in COLUMNS if c.key not in columns]
    if "link" not in columns and not missing:
        raise ClipperError(f"{path.name} has no 'Video link' column.")
    next_col = ws.max_column + 1
    for column in missing:
        _style_column(ws, next_col, column, cfg)
        columns[column.key] = next_col
        next_col += 1
    return wb, ws, columns


def check_not_open(path: Path) -> None:
    """On Windows, Excel locks the file while it's open; find out before doing any work."""
    try:
        with path.open("r+b"):
            pass
    except PermissionError:
        raise ClipperError(f"{path.name} is open in Excel. Close it, then run again.") from None


# ---------- reading a row ----------

@dataclass
class RowJob:
    row: int
    status: str
    link: str
    slices: list[tuple[float, float | None]] = field(default_factory=list)
    count: int = 1
    hooks: list[str | None] = field(default_factory=list)
    check_first: bool = True
    lang: str | None = None
    translate: bool | None = None
    layout: str | None = None
    zoom: float | None = None
    music: str | None = None
    facecam: str | None = None
    credit: bool | None = None
    gemini: bool | None = None
    game: str | None = None
    notes: list[str] = field(default_factory=list)

    def options(self) -> MakeOptions:
        return MakeOptions(
            hooks=self.hooks or None, slices=self.slices or None, count=self.count, lang=self.lang,
            translate=self.translate, layout=self.layout, zoom=self.zoom, music=self.music, credit=self.credit,
            facecam=self.facecam,
            gemini=self.gemini, game=self.game, prompt=False, force=self.status == "redo",
            review=self.check_first,
        )

    def render_overrides(self) -> dict:
        return {"layout": self.layout, "zoom": self.zoom, "credit": self.credit, "music": self.music,
                "facecam": self.facecam}


_YES = {"yes", "y", "true", "1", "on", "✓", "x"}
_NO = {"no", "n", "false", "0", "off"}


def _text(value) -> str:
    if value is None:
        return ""
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    return str(value).strip()


def _yes_no(value, name: str) -> bool | None:
    text = _text(value).lower()
    if not text:
        return None
    if text in _YES:
        return True
    if text in _NO:
        return False
    raise ClipperError(f"'{name}' must be Yes or No (it says '{_text(value)}')")


def _time_value(value, notes: list[str]) -> str:
    """Excel may store '1:20' as a time of day (1 h 20 min). Gaming clip times are minutes:seconds, so read it so."""
    if isinstance(value, dt.time):
        if value.second == 0:
            notes.append(f"Excel changed '{value.hour}:{value.minute:02d}' into a time of day; read it as minutes:seconds")
            return f"{value.hour}:{value.minute:02d}"
        return f"{value.hour}:{value.minute:02d}:{value.second:02d}"
    if isinstance(value, dt.datetime):
        return _time_value(value.time(), notes)
    if isinstance(value, dt.timedelta):
        return str(int(value.total_seconds()))
    return _text(value)


def parse_times(text: str) -> list[tuple[float, float | None]]:
    slices = []
    for part in re.split(r"[,;\n]+", text):
        part = part.strip()
        if not part:
            continue
        m = re.fullmatch(r"(.+?)\s*(?:-|–|—|\bto\b)\s*(.+)", part, re.I)
        try:
            slices.append((parse_time(m.group(1)), parse_time(m.group(2))) if m else (parse_time(part), None))
        except ClipperError:
            raise ClipperError(f"Can't read the clip time '{part}'. Write it like 1:20-1:45 (several separated by commas).") from None
    return slices


def _music(value, cfg: Config) -> str | None:
    text = _text(value)
    if not text or text.lower() in ("random", "auto"):
        return None
    if text.lower() in ("none", "no", "off"):
        return "none"
    path = Path(text).expanduser()
    if path.is_file():
        return str(path)
    folder = cfg.path(cfg.music.folder)
    for candidate in folder.rglob("*") if folder.is_dir() else []:
        if candidate.is_file() and text.lower() in (candidate.name.lower(), candidate.stem.lower()):
            return str(candidate)
    raise ClipperError(f"Music file '{text}' not found in {cfg.music.folder}")


def parse_row(row: int, cells: dict, cfg: Config) -> RowJob:
    job = RowJob(row=row, status=_text(cells.get("status")).lower(), link=_text(cells.get("link")),
                 check_first=cfg.sheet.check_captions_first)
    times = _time_value(cells.get("times"), job.notes)
    if times:
        job.slices = parse_times(times)
    count = _text(cells.get("count"))
    if count:
        try:
            job.count = int(float(count))
        except ValueError:
            raise ClipperError(f"'How many clips' must be a number (it says '{count}')") from None
        if not 1 <= job.count <= 10:
            raise ClipperError("'How many clips' must be between 1 and 10")
        if job.slices:
            job.notes.append("'How many clips' ignored because clip times are given")
    hooks = _text(cells.get("hooks"))
    if hooks:
        job.hooks = [None if not h.strip() else ("" if h.strip() == "-" else h.strip()) for h in hooks.split(";")]
    check = _yes_no(cells.get("check"), "Check captions first")
    if check is not None:
        job.check_first = check
    lang = _text(cells.get("lang")).lower()
    if lang:
        mapping = {"auto": None, "english": "en", "en": "en", "hindi": "hi", "hi": "hi", "hinglish": "hi"}
        if lang not in mapping:
            raise ClipperError("'Caption language' must be Auto, English or Hindi")
        job.lang = mapping[lang]
    job.translate = _yes_no(cells.get("translate"), "Translate Hindi to English")
    layout = _text(cells.get("layout")).lower()
    if layout:
        if layout not in ("blur", "crop"):
            raise ClipperError("'Layout' must be Blur or Crop")
        job.layout = layout
    zoom = _text(cells.get("zoom"))
    if zoom:
        try:
            job.zoom = float(zoom.replace(",", "."))
        except ValueError:
            raise ClipperError(f"'Zoom' must be a number like 1.25 (it says '{zoom}')") from None
        if not 1.0 <= job.zoom <= 2.0:
            raise ClipperError("'Zoom' must be between 1 and 2")
    job.music = _music(cells.get("music"), cfg)
    facecam = _text(cells.get("facecam"))
    if facecam:
        hint = parse_hint(facecam)  # raises a readable error for anything it doesn't understand
        job.facecam = "off" if hint is None else (",".join(f"{v:g}" for v in hint) if isinstance(hint, tuple) else hint)
    job.credit = _yes_no(cells.get("credit"), "Show credit")
    job.gemini = _yes_no(cells.get("gemini"), "Use Gemini")
    game = _text(cells.get("game")).lower()
    if game:
        if game not in cfg.games:
            raise ClipperError(f"'Game' must be one of: {', '.join(cfg.games)} (or empty)")
        job.game = game
    return job


# ---------- running ----------

@dataclass
class Summary:
    prepared: int = 0
    rendered: int = 0
    failed: int = 0
    waiting: int = 0
    rows: list[str] = field(default_factory=list)


class _Saver:
    """Saves after every row. If Excel grabbed the file meanwhile, saves a copy instead of losing the results."""

    def __init__(self, wb, path: Path, rep: Reporter):
        self.wb, self.path, self.rep, self.fallback = wb, path, rep, None

    def save(self) -> None:
        target = self.fallback or self.path
        for attempt in range(3):
            try:
                self.wb.save(target)
                return
            except PermissionError:
                time.sleep(2)
        self.fallback = self.path.with_name(f"{self.path.stem} (results {dt.datetime.now():%Y-%m-%d %H-%M}).xlsx")
        self.rep.warn(f"{self.path.name} is open in Excel, so the results are saved in '{self.fallback.name}' instead.")
        self.wb.save(self.fallback)


def _put(ws, row: int, columns: dict, key: str, value, fill: PatternFill | None = None) -> None:
    cell = ws.cell(row=row, column=columns[key], value=value)
    if BY_KEY[key].output:
        cell.alignment = Alignment(wrap_text=True, vertical="top")
    if fill is not None:
        cell.fill = fill


def _set_status(ws, row: int, columns: dict, status: str) -> None:
    fill = next((f for k, f in STATUS_FILLS.items() if status.lower().startswith(k)), PatternFill(fill_type=None))
    _put(ws, row, columns, "status", status, fill)
    _put(ws, row, columns, "last_run", dt.datetime.now().strftime("%Y-%m-%d %H:%M"))


def _job(folder: Path) -> dict:
    import json

    try:
        return json.loads((folder / JOB_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _link(cell, path: Path) -> None:
    cell.hyperlink = str(path)
    cell.font = Font(color="0563C1", underline="single")


def run(cfg: Config, rep: Reporter | None = None, path: Path | None = None) -> Summary:
    rep = rep or Reporter()
    path = path or cfg.path(cfg.sheet.file)
    summary = Summary()
    if not path.exists():
        create_sheet(path, cfg)
        rep.info(f"Created {path}. Put your video links in it (one per row), save and close it, then run again.")
        return summary
    check_not_open(path)
    wb, ws, columns = open_sheet(path, cfg)
    saver = _Saver(wb, path, rep)
    saver.save()  # writes any columns that were added

    for row in range(2, ws.max_row + 1):
        cells = {key: ws.cell(row=row, column=col).value for key, col in columns.items()}
        link, status = _text(cells.get("link")), _text(cells.get("status")).lower()
        if not link or status in ("done", "skip", "example", "edit captions") or status.startswith("failed"):
            if status == "edit captions":
                summary.waiting += 1
            continue
        rep.info(f"--- Row {row}: {link}")
        try:
            job = parse_row(row, cells, cfg)
            if status == "ready":
                _render_row(ws, row, columns, cells, job, cfg, rep, summary)
            elif status in ("", "redo", "working..."):  # 'Working...' = an earlier run was interrupted
                _prepare_row(ws, row, columns, job, cfg, rep, summary)
            else:
                job.notes.append(f"Status '{cells.get('status')}' isn't one the tool knows, so the row was skipped")
                _put(ws, row, columns, "notes", "; ".join(job.notes))
        except KeyboardInterrupt:
            _set_status(ws, row, columns, "")
            saver.save()
            raise
        except Exception as exc:
            message = str(exc).strip().splitlines()[0] if str(exc).strip() else type(exc).__name__
            if not isinstance(exc, ClipperError):
                _log_crash(cfg, row, link)
                message = f"{type(exc).__name__}: {message}"
            _set_status(ws, row, columns, f"Failed: {message[:200]}")
            summary.failed += 1
            rep.warn(f"Row {row} failed: {message}")
        saver.save()
    return summary


def _prepare_row(ws, row: int, columns: dict, job: RowJob, cfg: Config, rep: Reporter, summary: Summary) -> None:
    _set_status(ws, row, columns, "Working...")
    prepared = prepare(job.link, job.options(), cfg, rep)
    folders = prepared.folders
    _put(ws, row, columns, "title", prepared.video.title)
    notes = list(job.notes) + prepared.skipped
    if not folders:
        _put(ws, row, columns, "notes", "; ".join(notes + ["no new clips (all of them were made before; set Redo to make them again)"]))
        _set_status(ws, row, columns, "Done")
        return
    jobs = [_job(f) for f in folders]
    sources = sorted({j.get("moment", {}).get("source", "") for j in jobs} - {""})
    source_names = {"heatmap": "Most replayed graph", "chat": "live-chat bursts", "comments": "viewers' timestamp "
                    "comments", "loudness": "loudest moments (weak signal)", "manual": "your clip times"}
    notes.insert(0, "moments from: " + ", ".join(source_names.get(s, s) for s in sources))
    for j in jobs:
        gemini_note = j.get("captions", {}).get("gemini_note", "")
        if gemini_note.startswith("Gemini not used") and gemini_note not in notes:
            notes.append(gemini_note)
    _put(ws, row, columns, "folders", "\n".join(str(f) for f in folders))
    _link(ws.cell(row=row, column=columns["folders"]), folders[0].parent)
    _put(ws, row, columns, "hooks_used", "\n".join(f"{i}) {j.get('hook') or '(none)'}" for i, j in enumerate(jobs, 1)))
    _put(ws, row, columns, "titles", "\n".join(f"{i}) {(j.get('titles') or [''])[0]}" for i, j in enumerate(jobs, 1)))
    _put(ws, row, columns, "notes", "; ".join(notes))
    _put(ws, row, columns, "videos", None)
    summary.prepared += len(folders)
    if job.check_first:
        _set_status(ws, row, columns, "Edit captions")
        summary.waiting += 1
        summary.rows.append(f"Row {row}: check captions.srt and hook.txt in {folders[0].parent}")
        return
    _render_folders(ws, row, columns, folders, job, cfg, rep, summary, overrides=None)


def _render_row(ws, row: int, columns: dict, cells: dict, job: RowJob, cfg: Config, rep: Reporter, summary: Summary) -> None:
    folders = [Path(p.strip()) for p in _text(cells.get("folders")).splitlines() if p.strip()]
    if not folders:
        raise ClipperError("Status is Ready but 'Clip folders' is empty. Clear the Status to start this row again.")
    _render_folders(ws, row, columns, folders, job, cfg, rep, summary, overrides=job.render_overrides())


def _render_folders(ws, row, columns, folders, job, cfg, rep, summary, overrides) -> None:
    videos = []  # Status stays as it is until the end, so an interrupted render is simply tried again
    for folder in folders:
        videos.append(render_clip(folder, cfg, rep, overrides))
        summary.rendered += 1
    _put(ws, row, columns, "videos", "\n".join(str(v) for v in videos))
    _link(ws.cell(row=row, column=columns["videos"]), videos[0])
    jobs = [_job(f) for f in folders]
    _put(ws, row, columns, "hooks_used", "\n".join(f"{i}) {j.get('hook') or '(none)'}" for i, j in enumerate(jobs, 1)))
    _set_status(ws, row, columns, "Done")


def _log_crash(cfg: Config, row: int, link: str) -> None:
    folder = cfg.path(cfg.cache_dir) / "logs"
    folder.mkdir(parents=True, exist_ok=True)
    with (folder / f"run-{dt.date.today()}.log").open("a", encoding="utf-8") as fh:
        fh.write(f"\n=== {dt.datetime.now():%H:%M:%S} row {row}: {link}\n{traceback.format_exc()}")
