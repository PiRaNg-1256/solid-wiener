"""Making clips, in two steps:

1. prepare: find the moment, download just that part, write captions.srt (editable, with timestamps),
   hook.txt (editable) and post.txt (titles, description) into output/<video title>/clip .../
2. render: read the captions and hook (with your edits) and make clip.mp4.

`make` does both in one go unless `review` is on."""

from __future__ import annotations

import copy
import hashlib
import json
import shutil
import time
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path

import numpy as np

from . import __version__, align, audio, captions, db, facecam, gemini, media, package, profanity, render, signals, sponsorblock
from .analysis import envelope_db, pseudo_heatmap
from .config import Config, validate
from .download import cut_local, download_audio, download_segment
from .hinglish import to_latin
from .layout import build_layout
from .metadata import VideoInfo, chapter_at, fetch_info
from .moments import Moment, find_moments, manual_moment
from .refine import refine_window
from .transcribe import Transcript, Word, transcribe
from .util import ClipperError, fmt_time, has_devanagari, is_url, normalize_url, slugify

JOB_FILE, CAPTIONS_FILE, HOOK_FILE = "job.json", "captions.srt", "hook.txt"


@dataclass
class MakeOptions:
    hook: str | None = None  # None = Gemini writes one (or you're asked), "" = no hook
    hooks: list[str] | None = None  # one per clip, in order (from the sheet); missing ones fall back to `hook`
    count: int = 1
    slices: list[tuple[float, float | None]] | None = None  # your own clip times: (start, end or None)
    start: float | None = None  # single slice from the command line
    end: float | None = None
    layout: str | None = None
    zoom: float | None = None
    music: str | None = None  # None = random track from the music folder, "none", or a file path
    lang: str | None = None  # None/"auto" = detect among config languages, or "en" / "hi"
    hindi_script: str | None = None  # latin | devanagari (None = use config)
    gemini: bool | None = None  # False = don't ask Gemini even if a key is set
    translate: bool | None = None
    captions: bool | None = None
    credit: bool | None = None
    game: str | None = None
    force: bool = False
    prompt: bool = True
    refresh: bool = False
    review: bool = False  # stop after the captions so they can be edited, render later
    facecam: str | None = None  # "" = off, auto, a corner, or x,y,w,h in percent (None = use config)


@dataclass
class HookContext:
    index: int
    total: int
    start: float  # seconds in the source video
    end: float
    transcript: str
    language: str
    suggestions: list[str] = field(default_factory=list)


@dataclass
class Prepared:
    video: VideoInfo
    folders: list[Path] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)


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
    if opts.facecam is not None:
        cfg.style.facecam = opts.facecam
    facecam.parse_hint(cfg.style.facecam)  # a readable error now rather than halfway through
    if opts.lang not in (None, "", "auto") and opts.lang not in ("en", "hi"):
        raise ClipperError("Caption language must be auto, en or hi")
    validate(cfg)
    return cfg


def load_source(source: str, cfg: Config, refresh: bool = False, rep: Reporter | None = None) -> tuple[VideoInfo, Path | None]:
    rep = rep or Reporter()
    if is_url(source):
        with rep.step("Reading video info from YouTube"):
            return fetch_info(normalize_url(source), cfg, refresh=refresh), None
    path = Path(source.strip().strip('"')).expanduser()
    if not path.is_file():
        raise ClipperError(f"That's not a link, and there's no file called: {source}")
    info = media.probe(path)
    if not info.has_video:
        raise ClipperError("That file has no video in it.")
    tag = hashlib.sha1(str(path.resolve()).encode("utf-8")).hexdigest()[:6]
    video = VideoInfo(
        id=f"{slugify(path.stem, 30, 'local')}-{tag}", url=str(path), title=path.stem, channel="", handle="",
        duration=info.duration, local_path=str(path.resolve()),
    )
    return video, path


def _slices(opts: MakeOptions) -> list[tuple[float, float | None]]:
    if opts.slices:
        return list(opts.slices)
    if opts.start is not None:
        return [(opts.start, opts.end)]
    return []


