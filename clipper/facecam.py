"""Find a streamer's webcam (facecam) box in the video, for the split layout.

- "auto": look for a face that stays in the same place in most frames (game characters move around; the
  streamer's webcam doesn't), then find the webcam's rectangle around it.
- a corner ("bottom-right" ...): look for the webcam's rectangle in that corner, with or without a face.
- "x,y,w,h": an exact box in percent of the frame, e.g. "75,70,25,30".
The rectangle is found from its border: over many frames the gameplay moves but the webcam's edge stays put, so
the averaged picture has a strong, straight edge exactly there."""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from . import media
from .config import PROJECT_DIR
from .util import ClipperError

MODEL = PROJECT_DIR / "assets" / "models" / "face_detection_yunet_2023mar.onnx"
CORNERS = ("top-left", "top-right", "bottom-left", "bottom-right")


@dataclass
class FacecamBox:
    x: int
    y: int
    w: int
    h: int
    how: str
    face: tuple[float, float] | None = None  # centre of the face, in source pixels

    def as_list(self) -> list[int]:
        return [self.x, self.y, self.w, self.h]


def parse_hint(text: str | None):
    """'' -> None (off), 'auto', a corner name, or a (x, y, w, h) tuple in percent."""
    t = (text or "").strip().lower()
    if t in ("", "no", "off", "none", "false"):
        return None
    if t in ("auto", "yes", "on", "true"):
        return "auto"
    corner = re.sub(r"[\s_]+", "-", t)
    if corner in CORNERS:
        return corner
    numbers = re.split(r"[,;\s]+", t.replace("%", ""))
    try:
        box = tuple(float(n) for n in numbers if n)
    except ValueError:
        box = ()
    if len(box) == 4 and all(0 <= v <= 100 for v in box) and box[2] > 2 and box[3] > 2:
        return box
    raise ClipperError("Facecam must be Auto, a corner (Bottom right, Bottom left, Top right, Top left) or "
                       "x,y,w,h in percent of the frame (like 75,70,25,30)")


def sample_frames(path: Path, start: float, end: float, src_w: int, src_h: int, n: int = 12, width: int = 640) -> list[np.ndarray]:
    """n small BGR frames spread over [start, end]."""
    height = max(2, int(round(width * src_h / src_w / 2)) * 2)
    frames = []
    for k in range(n):
        t = start + (end - start) * (k + 0.5) / n
        cmd = [media.ffmpeg_bin(), "-hide_banner", "-nostdin", "-v", "error", "-ss", f"{max(0.0, t):.3f}", "-i", str(path),
               "-frames:v", "1", "-vf", f"scale={width}:{height}", "-f", "rawvideo", "-pix_fmt", "bgr24", "-"]
        out = subprocess.run(cmd, capture_output=True).stdout
        if len(out) == width * height * 3:
            frames.append(np.frombuffer(out, np.uint8).reshape(height, width, 3))
    return frames


_DETECTOR = None


def _detector(size: tuple[int, int]):
    global _DETECTOR
    import cv2

    try:
        cv2.utils.logging.setLogLevel(cv2.utils.logging.LOG_LEVEL_ERROR)
    except Exception:
        pass
    if _DETECTOR is None and MODEL.exists():
        _DETECTOR = cv2.FaceDetectorYN.create(str(MODEL), "", size, 0.75)
    if _DETECTOR is not None:
        _DETECTOR.setInputSize(size)
    return _DETECTOR


def detect_faces(frames: list[np.ndarray]) -> list[list[tuple[float, float, float, float, float]]]:
    """Faces per frame as (x, y, w, h, score)."""
    if not frames:
        return []
    h, w = frames[0].shape[:2]
    detector = _detector((w, h))
    found = []
    for frame in frames:
        faces = []
        if detector is not None:
            _, result = detector.detect(frame)
            for f in result if result is not None else []:
                faces.append((float(f[0]), float(f[1]), float(f[2]), float(f[3]), float(f[-1])))
        found.append(faces)
    return found


def stable_face(per_frame: list[list[tuple]], width: int, height: int, corner: str | None = None):
    """The face that sits in the same spot in most frames: (x, y, w, h, frames seen) or None."""
    detections = [(i, f) for i, faces in enumerate(per_frame) for f in faces]
    if corner:
        detections = [(i, f) for i, f in detections if _in_corner(f[0] + f[2] / 2, f[1] + f[3] / 2, width, height, corner)]
    groups: list[list[tuple[int, tuple]]] = []
    for i, f in detections:
        cx, cy = f[0] + f[2] / 2, f[1] + f[3] / 2
        for group in groups:
            gx = np.median([g[1][0] + g[1][2] / 2 for g in group])
            gy = np.median([g[1][1] + g[1][3] / 2 for g in group])
            if abs(cx - gx) < 0.06 * width and abs(cy - gy) < 0.06 * width:
                group.append((i, f))
                break
        else:
            groups.append([(i, f)])
    needed = max(3, int(0.5 * len(per_frame)))
    good = []
    for group in groups:
        seen = len({i for i, _ in group})
        centres = np.array([[f[0] + f[2] / 2, f[1] + f[3] / 2] for _, f in group])
        if seen >= needed and centres.std(axis=0).max() < 0.03 * width:
            good.append((seen, group))
    if not good:
        return None

    def corner_distance(group):
        cx = np.median([f[0] + f[2] / 2 for _, f in group]) / width
        cy = np.median([f[1] + f[3] / 2 for _, f in group]) / height
        return min(abs(cx - a) + abs(cy - b) for a in (0, 1) for b in (0, 1))

    seen, group = max(good, key=lambda g: (g[0], -corner_distance(g[1])))
    box = np.median(np.array([f[:4] for _, f in group]), axis=0)
    return (*[float(v) for v in box], seen)


