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
    return max(2, int(round(x / 2)) * 2)


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


def build_layout(name: str, src_w: int, src_h: int, zoom: float = 1.25) -> Layout:
    if src_w <= 0 or src_h <= 0:
        raise ValueError("source video has no size")
    if src_w / src_h < 0.75:  # already vertical (e.g. a Short): just fill the screen
        return crop_layout(src_w, src_h)
    if name == "crop":
        return crop_layout(src_w, src_h)
    return blur_layout(src_w, src_h, zoom)
