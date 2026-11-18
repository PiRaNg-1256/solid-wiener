"""The `make` command: from a YouTube link (or a video file) to finished Shorts in the output folder."""

from __future__ import annotations

import copy
import shutil
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import numpy as np

from . import __version__, align, audio, captions, db, gemini, media, package, render, sponsorblock
from .analysis import envelope_db, pseudo_heatmap
from .config import Config, validate
from .download import cut_local, download_audio, download_segment
from .hinglish import to_latin
from .layout import build_layout
from .metadata import VideoInfo, chapter_at, fetch_info
from .moments import Moment, find_moments, manual_moment
from .refine import refine_window
from .transcribe import Transcript, Word, transcribe
from .util import ClipperError, fmt_time, is_url, normalize_url, slugify


@dataclass
class MakeOptions:
    hook: str | None = None  # None = ask (if prompting), "" = no hook
    count: int = 1
    start: float | None = None
    end: float | None = None
    layout: str | None = None
    zoom: float | None = None
    music: str | None = None  # None = random track from the music folder, "none", or a file path
    lang: str | None = None  # None/"auto" = detect among config languages, or "en" / "hi"
    hindi_script: str | None = None  # both | latin | devanagari (None = use config)
    gemini: bool | None = None  # False = don't ask Gemini even if a key is set
    translate: bool | None = None
    captions: bool | None = None
    credit: bool | None = None
    game: str | None = None
    force: bool = False
    prompt: bool = True
    refresh: bool = False


@dataclass
class HookContext:
    index: int
    total: int
    start: float  # seconds in the source video
    end: float
    transcript: str
    language: str
    suggestions: list[str] = field(default_factory=list)


class Reporter:
    """Progress output. The command line uses a Rich version; tests use this silent one."""

    @contextmanager
    def step(self, text: str):
        yield

    def info(self, text: str) -> None:
        pass

    def warn(self, text: str) -> None:
        pass

    def ask_hook(self, ctx: HookContext) -> str | None:
        return None


def _with_overrides(cfg: Config, opts: MakeOptions) -> Config:
    cfg = copy.deepcopy(cfg)
    if opts.layout:
        cfg.style.layout = opts.layout
    if opts.zoom:
        cfg.style.zoom = opts.zoom
    if opts.captions is not None:
        cfg.captions.enabled = opts.captions
    if opts.translate is not None:
        cfg.captions.translate_to_english = opts.translate
    if opts.credit is not None:
        cfg.style.credit = opts.credit
    if opts.music is not None and opts.music.strip().lower() == "none":
        cfg.music.enabled = False
    if opts.hindi_script:
        cfg.captions.hindi_script = opts.hindi_script.lower()
    if opts.lang not in (None, "", "auto") and opts.lang not in ("en", "hi"):
        raise ClipperError("--lang must be auto, en or hi")
    validate(cfg)
    return cfg


def load_source(source: str, cfg: Config, refresh: bool = False, rep: Reporter | None = None) -> tuple[VideoInfo, Path | None]:
    rep = rep or Reporter()
    if is_url(source):
        with rep.step("Reading video info from YouTube"):
            return fetch_info(normalize_url(source), cfg, refresh=refresh), None
    path = Path(source).expanduser()
    if not path.is_file():
        raise ClipperError(f"That's not a link, and there's no file called: {source}")
    info = media.probe(path)
    if not info.has_video:
        raise ClipperError("That file has no video in it.")
    video = VideoInfo(
        id=slugify(path.stem, 30, "local"), url=str(path), title=path.stem, channel="", handle="",
        duration=info.duration, local_path=str(path.resolve()),
    )
    return video, path


