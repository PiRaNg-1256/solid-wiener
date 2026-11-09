"""Settings: built-in defaults, overridden by config.yaml in the project folder."""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from .util import ClipperError, hex_to_ass

PACKAGE_DIR = Path(__file__).resolve().parent
PROJECT_DIR = PACKAGE_DIR.parent


@dataclass
class ClipConfig:
    min_seconds: float = 20.0
    max_seconds: float = 59.0  # Shorts over 60 s with any copyright claim get blocked
    target_seconds: float = 30.0  # used when the replay spike is wider than max_seconds
    lead_in_seconds: float = 5.0  # start this much before the spike (the setup)
    tail_seconds: float = 3.0  # keep this much after the spike (the reaction)
    skip_start_percent: float = 3.0  # the very start of a video inflates the graph
    skip_end_percent: float = 2.0
    min_prominence: float = 0.08  # how much a spike must stand out (graph values are 0-1)
    min_gap_seconds: float = 60.0  # between two clips taken from the same video
    padding_seconds: float = 10.0  # extra footage downloaded around the moment
    use_sponsorblock: bool = True


@dataclass
class StyleConfig:
    layout: str = "blur"  # blur | crop
    zoom: float = 1.25  # blur layout: >1 makes the gameplay bigger by trimming the sides
    font_file: str = "Poppins-Black.ttf"  # in assets/fonts; must contain Hindi letters for Hindi captions
    credit_font_file: str = "Poppins-ExtraBold.ttf"
    caption_size: int = 84  # letter size in pixels on a 1080x1920 frame
    caption_color: str = "#FFFFFF"
    highlight_color: str = "#FFE600"
    caption_max_words: int = 3
    caption_uppercase: bool = True
    hook_size: int = 54
    hook_uppercase: bool = True
    credit_size: int = 28
    hook_seconds: float = 0.0  # 0 = show the hook for the whole clip
    credit: bool = True
    credit_text: str = "footage: {creator}"


@dataclass
class CaptionConfig:
    enabled: bool = True
    languages: list[str] = field(default_factory=lambda: ["en", "hi"])
    translate_to_english: bool = False
    model: str = "small"  # tiny | base | small | medium | large-v3
    device: str = "cpu"  # cpu | cuda | auto


@dataclass
class MusicConfig:
    enabled: bool = True
    folder: str = "assets/music"
    volume_db: float = -16.0  # music level relative to the game audio
    duck_db: float = -8.0  # extra cut while someone is talking
    export_without_music: bool = True


@dataclass
class RenderConfig:
    preset: str = "medium"  # x264 speed: veryfast | faster | fast | medium | slow
    crf: int = 19  # quality: lower = better and bigger
    max_fps: int = 60
    loudness_lufs: float = -14.0
    true_peak_db: float = -1.0
    keep_work_files: bool = False


@dataclass
class YouTubeConfig:
    cookies_from_browser: str = ""  # e.g. firefox (use a throwaway Google account)
    cookies_file: str = ""
    sleep_between_requests: float = 0.0


@dataclass
class GameConfig:
    name: str = ""
    keywords: list[str] = field(default_factory=list)
    hashtags: list[str] = field(default_factory=list)


def _default_games() -> dict[str, GameConfig]:
    return {
        "gta6": GameConfig(
            "GTA 6",
            ["gta 6", "gta vi", "gta6", "gtavi", "grand theft auto vi", "grand theft auto 6"],
            ["#GTA6", "#GTAVI", "#GrandTheftAuto"],
        ),
        "gta5": GameConfig(
            "GTA 5",
            ["gta 5", "gta v", "gta5", "gtav", "gta online", "grand theft auto v"],
            ["#GTA5", "#GTAV", "#GTAOnline"],
        ),
        "rdr2": GameConfig(
            "Red Dead Redemption 2",
            ["rdr2", "rdr 2", "red dead redemption 2", "red dead 2"],
            ["#RDR2", "#RedDeadRedemption2"],
        ),
    }