def choose_moments(video: VideoInfo, local: Path | None, cfg: Config, opts: MakeOptions, rep: Reporter) -> list[Moment]:
    slices = _slices(opts)
    if slices:
        moments = []
        for start, end in slices:
            end = end if end is not None else start + cfg.clip.target_seconds
            if video.duration and start >= video.duration:
                raise ClipperError(f"Clip time {fmt_time(start)} is after the end of the video ({fmt_time(video.duration)}).")
            if end <= start:
                raise ClipperError(f"Clip time {fmt_time(start)}-{fmt_time(end)}: the end must be after the start.")
            end = min(end, video.duration) if video.duration else end
            if end - start > 60:
                rep.warn(f"{fmt_time(start)}-{fmt_time(end)} is over 60 seconds: YouTube blocks such Shorts if anyone files a copyright claim.")
            moments.append(manual_moment(start, end))
        return moments

    count = max(1, opts.count)
    if video.heatmap:
        masked: list = []
        if cfg.clip.use_sponsorblock and not video.is_local:
            with rep.step("Checking SponsorBlock for sponsor and intro segments"):
                masked, status = sponsorblock.fetch_segments(video.id)
            rep.info(f"SponsorBlock: {status}")
        moments = find_moments(video.heatmap, video.duration, cfg.clip, masked, count)
        if moments:
            return moments
        rep.warn("The 'Most replayed' graph is flat (no clear spike).")
    elif not video.is_local:
        rep.info("No 'Most replayed' data yet (new video or few views): looking at what viewers point at instead.")

    if not video.is_local and video.duration:
        if video.was_live:
            heat = []
            with rep.step("Reading the live-chat replay (long streams take a few minutes)"):
                try:
                    chat = signals.fetch_chat_replay(video.url, video.id, cfg)
                    heat = signals.chat_heatmap(chat, video.duration) if chat else []
                except ClipperError as exc:
                    rep.warn(str(exc).splitlines()[0])
            moments = find_moments(heat, video.duration, cfg.clip, (), count, source="chat") if heat else []
            if moments:
                return moments
        heat = []
        with rep.step("Reading comments for timestamps viewers mention"):
            try:
                heat = signals.comments_heatmap(signals.fetch_comments(video.url, video.id, cfg), video.duration)
            except ClipperError as exc:
                rep.warn(str(exc).splitlines()[0])
        moments = find_moments(heat, video.duration, cfg.clip, (), count, source="comments") if heat else []
        if moments:
            return moments
        rep.warn("Not enough timestamp comments yet. Using the loudest moments, which is a weaker signal.")

    with rep.step("Finding the loudest moments"):
        audio_file = local or download_audio(video.url, video.id, cfg)
        mono = media.decode_audio(audio_file, sr=4000)
        times, db_values = envelope_db(mono, 4000, hop=0.25, window=0.5)
        duration = video.duration or (float(times[-1]) if len(times) else 0.0)
        heat = pseudo_heatmap(times, db_values, bucket=2.0, smooth=6.0)
        moments = find_moments(heat, duration, cfg.clip, (), count, source="loudness")
    if not moments:
        raise ClipperError("Couldn't find a moment that stands out. Put your own clip times in.")
    return moments


@dataclass
class BetterWords:
    """Caption words from Gemini, already placed on the local model's timings."""

    language: str
    native: list[Word]  # normal script (Hindi in Devanagari)
    latin: list[Word]  # English letters
    model: str


def words_from_gemini(result: gemini.GeminiWords, clip_words: list[Word]) -> tuple[BetterWords | None, str]:
    keys = [align.key(latin) for _text, latin in result.words]
    native = align.align([text for text, _latin in result.words], keys, clip_words)
    latin = align.align([latin for _text, latin in result.words], keys, clip_words)
    if native is None or latin is None:
        return None, "Gemini's words didn't line up with the speech, kept the local words"
    language = "hi" if result.language in ("hi", "ur") else result.language
    return BetterWords(language, native, latin, result.model), f"caption words from Gemini ({result.model})"


def caption_words(words: list[Word], transcript: Transcript, hindi_script: str,
                  better: BetterWords | None = None) -> tuple[str, list[Word]]:
    """(script, words) for the captions: Hindi in English letters by default, or in Devanagari if chosen."""
    if better is not None and not transcript.translated:
        language, native, latin = better.language, better.native, better.latin
    else:
        language, native = transcript.language, words
        latin = [Word(w.start, w.end, to_latin(w.text), w.prob) for w in words]
    if language != "hi" or transcript.translated or not native:
        return "", native
    if hindi_script == "devanagari":
        return "devanagari", native
    return "latin", latin


