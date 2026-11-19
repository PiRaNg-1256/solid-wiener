"""9:16 layouts as FFmpeg filter chains, plus where the text goes for each layout."""

from __future__ import annotations

from dataclasses import dataclass

OUT_W, OUT_H = 1080, 1920
UI_BOTTOM = 1600  # YouTube's title / channel name overlay covers the screen below roughly this line
INSET = 0.03  # facecam layout: the webcam panel is cut this far inside the webcam box found, on each side


@dataclass
class Layout:
    name: str
    filter: str  # FFmpeg filters from the input video to a 1080x1920 frame (no output label)
    hook_top: int  # top edge of the hook text box
    caption_bottom: int  # bottom edge of the caption line
    credit_top: int
    gameplay: tuple[int, int, int, int]  # x, y, w, h of the sharp gameplay area
    hook_center: int | None = None  # if set, the hook box is centred on this line instead (captions move below it)


SCALE_FLAGS = "lanczos+accurate_rnd+full_chroma_int"  # sharpest standard scaler, colours kept at full detail


def enlarge(factor: float, cas: bool = True) -> tuple[str, str]:
    """(before, after) filters for a picture scaled up `factor` times: a light clean-up of compression blocks
    before (they would be enlarged too), adaptive sharpening after. Nothing for pictures made smaller."""
    before = "hqdn3d=1.2:1.2:3:3," if factor > 1.6 else ""
    if factor <= 1.08:
        return before, ""
    amount = min(0.7, 0.25 + 0.2 * (factor - 1))
    after = f",cas={amount:.2f}" if cas else f",unsharp=5:5:{amount + 0.3:.2f}:5:5:0"
    return before, after


def punch(src: str, out: str, w: int, h: int, windows, amount: float = 0.08) -> str:
    """Filters from [src] to [out]: during each (start, end) window the picture jumps `amount` closer (a 'zoom
    cut' on the big moment), then back. A second, enlarged copy is switched on by time, so the timing stays exact."""
    windows = [(a, b) for a, b in windows or [] if b > a]
    if not windows or amount <= 0:
        return f"[{src}]null[{out}]"
    zw, zh = _even(w * (1 + amount)), _even(h * (1 + amount))
    when = "+".join(f"between(t,{a:.3f},{b:.3f})" for a, b in windows)
    return (f"[{src}]split=2[{src}a][{src}b];"
            f"[{src}b]scale={zw}:{zh}:flags={SCALE_FLAGS},crop={w}:{h}:(iw-{w})/2:(ih-{h})/2[{src}z];"
            f"[{src}a][{src}z]overlay=0:0:enable='{when}'[{out}]")


def _even(x: float) -> int:
    """Even size of at least 2 pixels (video sizes must be even)."""
    return max(2, int(round(x / 2)) * 2)


def _pos(x: float) -> int:
    """Even position, 0 allowed."""
    return max(0, int(round(x / 2)) * 2)


