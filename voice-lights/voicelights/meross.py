"""Local-LAN client for Meross Wi-Fi bulbs (MSL120 and relatives).

Meross devices expose an HTTP endpoint on port 80 at /config that speaks the
same JSON envelope the cloud MQTT transport uses. Every request carries an
MD5 signature derived from the device key, so control never has to leave the
local network -- no cloud round trip, no Siri, no HomeKit.

Envelope shape::

    {"header": {"messageId", "namespace", "method", "payloadVersion",
                "from", "timestamp", "sign"},
     "payload": {...}}

where ``sign = md5(messageId + key + timestamp)``.
"""

from __future__ import annotations

import hashlib
import json
import random
import socket
import string
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any

# Namespaces we use.
NS_ALL = "Appliance.System.All"
NS_TOGGLEX = "Appliance.Control.ToggleX"
NS_LIGHT = "Appliance.Control.Light"

# ``capacity`` is a bitmask telling the bulb which fields of the light payload
# are meaningful. Sending a field without its bit set is silently ignored.
CAP_RGB = 1
CAP_TEMPERATURE = 2
CAP_LUMINANCE = 4

# Device-reported error codes worth naming.
ERR_SIGN = 5001  # bad signature -> wrong device key


class MerossError(RuntimeError):
    """Any failure talking to a bulb."""


class MerossTimeout(MerossError):
    """The bulb did not answer in time (asleep, off at the wall, wrong IP)."""


class MerossAuthError(MerossError):
    """The bulb rejected our signature -- the device key is wrong."""


def _random_hex(n: int = 16) -> str:
    return "".join(random.choices(string.hexdigits.lower(), k=n))


def message_id() -> str:
    return hashlib.md5(_random_hex().encode()).hexdigest()


def sign(msg_id: str, key: str, timestamp: int) -> str:
    return hashlib.md5(f"{msg_id}{key}{timestamp}".encode()).hexdigest()


def build_envelope(
    namespace: str,
    method: str,
    payload: dict[str, Any],
    key: str,
    ip: str,
    *,
    msg_id: str | None = None,
    timestamp: int | None = None,
) -> dict[str, Any]:
    """Build a signed request envelope. Pure function, so it is testable."""
    msg_id = msg_id or message_id()
    timestamp = int(time.time()) if timestamp is None else timestamp
    return {
        "header": {
            "from": f"http://{ip}/config",
            "messageId": msg_id,
            "method": method,
            "namespace": namespace,
            "payloadVersion": 1,
            "sign": sign(msg_id, key, timestamp),
            "timestamp": timestamp,
            "triggerSrc": "AndroidLocal",
        },
        "payload": payload,
    }


def clamp(value: float, low: int, high: int) -> int:
    return int(max(low, min(high, round(value))))


def rgb_to_int(r: int, g: int, b: int) -> int:
    return (clamp(r, 0, 255) << 16) | (clamp(g, 0, 255) << 8) | clamp(b, 0, 255)


def int_to_rgb(value: int) -> tuple[int, int, int]:
    return ((value >> 16) & 0xFF, (value >> 8) & 0xFF, value & 0xFF)


@dataclass
class BulbState:
    """Last known state of a bulb. ``online`` is False until we hear from it."""

    online: bool = False
    on: bool | None = None
    luminance: int | None = None
    rgb: tuple[int, int, int] | None = None
    temperature: int | None = None
    error: str | None = None
    updated_at: float = 0.0

    def as_dict(self) -> dict[str, Any]:
        return {
            "online": self.online,
            "on": self.on,
            "luminance": self.luminance,
            "rgb": list(self.rgb) if self.rgb else None,
            "hex": "#%02x%02x%02x" % self.rgb if self.rgb else None,
            "temperature": self.temperature,
            "error": self.error,
            "updated_at": self.updated_at,
        }