def _describe(m: Moment) -> str:
    return {
        "heatmap": f"replay spike at {fmt_time(m.peak_time)}",
        "chat": f"live-chat burst at {fmt_time(m.peak_time)}",
        "comments": f"viewers' timestamps around {fmt_time(m.peak_time)}",
        "loudness": f"loudest part around {fmt_time(m.peak_time)}",
    }.get(m.source, "your clip times")


def prepare(source: str, opts: MakeOptions, cfg: Config, rep: Reporter | None = None) -> Prepared:
    """Step 1 for every clip of one video. Returns the clip folders (with captions.srt, hook.txt, post.txt)."""
    rep = rep or Reporter()
    cfg = _with_overrides(cfg, opts)
    media.init(cfg.ffmpeg)
    video, local = load_source(source, cfg, opts.refresh, rep)
    if not video.is_local:
        rep.info(f"{video.title} | {video.channel} | {fmt_time(video.duration)}")
    moments = choose_moments(video, local, cfg, opts, rep)
    cache = cfg.path(cfg.cache_dir)
    result = Prepared(video)
    for index, moment in enumerate(moments, 1):
        if not opts.force and not video.is_local:
            earlier = db.find_overlap(cache, video.id, moment.start, moment.end)
            if earlier:
                result.skipped.append(f"{fmt_time(moment.start)}-{fmt_time(moment.end)} was already clipped")
                rep.warn(f"Skipped {fmt_time(moment.start)}-{fmt_time(moment.end)}: already clipped ({earlier}).")
                continue
        result.folders.append(prepare_clip(video, local, moment, index, len(moments), cfg, opts, rep))
    return result


def _pick_hook(opts: MakeOptions, index: int) -> str | None:
    if opts.hooks and index - 1 < len(opts.hooks) and opts.hooks[index - 1] is not None:
        return opts.hooks[index - 1]
    return opts.hook