@dataclass
class Config:
    output_dir: str = "output"
    cache_dir: str = "cache"
    ffmpeg: str = ""  # path to ffmpeg if it isn't found automatically
    default_hashtags: list[str] = field(default_factory=lambda: ["#gaming", "#shorts"])
    title_templates: list[str] = field(
        default_factory=lambda: [
            "{hook} 😳 {tag} #shorts",
            "Wait for it… 💀 {tag} #shorts",
            "Nobody expected this in {game} 🤯 #shorts",
        ]
    )
    clip: ClipConfig = field(default_factory=ClipConfig)
    style: StyleConfig = field(default_factory=StyleConfig)
    captions: CaptionConfig = field(default_factory=CaptionConfig)
    music: MusicConfig = field(default_factory=MusicConfig)
    render: RenderConfig = field(default_factory=RenderConfig)
    youtube: YouTubeConfig = field(default_factory=YouTubeConfig)
    games: dict[str, GameConfig] = field(default_factory=_default_games)
    root: Path = PROJECT_DIR  # folder that relative paths are resolved against (not a YAML key)

    def path(self, value: str | Path) -> Path:
        p = Path(value).expanduser()
        return p if p.is_absolute() else self.root / p

    @property
    def fonts_dir(self) -> Path:
        return PROJECT_DIR / "assets" / "fonts"


def _coerce(current, value, name: str):
    if isinstance(current, bool):
        if isinstance(value, bool):
            return value
        raise ClipperError(f"Setting '{name}' must be true or false")
    if isinstance(current, int):
        try:
            return int(value)
        except (TypeError, ValueError):
            raise ClipperError(f"Setting '{name}' must be a whole number") from None
    if isinstance(current, float):
        try:
            return float(value)
        except (TypeError, ValueError):
            raise ClipperError(f"Setting '{name}' must be a number") from None
    if isinstance(current, str):
        return "" if value is None else str(value)
    if isinstance(current, list):
        if value is None:
            return []
        if isinstance(value, list):
            return [str(v) for v in value]
        return [str(value)]
    return value


def _merge(target, data: dict, where: str = "") -> None:
    if not isinstance(data, dict):
        raise ClipperError(f"Setting '{where.rstrip('.') or 'config'}' must be a group of settings")
    for key, value in data.items():
        name = f"{where}{key}"
        if key == "root" or not hasattr(target, key):
            raise ClipperError(f"Unknown setting '{name}' in config.yaml")
        current = getattr(target, key)
        if key == "games" and isinstance(current, dict):
            games = dict(current)
            for game_key, game_data in (value or {}).items():
                game = dataclasses.replace(games[game_key]) if game_key in games else GameConfig()
                _merge(game, game_data or {}, f"games.{game_key}.")
                games[str(game_key)] = game
            setattr(target, key, games)
        elif dataclasses.is_dataclass(current):
            _merge(current, value or {}, f"{name}.")
        else:
            setattr(target, key, _coerce(current, value, name))


def validate(cfg: Config) -> None:
    if cfg.style.layout not in ("blur", "crop"):
        raise ClipperError("style.layout must be 'blur' or 'crop'")
    if not 1.0 <= cfg.style.zoom <= 2.0:
        raise ClipperError("style.zoom must be between 1.0 and 2.0")
    if not 5 <= cfg.clip.min_seconds <= cfg.clip.max_seconds <= 180:
        raise ClipperError("clip lengths must satisfy 5 <= min_seconds <= max_seconds <= 180")
    if cfg.captions.device not in ("cpu", "cuda", "auto"):
        raise ClipperError("captions.device must be cpu, cuda or auto")
    for color in (cfg.style.caption_color, cfg.style.highlight_color):
        hex_to_ass(color)  # raises a readable error if the colour is malformed


def load_config(path: str | Path | None = None) -> Config:
    cfg = Config()
    config_path = Path(path) if path else PROJECT_DIR / "config.yaml"
    if path and not config_path.exists():
        raise ClipperError(f"Config file not found: {config_path}")
    if config_path.exists():
        try:
            data = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
        except yaml.YAMLError as exc:
            raise ClipperError(f"config.yaml has a formatting mistake:\n{exc}") from None
        _merge(cfg, data)
        cfg.root = config_path.resolve().parent
    validate(cfg)
    return cfg