@dataclass
class Bulb:
    """One addressable bulb on the LAN."""

    id: str
    label: str
    ip: str
    key: str = ""
    channel: int = 0
    serial: str = ""
    model: str = ""
    aliases: list[str] = field(default_factory=list)
    timeout: float = 4.0
    retries: int = 1
    invert_temperature: bool = False
    state: BulbState = field(default_factory=BulbState)

    # -- transport ---------------------------------------------------------
    def request(
        self, namespace: str, method: str, payload: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        envelope = build_envelope(namespace, method, payload or {}, self.key, self.ip)
        body = json.dumps(envelope).encode()
        url = f"http://{self.ip}/config"
        last: Exception | None = None

        for attempt in range(self.retries + 1):
            req = urllib.request.Request(
                url, data=body, headers={"Content-Type": "application/json"}
            )
            try:
                # Bulbs are on the LAN; never send this through a proxy.
                opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
                with opener.open(req, timeout=self.timeout) as resp:
                    raw = resp.read()
                break
            except (socket.timeout, TimeoutError):
                last = MerossTimeout(f"{self.label} did not answer at {self.ip}")
            except urllib.error.URLError as exc:
                reason = getattr(exc, "reason", exc)
                if isinstance(reason, (socket.timeout, TimeoutError)):
                    last = MerossTimeout(f"{self.label} did not answer at {self.ip}")
                else:
                    last = MerossError(f"{self.label} unreachable at {self.ip}: {reason}")
            except OSError as exc:
                last = MerossError(f"{self.label} unreachable at {self.ip}: {exc}")
            if attempt < self.retries:
                time.sleep(0.15)
        else:
            self.state.online = False
            self.state.error = str(last)
            raise last  # type: ignore[misc]

        try:
            data = json.loads(raw.decode())
        except ValueError as exc:
            raise MerossError(f"{self.label} sent a non-JSON reply: {exc}") from exc

        self._raise_for_error(data)
        self.state.online = True
        self.state.error = None
        return data.get("payload", {})

    def _raise_for_error(self, data: dict[str, Any]) -> None:
        payload = data.get("payload") or {}
        error = payload.get("error")
        if not error:
            return
        code = error.get("code")
        if code == ERR_SIGN:
            raise MerossAuthError(
                f"{self.label} rejected the device key (code {code}). "
                "Run `python run.py key` to fetch the right one."
            )
        raise MerossError(f"{self.label} returned error {code}: {error.get('detail', '')}")

    # -- reads -------------------------------------------------------------
    def refresh(self) -> BulbState:
        payload = self.request(NS_ALL, "GET")
        digest = (payload.get("all") or {}).get("digest") or {}
        self._absorb_digest(digest)
        system = (payload.get("all") or {}).get("system") or {}
        hardware = system.get("hardware") or {}
        self.model = hardware.get("type") or self.model
        self.serial = hardware.get("macAddress") or self.serial
        self.state.updated_at = time.time()
        return self.state

    def _absorb_digest(self, digest: dict[str, Any]) -> None:
        for entry in digest.get("togglex") or []:
            if entry.get("channel", 0) == self.channel:
                self.state.on = bool(entry.get("onoff"))
        light = digest.get("light") or {}
        if light:
            self._absorb_light(light)

    def _absorb_light(self, light: dict[str, Any]) -> None:
        capacity = light.get("capacity", 0)
        if "luminance" in light:
            self.state.luminance = light["luminance"]
        if capacity & CAP_RGB and "rgb" in light:
            self.state.rgb = int_to_rgb(light["rgb"])
            self.state.temperature = None
        if capacity & CAP_TEMPERATURE and "temperature" in light:
            self.state.temperature = light["temperature"]
            self.state.rgb = None
        if "onoff" in light:
            self.state.on = bool(light["onoff"])

    # -- writes ------------------------------------------------------------
    def set_power(self, on: bool) -> None:
        self.request(
            NS_TOGGLEX,
            "SET",
            {"togglex": {"channel": self.channel, "onoff": 1 if on else 0}},
        )
        self.state.on = on
        self.state.updated_at = time.time()

    def set_light(
        self,
        *,
        rgb: tuple[int, int, int] | None = None,
        temperature: int | None = None,
        luminance: int | None = None,
    ) -> None:
        """Set colour, white temperature and/or brightness in one call.

        RGB and temperature are mutually exclusive on these bulbs -- setting one
        clears the other -- so callers should pass at most one of them.
        """
        if rgb is None and temperature is None and luminance is None:
            return

        light: dict[str, Any] = {"channel": self.channel}
        capacity = 0
        if rgb is not None:
            light["rgb"] = rgb_to_int(*rgb)
            capacity |= CAP_RGB
        if temperature is not None:
            value = clamp(temperature, 1, 100)
            if self.invert_temperature:
                value = 101 - value
            light["temperature"] = value
            capacity |= CAP_TEMPERATURE
        if luminance is not None:
            light["luminance"] = clamp(luminance, 1, 100)
            capacity |= CAP_LUMINANCE
        light["capacity"] = capacity

        try:
            self.request(NS_LIGHT, "SET", {"light": light})
        except MerossAuthError:
            raise
        except MerossError:
            # Firmware varies (these three bulbs alone run 7.2.7 and 7.2.23).
            # Some builds refuse a combined capacity mask; retry field by field.
            if bin(capacity).count("1") < 2:
                raise
            self._set_light_separately(rgb, temperature, luminance)

        self._absorb_light(light)
        self.state.updated_at = time.time()

    def _set_light_separately(
        self,
        rgb: tuple[int, int, int] | None,
        temperature: int | None,
        luminance: int | None,
    ) -> None:
        if rgb is not None:
            self.request(
                NS_LIGHT,
                "SET",
                {"light": {"channel": self.channel, "rgb": rgb_to_int(*rgb), "capacity": CAP_RGB}},
            )
        if temperature is not None:
            value = clamp(temperature, 1, 100)
            if self.invert_temperature:
                value = 101 - value
            self.request(
                NS_LIGHT,
                "SET",
                {"light": {"channel": self.channel, "temperature": value, "capacity": CAP_TEMPERATURE}},
            )
        if luminance is not None:
            self.request(
                NS_LIGHT,
                "SET",
                {
                    "light": {
                        "channel": self.channel,
                        "luminance": clamp(luminance, 1, 100),
                        "capacity": CAP_LUMINANCE,
                    }
                },
            )

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "label": self.label,
            "ip": self.ip,
            "model": self.model,
            "serial": self.serial,
            "aliases": self.aliases,
            "state": self.state.as_dict(),
        }