def prepare_clip(
    video: VideoInfo, local: Path | None, moment: Moment, index: int, total: int, cfg: Config, opts: MakeOptions, rep: Reporter
) -> Path:
    cache = cfg.path(cfg.cache_dir)
    dl_start = max(0.0, moment.start - cfg.clip.padding_seconds)
    dl_end = moment.end + cfg.clip.padding_seconds
    if video.duration:
        dl_end = min(video.duration, dl_end)
    rep.info(f"Clip {index}/{total}: around {fmt_time(moment.start)}-{fmt_time(moment.end)} ({_describe(moment)})")

    with rep.step("Cutting this part of the video" if local else "Downloading just this part of the video"):
        if local:
            segment = cut_local(local, dl_start, dl_end, cache / "videos" / video.id)
        else:
            segment = download_segment(video.url, video.id, dl_start, dl_end, cfg)
    seg = media.probe(segment)
    if not seg.has_video or seg.duration <= 0:
        raise ClipperError("The downloaded part has no usable video.")

    with rep.step("Finding scene cuts and the most intense moment"):
        mono = media.decode_audio(segment, sr=16000) if seg.has_audio else np.zeros(int(seg.duration * 16000), np.float32)
        env_t, env_db = envelope_db(mono, 16000)
        cuts = media.scene_cuts(segment)

    game = package.detect_game(f"{video.title} {_pick_hook(opts, index) or ''}", cfg.games, opts.game)
    game_cfg = game[1] if game else None
    vocabulary = list(cfg.captions.vocabulary) + (list(game_cfg.vocabulary) if game_cfg else [])

    transcript = Transcript(note="captions turned off")
    if cfg.captions.enabled and seg.has_audio:
        with rep.step("Listening for speech to make captions (first run: downloads the model, tests the GPU)"):
            language = None if opts.lang in (None, "", "auto") else opts.lang
            transcript = transcribe(mono, cfg.captions, language=language, cache_dir=cache,
                                    vocabulary=list(game_cfg.vocabulary) if game_cfg else [])
        if transcript.removed:
            rep.info(f"Dropped {transcript.removed} line(s) the speech model made up (like 'Thanks for watching').")
        if transcript.words:
            rep.info(f"Speech: {len(transcript.words)} words, language '{transcript.language}', {transcript.engine}"
                     + (" (translated to English)" if transcript.translated else ""))
            if transcript.note and transcript.note != "no NVIDIA GPU found":  # a GPU exists but couldn't be used
                rep.warn(f"Speech model is on the CPU: {transcript.note}")
        else:
            rep.info(f"Speech: none ({transcript.note or 'no words recognised'})")

    refined = refine_window(
        moment.start - dl_start, moment.end - dl_start,
        words=transcript.words, cuts=cuts, env_times=env_t, env_db=env_db,
        seg_duration=seg.duration, cfg=cfg.clip, wide=moment.wide, manual=moment.source == "manual",
    )
    s, e = refined.start, refined.end
    source_start, source_end = dl_start + s, dl_start + e
    clip_words = [
        Word(max(0.0, w.start - s), min(e - s, w.end - s), w.text, w.prob)
        for w in transcript.words
        if w.end > s + 0.05 and w.start < e - 0.05
    ]

    cam = None
    hint = facecam.parse_hint(cfg.style.facecam)
    if hint is not None:
        with rep.step("Finding the streamer's webcam (facecam)"):
            cam = facecam.find_facecam(segment, s, e, seg.width, seg.height, hint)
        if cam:
            rep.info(f"Facecam: {cam.how}")
        else:
            rep.warn("No webcam found in this clip, so it uses the normal layout.")

    # one Gemini request: better caption words + hook + titles + description
    gem, better, gemini_note = None, None, ""
    if opts.gemini is not False and gemini.available(cfg.captions.gemini):
        with rep.step("Asking Gemini (free tier) for caption words, a hook and titles"):
            frames = [f for f in (media.frame_jpeg(segment, s + (e - s) * k) for k in (0.15, 0.4, 0.65, 0.9)) if f]
            context = (f"Video title: {video.title}\nChannel: {video.channel or 'unknown'}\n"
                       f"Game: {game_cfg.name if game_cfg else 'unknown'}")
            if vocabulary:
                context += "\nNames and words that may be said: " + ", ".join(dict.fromkeys(vocabulary))
            try:
                gem = gemini.transcribe_clip(mono[int(s * 16000): int(e * 16000)], cfg.captions.gemini,
                                             frames=frames, context=context)
                gemini_note = f"hook and titles from Gemini ({gem.model})"
            except gemini.GeminiError as exc:
                gemini_note = f"Gemini not used: {exc}"
        if gem and gem.words and clip_words and cfg.captions.enabled and not transcript.translated:
            better, words_note = words_from_gemini(gem, clip_words)
            if better:  # swears Gemini censored or softened: take them from the local model's words
                restored = profanity.restore(better.native, clip_words)
                profanity.restore(better.latin, clip_words, to_text=to_latin)
                if restored:
                    words_note += f" ({restored} swear word{'s' if restored > 1 else ''} put back as said)"
            gemini_note = f"{words_note}; {gemini_note}"
        (rep.info if gem else rep.warn)(gemini_note)

    script, chosen = caption_words(clip_words, transcript, cfg.captions.hindi_script, better)
    lines = captions.make_lines(chosen, cfg.style, cfg.fonts_dir, e - s) if cfg.captions.enabled else []
    spoken = " ".join(line.text for line in lines)

    hook = _pick_hook(opts, index)
    hook_source = "you"
    if hook is None:
        ideas = ([gem.hook] if gem and gem.hook else []) + package.hook_suggestions(
            video, chapter_at(video, dl_start + refined.payoff), spoken)
        if script == "latin":
            ideas = [to_latin(i) if has_devanagari(i) else i for i in ideas]
        ideas = list(dict.fromkeys(i for i in ideas if i and not profanity.has_swear(i)))
        hook_source = "gemini" if gem and gem.hook else "video title"
        if opts.prompt:
            answer = rep.ask_hook(HookContext(index, total, source_start, source_end, spoken, transcript.language, ideas))
            if answer is not None and answer != (ideas[0] if ideas else ""):
                hook_source = "you"
            hook = answer
        else:
            hook = ideas[0] if ideas else ""
    hook = (hook or "").strip()

    tags = package.hashtags_for(game_cfg, cfg.default_hashtags)
    for tag in (gem.hashtags if gem else []):
        if tag.lower() not in {t.lower() for t in tags} and not profanity.has_swear_root(tag):
            tags.insert(len(tags) - 1, tag)
    clean_titles = [t for t in (gem.titles if gem else []) if not profanity.has_swear(t)]
    if clean_titles:  # YouTube gives no ads to a title with strong language: never offer one
        lead = tags[0] if tags and tags[0].lower() != "#shorts" else ""
        titles = [package._fit_title(f"{t} {lead} #shorts") for t in clean_titles]
        titles_source = "gemini"
    else:
        titles = package.title_options(hook, game_cfg, cfg.title_templates, tags)
        titles_source = "template"

    track = audio.pick_track(cfg.path(cfg.music.folder), opts.music) if cfg.music.enabled else None
    if cfg.music.enabled and track is None:
        rep.warn(f"No music files in {cfg.path(cfg.music.folder)}, so this clip has no music.")

    named = (moment.start, moment.end) if moment.source == "manual" else (source_start, source_end)
    folder = package.clip_folder(package.video_folder(cfg.path(cfg.output_dir), video), *named)
    captions.write_srt(lines, folder / CAPTIONS_FILE)
    (folder / HOOK_FILE).write_text(hook + "\n", encoding="utf-8")
    job = {
        "version": 2,
        "state": "prepared",
        "prepared_at": datetime.now().isoformat(timespec="seconds"),
        "tool_version": __version__,
        "video": asdict(video),
        "segment": str(Path(segment).resolve()),
        "segment_offset": dl_start,
        "clip_start": s,
        "clip_end": e,
        "source_start": round(source_start, 3),
        "source_end": round(source_end, 3),
        "payoff": round(refined.payoff - s, 3),
        "has_audio": seg.has_audio,
        "width": seg.width,
        "height": seg.height,
        "fps": seg.fps,
        "moment": moment.to_dict(),
        "refine_notes": refined.notes,
        "captions": {
            "enabled": cfg.captions.enabled, "language": transcript.language, "script": script,
            "probability": round(transcript.probability, 3), "translated": transcript.translated,
            "engine": transcript.engine, "note": transcript.note, "made_up_lines_dropped": transcript.removed,
            "words_from": f"gemini:{better.model}" if better else "local", "gemini_note": gemini_note,
        },
        "lines": [{"start": ln.start, "end": ln.end, "words": [[w.start, w.end, w.text] for w in ln.words]} for ln in lines],
        "hook": hook,
        "hook_source": hook_source,
        "titles": titles,
        "titles_source": titles_source,
        "summary": gem.description if gem else "",
        "hashtags": tags,
        "game": game[0] if game else None,
        "music": {"path": str(track.path), "credit": track.credit} if track else None,
        "settings": {"layout": cfg.style.layout, "zoom": cfg.style.zoom, "credit": cfg.style.credit,
                     "captions": cfg.captions.enabled, "music": opts.music, "facecam": cfg.style.facecam},
        "facecam": _facecam_record(cfg.style.facecam, cam),
    }
    (folder / JOB_FILE).write_text(json.dumps(job, ensure_ascii=False, indent=1), encoding="utf-8")
    _write_post(folder, job, cfg, review=True)
    try:  # a nice-to-have: never lose the prepared clip over it
        with rep.step("Making a preview picture of the Short"):
            _preview(folder, job, cfg, lines, hook, video, segment)
    except Exception as exc:  # noqa: BLE001
        rep.warn(f"Couldn't make preview.jpg ({exc}); the clip itself is fine.")
    if not video.is_local:
        db.record(cache, video.id, source_start, source_end, folder, hook)
    return folder