def choose_moments(video: VideoInfo, local: Path | None, cfg: Config, opts: MakeOptions, rep: Reporter) -> list[Moment]:
    if opts.start is not None:
        start = opts.start
        end = opts.end if opts.end is not None else start + cfg.clip.target_seconds
        if video.duration and start >= video.duration:
            raise ClipperError(f"--start is after the end of the video ({fmt_time(video.duration)}).")
        if end <= start:
            raise ClipperError("--end must be after --start.")
        end = min(end, video.duration) if video.duration else end
        if end - start > 60:
            rep.warn("Over 60 seconds: YouTube blocks such Shorts if anyone files a copyright claim.")
        return [manual_moment(start, end)]

    if video.heatmap:
        masked: list = []
        if cfg.clip.use_sponsorblock and not video.is_local:
            with rep.step("Checking SponsorBlock for sponsor and intro segments"):
                masked, status = sponsorblock.fetch_segments(video.id)
            rep.info(f"SponsorBlock: {status}")
        moments = find_moments(video.heatmap, video.duration, cfg.clip, masked, opts.count)
        if moments:
            return moments
        rep.warn("The 'Most replayed' graph is flat (no clear spike), so the loudest moments are used instead.")
    else:
        rep.warn(
            "This video has no 'Most replayed' data yet (too new or not enough views). Using the loudest "
            "moments instead, which is a weaker signal. You can pick a moment yourself with --start mm:ss."
        )
    with rep.step("Finding the loudest moments"):
        audio_file = local or download_audio(video.url, video.id, cfg)
        mono = media.decode_audio(audio_file, sr=4000)
        times, db_values = envelope_db(mono, 4000, hop=0.25, window=0.5)
        duration = video.duration or (float(times[-1]) if len(times) else 0.0)
        heat = pseudo_heatmap(times, db_values, bucket=2.0, smooth=6.0)
        moments = find_moments(heat, duration, cfg.clip, (), opts.count, source="loudness")
    if not moments:
        raise ClipperError("Couldn't find a moment that stands out. Pick one yourself with --start mm:ss.")
    return moments


@dataclass
class BetterWords:
    """Caption words from Gemini, already placed on the local model's timings."""

    language: str
    native: list[Word]  # normal script (Hindi in Devanagari)
    latin: list[Word]  # English letters
    model: str


def caption_variants(
    words: list[Word], transcript: Transcript, hindi_script: str, better: BetterWords | None = None
) -> list[tuple[str, str, list[Word]]]:
    """[(file name, script, words)]. The first one becomes clip.mp4. Hindi speech can be captioned in
    English letters ("latin"), Hindi script ("devanagari"), or both (a second video file)."""
    if better is not None and not transcript.translated:
        language, native, latin = better.language, better.native, better.latin
    else:
        language, native = transcript.language, words
        latin = [Word(w.start, w.end, to_latin(w.text), w.prob) for w in words]
    if language != "hi" or transcript.translated or not native:
        return [("clip", "", native)]
    if hindi_script == "devanagari":
        return [("clip", "devanagari", native)]
    if hindi_script == "latin":
        return [("clip", "latin", latin)]
    return [("clip", "latin", latin), ("clip_devanagari", "devanagari", native)]


def ask_gemini(clip_audio: np.ndarray, clip_words: list[Word], cfg: Config) -> tuple[BetterWords | None, str]:
    """Gemini's words on the local timings, or (None, reason) to keep the local words."""
    try:
        result = gemini.transcribe_clip(clip_audio, cfg.captions.gemini)
    except gemini.GeminiError as exc:
        return None, f"Gemini not used, kept the local words: {exc}"
    keys = [align.key(latin) for _text, latin in result.words]
    native = align.align([text for text, _latin in result.words], keys, clip_words)
    latin = align.align([latin for _text, latin in result.words], keys, clip_words)
    if native is None or latin is None:
        return None, "Gemini's words didn't line up with the speech, kept the local words"
    language = "hi" if result.language in ("hi", "ur") else result.language
    return BetterWords(language, native, latin, result.model), f"caption words from Gemini ({result.model})"


def _describe(m: Moment) -> str:
    if m.source == "heatmap":
        return f"replay spike at {fmt_time(m.peak_time)}, height {m.height:.2f}, stands out by {m.prominence:.2f}"
    if m.source == "loudness":
        return f"loudest part around {fmt_time(m.peak_time)}"
    return "your chosen times"


def make(source: str, opts: MakeOptions, cfg: Config, rep: Reporter | None = None) -> list[Path]:
    rep = rep or Reporter()
    cfg = _with_overrides(cfg, opts)
    media.init(cfg.ffmpeg)
    video, local = load_source(source, cfg, opts.refresh, rep)
    if not video.is_local:
        rep.info(f"{video.title} | {video.channel} | {fmt_time(video.duration)}")
    moments = choose_moments(video, local, cfg, opts, rep)
    cache = cfg.path(cfg.cache_dir)
    folders = []
    for index, moment in enumerate(moments, 1):
        if not opts.force and not video.is_local:
            earlier = db.find_overlap(cache, video.id, moment.start, moment.end)
            if earlier:
                rep.warn(f"Skipped {fmt_time(moment.start)}-{fmt_time(moment.end)}: you already clipped it ({earlier}). Use --force to redo.")
                continue
        folders.append(make_clip(video, local, moment, index, len(moments), cfg, opts, rep))
    return folders


