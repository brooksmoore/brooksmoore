"""A fake MSL120 that speaks the Meross local protocol.

Lets the whole stack -- signing, transport, controller, scenes, web UI -- be
exercised without touching real hardware, and lets firmware quirks be
reproduced on demand (see --strict-capacity).

    python tools/mock_bulb.py --count 3 --key testkey --port 8081
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


class FakeBulb:
    def __init__(self, name: str, key: str, strict_capacity: bool = False) -> None:
        self.name = name
        self.key = key
        self.strict_capacity = strict_capacity
        self.on = False
        self.luminance = 50
        self.rgb = 0xFFFFFF
        self.temperature = None
        self.mode = "rgb"
        self.requests: list[tuple[str, dict]] = []
        self.lock = threading.Lock()

    # -- protocol ----------------------------------------------------------
    def handle(self, envelope: dict) -> dict:
        header = envelope.get("header") or {}
        payload = envelope.get("payload") or {}
        namespace = header.get("namespace", "")

        expected = hashlib.md5(
            f"{header.get('messageId','')}{self.key}{header.get('timestamp','')}".encode()
        ).hexdigest()
        if header.get("sign") != expected:
            return self._reply(namespace, {"error": {"code": 5001, "detail": "sign error"}})

        with self.lock:
            self.requests.append((namespace, payload))
            if namespace == "Appliance.System.All":
                return self._reply(namespace, {"all": self._all()})
            if namespace == "Appliance.Control.ToggleX":
                self.on = bool(payload["togglex"]["onoff"])
                return self._reply(namespace, {})
            if namespace == "Appliance.Control.Light":
                return self._reply(namespace, self._set_light(payload["light"]))
        return self._reply(namespace, {"error": {"code": 5000, "detail": "unknown namespace"}})

    def _set_light(self, light: dict) -> dict:
        capacity = light.get("capacity", 0)
        if self.strict_capacity and bin(capacity).count("1") > 1:
            # Some firmware builds reject a combined capacity mask.
            return {"error": {"code": 5002, "detail": "capacity not supported"}}
        if capacity & 1 and "rgb" in light:
            self.rgb = light["rgb"]
            self.temperature = None
            self.mode = "rgb"
        if capacity & 2 and "temperature" in light:
            self.temperature = light["temperature"]
            self.mode = "temperature"
        if capacity & 4 and "luminance" in light:
            self.luminance = light["luminance"]
        return {}

    def _all(self) -> dict:
        light = {"channel": 0, "luminance": self.luminance, "onoff": int(self.on)}
        if self.mode == "rgb":
            light.update({"rgb": self.rgb, "capacity": 5})
        else:
            light.update({"temperature": self.temperature, "capacity": 6})
        return {
            "system": {
                "hardware": {
                    "type": "msl120",
                    "uuid": f"mock-{self.name}",
                    "macAddress": f"00:00:00:00:00:{ord(self.name[-1]):02x}",
                },
                "firmware": {"version": "7.2.23"},
            },
            "digest": {
                "togglex": [{"channel": 0, "onoff": int(self.on)}],
                "light": light,
            },
        }

    @staticmethod
    def _reply(namespace: str, payload: dict) -> dict:
        return {
            "header": {
                "namespace": namespace,
                "method": "SETACK",
                "payloadVersion": 1,
                "timestamp": int(time.time()),
            },
            "payload": payload,
        }

    def describe(self) -> str:
        if not self.on:
            return f"{self.name}: off"
        colour = (
            f"#{self.rgb:06x}" if self.mode == "rgb" else f"{self.temperature}K-ish"
        )
        return f"{self.name}: on {self.luminance}% {colour}"


def make_handler(bulb: FakeBulb, quiet: bool):
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def do_POST(self):  # noqa: N802
            length = int(self.headers.get("Content-Length", 0))
            try:
                envelope = json.loads(self.rfile.read(length).decode())
            except ValueError:
                self.send_error(400)
                return
            body = json.dumps(bulb.handle(envelope)).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            if not quiet:
                print(f"  {bulb.describe()}", flush=True)

        def log_message(self, *args):
            pass

    return Handler


def serve(bulb: FakeBulb, port: int, quiet: bool = False) -> ThreadingHTTPServer:
    server = ThreadingHTTPServer(("127.0.0.1", port), make_handler(bulb, quiet))
    # Short poll interval: shutdown() otherwise blocks for up to 0.5s, which
    # dominates the runtime of a test suite that creates a server per test.
    threading.Thread(
        target=lambda: server.serve_forever(poll_interval=0.02), daemon=True
    ).start()
    return server


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--count", type=int, default=3)
    ap.add_argument("--port", type=int, default=8081, help="first port")
    ap.add_argument("--key", default="testkey")
    ap.add_argument("--strict-capacity", action="store_true",
                    help="reject combined capacity masks, like older firmware")
    ap.add_argument("--names", default="torch,lamp,mushroom")
    args = ap.parse_args()

    names = args.names.split(",")
    for i in range(args.count):
        name = names[i] if i < len(names) else f"bulb{i}"
        bulb = FakeBulb(name, args.key, args.strict_capacity)
        serve(bulb, args.port + i)
        print(f"mock {name} on 127.0.0.1:{args.port + i}")

    print("\nPoint config.json at these, e.g.")
    print(json.dumps(
        {"key": args.key,
         "devices": [
             {"id": names[i] if i < len(names) else f"bulb{i}",
              "label": (names[i] if i < len(names) else f"bulb{i}").title() + " Light",
              "ip": f"127.0.0.1:{args.port + i}"}
             for i in range(args.count)]},
        indent=2))
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    sys.exit(main())