def _facecam_record(setting: str, cam: facecam.FacecamBox | None) -> dict | None:
    if facecam.parse_hint(setting) is None:  # "" or Off
        return None
    return {"setting": setting, "box": cam.as_list() if cam else None, "face": list(cam.face) if cam and cam.face else None,
            "how": cam.how if cam else "no webcam found"}


def _layout_for(job: dict, cfg: Config):
    cam = job.get("facecam") or {}
    if cam.get("box"):
        return build_layout(cfg.style.layout, job["width"], job["height"], cfg.style.zoom, facecam=cam["box"],
                            share=cfg.style.facecam_share, face=tuple(cam["face"]) if cam.get("face") else None)
    return build_layout(cfg.style.layout, job["width"], job["height"], cfg.style.zoom)


def _overlay_files(work: Path, cfg: Config, layout, lines, duration: float, hook: str, video: VideoInfo) -> None:
    """Fonts and the subtitle file (hook, captions, credit) that FFmpeg needs inside the work folder."""
    fonts = work / "fonts"
    fonts.mkdir(parents=True, exist_ok=True)
    for name in {cfg.style.font_file, cfg.style.credit_font_file}:
        src = cfg.fonts_dir / name
        if not src.is_file():
            raise ClipperError(f"Font file missing: {src}")
        shutil.copy2(src, fonts / name)
    (work / "clip.ass").write_text(
        captions.build_ass(lines, layout=layout, style=cfg.style, fonts_dir=cfg.fonts_dir, duration=duration,
                           hook=hook or None, credit=package.credit_line(cfg, video),
                           profanity=cfg.captions.profanity),
        encoding="utf-8",
    )


