"""The manual (README.md) and config.example.yaml stay complete: every command, option, setting and sheet column."""

from __future__ import annotations

import dataclasses
import re

import typer
import yaml

from clipper import cli, sheet
from clipper.config import PROJECT_DIR, Config

README = (PROJECT_DIR / "README.md").read_text(encoding="utf-8")
EXAMPLE = (PROJECT_DIR / "config.example.yaml").read_text(encoding="utf-8")
SKIP = {"root", "base_dir", "games", "captions.gemini.api_key"}  # internal, documented separately, or old


def settings(obj=None, prefix=""):
    obj = obj or Config()
    for f in dataclasses.fields(obj):
        name = prefix + f.name
        if name in SKIP:
            continue
        value = getattr(obj, f.name)
        if dataclasses.is_dataclass(value):
            yield from settings(value, name + ".")
        else:
            yield name, value


def test_every_command_and_option_is_in_the_manual():
    group = typer.main.get_command(cli.app)
    missing = []
    for name, command in group.commands.items():
        if f"### {name}:" not in README:
            missing.append(f"command {name}")
        for param in command.params:
            for opt in [*getattr(param, "opts", []), *getattr(param, "secondary_opts", [])]:
                if opt.startswith("--") and opt != "--help" and opt not in README:
                    missing.append(f"{name} {opt}")
    assert not missing, missing


def test_every_setting_is_in_the_manual_and_the_example():
    missing = [name for name, _ in settings() if f"`{name}`" not in README]
    assert not missing, f"not in README.md: {missing}"
    example = yaml.safe_load(EXAMPLE)
    wrong = []
    for name, default in settings():
        node = example
        for part in name.split("."):
            node = node.get(part) if isinstance(node, dict) else None
        if node is None and default not in ("", [], None):
            wrong.append(f"{name} missing")
        elif node is not None and isinstance(default, (int, float)) and not isinstance(default, bool):
            if float(node) != float(default):
                wrong.append(f"{name}: example {node} != default {default}")
        elif node is not None and str(node) != str(default) and list(map(str, node if isinstance(node, list) else [node])) \
                != list(map(str, default if isinstance(default, list) else [default])):
            wrong.append(f"{name}: example {node!r} != default {default!r}")
    assert not wrong, wrong


def test_every_sheet_column_is_in_the_manual():
    inputs = [c.header for c in sheet.COLUMNS if not c.output]
    missing = [h for h in inputs if f"**{h}**" not in README]
    assert not missing, missing


def test_manual_links_point_to_real_sections():
    anchors = set()
    for heading in re.findall(r"^#{2,3} (.+)$", README, re.M):
        anchors.add(re.sub(r"[^\w\- ]", "", heading.lower()).replace(" ", "-"))
    broken = [a for a in re.findall(r"\]\(#([^)]+)\)", README) if a not in anchors]
    assert not broken, broken
