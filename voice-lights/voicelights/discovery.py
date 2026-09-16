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
    key: str = ""  # the candidate key this device accepted

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


def _split_host(ip: str, default_port: int = 80) -> tuple[str, int]:
    """Accept "host" or "host:port"; bulbs are on 80 but --hosts may override."""
    if ip.count(":") == 1:
        host, _, port = ip.partition(":")
        if port.isdigit():
            return host, int(port)
    return ip, default_port


def _port_open(ip: str, port: int = 80, timeout: float = 0.35) -> bool:
    host, port = _split_host(ip, port)
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(timeout)
        return sock.connect_ex((host, port)) == 0


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


def probe_keys(ip: str, keys: Iterable[str], timeout: float = 2.0) -> Found | None:
    """Try each candidate device key against one host.

    Bulbs provisioned through HomeKit rather than the Meross app often accept
    an empty key, so trying "" costs one request and can remove the need for a
    Meross login entirely. Returns the first key that is accepted; if every
    candidate is rejected, returns the rejection so the caller can still report
    that a Meross device is there.
    """
    rejected: Found | None = None
    for key in keys:
        found = probe(ip, key, timeout)
        if found is None:
            return None  # Not a Meross device at all.
        if found.key_ok:
            found.key = key
            return found
        rejected = found
    return rejected


def scan(
    hosts: Iterable[str] | None = None,
    key: str | None = None,
    keys: Iterable[str] | None = None,
    workers: int = 64,
    progress=None,
) -> list[Found]:
    """Scan the LAN (or an explicit host list) for Meross devices."""
    if hosts is None:
        network = local_subnet()
        hosts = [str(h) for h in network.hosts()]
    hosts = list(hosts)

    candidates = list(keys) if keys is not None else [key or ""]
    # Always worth trying the empty key; it is one extra request per device.
    if "" not in candidates:
        candidates.append("")

    with ThreadPoolExecutor(max_workers=workers) as pool:
        open_hosts = [
            ip for ip, is_open in zip(hosts, pool.map(_port_open, hosts)) if is_open
        ]
        if progress:
            progress(f"{len(open_hosts)} hosts listening on port 80; probing")
        results = list(pool.map(lambda ip: probe_keys(ip, candidates), open_hosts))

    return [found for found in results if found]