def _work_dir(cfg: Config, video: VideoInfo, source_start: float) -> Path:
    work = cfg.path(cfg.cache_dir) / "work" / f"{slugify(video.id, 30, 'clip')}_{int(source_start)}_{int(time.time() * 1000) % 10**8}"
    work.mkdir(parents=True, exist_ok=True)
    return work


def _preview(folder: Path, job: dict, cfg: Config, lines, hook: str, video: VideoInfo, segment: Path) -> None:
    """preview.jpg: one frame of the Short as it will look, to check the layout while checking captions."""
    work = _work_dir(cfg, video, job["source_start"])
    try:
        layout = _layout_for(job, cfg)
        _overlay_files(work, cfg, layout, lines, job["clip_end"] - job["clip_start"], hook, video)
        render.preview_frame(segment, job["clip_start"], min(job["payoff"], job["clip_end"] - job["clip_start"] - 0.1),
                             layout, work, "clip.ass", folder / "preview.jpg")
    finally:
        if not cfg.render.keep_work_files:
            shutil.rmtree(work, ignore_errors=True)


def _write_post(folder: Path, job: dict, cfg: Config, review: bool) -> None:
    video = VideoInfo(**job["video"])
    hook = job.get("hook") or None
    titles = job["titles"]
    if job.get("titles_source") == "template":
        game = cfg.games.get(job.get("game") or "")
        titles = package.title_options(hook, game, cfg.title_templates, job["hashtags"])
    credit_on = job["settings"].get("credit", True) and not video.is_local
    music = (job.get("music") or {}).get("credit")
    desc = package.description(hook, video, job["source_start"], music, job["hashtags"], credit_on, job.get("summary", ""))
    notes = list(job.get("refine_notes", []))
    if job["captions"].get("gemini_note"):
        notes.append(job["captions"]["gemini_note"])
    if job["captions"].get("script") == "latin":
        notes.append("Captions: Hindi in English letters.")
    top = ("NOT RENDERED YET. Fix the words in captions.srt and the hook in hook.txt if needed, then set the row's "
           "Status to Ready in clips.xlsx (or run: clipper render \"this folder\").") if review else ""
    package.write_post(folder, titles, desc, video, job["source_start"], job["source_end"], notes, top_note=top)


def read_hook(folder: Path, job: dict) -> str:
    path = folder / HOOK_FILE
    if not path.exists():
        return job.get("hook") or ""
    rows = [r.strip() for r in path.read_text(encoding="utf-8-sig").splitlines() if r.strip()]
    return " | ".join(rows)  # several lines in hook.txt = several lines on screen


