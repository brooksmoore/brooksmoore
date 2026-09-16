"""Turn a spoken phrase into bulb commands.

Deliberately not an LLM: a voice light switch has to answer in the time it
takes to let go of a button, and it has to behave identically every time you
say the same sentence. This is a small, total, testable grammar instead.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Iterable

# --------------------------------------------------------------------------
# Vocabulary
# --------------------------------------------------------------------------

COLORS: dict[str, tuple[int, int, int]] = {
    "red": (255, 0, 0),
    "crimson": (220, 20, 60),
    "scarlet": (255, 36, 0),
    "orange": (255, 96, 0),
    "amber": (255, 150, 30),
    "gold": (255, 190, 60),
    "yellow": (255, 230, 60),
    "lime": (160, 255, 40),
    "green": (0, 255, 60),
    "forest green": (30, 160, 60),
    "mint": (140, 255, 200),
    "teal": (0, 200, 180),
    "turquoise": (60, 230, 220),
    "cyan": (0, 255, 255),
    "aqua": (0, 255, 255),
    "sky blue": (110, 200, 255),
    "blue": (0, 60, 255),
    "deep blue": (0, 20, 200),
    "navy": (10, 20, 140),
    "indigo": (75, 0, 200),
    "violet": (160, 60, 255),
    "purple": (160, 0, 255),
    "lavender": (200, 160, 255),
    "magenta": (255, 0, 200),
    "pink": (255, 80, 180),
    "hot pink": (255, 20, 147),
    "rose": (255, 120, 140),
    "salmon": (255, 130, 110),
    "peach": (255, 170, 120),
    "coral": (255, 110, 90),
}

# Meross white temperature runs 1..100. On stock firmware 1 is the warmest
# (~2700K) and 100 the coolest (~6500K). If a bulb has it backwards, flip
# "invert_temperature" in config.json rather than editing this table.
TEMPERATURES: dict[str, int] = {
    "candle": 1,
    "candlelight": 1,
    "sunset": 8,
    "warm": 15,
    "warm white": 15,
    "soft white": 25,
    "soft": 25,
    "neutral": 50,
    "neutral white": 50,
    "white": 50,
    "cool": 75,
    "cool white": 75,
    "daylight": 90,
    "cold": 100,
    "icy": 100,
}

OFF_WORDS = {"off", "out", "kill", "shut", "darkness", "dark"}
ON_WORDS = {"on"}
TOGGLE_WORDS = {"toggle", "flip", "switch"}
ALL_WORDS = {"all", "everything", "every", "lights", "light", "studio", "room", "them"}
PRONOUNS = {"it", "that", "this", "them", "those", "these"}
# "status" on its own is a status request. A bare question word is not: "what's
# the weather tomorrow" must not be answered with a light report, so a question
# only counts when it also mentions a light or its power state.
STATUS_NOUNS = {"status", "state", "report"}
QUESTION_WORDS = {"what's", "whats", "what", "which", "is", "are", "how"}

BRIGHTER_WORDS = {"brighter", "brighten", "up", "higher", "more"}
DIMMER_WORDS = {"dimmer", "darker", "down", "lower", "less"}

BIG_STEP_WORDS = {"way", "much", "lot", "loads", "significantly", "heaps"}
SMALL_STEP_WORDS = {"bit", "little", "touch", "slightly", "hair", "tad", "smidge"}

DEFAULT_STEP = 20
SMALL_STEP = 10
BIG_STEP = 35

NUMBER_WORDS: dict[str, int] = {
    "zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
    "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
    "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14,
    "fifteen": 15, "sixteen": 16, "seventeen": 17, "eighteen": 18,
    "nineteen": 19, "twenty": 20, "thirty": 30, "forty": 40, "fourty": 40,
    "fifty": 50, "sixty": 60, "seventy": 70, "eighty": 80, "ninety": 90,
    "hundred": 100,
}

# Phrases that stand in for an absolute brightness.
LEVEL_PHRASES: dict[str, int] = {
    "full": 100,
    "full blast": 100,
    "max": 100,
    "maximum": 100,
    "all the way up": 100,
    "half": 50,
    "halfway": 50,
    "quarter": 25,
    "lowest": 1,
    "minimum": 1,
    "as low as it goes": 1,
    "nightlight": 5,
    "night light": 5,
}

@dataclass
class Command:
    """One thing to do to one or more bulbs."""

    action: str  # on | off | toggle | brightness | brightness_delta | rgb | temperature | scene | status
    targets: list[str] = field(default_factory=list)
    value: Any = None
    said: str = ""  # human-readable echo, e.g. "Torch 40%"


@dataclass
class ParseResult:
    commands: list[Command] = field(default_factory=list)
    transcript: str = ""
    normalized: str = ""
    understood: bool = False
    reason: str = ""


# --------------------------------------------------------------------------
# Normalisation
# --------------------------------------------------------------------------

_PUNCT = re.compile(r"[^\w%\s'+-]")
_SPACES = re.compile(r"\s+")


def normalize(text: str) -> str:
    text = text.lower().strip()
    text = text.replace("&", " and ")
    text = _PUNCT.sub(" ", text)
    text = _SPACES.sub(" ", text).strip()
    return text


def words_to_numbers(tokens: list[str]) -> list[str]:
    """Collapse spoken numbers ("twenty five") into digits ("25").

    Browser speech recognition usually hands back digits already, but not
    always, and never for "a hundred".
    """
    out: list[str] = []
    i = 0
    while i < len(tokens):
        tok = tokens[i]
        if tok in NUMBER_WORDS:
            value = NUMBER_WORDS[tok]
            # "twenty five" -> 25
            if (
                value in (20, 30, 40, 50, 60, 70, 80, 90)
                and i + 1 < len(tokens)
                and tokens[i + 1] in NUMBER_WORDS
                and 1 <= NUMBER_WORDS[tokens[i + 1]] <= 9
            ):
                value += NUMBER_WORDS[tokens[i + 1]]
                i += 1
            # "a hundred" / "one hundred"
            elif (
                i + 1 < len(tokens)
                and tokens[i + 1] == "hundred"
                and value == 1
            ):
                value = 100
                i += 1
            out.append(str(value))
        elif tok == "a" and i + 1 < len(tokens) and tokens[i + 1] == "hundred":
            out.append("100")
            i += 1
        else:
            out.append(tok)
        i += 1
    return out


def _phrase_at(tokens: list[str], index: int, phrase: str) -> bool:
    parts = phrase.split()
    return tokens[index : index + len(parts)] == parts


def _find_phrase(tokens: list[str], table: Iterable[str]) -> tuple[str | None, int, int]:
    """Longest-match a multi-word phrase. Returns (phrase, start, end)."""
    best: tuple[str | None, int, int] = (None, -1, -1)
    for phrase in sorted(table, key=lambda p: -len(p.split())):
        parts = phrase.split()
        for i in range(len(tokens) - len(parts) + 1):
            if tokens[i : i + len(parts)] == parts:
                if len(parts) > (best[2] - best[1]):
                    best = (phrase, i, i + len(parts))
                break
    return best


# --------------------------------------------------------------------------
# Parser
# --------------------------------------------------------------------------

_SPLIT = re.compile(r"\bthen\b|\balso\b|;")


class IntentParser:
    def __init__(
        self,
        devices: dict[str, list[str]],
        scene_phrases: dict[str, str] | None = None,
        wake_words: Iterable[str] = ("lights", "studio", "computer"),
    ) -> None:
        # devices: {device_id: [alias, ...]}
        self.devices = devices
        # {spoken phrase: scene id}, e.g. {"movie": "movie", "film": "movie"}
        self.scene_phrases = scene_phrases or {}
        self.wake_words = set(wake_words)
        self.alias_index: list[tuple[str, str]] = []
        for dev_id, aliases in devices.items():
            for alias in aliases:
                self.alias_index.append((normalize(alias), dev_id))
        # Longest aliases first so "torch light" beats "torch".
        self.alias_index.sort(key=lambda pair: -len(pair[0].split()))

    # -- public ------------------------------------------------------------
    def parse(self, text: str, last_targets: list[str] | None = None) -> ParseResult:
        result = ParseResult(transcript=text)
        normalized = normalize(text)
        result.normalized = normalized
        if not normalized:
            result.reason = "I didn't catch that."
            return result

        tokens = words_to_numbers(normalized.split())
        tokens = self._strip_wake_word(tokens)
        groups, tokens = self._extract_target_groups(tokens)

        carry = list(last_targets or [])
        for clause in self._split_clauses(tokens):
            commands, targets = self._parse_clause(clause, groups, carry)
            if targets:
                carry = targets
            result.commands.extend(commands)

        result.understood = bool(result.commands)
        if not result.understood:
            result.reason = f'I heard "{text}" but there was no light command in it.'
        return result

    # -- stages ------------------------------------------------------------
    def _strip_wake_word(self, tokens: list[str]) -> list[str]:
        """Drop a leading "hey lights" so always-on mode can use a wake word."""
        i = 0
        while i < len(tokens) and i < 3 and tokens[i] in {"hey", "ok", "okay", "yo"}:
            i += 1
        if i and i < len(tokens) and tokens[i] in self.wake_words:
            return tokens[i + 1 :]
        return tokens

    def _extract_target_groups(
        self, tokens: list[str]
    ) -> tuple[dict[str, list[str]], list[str]]:
        """Replace device names with placeholder tokens.

        Done before clause splitting so that "torch and lamp off" is one
        command against two bulbs, not a broken split on "and".
        """
        groups: dict[str, list[str]] = {}
        out: list[str] = []
        i = 0
        counter = 0
        while i < len(tokens):
            match = self._match_alias(tokens, i)
            if not match:
                out.append(tokens[i])
                i += 1
                continue

            dev_ids, consumed = match
            i += consumed
            # Absorb "and <device>" / ", <device>" chains into one group.
            while i < len(tokens):
                j = i
                if tokens[j] in {"and", "plus", "with"}:
                    j += 1
                elif tokens[j] == "the":
                    j += 1
                else:
                    break
                while j < len(tokens) and tokens[j] == "the":
                    j += 1
                nxt = self._match_alias(tokens, j)
                if not nxt:
                    break
                for dev in nxt[0]:
                    if dev not in dev_ids:
                        dev_ids.append(dev)
                i = j + nxt[1]

            token = f"__t{counter}"
            counter += 1
            groups[token] = dev_ids
            out.append(token)
        return groups, out

    def _match_alias(self, tokens: list[str], index: int) -> tuple[list[str], int] | None:
        if index >= len(tokens):
            return None
        for alias, dev_id in self.alias_index:
            if _phrase_at(tokens, index, alias):
                return [dev_id], len(alias.split())
        if tokens[index] in ALL_WORDS and tokens[index] not in {"light", "lights"}:
            return list(self.devices), 1
        # "lights"/"light" only counts as a target when nothing else claimed it
        # ("all the lights off", "lights out").
        if tokens[index] in {"lights", "light"}:
            return list(self.devices), 1
        return None

    def _split_clauses(self, tokens: list[str]) -> list[list[str]]:
        text = " ".join(tokens)
        parts = [p.strip() for p in _SPLIT.split(text) if p.strip()]
        clauses: list[list[str]] = []
        for part in parts:
            clauses.extend(self._split_on_and(part.split()))
        return clauses or [tokens]

    def _split_on_and(self, tokens: list[str]) -> list[list[str]]:
        """Split on "and" only when both sides look like separate commands."""
        out: list[list[str]] = []
        current: list[str] = []
        for tok in tokens:
            if tok == "and" and current and self._has_action(current):
                out.append(current)
                current = []
                continue
            current.append(tok)
        if current:
            out.append(current)
        return out

    def _has_action(self, tokens: list[str]) -> bool:
        if any(t in OFF_WORDS | ON_WORDS | TOGGLE_WORDS for t in tokens):
            return True
        if any(t in BRIGHTER_WORDS | DIMMER_WORDS or t == "dim" for t in tokens):
            return True
        if any(re.fullmatch(r"\d{1,3}%?", t) for t in tokens):
            return True
        if _find_phrase(tokens, COLORS)[0] or _find_phrase(tokens, TEMPERATURES)[0]:
            return True
        if _find_phrase(tokens, LEVEL_PHRASES)[0] or _find_phrase(tokens, self.scene_phrases)[0]:
            return True
        return False

    # -- clause ------------------------------------------------------------
    def _parse_clause(
        self,
        tokens: list[str],
        groups: dict[str, list[str]],
        carry: list[str],
    ) -> tuple[list[Command], list[str]]:
        if not tokens:
            return [], []

        targets: list[str] = []
        rest: list[str] = []
        for tok in tokens:
            if tok in groups:
                for dev in groups[tok]:
                    if dev not in targets:
                        targets.append(dev)
            else:
                rest.append(tok)

        explicit = bool(targets)
        if not targets:
            if any(t in PRONOUNS for t in rest) and carry:
                targets = list(carry)
            else:
                targets = list(self.devices)

        # Scenes ignore targets: a scene names the whole room's look.
        phrase, _, _ = _find_phrase(rest, self.scene_phrases)
        if phrase and not any(t in OFF_WORDS for t in rest):
            scene_id = self.scene_phrases[phrase]
            return [Command("scene", list(self.devices), scene_id, said=phrase.title())], []

        commands = self._actions_for(rest, targets, explicit)
        return commands, targets if explicit or commands else []

    def _actions_for(
        self, tokens: list[str], targets: list[str], explicit: bool = False
    ) -> list[Command]:
        label = self._label(targets)

        if self._is_status_question(tokens, explicit):
            return [Command("status", targets, None, said="Status")]

        # Off wins over everything else: "goodnight", "lights out", "kill it".
        if any(t in OFF_WORDS for t in tokens):
            return [Command("off", targets, None, said=f"{label} off")]

        number = self._number(tokens)
        level_phrase, _, _ = _find_phrase(tokens, LEVEL_PHRASES)
        if number is None and level_phrase:
            number = LEVEL_PHRASES[level_phrase]

        relative = self._relative(tokens)
        commands: list[Command] = []

        # A colour word always wins: "warm red" is red, while "warm white" has
        # no colour word at all and falls through to the white-temperature
        # table. Keeping "white" out of COLORS is what makes that work.
        color_name, _, _ = _find_phrase(tokens, COLORS)
        temp_name, _, _ = _find_phrase(tokens, TEMPERATURES)

        if color_name:
            commands.append(
                Command("rgb", targets, COLORS[color_name], said=f"{label} {color_name}")
            )
        elif temp_name:
            commands.append(
                Command(
                    "temperature",
                    targets,
                    TEMPERATURES[temp_name],
                    said=f"{label} {temp_name}",
                )
            )

        if number is not None:
            if number <= 0:
                commands.append(Command("off", targets, None, said=f"{label} off"))
            elif relative:
                delta = relative * number
                commands.append(
                    Command(
                        "brightness_delta",
                        targets,
                        delta,
                        said=f"{label} {'+' if delta > 0 else ''}{delta}%",
                    )
                )
            else:
                commands.append(
                    Command("brightness", targets, number, said=f"{label} {number}%")
                )
        elif relative:
            step = self._step(tokens) * relative
            commands.append(
                Command(
                    "brightness_delta",
                    targets,
                    step,
                    said=f"{label} {'brighter' if step > 0 else 'dimmer'}",
                )
            )

        if commands:
            return commands

        if any(t in ON_WORDS for t in tokens):
            return [Command("on", targets, None, said=f"{label} on")]
        if any(t in TOGGLE_WORDS for t in tokens):
            return [Command("toggle", targets, None, said=f"{label} toggled")]
        return []

    # -- helpers -----------------------------------------------------------
    def _is_status_question(self, tokens: list[str], explicit: bool) -> bool:
        if self._number(tokens):
            return False
        if any(t in STATUS_NOUNS for t in tokens):
            return True
        if not any(t in QUESTION_WORDS for t in tokens):
            return False
        return explicit or any(t in ON_WORDS | OFF_WORDS for t in tokens)

    @staticmethod
    def _number(tokens: list[str]) -> int | None:
        for tok in tokens:
            match = re.fullmatch(r"(\d{1,3})%?", tok)
            if match:
                return max(0, min(100, int(match.group(1))))
        return None

    @staticmethod
    def _relative(tokens: list[str]) -> int:
        """+1 brighter, -1 dimmer, 0 absolute."""
        joined = set(tokens)
        if joined & BRIGHTER_WORDS:
            return 1
        if joined & DIMMER_WORDS:
            return -1
        # Bare "dim" with no number is relative; "dim to 30" is absolute.
        if "dim" in joined and not any(re.fullmatch(r"\d{1,3}%?", t) for t in tokens):
            return -1
        return 0

    @staticmethod
    def _step(tokens: list[str]) -> int:
        joined = set(tokens)
        if joined & BIG_STEP_WORDS:
            return BIG_STEP
        if joined & SMALL_STEP_WORDS:
            return SMALL_STEP
        return DEFAULT_STEP

    def _label(self, targets: list[str]) -> str:
        if len(targets) == len(self.devices) and len(targets) > 1:
            return "All"
        return ", ".join(
            (self.devices[t][0].title() if self.devices.get(t) else t) for t in targets
        )
