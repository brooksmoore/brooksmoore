"""Executes parsed commands against the bulbs."""

from __future__ import annotations

import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from .intents import COLORS, TEMPERATURES, Command, IntentParser
from .meross import Bulb, MerossError, clamp

HEX = re.compile(r"#?([0-9a-fA-F]{6})")


def resolve_color(value: Any) -> tuple[int, int, int]:
    """Accept a colour name, an #rrggbb string or an [r,g,b] triple."""
    if isinstance(value, (list, tuple)) and len(value) == 3:
        return (int(value[0]), int(value[1]), int(value[2]))
    if isinstance(value, str):
        name = value.strip().lower()
        if name in COLORS:
            return COLORS[name]
        match = HEX.fullmatch(name)
        if match:
            raw = match.group(1)
            return (int(raw[0:2], 16), int(raw[2:4], 16), int(raw[4:6], 16))
    raise ValueError(f"Unknown colour {value!r}")


def resolve_temperature(value: Any) -> int:
    if isinstance(value, (int, float)):
        return clamp(value, 1, 100)
    name = str(value).strip().lower()
    if name in TEMPERATURES:
        return TEMPERATURES[name]
    raise ValueError(f"Unknown white temperature {value!r}")


class Controller:
    """Owns the bulbs, the parser and the last-known state."""

    def __init__(self, config: dict[str, Any]) -> None:
        self.config = config
        self.lock = threading.Lock()
        self.last_targets: list[str] = []
        self.pool = ThreadPoolExecutor(max_workers=8, thread_name_prefix="bulb")

        self.bulbs: dict[str, Bulb] = {}
        for entry in config["devices"]:
            bulb = Bulb(
                id=entry["id"],
                label=entry.get("label") or entry["id"].title(),
                ip=entry["ip"],
                key=entry.get("key") or config.get("key", ""),
                channel=entry.get("channel", 0),
                serial=entry.get("serial", ""),
                model=entry.get("model", ""),
                aliases=entry.get("aliases") or [],
                timeout=float(config.get("timeout", 4.0)),
                retries=int(config.get("retries", 1)),
                invert_temperature=bool(config.get("invert_temperature", False)),
            )
            self.bulbs[bulb.id] = bulb

        self.scenes: dict[str, Any] = config.get("scenes") or {}
        self.parser = IntentParser(
            devices=self.parser_devices(),
            scene_phrases=self.scene_phrases(),
            wake_words=config.get("wake_words") or ["lights"],
        )

    # -- vocabulary --------------------------------------------------------
    def parser_devices(self) -> dict[str, list[str]]:
        """{device id: [display name, alias, ...]} -- element 0 is the label."""
        out: dict[str, list[str]] = {}
        for bulb in self.bulbs.values():
            names = [bulb.label]
            # "Torch Light" should also answer to "torch".
            words = bulb.label.lower().split()
            if len(words) > 1 and words[-1] in {"light", "lamp", "bulb"}:
                names.append(" ".join(words[:-1]))
            names.append(bulb.label.lower())
            names.extend(bulb.aliases)
            names.append(bulb.id)
            seen, unique = set(), []
            for name in names:
                if name.lower() not in seen:
                    seen.add(name.lower())
                    unique.append(name)
            out[bulb.id] = unique
        return out

    def scene_phrases(self) -> dict[str, str]:
        phrases: dict[str, str] = {}
        for scene_id, scene in self.scenes.items():
            phrases[scene_id.lower()] = scene_id
            for phrase in scene.get("phrases", []):
                phrases[phrase.lower()] = scene_id
        return phrases

    # -- entry points ------------------------------------------------------
    def say(self, text: str) -> dict[str, Any]:
        """Parse a spoken phrase and run it. The single front door."""
        result = self.parser.parse(text, self.last_targets)
        if not result.understood:
            return {
                "ok": False,
                "transcript": text,
                "reply": result.reason,
                "commands": [],
                "devices": self.snapshot(),
            }
        outcome = self.execute(result.commands)
        outcome["transcript"] = text
        return outcome

    def execute(self, commands: list[Command]) -> dict[str, Any]:
        started = time.perf_counter()
        spoken: list[str] = []
        errors: list[str] = []

        for command in commands:
            if command.targets and command.action != "scene":
                self.last_targets = list(command.targets)
            said, failed = self._run(command)
            if said:
                spoken.append(said)
            errors.extend(failed)

        reply = "; ".join(spoken) if spoken else "Nothing to do"
        if errors:
            reply = f"{reply} ({'; '.join(errors)})" if spoken else "; ".join(errors)
        return {
            "ok": not errors,
            "reply": reply,
            "commands": [
                {"action": c.action, "targets": c.targets, "value": c.value, "said": c.said}
                for c in commands
            ],
            "errors": errors,
            "ms": int((time.perf_counter() - started) * 1000),
            "devices": self.snapshot(),
        }

    # -- execution ---------------------------------------------------------
    def _run(self, command: Command) -> tuple[str, list[str]]:
        if command.action == "scene":
            return self._run_scene(str(command.value))
        if command.action == "status":
            return self._status(), []

        targets = [self.bulbs[t] for t in command.targets if t in self.bulbs]
        if not targets:
            return "", [f"I don't know a light called {', '.join(command.targets)}"]

        results = list(self.pool.map(lambda b: self._apply(b, command), targets))
        errors = [err for err in results if err]
        return command.said, errors

    def _apply(self, bulb: Bulb, command: Command) -> str | None:
        try:
            action = command.action
            if action == "on":
                bulb.set_power(True)
            elif action == "off":
                bulb.set_power(False)
            elif action == "toggle":
                if bulb.state.on is None:
                    bulb.refresh()
                bulb.set_power(not bulb.state.on)
            elif action == "brightness":
                self._set_brightness(bulb, int(command.value))
            elif action == "brightness_delta":
                current = bulb.state.luminance
                if current is None or not bulb.state.updated_at:
                    bulb.refresh()
                    current = bulb.state.luminance
                base = current if current is not None else 50
                # A light that is off starts from zero, so "brighter" turns it on.
                if bulb.state.on is False:
                    base = 0
                self._set_brightness(bulb, base + int(command.value))
            elif action == "rgb":
                self._ensure_on(bulb)
                bulb.set_light(rgb=resolve_color(command.value))
            elif action == "temperature":
                self._ensure_on(bulb)
                bulb.set_light(temperature=resolve_temperature(command.value))
            else:
                return f"Unsupported action {action}"
        except MerossError as exc:
            return str(exc)
        except ValueError as exc:
            return str(exc)
        return None

    def _set_brightness(self, bulb: Bulb, level: int) -> None:
        level = clamp(level, 0, 100)
        if level <= 0:
            bulb.set_power(False)
            return
        self._ensure_on(bulb)
        bulb.set_light(luminance=max(1, level))

    @staticmethod
    def _ensure_on(bulb: Bulb) -> None:
        """Colour and brightness on a dark bulb should light it up."""
        if bulb.state.on is not True:
            bulb.set_power(True)

    def _run_scene(self, scene_id: str) -> tuple[str, list[str]]:
        scene = self.scenes.get(scene_id)
        if not scene:
            return "", [f"No scene called {scene_id}"]

        jobs: list[tuple[Bulb, dict[str, Any]]] = []
        for step in scene.get("steps", []):
            device = step.get("device", "all")
            bulbs = (
                list(self.bulbs.values())
                if device in ("all", "*")
                else [self.bulbs[device]] if device in self.bulbs else []
            )
            for bulb in bulbs:
                jobs.append((bulb, step))

        errors = [err for err in self.pool.map(lambda j: self._apply_step(*j), jobs) if err]
        return scene.get("label", scene_id.title()), errors

    def _apply_step(self, bulb: Bulb, step: dict[str, Any]) -> str | None:
        try:
            if step.get("power") is False:
                bulb.set_power(False)
                return None
            if "brightness" in step or "color" in step or "temperature" in step or step.get("power"):
                self._ensure_on(bulb)
            kwargs: dict[str, Any] = {}
            if "color" in step:
                kwargs["rgb"] = resolve_color(step["color"])
            elif "temperature" in step:
                kwargs["temperature"] = resolve_temperature(step["temperature"])
            if "brightness" in step:
                kwargs["luminance"] = clamp(step["brightness"], 1, 100)
            if kwargs:
                bulb.set_light(**kwargs)
        except (MerossError, ValueError) as exc:
            return f"{bulb.label}: {exc}"
        return None

    # -- state -------------------------------------------------------------
    def _status(self) -> str:
        self.refresh_all()
        parts = []
        for bulb in self.bulbs.values():
            if not bulb.state.online:
                parts.append(f"{bulb.label} offline")
            elif bulb.state.on:
                parts.append(f"{bulb.label} {bulb.state.luminance or '?'}%")
            else:
                parts.append(f"{bulb.label} off")
        return ", ".join(parts)

    def refresh_all(self) -> dict[str, Any]:
        def refresh(bulb: Bulb) -> None:
            try:
                bulb.refresh()
            except MerossError as exc:
                bulb.state.online = False
                bulb.state.error = str(exc)

        list(self.pool.map(refresh, list(self.bulbs.values())))
        return self.snapshot()

    def snapshot(self) -> list[dict[str, Any]]:
        return [bulb.as_dict() for bulb in self.bulbs.values()]

    def vocabulary(self) -> dict[str, Any]:
        """What the UI shows as "things you can say"."""
        return {
            "devices": {b.id: self.parser_devices()[b.id] for b in self.bulbs.values()},
            "scenes": {
                sid: scene.get("phrases", [sid]) for sid, scene in self.scenes.items()
            },
            "colors": sorted(COLORS),
            "temperatures": sorted(TEMPERATURES),
            "wake_words": self.config.get("wake_words", []),
        }
