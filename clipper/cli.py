"""Command line: clipper make | run | render | menu | peaks | doctor | gemini-test | update."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import sysconfig
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console
from rich.markup import escape
from rich.prompt import Prompt
from rich.table import Table

from . import __version__, gemini, gpu, media, render, sheet, sponsorblock
from .audio import list_tracks
from .config import PROJECT_DIR, load_config
from .metadata import fetch_info
from .moments import find_moments, heatmap_arrays, mask_buckets
from .pipeline import HookContext, MakeOptions, Reporter, make as run_make, render_clip
from .transcribe import model_is_downloaded
from .util import ClipperError, fmt_time, normalize_url, parse_time

console = Console(highlight=False)
app = typer.Typer(
    add_completion=False,
    no_args_is_help=True,
    pretty_exceptions_show_locals=False,
    help="Turn the most-replayed moment of a gaming video into a ready-to-post vertical Short.",
)


class RichReporter(Reporter):
    @contextmanager
    def step(self, text: str):
        started = time.monotonic()
        with console.status(f"{text}…", spinner="dots"):
            yield
        console.print(f"[green]✓[/green] {escape(text)} [dim]({time.monotonic() - started:.1f}s)[/dim]")

    def info(self, text: str) -> None:
        console.print(f"  {escape(text)}")

    def warn(self, text: str) -> None:
        console.print(f"[yellow]![/yellow] {escape(text)}")

    def ask_hook(self, ctx: HookContext) -> str | None:
        console.rule(f"Clip {ctx.index}/{ctx.total}: {fmt_time(ctx.start)}-{fmt_time(ctx.end)} ({ctx.end - ctx.start:.0f}s)")
        if ctx.transcript:
            console.print(f"[dim]What's said ({ctx.language}):[/dim] {escape(ctx.transcript)}")
        else:
            console.print("[dim]No speech in this clip.[/dim]")
        if ctx.suggestions:
            console.print("[dim]Ideas:[/dim]")
            for i, idea in enumerate(ctx.suggestions, 1):
                console.print(f"  {i}) {escape(idea)}")
        answer = Prompt.ask(
            "Hook text (Enter = idea 1, a number = that idea, - = no hook, or type your own; | = new line)",
            default="",
            show_default=False,
        ).strip()
        if not answer:
            return ctx.suggestions[0] if ctx.suggestions else ""
        if answer == "-":
            return ""
        if answer.isdigit() and 1 <= int(answer) <= len(ctx.suggestions):
            return ctx.suggestions[int(answer) - 1]
        return answer


@app.command(help="Make Shorts from the most-replayed part of a YouTube video (or from a video file).")
def make(
    source: str = typer.Argument(..., help="YouTube link (put it in quotes) or a path to a video file"),
    hook: Optional[str] = typer.Option(None, help="Text at the top of the Short ('|' = new line). Skips the question."),
    count: int = typer.Option(1, min=1, max=10, help="How many clips to make from this video"),
    start: Optional[str] = typer.Option(None, help="Choose the moment yourself: start time, e.g. 12:30"),
    end: Optional[str] = typer.Option(None, help="End time when using --start, e.g. 12:58"),
    layout: Optional[str] = typer.Option(None, help="blur (whole gameplay + blurred background) or crop (fill the screen)"),
    zoom: Optional[float] = typer.Option(None, help="blur layout: 1.0 = whole frame, 1.25 = bigger gameplay"),
    facecam: Optional[str] = typer.Option(None, help="Webcam on top, gameplay below: auto, bottom-right, bottom-left, "
                                          "top-right, top-left, or x,y,w,h in percent"),
    music: Optional[str] = typer.Option(None, help="'none', or a music file. Default: random track from assets/music"),
    lang: Optional[str] = typer.Option(None, help="Caption language: auto, en or hi"),
    translate: Optional[bool] = typer.Option(None, "--translate/--no-translate", help="Hindi speech -> English captions"),
    hindi_script: Optional[str] = typer.Option(
        None, "--hindi-script", help="Hindi captions: latin (English letters, the default) or devanagari"
    ),
    effects: Optional[str] = typer.Option(None, help="default (zoom cut, word pop, hook pop, shadow), none, or all "
                                          "(adds the progress bar and sound effects)"),
    profanity: Optional[str] = typer.Option(None, help="Swear words: mask (shown as f**k), strict (also damn/hell), "
                                            "or show"),
    bleep: Optional[bool] = typer.Option(None, "--bleep/--no-bleep", help="Also beep the hidden swear words"),
    no_captions: bool = typer.Option(False, "--no-captions", help="Don't add captions"),
    no_gemini: bool = typer.Option(False, "--no-gemini", help="Don't ask Gemini, use only the local speech model"),
    no_credit: bool = typer.Option(False, "--no-credit", help="Don't show the creator's name on screen"),
    game: Optional[str] = typer.Option(None, help="Game for hashtags (gta6, gta5, rdr2 or your own from config.yaml)"),
    force: bool = typer.Option(False, "--force", help="Make it even if you already clipped this part"),
    no_prompt: bool = typer.Option(False, "--no-prompt", help="Don't ask for hook text"),
    refresh: bool = typer.Option(False, "--refresh", help="Re-read the video info instead of using the saved copy"),
    review: bool = typer.Option(False, "--review", help="Stop after the captions so you can edit them; then: clipper render"),
    config: Optional[Path] = typer.Option(None, help="Use a different settings file"),
) -> None:
    cfg = load_config(config)
    opts = MakeOptions(
        hook=hook,
        count=count,
        start=parse_time(start) if start else None,
        end=parse_time(end) if end else None,
        layout=layout,
        zoom=zoom,
        facecam=facecam,
        effects=effects.lower() if effects else None,
        profanity=profanity.lower() if profanity else None,
        bleep=bleep,
        music=music,
        lang=lang.lower() if lang else None,
        translate=translate,
        hindi_script=hindi_script,
        captions=False if no_captions else None,
        gemini=False if no_gemini else None,
        credit=False if no_credit else None,
        game=game,
        force=force,
        prompt=not no_prompt and sys.stdin.isatty(),
        refresh=refresh,
        review=review,
    )
    if opts.end is not None and opts.start is None:
        raise ClipperError("--end only works together with --start.")
    folders = run_make(source, opts, cfg, RichReporter())
    if not folders:
        console.print("[yellow]No new clips were made.[/yellow]")
        return
    console.rule("Captions ready to check" if review else "Done")
    for folder in folders:
        console.print(f"[green]✓[/green] {escape(str(folder))}")
    if review:
        console.print("Fix the words in captions.srt and the hook in hook.txt, then make the videos with:")
        console.print('  clipper render "<clip folder>"')
    else:
        console.print("Watch each clip.mp4. Titles and description to paste are in post.txt.")


@app.command("render", help="Make the videos of clip folders prepared earlier (after you edited captions.srt / hook.txt).")
def render_command(
    folders: list[Path] = typer.Argument(..., help="One or more clip folders"),
    config: Optional[Path] = typer.Option(None, help="Use a different settings file"),
) -> None:
    cfg = load_config(config)
    reporter = RichReporter()
    for folder in folders:
        video = render_clip(folder, cfg, reporter)
        console.print(f"[green]✓[/green] {escape(str(video))}")


@app.command(help="Work through clips.xlsx: new rows get captions, rows set to Ready get their videos made.")
def run(
    sheet_file: Optional[Path] = typer.Option(None, "--sheet", help="Use a different Excel file"),
    config: Optional[Path] = typer.Option(None, help="Use a different settings file"),
) -> None:
    cfg = load_config(config)
    path = sheet_file or cfg.path(cfg.sheet.file)
    existed = path.exists()
    summary = sheet.run(cfg, RichReporter(), path)
    if not existed:
        console.print(f"[green]Created {escape(str(path))}[/green]. Open it, put video links in (one per row), "
                      "save and close it, then run this again. The 'How to use' tab explains every column.")
        return
    console.rule("Finished")
    console.print(f"Clips with captions to check: {summary.prepared}   Videos made: {summary.rendered}   "
                  f"Rows failed: {summary.failed}")
    for line in summary.rows:
        console.print(f"  {escape(line)}")
    if summary.waiting:
        console.print(f"{summary.waiting} row(s) say 'Edit captions': fix captions.srt / hook.txt in their clip "
                      "folders, set Status to Ready, save and close the sheet, and run again.")
    if summary.failed:
        console.print("Failed rows show the reason in the Status column. Fix it, clear the Status, and run again.")


BARS = " ▁▂▃▄▅▆▇█"


@app.command(help="Show a video's 'Most replayed' graph and the moments that would be picked (downloads nothing).")
def peaks(
    url: str = typer.Argument(..., help="YouTube link (put it in quotes)"),
    count: int = typer.Option(5, min=1, max=20, help="How many moments to list"),
    refresh: bool = typer.Option(False, "--refresh", help="Re-read the video info instead of using the saved copy"),
    config: Optional[Path] = typer.Option(None, help="Use a different settings file"),
) -> None:
    cfg = load_config(config)
    media.init(cfg.ffmpeg)
    rep = RichReporter()
    with rep.step("Reading video info from YouTube"):
        video = fetch_info(normalize_url(url), cfg, refresh=refresh)
    views = f"{video.view_count:,} views" if video.view_count else "views unknown"
    console.print(f"[bold]{escape(video.title)}[/bold]\n  {escape(video.channel)} | {fmt_time(video.duration)} | {views}")
    if not video.heatmap:
        console.print(
            "[yellow]![/yellow] No 'Most replayed' data for this video yet (too new or not enough views). "
            "`clipper make` will fall back to the loudest moments, or use --start mm:ss."
        )
        return
    masked, status = sponsorblock.fetch_segments(video.id) if cfg.clip.use_sponsorblock else ([], "turned off")
    moments = find_moments(video.heatmap, video.duration, cfg.clip, masked, count)
    starts, ends, values = heatmap_arrays(video.heatmap)
    mask = mask_buckets(starts, ends, video.duration, cfg.clip, masked)
    graph = "".join(
        f"[dim]{BARS[min(8, int(round(v * 8)))]}[/dim]" if m else BARS[min(8, int(round(v * 8)))]
        for v, m in zip(values, mask)
    )
    marks = [" "] * len(values)
    for rank, m in enumerate(moments, 1):
        i = int(min(len(values) - 1, max(0, (m.peak_time - starts[0]) / max(1e-9, ends[0] - starts[0]))))
        marks[i] = str(rank % 10)
    console.print(f"\nMost replayed graph ({len(values)} points; dim = ignored: intro/outro/sponsors, SponsorBlock {status}):")
    console.print(graph)
    console.print("".join(marks))
    console.print(f"0:00{' ' * max(1, len(values) - 4 - len(fmt_time(video.duration)))}{fmt_time(video.duration)}\n")
    if not moments:
        console.print("[yellow]The graph has no clear spike.[/yellow]")
        return
    table = Table(show_edge=False)
    for column in ("#", "Clip window", "Spike at", "Height", "Stands out by", "Score"):
        table.add_column(column)
    for rank, m in enumerate(moments, 1):
        table.add_row(
            str(rank), f"{fmt_time(m.start)}-{fmt_time(m.end)} ({m.length:.0f}s)" + (" wide*" if m.wide else ""),
            fmt_time(m.peak_time), f"{m.height:.2f}", f"{m.prominence:.2f}", f"{m.score:.2f}",
        )
    console.print(table)
    if any(m.wide for m in moments):
        console.print("[dim]* wide spike: the clip will be centred on the most intense part of it.[/dim]")
    console.print(f'\nMake the top {"one" if count == 1 else "ones"}:  clipper make "{escape(url)}"' + (f" --count {min(count, len(moments))}" if count > 1 else ""))


def _find_deno() -> str | None:
    try:
        from yt_dlp.utils._jsruntime import _find_exe  # same lookup yt-dlp itself uses

        found = _find_exe("deno")
        if found and (os.path.isabs(found) and os.path.exists(found) or shutil.which(found)):
            return found if os.path.isabs(found) else shutil.which(found)
    except Exception:
        pass
    scripts = Path(sysconfig.get_path("scripts")) / ("deno.exe" if os.name == "nt" else "deno")
    return str(scripts) if scripts.exists() else shutil.which("deno")


@app.command(help="Check that everything the tool needs is installed and working.")
def doctor(
    config: Optional[Path] = typer.Option(None, help="Use a different settings file"),
    recheck_gpu: bool = typer.Option(False, "--recheck-gpu", help="Forget the saved GPU test (e.g. after a driver update)"),
) -> None:
    rows: list[tuple[str, bool, str]] = []
    required_failed = False

    def add(name: str, ok: bool, detail: str, required: bool = True) -> None:
        nonlocal required_failed
        rows.append((name, ok, detail))
        required_failed |= required and not ok

    add("Python", sys.version_info >= (3, 10), sys.version.split()[0])
    try:
        cfg = load_config(config)
        add("Settings", True, str(cfg.root / "config.yaml") if (cfg.root / "config.yaml").exists() else "built-in defaults")
    except ClipperError as exc:
        add("Settings", False, str(exc))
        cfg = None
    try:
        media.init(cfg.ffmpeg if cfg else "")
        add("FFmpeg", True, f"{media.ffmpeg_version()} ({media.ffmpeg_bin()})")
        has_ass, has_x264 = media.ffmpeg_has("filters", "ass"), media.ffmpeg_has("encoders", "libx264")
        add("FFmpeg subtitles (libass)", has_ass, "ok" if has_ass else "missing: install the full build (winget install Gyan.FFmpeg)")
        add("FFmpeg H.264 encoder", has_x264, "ok" if has_x264 else "missing: install the full build (winget install Gyan.FFmpeg)")
    except ClipperError as exc:
        add("FFmpeg", False, str(exc).splitlines()[0] + " -> winget install Gyan.FFmpeg")
    try:
        import yt_dlp.version

        add("yt-dlp (YouTube downloader)", True, yt_dlp.version.__version__)
    except Exception as exc:
        add("yt-dlp (YouTube downloader)", False, f"not installed ({exc})")
    deno = _find_deno()
    if deno:
        version = subprocess.run([deno, "--version"], capture_output=True, text=True).stdout.split("\n")[0]
        add("Deno (needed by yt-dlp for YouTube)", True, version or deno)
    else:
        add("Deno (needed by yt-dlp for YouTube)", False, 'run:  clipper update   (installs yt-dlp[default,deno])')
    if cfg:
        missing = [f for f in {cfg.style.font_file, cfg.style.credit_font_file} if not (cfg.fonts_dir / f).is_file()]
        add("Fonts", not missing, "ok" if not missing else f"missing in assets/fonts: {', '.join(missing)}")
        tracks = list_tracks(cfg.path(cfg.music.folder))
        add("Music", bool(tracks), f"{len(tracks)} track(s) in {cfg.music.folder}" if tracks else f"no tracks in {cfg.music.folder} (clips will have no music)", required=False)
        if recheck_gpu:
            gpu.forget_gpu_checks(cfg.path(cfg.cache_dir))
        gpus = gpu.nvidia_gpus()
        add("NVIDIA GPU", True, ", ".join(f"{g.name} ({g.memory_mb // 1024} GB)" for g in gpus) or "none found", required=False)
        speech_model = cfg.captions.model
        if gpus and cfg.captions.device != "cpu":
            gpu.prepare_cuda_libraries()
            try:
                import ctranslate2

                cuda_ok = ctranslate2.get_cuda_device_count() > 0
            except Exception:
                cuda_ok = False
            add("GPU library for speech (cuBLAS)", cuda_ok,
                "ready" if cuda_ok else "missing or driver too old: run setup.cmd again, or update the NVIDIA driver",
                required=False)
            if speech_model == "auto":
                speech_model = "large-v3" if cuda_ok else "small"
            where = "GPU (tested on the first clip, then remembered)" if cuda_ok else "CPU"
        else:
            if speech_model == "auto":
                speech_model = "small"
            where = "CPU"
        downloaded = model_is_downloaded(speech_model)
        add("Speech model", True, f"{speech_model} on the {where}, "
            + ("downloaded" if downloaded else "downloads on first use (one time)"), required=False)
        names = {"h264_nvenc": "NVIDIA GPU", "h264_amf": "AMD GPU", "h264_qsv": "Intel GPU", "libx264": "CPU"}
        if media.ffmpeg_has("encoders", "libx264"):
            chosen = render.pick_encoder(cfg.render)
            add("Video encoding", True, f"{chosen} ({names.get(chosen, chosen)})", required=False)
        try:
            import cv2  # noqa: F401

            from .facecam import MODEL

            add("Facecam detector", MODEL.exists(), "ready" if MODEL.exists() else f"missing model file {MODEL.name}",
                required=False)
        except ImportError:
            add("Facecam detector", False, "OpenCV missing: run setup.cmd again", required=False)
        sheet_path = cfg.path(cfg.sheet.file)
        add("Excel sheet", True, str(sheet_path) if sheet_path.exists() else f"{cfg.sheet.file} (created on the first run)",
            required=False)
        key_set = bool(gemini.api_key(cfg.captions.gemini))
        add("Gemini (optional)", True, "key set: run  clipper gemini-test  to check your free quota" if key_set
            else "not set: paste a free key into gemini_api_key at the top of config.yaml", required=False)
        out = cfg.path(cfg.output_dir)
        try:
            out.mkdir(parents=True, exist_ok=True)
            probe = out / ".write_test"
            probe.write_text("ok")
            probe.unlink()
            add("Output folder", True, str(out))
        except OSError as exc:
            add("Output folder", False, f"can't write to {out}: {exc}")

    table = Table(show_edge=False, title=f"clipper {__version__}")
    table.add_column("")
    table.add_column("Check")
    table.add_column("Details")
    for name, ok, detail in rows:
        table.add_row("[green]✓[/green]" if ok else "[red]✗[/red]", name, escape(detail))
    console.print(table)
    if required_failed:
        raise typer.Exit(1)
    console.print("[green]Ready.[/green] Try:  clipper peaks \"<youtube link>\"")


@app.command("gemini-test", help="Check that your Gemini key works on the free tier (one tiny request per model).")
def gemini_test(config: Optional[Path] = typer.Option(None, help="Use a different settings file")) -> None:
    cfg = load_config(config)
    table = Table(show_edge=False, title="Gemini free tier")
    table.add_column("")
    table.add_column("Model")
    table.add_column("Result")
    results = gemini.check(cfg.captions.gemini)
    for model, ok, detail in results:
        table.add_row("[green]✓[/green]" if ok else "[red]✗[/red]", model, escape(detail))
    console.print(table)
    if any(ok for _m, ok, _d in results):
        console.print("Gemini will check caption words. When the free quota runs out, the local model takes over by itself.")
    else:
        console.print("Captions will use only the local model. Free keys sometimes start with zero quota for a model;"
                      " check your limits at aistudio.google.com.")


def _git(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(PROJECT_DIR), *args], capture_output=True, text=True, errors="replace")


def _has_gpu_extra() -> bool:
    try:
        from importlib.metadata import version

        version("nvidia-cublas-cu12")
        return True
    except Exception:
        return False


def update_tool() -> bool:
    """Newest version of the tool (git installs), then anything new it needs. Returns False if it went wrong."""
    if not (PROJECT_DIR / ".git").exists() or not shutil.which("git"):
        console.print("This copy wasn't downloaded with git, so it can't update itself. To update: download the "
                      "newest ZIP from GitHub, unzip it over this folder (your config.yaml, clips.xlsx and output "
                      "folder are kept), then double-click setup.cmd.")
        return True
    before = _git("rev-parse", "HEAD").stdout.strip()
    console.print("Getting the newest version of the tool…")
    pulled = _git("pull", "--ff-only")
    if pulled.returncode != 0:
        console.print(f"[red]✗[/red] git couldn't update this folder:\n{escape(pulled.stderr.strip()[-600:])}\n"
                      "Usually a file the tool ships with was changed here. Run 'git status' to see which, or "
                      "download a fresh copy (keep config.yaml, clips.xlsx and the output folder).")
        return False
    after = _git("rev-parse", "HEAD").stdout.strip()
    if after == before:
        console.print("[green]✓[/green] The tool is already the newest version.")
        return True
    log = _git("log", "--oneline", "--no-decorate", f"{before}..{after}").stdout.strip().splitlines()
    console.print(f"[green]✓[/green] Updated ({len(log)} change{'s' if len(log) != 1 else ''}):")
    for line in log[:10]:
        console.print(f"  {escape(line)}")
    target = f"{PROJECT_DIR}[gpu]" if _has_gpu_extra() else str(PROJECT_DIR)
    console.print("Installing anything new the update needs…")
    code = subprocess.call([sys.executable, "-m", "pip", "install", "--quiet", "--disable-pip-version-check", "-e", target])
    if code != 0:
        console.print("[red]✗[/red] Installing failed. Double-click setup.cmd to finish the update.")
    return code == 0


@app.command(help="Update the tool to its newest version (if it was downloaded with git) and the YouTube downloader. "
                  "The downloader update fixes most 'download failed' errors.")
def update(
    downloader_only: bool = typer.Option(False, "--downloader-only", help="Only update the YouTube downloader"),
) -> None:
    ok = True if downloader_only else update_tool()
    console.print("Updating the YouTube downloader…")
    code = subprocess.call([sys.executable, "-m", "pip", "install", "--upgrade", "--quiet", "--disable-pip-version-check",
                            "yt-dlp[default,deno]"])
    if code == 0:
        console.print("[green]✓[/green] YouTube downloader updated.")
    raise typer.Exit(0 if ok and code == 0 else 1)


def _open(path: Path) -> None:
    """Open a file or folder the way double-clicking would."""
    try:
        if sys.platform.startswith("win"):
            os.startfile(str(path))  # noqa: S606 - opening the user's own files is the point
        else:
            subprocess.Popen(["open" if sys.platform == "darwin" else "xdg-open", str(path)])
        console.print(f"Opened {escape(str(path))}")
    except Exception:
        console.print(f"Open this yourself: {escape(str(path))}")


MENU = [
    ("1", "Work through clips.xlsx (new rows get captions, rows set to Ready get their videos)"),
    ("2", "Make clips from one YouTube link now"),
    ("3", "Open clips.xlsx"),
    ("4", "Open the output folder (your clips)"),
    ("5", "Check the setup"),
    ("6", "Update the tool"),
    ("7", "Test the Gemini key"),
    ("0", "Quit"),
]


@app.command(help="A simple numbered menu for everything (what menu.cmd opens).")
def menu(config: Optional[Path] = typer.Option(None, help="Use a different settings file")) -> None:
    while True:
        console.rule("Gaming Shorts Clipper")
        for key, text in MENU:
            console.print(f"  [bold]{key}[/bold]  {text}")
        choice = Prompt.ask("Type a number and press Enter", choices=[k for k, _ in MENU], default="1")
        if choice == "0":
            return
        try:
            cfg = load_config(config)
            if choice == "1":
                run(sheet_file=None, config=config)
            elif choice == "2":
                link = Prompt.ask("Paste the YouTube link (or the path of a video file)").strip().strip('"')
                if not link:
                    continue
                times = Prompt.ask("Your own times, like 12:30-12:58 (Enter = let the tool find the best moment)",
                                   default="", show_default=False).strip()
                opts = MakeOptions()
                if times:
                    start, _, end = times.partition("-")
                    opts.start, opts.end = parse_time(start.strip()), parse_time(end.strip()) if end.strip() else None
                else:
                    count = Prompt.ask("How many clips?", default="1")
                    opts.count = max(1, min(10, int(count))) if count.isdigit() else 1
                for folder in run_make(normalize_url(link), opts, cfg, RichReporter()):
                    console.print(f"[green]✓[/green] {escape(str(folder))}")
            elif choice == "3":
                path = cfg.path(cfg.sheet.file)
                if not path.exists():
                    sheet.create_sheet(path, cfg)
                _open(path)
            elif choice == "4":
                out = cfg.path(cfg.output_dir)
                out.mkdir(parents=True, exist_ok=True)
                _open(out)
            elif choice == "5":
                doctor(config=config, recheck_gpu=False)
            elif choice == "6":
                update_tool()
            elif choice == "7":
                gemini_test(config=config)
        except typer.Exit:
            pass
        except ClipperError as exc:
            console.print(f"[red]✗[/red] {escape(str(exc))}")
        except KeyboardInterrupt:
            console.print("\n[yellow]Stopped.[/yellow]")


def main() -> None:
    os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")  # harmless Windows warning from the model download
    for stream in (sys.stdout, sys.stderr):  # Hindi text must not crash when output goes to a file
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    try:
        app(prog_name="clipper")
    except ClipperError as exc:
        console.print(f"[red]✗[/red] {escape(str(exc))}")
        sys.exit(1)
    except KeyboardInterrupt:
        console.print("\n[yellow]Stopped.[/yellow]")
        sys.exit(130)