def blur_layout(src_w: int, src_h: int, zoom: float = 1.25, cas: bool = True, punches=(), punch_amount: float = 0.08,
                shadow: bool = False) -> Layout:
    """Gameplay in the middle, a blurred copy of it filling the rest of the frame."""
    zoom = max(1.0, zoom)
    crop_w, crop_h = _even(src_w / zoom), _even(src_h)
    fg_w, fg_h = OUT_W, _even(OUT_W * crop_h / crop_w)
    max_fg_h = 1100
    if fg_h > max_fg_h:  # tall sources: limit the height and keep the aspect ratio
        fg_h, fg_w = max_fg_h, _even(max_fg_h * crop_w / crop_h)
    fg_x = (OUT_W - fg_w) // 2
    fg_y = _even(max(330, (OUT_H - fg_h) / 2 - 60))
    fg_bottom = fg_y + fg_h
    pre, post = enlarge(fg_w / crop_w, cas)
    depth = ""
    if shadow:  # a soft dark halo under the gameplay and darker corners: the gameplay looks lifted off the background
        q = 270 / OUT_W
        depth = (f"drawbox=x={int(fg_x * q) - 3}:y={int(fg_y * q) + 2}:w={int(fg_w * q) + 6}:h={int(fg_h * q) + 6}:"
                 "color=black@0.6:t=fill,")
    flt = (
        "split=2[clipbg][clipfg];"
        # blur a small copy then scale it up: same look, a fraction of the work
        f"[clipbg]scale=270:480:force_original_aspect_ratio=increase,crop=270:480,{depth}boxblur=10:2,"
        f"eq=brightness=-0.08:saturation=1.15,{'vignette=PI/5,' if shadow else ''}scale={OUT_W}:{OUT_H},setsar=1[clipbgo];"
        f"[clipfg]crop={crop_w}:{crop_h}:(iw-{crop_w})/2:(ih-{crop_h})/2,{pre}scale={fg_w}:{fg_h}:flags={SCALE_FLAGS}"
        f"{post},setsar=1[clipfgs];"
        + punch("clipfgs", "clipfgo", fg_w, fg_h, punches, punch_amount) + ";"
        f"[clipbgo][clipfgo]overlay={fg_x}:{fg_y}"
    )
    return Layout(
        name="blur",
        filter=flt,
        hook_top=max(180, fg_y - 300),
        caption_bottom=min(UI_BOTTOM - 40, fg_bottom + 260),
        credit_top=fg_bottom + 18,
        gameplay=(fg_x, fg_y, fg_w, fg_h),
    )


def crop_layout(src_w: int, src_h: int, cas: bool = True, punches=(), punch_amount: float = 0.08) -> Layout:
    """Fill the whole screen with the centre of the video (cuts off the sides of 16:9 footage)."""
    target = OUT_W / OUT_H
    if src_w / src_h > target:
        crop_w, crop_h = _even(src_h * target), _even(src_h)
    else:
        crop_w, crop_h = _even(src_w), _even(src_w / target)
    pre, post = enlarge(OUT_W / crop_w, cas)
    flt = f"crop={crop_w}:{crop_h}:(iw-{crop_w})/2:(ih-{crop_h})/2,{pre}scale={OUT_W}:{OUT_H}:flags={SCALE_FLAGS}{post},setsar=1"
    if punches:
        flt += "[clipcs];" + punch("clipcs", "clipco", OUT_W, OUT_H, punches, punch_amount) + ";[clipco]null"
    return Layout(name="crop", filter=flt, hook_top=220, caption_bottom=1400, credit_top=1480, gameplay=(0, 0, OUT_W, OUT_H))


