"""Find Meross bulbs on the local network.

mDNS would be tidier, but HomeKit-paired bulbs advertise as _hap._tcp with
opaque names, so a direct probe of the local /24 is both simpler and more
reliable: a Meross device answers the /config endpoint even when the device
key is wrong (it replies with error 5001), which is exactly what makes it
identifiable before we know the key.
"""

from __future__ import annotations

import ipaddress
import json
import socket
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Iterable

from .meross import NS_ALL, build_envelope


@dataclass
class Found:
    ip: str
    model: str = ""
    uuid: str = ""
    mac: str = ""
    firmware: str = ""
    is_light: bool = False
    key_ok: bool = True

    def as_device(self, index: int) -> dict:
        return {
            "id": f"bulb{index}",
            "label": f"Bulb {index}",
            "ip": self.ip,
            "model": self.model,
            "serial": self.mac,
            "aliases": [],
            "channel": 0,
        }


def local_subnet() -> ipaddress.IPv4Network:
    """Best guess at the LAN we are on, as a /24."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        # No packets are sent; this just picks the default route's interface.
        sock.connect(("8.8.8.8", 80))
        ip = sock.getsockname()[0]
    except OSError:
        ip = "192.168.1.1"
    finally:
        sock.close()
    return ipaddress.ip_network(f"{ip}/24", strict=False)


def _port_open(ip: str, port: int = 80, timeout: float = 0.35) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(timeout)
        return sock.connect_ex((ip, port)) == 0


def probe(ip: str, key: str = "", timeout: float = 2.0) -> Found | None:
    """Ask one host whether it is a Meross device."""
    envelope = build_envelope(NS_ALL, "GET", {}, key, ip)
    request = urllib.request.Request(
        f"http://{ip}/config",
        data=json.dumps(envelope).encode(),
        headers={"Content-Type": "application/json"},
    )
    try:
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(request, timeout=timeout) as resp:
            data = json.loads(resp.read().decode())
    except (urllib.error.URLError, OSError, ValueError):
        return None

    header = data.get("header") or {}
    payload = data.get("payload") or {}
    if "namespace" not in header:
        return None  # Something else is listening on port 80.

    found = Found(ip=ip)
    if payload.get("error"):
        # A signature rejection still proves this is a Meross device.
        found.key_ok = False
        return found

    system = (payload.get("all") or {}).get("system") or {}
    hardware = system.get("hardware") or {}
    firmware = system.get("firmware") or {}
    found.model = hardware.get("type", "")
    found.uuid = hardware.get("uuid", "")
    found.mac = hardware.get("macAddress", "")
    found.firmware = firmware.get("version", "")
    digest = (payload.get("all") or {}).get("digest") or {}
    found.is_light = "light" in digest
    return found


def scan(
    hosts: Iterable[str] | None = None,
    key: str = "",
    workers: int = 64,
    progress=None,
) -> list[Found]:
    """Scan the LAN (or an explicit host list) for Meross devices."""
    if hosts is None:
        network = local_subnet()
        hosts = [str(h) for h in network.hosts()]
    hosts = list(hosts)

    with ThreadPoolExecutor(max_workers=workers) as pool:
        open_hosts = [
            ip for ip, is_open in zip(hosts, pool.map(_port_open, hosts)) if is_open
        ]
        if progress:
            progress(f"{len(open_hosts)} hosts listening on port 80; probing")
        results = list(pool.map(lambda ip: probe(ip, key), open_hosts))

    return [found for found in results if found]