def _in_corner(x: float, y: float, width: int, height: int, corner: str) -> bool:
    right, bottom = x > width / 2, y > height / 2
    return {"top-left": not right and not bottom, "top-right": right and not bottom,
            "bottom-left": not right and bottom, "bottom-right": right and bottom}[corner]


def edge_maps(frames: list[np.ndarray]) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """Two clues for the webcam's border, each as vertical-edge and horizontal-edge maps:
    1. edges that are there in every frame (moving gameplay averages out, the webcam's edge stays);
    2. a jump in how much pixels change over time (gameplay changes a lot, a webcam's room hardly at all)."""
    grays = np.stack([f.astype(np.float32).mean(axis=2) for f in frames])
    vx = np.zeros(grays.shape[1:], np.float32)
    hy = np.zeros(grays.shape[1:], np.float32)
    vx[:, 1:] = np.abs(np.diff(grays, axis=2)).mean(axis=0)
    hy[1:, :] = np.abs(np.diff(grays, axis=1)).mean(axis=0)
    change = grays.std(axis=0)
    sx = np.zeros_like(change)
    sy = np.zeros_like(change)
    sx[:, 1:] = np.abs(np.diff(change, axis=1))
    sy[1:, :] = np.abs(np.diff(change, axis=0))
    return vx, hy, sx, sy