def facecam_layout(
    src_w: int, src_h: int, cam: tuple[int, int, int, int], share: float = 0.5, face: tuple[float, float] | None = None,
    cas: bool = True, punches=(), punch_amount: float = 0.08,
) -> Layout:
    """Streamer's webcam enlarged on top, gameplay below. The webcam's original spot is kept out of the
    gameplay part (or blurred over if it can't be avoided), so the face never shows twice."""
    top_h = _even(OUT_H * min(0.6, max(0.3, share)))
    bot_h = OUT_H - top_h
    cx, cy, cw, ch = (float(v) for v in cam)
    fx, fy = face if face else (cx + cw / 2, cy + ch * 0.45)

    # webcam: trim it to the top panel's shape, keeping the face in view. Stay a little inside the box: a box found
    # a few pixels too big would show a strip of gameplay along the panel's edge (scaled up ~3x), while a few
    # pixels of webcam edge (often a frame graphic anyway) are never missed.
    bx, by, bw, bh = cx + INSET * cw, cy + INSET * ch, cw * (1 - 2 * INSET), ch * (1 - 2 * INSET)
    aspect = OUT_W / top_h
    if bw / bh > aspect:
        nw = bh * aspect
        nx = min(max(bx, fx - nw / 2), bx + bw - nw)
        crop = (nx, by, nw, bh)
    else:
        nh = bw / aspect
        ny = min(max(by, fy - 0.42 * nh), by + bh - nh)
        crop = (bx, ny, bw, nh)
    kx, ky, kw, kh = _pos(crop[0]), _pos(crop[1]), _even(crop[2]), _even(crop[3])
    kw, kh = min(kw, src_w - kx), min(kh, src_h - ky)

    # gameplay: the biggest piece with the bottom panel's shape, moved sideways away from the webcam
    g_aspect = OUT_W / bot_h
    gh = float(src_h)
    gw = gh * g_aspect
    if gw > src_w:
        gw = float(src_w)
        gh = gw / g_aspect
    gx, gy = (src_w - gw) / 2, (src_h - gh) / 2
    if gx < cx + cw and cx < gx + gw:
        if cx + cw / 2 > src_w / 2:  # webcam on the right: take the gameplay from its left
            gx = max(0.0, min(gx, cx - gw))
        else:
            gx = min(src_w - gw, max(gx, cx + cw))
    gx, gy, gw, gh = _pos(gx), _pos(gy), _even(gw), _even(gh)
    gw, gh = min(gw, src_w - gx), min(gh, src_h - gy)
    game = f"[clipgame]crop={gw}:{gh}:{gx}:{gy}"
    ox = min(gx + gw, cx + cw) - max(gx, cx)
    oy = min(gy + gh, cy + ch) - max(gy, cy)
    if ox > 2 and oy > 2:  # still in view: blur over it
        px, py = _pos(max(gx, cx) - gx), _pos(max(gy, cy) - gy)
        pw, ph = min(_even(ox), gw - px), min(_even(oy), gh - py)
        game += (f",split=2[clipga][clipgb];[clipgb]crop={pw}:{ph}:{px}:{py},boxblur=20:3[clipgp];"
                 f"[clipga][clipgp]overlay={px}:{py}")
    cam_pre, cam_post = enlarge(OUT_W / kw, cas)
    game_pre, game_post = enlarge(OUT_W / gw, cas)
    flt = (
        "split=2[clipcam][clipgame];"
        f"[clipcam]crop={kw}:{kh}:{kx}:{ky},{cam_pre}scale={OUT_W}:{top_h}:flags={SCALE_FLAGS}{cam_post},setsar=1[cliptop];"
        f"{game},{game_pre}scale={OUT_W}:{bot_h}:flags={SCALE_FLAGS}{game_post},setsar=1[clipbots];"
        + punch("clipbots", "clipbot", OUT_W, bot_h, punches, punch_amount) + ";"
        f"[cliptop][clipbot]vstack,drawbox=x=0:y={top_h - 3}:w={OUT_W}:h=6:color=black@0.5:t=fill"
    )
    return Layout(
        name="facecam",
        filter=flt,
        hook_top=150,
        caption_bottom=top_h + 80,  # no hook: captions sit on the line between webcam and gameplay
        credit_top=UI_BOTTOM - 120,
        gameplay=(0, top_h, OUT_W, bot_h),
        hook_center=top_h,  # the top of the screen is the streamer's face: the hook goes on the line instead
    )


def build_layout(name: str, src_w: int, src_h: int, zoom: float = 1.25, facecam: tuple | None = None,
                 share: float = 0.5, face: tuple[float, float] | None = None, cas: bool = True,
                 punches=(), punch_amount: float = 0.08, shadow: bool = False) -> Layout:
    """cas: FFmpeg has the 'cas' sharpening filter (any FFmpeg from 2021 on); otherwise 'unsharp' is used.
    punches: (start, end) times of zoom cuts on the gameplay. shadow: blur layout's soft shadow and vignette."""
    if src_w <= 0 or src_h <= 0:
        raise ValueError("source video has no size")
    if facecam is not None:
        return facecam_layout(src_w, src_h, tuple(int(v) for v in facecam), share, face, cas, punches, punch_amount)
    if src_w / src_h < 0.75 or name == "crop":  # already vertical (e.g. a Short) or chosen: fill the screen
        return crop_layout(src_w, src_h, cas, punches, punch_amount)
    return blur_layout(src_w, src_h, zoom, cas, punches, punch_amount, shadow)
