"""Command line: clipper make | peaks | doctor | update."""

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

from . import __version__, media, sponsorblock
from .audio import list_tracks
from .config import load_config
from .metadata import fetch_info
from .moments import find_moments, heatmap_arrays, mask_buckets
from .pipeline import HookContext, MakeOptions, Reporter, make as run_make
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
            "Hook text for the top of the Short (type it, or a number to use an idea, Enter for none; | = new line)",
            default="",
            show_default=False,
        ).strip()
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
    music: Optional[str] = typer.Option(None, help="'none', or a music file. Default: random track from assets/music"),
    lang: Optional[str] = typer.Option(None, help="Caption language: auto, en or hi"),
    translate: Optional[bool] = typer.Option(None, "--translate/--no-translate", help="Hindi speech -> English captions"),
    hindi_script: Optional[str] = typer.Option(
        None, "--hindi-script", help="Hindi captions: both (two files), latin (English letters) or devanagari"
    ),
    no_captions: bool = typer.Option(False, "--no-captions", help="Don't add captions"),
    no_credit: bool = typer.Option(False, "--no-credit", help="Don't show the creator's name on screen"),
    game: Optional[str] = typer.Option(None, help="Game for hashtags (gta6, gta5, rdr2 or your own from config.yaml)"),
    force: bool = typer.Option(False, "--force", help="Make it even if you already clipped this part"),
    no_prompt: bool = typer.Option(False, "--no-prompt", help="Don't ask for hook text"),
    refresh: bool = typer.Option(False, "--refresh", help="Re-read the video info instead of using the saved copy"),
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
        music=music,
        lang=lang.lower() if lang else None,
        translate=translate,
        hindi_script=hindi_script,
        captions=False if no_captions else None,
        credit=False if no_credit else None,
        game=game,
        force=force,
        prompt=not no_prompt and sys.stdin.isatty(),
        refresh=refresh,
    )
    if opts.end is not None and opts.start is None:
        raise ClipperError("--end only works together with --start.")
    folders = run_make(source, opts, cfg, RichReporter())
    if not folders:
        console.print("[yellow]No new clips were made.[/yellow]")
        return
    console.rule("Done")
    for folder in folders:
        console.print(f"[green]✓[/green] {escape(str(folder))}")
    console.print(
        "Watch each clip, then move the good ones into [bold]output/ready[/bold] and post from there. "
        "Titles and description are in post.txt."
    )


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
def doctor(config: Optional[Path] = typer.Option(None, help="Use a different settings file")) -> None:
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
        downloaded = model_is_downloaded(cfg.captions.model)
        add("Speech model", True, f"'{cfg.captions.model}' " + ("downloaded" if downloaded else "downloads on first use (one time)"), required=False)
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


@app.command(help="Update the YouTube downloader. Fixes most 'download failed' errors.")
def update() -> None:
    cmd = [sys.executable, "-m", "pip", "install", "--upgrade", "yt-dlp[default,deno]"]
    console.print("Running: " + " ".join(cmd))
    raise typer.Exit(subprocess.call(cmd))


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