def _ridge(profile: np.ndarray, k: int = 9) -> np.ndarray:
    """How much each position stands out from its neighbours: a border is a sharp line, texture is not."""
    pad = np.pad(profile, k // 2, mode="edge")
    windows = np.lib.stride_tricks.sliding_window_view(pad, k)
    return profile - np.median(windows, axis=1)


def _candidates(profiles: list[np.ndarray], lo: float, hi: float, edge: int | None, keep: int = 4) -> list[int]:
    """The few positions in [lo, hi) that look most like a straight border line (+ the frame edge if allowed)."""
    lo, hi = max(1, int(lo)), min(len(profiles[0]) - 1, int(hi))
    found: list[int] = []
    if hi - lo >= 3:
        score = np.zeros(hi - lo)
        for profile in profiles:
            ridge = _ridge(profile)[lo:hi]
            score += np.clip(ridge / (float(np.median(np.abs(ridge))) + 1e-3), 0, 25)
        from scipy.signal import find_peaks

        peaks, props = find_peaks(score, height=4, distance=3)
        order = np.argsort(props["peak_heights"])[::-1][:keep] if len(peaks) else []
        found = [lo + int(peaks[i]) for i in order]
    if edge is not None:
        found.append(edge)
    return found


def _side_strength(maps, x0: int, y0: int, x1: int, y1: int, scales) -> list[float | None]:
    """How clearly each side of the rectangle is a border, measured along its whole length (None = frame edge)."""
    vx, hy, sx, sy = maps
    height, width = vx.shape
    iy0, iy1 = y0 + (y1 - y0) // 10, y1 - (y1 - y0) // 10
    ix0, ix1 = x0 + (x1 - x0) // 10, x1 - (x1 - x0) // 10

    def vertical(x):
        if x <= 0 or x >= width - 1 or iy1 <= iy0:
            return None
        band = slice(max(0, x - 1), x + 2)
        return float(vx[iy0:iy1, band].max(axis=1).mean() / scales[0] + sx[iy0:iy1, band].max(axis=1).mean() / scales[1])

    def horizontal(y):
        if y <= 0 or y >= height - 1 or ix1 <= ix0:
            return None
        band = slice(max(0, y - 1), y + 2)
        return float(hy[band, ix0:ix1].max(axis=0).mean() / scales[2] + sy[band, ix0:ix1].max(axis=0).mean() / scales[3])

    return [vertical(x0), vertical(x1), horizontal(y0), horizontal(y1)]


def fit_rectangle(maps, width: int, height: int, face=None, corner: str | None = None):
    """The rectangle whose four sides look most like a webcam's border. Must contain the face if there is one."""
    vx, hy, sx, sy = maps
    scales = [float(np.median(m)) + 1e-3 for m in maps]
    if face is not None:
        fx0, fy0, fx1, fy1 = face[0], face[1], face[0] + face[2], face[1] + face[3]
        px, py = 0.05 * face[2], 0.05 * face[3]
        rows = slice(int(max(0, fy0)), int(min(height, fy1)))
        cols = slice(int(max(0, fx0)), int(min(width, fx1)))
        ranges = [(0, fx0 - px), (fx1 + px, width), (0, fy0 - py), (fy1 + py, height)]
    else:
        right, bottom = corner.endswith("right"), corner.startswith("bottom")
        rows = slice(int(height * 0.55), height) if bottom else slice(0, int(height * 0.45))
        cols = slice(int(width * 0.55), width) if right else slice(0, int(width * 0.45))
        ranges = [(width * 0.3, width * 0.92) if right else (0, width * 0.1),
                  (width * 0.98, width) if right else (width * 0.08, width * 0.7),
                  (height * 0.3, height * 0.92) if bottom else (0, height * 0.1),
                  (height * 0.98, height) if bottom else (height * 0.08, height * 0.7)]
    col_profiles = [vx[rows].mean(axis=0), sx[rows].mean(axis=0)]
    row_profiles = [hy[:, cols].mean(axis=1), sy[:, cols].mean(axis=1)]
    lefts = _candidates(col_profiles, *ranges[0], edge=0)
    rights = _candidates(col_profiles, *ranges[1], edge=width)
    tops = _candidates(row_profiles, *ranges[2], edge=0)
    bottoms = _candidates(row_profiles, *ranges[3], edge=height)

    best, best_score = None, -1e9
    for x0 in lefts:
        for x1 in rights:
            w = x1 - x0
            if not 0.08 * width <= w <= 0.65 * width:
                continue
            for y0 in tops:
                for y1 in bottoms:
                    h = y1 - y0
                    if not 0.08 * height <= h <= 0.75 * height:
                        continue
                    sides = _side_strength(maps, x0, y0, x1, y1, scales)
                    real = [v for v in sides if v is not None]
                    if not real:
                        continue
                    average = sum(real) / len(real)
                    filled = [v if v is not None else average for v in sides]
                    score = sum(filled) / 4 + 0.15 * min(filled)  # a weak side counts against the whole box
                    aspect = w / h
                    if not 0.8 <= aspect <= 2.4:
                        score -= 0.5 * average
                    # a frame edge is only a guess (no border to see), so a real border nearby beats it
                    score -= 0.12 * average * (len(sides) - len(real))
                    if score > best_score:
                        best, best_score = (x0, y0, w, h), score
    return best


def _corner_box(corner: str, width: int, height: int) -> tuple[float, float, float, float]:
    w = 0.3 * width
    h = w * 9 / 16
    x = width - w if corner.endswith("right") else 0.0
    y = height - h if corner.startswith("bottom") else 0.0
    return x, y, w, h


def find_facecam(path: Path, start: float, end: float, src_w: int, src_h: int, hint) -> FacecamBox | None:
    """The webcam box in source pixels, or None when hint is 'auto' and no webcam is found."""
    if hint is None:
        return None
    if isinstance(hint, tuple):
        x, y, w, h = hint
        return _to_source((x / 100 * src_w, y / 100 * src_h, w / 100 * src_w, h / 100 * src_h), 1.0, src_w, src_h,
                          "your box")
    frames = sample_frames(path, start, end, src_w, src_h)
    if len(frames) < 3:
        return None
    height, width = frames[0].shape[:2]
    scale = src_w / width
    maps = edge_maps(frames)
    corner = hint if hint in CORNERS else None
    face = stable_face(detect_faces(frames), width, height, corner)
    if face is not None:
        fx, fy, fw, fh, seen = face
        rect = fit_rectangle(maps, width, height, face=(fx, fy, fw, fh))
        if rect is None:  # no border evidence at all: a typical webcam shape around the face
            bh = 2.7 * fh
            bw = bh * 16 / 9
            x0, y0 = max(0.0, fx + fw / 2 - bw / 2), max(0.0, fy + fh / 2 - 0.42 * bh)
            rect = (x0, y0, min(bw, width - x0), min(bh, height - y0))
        box = _to_source(rect, scale, src_w, src_h, f"webcam found from a face seen in {seen} of {len(frames)} frames")
        box.face = ((fx + fw / 2) * scale, (fy + fh / 2) * scale)
        return box
    if corner is None:
        return None
    rect = fit_rectangle(maps, width, height, corner=corner) or _corner_box(corner, width, height)
    return _to_source(rect, scale, src_w, src_h, f"{corner.replace('-', ' ')} corner")


def _to_source(box, scale: float, src_w: int, src_h: int, how: str) -> FacecamBox:
    x, y, w, h = (v * scale for v in box)
    x, y = max(0, int(round(x / 2)) * 2), max(0, int(round(y / 2)) * 2)
    w = max(16, min(int(round(w / 2)) * 2, src_w - x))
    h = max(16, min(int(round(h / 2)) * 2, src_h - y))
    return FacecamBox(x, y, w, h, how)