def render_clip(folder: Path, cfg: Config, rep: Reporter | None = None, overrides: dict | None = None) -> Path:
    """Step 2: make clip.mp4 from a prepared folder, using the (edited) captions.srt and hook.txt."""
    rep = rep or Reporter()
    folder = Path(folder)
    job_path = folder / JOB_FILE
    if not job_path.exists():
        raise ClipperError(f"{folder} isn't a prepared clip folder (no {JOB_FILE}).")
    job = json.loads(job_path.read_text(encoding="utf-8"))
    settings = dict(job["settings"])
    music_changed = False
    for key, value in (overrides or {}).items():
        if value is not None and settings.get(key) != value:
            music_changed |= key == "music"
            settings[key] = value
    cfg = copy.deepcopy(cfg)
    cfg.style.layout, cfg.style.zoom = settings["layout"], settings["zoom"]
    cfg.style.credit, cfg.captions.enabled = settings["credit"], settings["captions"]
    cfg.style.facecam = settings.get("facecam") or ""
    validate(cfg)
    media.init(cfg.ffmpeg)

    segment = Path(job["segment"])
    if not segment.exists():
        raise ClipperError("The downloaded part of this video is no longer in the cache folder. "
                           "Set the row's Status to Redo to make this clip again.")
    video = VideoInfo(**job["video"])
    s, e = job["clip_start"], job["clip_end"]
    duration = e - s
    hook = read_hook(folder, job)

    lines: list[captions.CaptionLine] = []
    if cfg.captions.enabled:
        previous = [captions.CaptionLine(ln["start"], ln["end"], [Word(a, b, t) for a, b, t in ln["words"]]) for ln in job["lines"]]
        srt = folder / CAPTIONS_FILE
        lines = captions.lines_from_srt(captions.read_srt(srt), previous) if srt.exists() else previous
    spoken = [w for line in lines for w in line.words]

    if music_changed:
        choice = settings.get("music")
        track = None if (choice or "").lower() == "none" else audio.pick_track(cfg.path(cfg.music.folder), choice)
        job["music"] = {"path": str(track.path), "credit": track.credit} if track else None
    track = None
    if job.get("music") and Path(job["music"]["path"]).exists():
        track = audio.MusicTrack(Path(job["music"]["path"]), job["music"]["credit"])

    setting = settings.get("facecam") or ""
    if (job.get("facecam") or {}).get("setting", "") != setting:  # changed in the sheet after the captions were made
        hint = facecam.parse_hint(setting)
        cam = None
        if hint is not None:
            with rep.step("Finding the streamer's webcam (facecam)"):
                cam = facecam.find_facecam(segment, s, e, job["width"], job["height"], hint)
        job["facecam"] = _facecam_record(setting if hint is not None else "", cam)

    work = _work_dir(cfg, video, job["source_start"])
    try:
        layout = _layout_for(job, cfg)
        _overlay_files(work, cfg, layout, lines, duration, hook, video)
        with rep.step("Mixing game audio and music"):
            bleeps = profanity.bleep_spans(spoken, cfg.captions.profanity) if cfg.captions.bleep == "all" else []
            mixed = audio.prepare_audio(segment, job["has_audio"], s, e, spoken, track, cfg.music, cfg.render, work,
                                        bleeps=bleeps)
        for old in ("clip.mp4", "clip_nomusic.mp4", "thumb.jpg", "preview.jpg"):
            (folder / old).unlink(missing_ok=True)
        with rep.step("Rendering the Short"):
            encoder = render.render_video(segment, s, e, layout, work, mixed.mixed, folder / "clip.mp4", cfg.render,
                                          job["fps"], ass_name="clip.ass")
            if mixed.track and cfg.music.export_without_music:
                render.replace_audio(folder / "clip.mp4", mixed.game, folder / "clip_nomusic.mp4")
            render.thumbnail(folder / "clip.mp4", job["payoff"], folder / "thumb.jpg")
    finally:
        if not cfg.render.keep_work_files:
            shutil.rmtree(work, ignore_errors=True)

    job.update(state="rendered", rendered_at=datetime.now().isoformat(timespec="seconds"), hook=hook,
               settings=settings, video_encoder=encoder)
    job_path.write_text(json.dumps(job, ensure_ascii=False, indent=1), encoding="utf-8")
    _write_post(folder, job, cfg, review=False)
    package.write_meta(folder, {
        "tool_version": __version__,
        "created_at": job["rendered_at"],
        "source": {k: job["video"].get(k) for k in ("id", "url", "title", "channel", "handle", "view_count", "upload_date")},
        "clip": {"source_start": job["source_start"], "source_end": job["source_end"], "duration": round(duration, 3),
                 "payoff_in_clip": job["payoff"]},
        "moment": job["moment"],
        "refine_notes": job["refine_notes"],
        "hook": hook,
        "hook_source": job["hook_source"],
        "titles": job["titles"],
        "game": job["game"],
        "captions": {**job["captions"], "lines": len(lines), "words": len(spoken)},
        "layout": {"name": layout.name, "zoom": cfg.style.zoom},
        "facecam": job.get("facecam"),
        "video_encoder": encoder,
        "music": None if not mixed.track else {
            "file": mixed.track.path.name, "credit": mixed.track.credit,
            "start_in_track": round(mixed.music_offset, 2), "gain_db": round(mixed.music_gain_db, 2),
        },
        "files": sorted(p.name for p in folder.iterdir()),
    })
    return folder / "clip.mp4"


def make(source: str, opts: MakeOptions, cfg: Config, rep: Reporter | None = None) -> list[Path]:
    """Both steps for one video (the command line). With review on, only step 1."""
    rep = rep or Reporter()
    prepared = prepare(source, opts, cfg, rep)
    if opts.review:
        return prepared.folders
    cfg = _with_overrides(cfg, opts)
    for folder in prepared.folders:
        render_clip(folder, cfg, rep)
    return prepared.folders