def make_clip(
    video: VideoInfo, local: Path | None, moment: Moment, index: int, total: int, cfg: Config, opts: MakeOptions, rep: Reporter
) -> Path:
    cache = cfg.path(cfg.cache_dir)
    dl_start = max(0.0, moment.start - cfg.clip.padding_seconds)
    dl_end = moment.end + cfg.clip.padding_seconds
    if video.duration:
        dl_end = min(video.duration, dl_end)
    rep.info(f"Clip {index}/{total}: around {fmt_time(moment.start)}-{fmt_time(moment.end)} ({_describe(moment)})")

    work = cache / "work" / f"{slugify(video.id, 30, 'clip')}_{int(moment.start)}_{int(time.time() * 1000) % 10**8}"
    work.mkdir(parents=True, exist_ok=True)
    folder: Path | None = None
    try:
        with rep.step("Cutting this part of the video" if local else "Downloading just this part of the video"):
            if local:
                segment = cut_local(local, dl_start, dl_end, work)
            else:
                segment = download_segment(video.url, video.id, dl_start, dl_end, cfg)
        seg = media.probe(segment)
        if not seg.has_video or seg.duration <= 0:
            raise ClipperError("The downloaded part has no usable video.")

        with rep.step("Finding scene cuts and the most intense moment"):
            mono = media.decode_audio(segment, sr=16000) if seg.has_audio else np.zeros(int(seg.duration * 16000), np.float32)
            env_t, env_db = envelope_db(mono, 16000)
            cuts = media.scene_cuts(segment)

        transcript = Transcript(note="captions turned off")
        if cfg.captions.enabled and seg.has_audio:
            with rep.step("Listening for speech to make captions (first run: downloads the model, tests the GPU)"):
                language = None if opts.lang in (None, "", "auto") else opts.lang
                transcript = transcribe(mono, cfg.captions, language=language, cache_dir=cache)
            if transcript.words:
                rep.info(f"Captions: {len(transcript.words)} words, language '{transcript.language}', {transcript.engine}"
                         + (" (translated to English)" if transcript.translated else ""))
                if transcript.note and transcript.note != "no NVIDIA GPU found":  # a GPU exists but couldn't be used
                    rep.warn(f"Speech model is on the CPU: {transcript.note}")
            else:
                rep.info(f"Captions: none ({transcript.note or 'no words recognised'})")

        refined = refine_window(
            moment.start - dl_start, moment.end - dl_start,
            words=transcript.words, cuts=cuts, env_times=env_t, env_db=env_db,
            seg_duration=seg.duration, cfg=cfg.clip, wide=moment.wide,
        )
        s, e = refined.start, refined.end
        clip_words = [
            Word(max(0.0, w.start - s), min(e - s, w.end - s), w.text, w.prob)
            for w in transcript.words
            if w.end > s + 0.05 and w.start < e - 0.05
        ]
        better, gemini_note = None, ""
        use_gemini = cfg.captions.enabled and clip_words and not transcript.translated and opts.gemini is not False
        if use_gemini and gemini.available(cfg.captions.gemini):
            with rep.step("Asking Gemini (free tier) to check the caption words"):
                better, gemini_note = ask_gemini(mono[int(s * 16000) : int(e * 16000)], clip_words, cfg)
            (rep.info if better else rep.warn)(gemini_note)
        variants = caption_variants(clip_words, transcript, cfg.captions.hindi_script, better)
        clip_text = " ".join(w.text for w in variants[0][2])
        source_start, source_end = dl_start + s, dl_start + e

        hook = opts.hook
        if hook is None and opts.prompt:
            ideas = package.hook_suggestions(video, chapter_at(video, dl_start + refined.payoff), clip_text)
            hook = rep.ask_hook(HookContext(index, total, source_start, source_end, clip_text, transcript.language, ideas))
        hook = (hook or "").strip() or None

        layout = build_layout(cfg.style.layout, seg.width, seg.height, cfg.style.zoom)
        fonts = work / "fonts"
        fonts.mkdir(exist_ok=True)
        for name in {cfg.style.font_file, cfg.style.credit_font_file}:
            src = cfg.fonts_dir / name
            if not src.is_file():
                raise ClipperError(f"Font file missing: {src}")
            shutil.copy2(src, fonts / name)
        credit = package.credit_line(cfg, video)
        for name, _script, words in variants:
            ass_text = captions.build_ass(
                words if cfg.captions.enabled else [], layout=layout, style=cfg.style, fonts_dir=cfg.fonts_dir,
                duration=e - s, hook=hook, credit=credit,
            )
            (work / f"{name}.ass").write_text(ass_text, encoding="utf-8")

        with rep.step("Mixing game audio and music"):
            track = audio.pick_track(cfg.path(cfg.music.folder), opts.music) if cfg.music.enabled else None
            if cfg.music.enabled and track is None:
                rep.warn(f"No music files in {cfg.path(cfg.music.folder)}, so this clip has no music.")
            mixed = audio.prepare_audio(segment, seg.has_audio, s, e, clip_words, track, cfg.music, cfg.render, work)

        folder = package.new_clip_folder(cfg.path(cfg.output_dir), slugify(hook or video.title, 40, slugify(video.id, 20, "clip")))
        encoders = []
        with rep.step("Rendering the Short" + (f" ({len(variants)} versions)" if len(variants) > 1 else "")):
            for name, _script, _words in variants:
                out = folder / f"{name}.mp4"
                encoders.append(render.render_video(segment, s, e, layout, work, mixed.mixed, out, cfg.render, seg.fps,
                                                    ass_name=f"{name}.ass"))
                if mixed.track and cfg.music.export_without_music:
                    render.replace_audio(out, mixed.game, folder / f"{name}_nomusic.mp4")
            render.thumbnail(folder / "clip.mp4", refined.payoff - s, folder / "thumb.jpg")

        game = package.detect_game(f"{video.title} {hook or ''}", cfg.games, opts.game)
        game_cfg = game[1] if game else None
        tags = package.hashtags_for(game_cfg, cfg.default_hashtags)
        titles = package.title_options(hook, game_cfg, cfg.title_templates, tags)
        desc = package.description(hook, video, source_start, mixed.track.credit if mixed.track else None, tags, credit is not None)
        notes = list(refined.notes) + ([gemini_note] if gemini_note else [])
        scripts = [script for _name, script, _words in variants if script]
        if scripts == ["latin", "devanagari"]:
            notes.append("Two versions: clip.mp4 has Hindi in English letters, clip_devanagari.mp4 has Hindi script.")
        elif scripts == ["latin"]:
            notes.append("Captions: Hindi in English letters. Use --translate for English captions.")
        elif scripts == ["devanagari"]:
            notes.append("Captions: Hindi script. Use --translate for English captions.")
        package.write_post(folder, titles, desc, video, source_start, source_end, notes)
        package.write_meta(folder, {
            "tool_version": __version__,
            "created_at": datetime.now().isoformat(timespec="seconds"),
            "source": {
                "id": video.id, "url": video.url, "title": video.title, "channel": video.channel,
                "handle": video.handle, "views": video.view_count, "upload_date": video.upload_date,
            },
            "clip": {
                "source_start": round(source_start, 3), "source_end": round(source_end, 3),
                "duration": round(e - s, 3), "payoff_in_clip": round(refined.payoff - s, 3),
            },
            "moment": moment.to_dict(),
            "refine_notes": refined.notes,
            "hook": hook,
            "game": game[0] if game else None,
            "captions": {
                "enabled": cfg.captions.enabled, "language": transcript.language,
                "probability": round(transcript.probability, 3), "translated": transcript.translated,
                "words": len(clip_words), "note": transcript.note, "model": cfg.captions.model,
                "hindi_scripts": scripts, "engine": transcript.engine,
                "words_from": f"gemini:{better.model}" if better else "local", "gemini_note": gemini_note,
            },
            "layout": {"name": layout.name, "zoom": cfg.style.zoom},
            "video_encoder": encoders[0] if encoders else None,
            "music": None if not mixed.track else {
                "file": mixed.track.path.name, "credit": mixed.track.credit,
                "start_in_track": round(mixed.music_offset, 2), "gain_db": round(mixed.music_gain_db, 2),
            },
            "files": sorted(p.name for p in folder.iterdir()),
        })
        if not video.is_local:
            db.record(cache, video.id, source_start, source_end, folder, hook)
        return folder
    except BaseException:
        if folder is not None:
            shutil.rmtree(folder, ignore_errors=True)
        raise
    finally:
        if not cfg.render.keep_work_files:
            shutil.rmtree(work, ignore_errors=True)
