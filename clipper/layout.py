"""9:16 layouts as FFmpeg filter chains, plus where the text goes for each layout."""

from __future__ import annotations

from dataclasses import dataclass

OUT_W, OUT_H = 1080, 1920
UI_BOTTOM = 1600  # YouTube's title / channel name overlay covers the screen below roughly this line


@dataclass
class Layout:
    name: str
    filter: str  # FFmpeg filters from the input video to a 1080x1920 frame (no output label)
    hook_top: int  # top edge of the hook text box
    caption_bottom: int  # bottom edge of the caption line
    credit_top: int
    gameplay: tuple[int, int, int, int]  # x, y, w, h of the sharp gameplay area


def _even(x: float) -> int:
    """Even size of at least 2 pixels (video sizes must be even)."""
    return max(2, int(round(x / 2)) * 2)


def _pos(x: float) -> int:
    """Even position, 0 allowed."""
    return max(0, int(round(x / 2)) * 2)


def blur_layout(src_w: int, src_h: int, zoom: float = 1.25) -> Layout:
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
    flt = (
        "split=2[clipbg][clipfg];"
        # blur a small copy then scale it up: same look, a fraction of the work
        "[clipbg]scale=270:480:force_original_aspect_ratio=increase,crop=270:480,boxblur=10:2,"
        f"eq=brightness=-0.08:saturation=1.15,scale={OUT_W}:{OUT_H},setsar=1[clipbgo];"
        f"[clipfg]crop={crop_w}:{crop_h}:(iw-{crop_w})/2:(ih-{crop_h})/2,scale={fg_w}:{fg_h}:flags=lanczos,setsar=1[clipfgo];"
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


def crop_layout(src_w: int, src_h: int) -> Layout:
    """Fill the whole screen with the centre of the video (cuts off the sides of 16:9 footage)."""
    target = OUT_W / OUT_H
    if src_w / src_h > target:
        crop_w, crop_h = _even(src_h * target), _even(src_h)
    else:
        crop_w, crop_h = _even(src_w), _even(src_w / target)
    flt = f"crop={crop_w}:{crop_h}:(iw-{crop_w})/2:(ih-{crop_h})/2,scale={OUT_W}:{OUT_H}:flags=lanczos,setsar=1"
    return Layout(name="crop", filter=flt, hook_top=220, caption_bottom=1400, credit_top=1480, gameplay=(0, 0, OUT_W, OUT_H))


def facecam_layout(
    src_w: int, src_h: int, cam: tuple[int, int, int, int], share: float = 0.5, face: tuple[float, float] | None = None
) -> Layout:
    """Streamer's webcam enlarged on top, gameplay below. The webcam's original spot is kept out of the
    gameplay part (or blurred over if it can't be avoided), so the face never shows twice."""
    top_h = _even(OUT_H * min(0.6, max(0.3, share)))
    bot_h = OUT_H - top_h
    cx, cy, cw, ch = (float(v) for v in cam)
    fx, fy = face if face else (cx + cw / 2, cy + ch * 0.45)

    # webcam: trim it to the top panel's shape, keeping the face in view
    aspect = OUT_W / top_h
    if cw / ch > aspect:
        nw = ch * aspect
        nx = min(max(cx, fx - nw / 2), cx + cw - nw)
        crop = (nx, cy, nw, ch)
    else:
        nh = cw / aspect
        ny = min(max(cy, fy - 0.42 * nh), cy + ch - nh)
        crop = (cx, ny, cw, nh)
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
    flt = (
        "split=2[clipcam][clipgame];"
        f"[clipcam]crop={kw}:{kh}:{kx}:{ky},scale={OUT_W}:{top_h}:flags=lanczos,unsharp=5:5:0.6:5:5:0.0,setsar=1[cliptop];"
        f"{game},scale={OUT_W}:{bot_h}:flags=lanczos,setsar=1[clipbot];"
        f"[cliptop][clipbot]vstack,drawbox=x=0:y={top_h - 3}:w={OUT_W}:h=6:color=black@0.5:t=fill"
    )
    return Layout(
        name="facecam",
        filter=flt,
        hook_top=150,
        caption_bottom=top_h + 80,  # captions sit on the line between webcam and gameplay
        credit_top=UI_BOTTOM - 120,
        gameplay=(0, top_h, OUT_W, bot_h),
    )


def build_layout(name: str, src_w: int, src_h: int, zoom: float = 1.25, facecam: tuple | None = None,
                 share: float = 0.5, face: tuple[float, float] | None = None) -> Layout:
    if src_w <= 0 or src_h <= 0:
        raise ValueError("source video has no size")
    if facecam is not None:
        return facecam_layout(src_w, src_h, tuple(int(v) for v in facecam), share, face)
    if src_w / src_h < 0.75:  # already vertical (e.g. a Short): just fill the screen
        return crop_layout(src_w, src_h)
    if name == "crop":
        return crop_layout(src_w, src_h)
    return blur_layout(src_w, src_h, zoom)
