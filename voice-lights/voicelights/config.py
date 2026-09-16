"""Config loading, with scene definitions and sensible defaults."""

from __future__ import annotations

import json
import os
import pathlib
from typing import Any

DEFAULT_PATHS = [
    pathlib.Path("config.json"),
    pathlib.Path.home() / ".voicelights" / "config.json",
]

# Scenes shipped out of the box, named for the three bulbs in the studio.
# Every step is {"device": id|"all", "power": bool, "brightness": 1-100,
# "color": name-or-#hex, "temperature": name-or-1-100}.
DEFAULT_SCENES: dict[str, Any] = {
    "night": {
        "phrases": ["goodnight", "good night", "night mode", "bedtime", "sleep"],
        "steps": [{"device": "all", "power": False}],
    },
    "movie": {
        "phrases": ["movie", "movie mode", "film", "cinema", "netflix"],
        "steps": [
            {"device": "torch", "power": False},
            {"device": "lamp", "power": False},
            {"device": "mushroom", "brightness": 12, "color": "deep blue"},
        ],
    },
    "focus": {
        "phrases": ["focus", "focus mode", "work", "work mode", "deep work"],
        "steps": [
            {"device": "all", "brightness": 100, "temperature": "daylight"},
        ],
    },
    "chill": {
        "phrases": ["chill", "chill mode", "relax", "wind down", "evening"],
        "steps": [
            {"device": "torch", "brightness": 25, "temperature": "warm"},
            {"device": "mushroom", "brightness": 20, "color": "purple"},
            {"device": "lamp", "power": False},
        ],
    },
    "reading": {
        "phrases": ["reading", "read", "reading mode", "book"],
        "steps": [
            {"device": "lamp", "brightness": 85, "temperature": "neutral"},
            {"device": "torch", "brightness": 30, "temperature": "warm"},
            {"device": "mushroom", "power": False},
        ],
    },
    "party": {
        "phrases": ["party", "party mode", "rave"],
        "steps": [
            {"device": "torch", "brightness": 90, "color": "magenta"},
            {"device": "lamp", "brightness": 90, "color": "cyan"},
            {"device": "mushroom", "brightness": 90, "color": "lime"},
        ],
    },
}

DEFAULTS: dict[str, Any] = {
    "key": "",
    "timeout": 4.0,
    "retries": 1,
    "invert_temperature": False,
    "wake_words": ["lights", "studio", "computer"],
    "devices": [],
    "scenes": DEFAULT_SCENES,
}


class ConfigError(RuntimeError):
    pass


def default_path() -> pathlib.Path:
    for path in DEFAULT_PATHS:
        if path.exists():
            return path
    return DEFAULT_PATHS[0]


def load(path: str | os.PathLike[str] | None = None) -> dict[str, Any]:
    target = pathlib.Path(path) if path else default_path()
    if not target.exists():
        raise ConfigError(
            f"No config at {target}. Run `python run.py discover` to create one."
        )
    try:
        data = json.loads(target.read_text())
    except ValueError as exc:
        raise ConfigError(f"{target} is not valid JSON: {exc}") from exc

    config = {**DEFAULTS, **data}
    config["_path"] = str(target)

    # The device key is the one secret here; allow keeping it out of the file.
    env_key = os.environ.get("MEROSS_KEY")
    if env_key:
        config["key"] = env_key

    if not config["devices"]:
        raise ConfigError(f"{target} lists no devices. Run `python run.py discover`.")
    seen = set()
    for device in config["devices"]:
        for field in ("id", "ip"):
            if not device.get(field):
                raise ConfigError(f"Device {device} is missing '{field}'.")
        if device["id"] in seen:
            raise ConfigError(f"Duplicate device id {device['id']!r}.")
        seen.add(device["id"])
    return config


def save(config: dict[str, Any], path: str | os.PathLike[str] | None = None) -> pathlib.Path:
    target = pathlib.Path(path or config.get("_path") or DEFAULT_PATHS[0])
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = {k: v for k, v in config.items() if not k.startswith("_")}
    target.write_text(json.dumps(payload, indent=2) + "\n")
    return target
