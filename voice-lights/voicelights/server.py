"""HTTP server for the voice UI.

Stdlib only, so `python run.py serve` works on a fresh machine with nothing
installed. Speech recognition happens in the browser; this process only parses
text and drives the bulbs.
"""

from __future__ import annotations

import json
import mimetypes
import pathlib
import queue
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import urlparse

from .controller import Controller

WEB_ROOT = pathlib.Path(__file__).parent / "web"


class Hub:
    """Fan-out of state changes to connected browsers (server-sent events)."""

    def __init__(self, controller: Controller, poll_seconds: float = 15.0) -> None:
        self.controller = controller
        self.poll_seconds = poll_seconds
        self.clients: set[queue.Queue] = set()
        self.lock = threading.Lock()
        self._stop = threading.Event()

    def subscribe(self) -> queue.Queue:
        q: queue.Queue = queue.Queue(maxsize=16)
        with self.lock:
            self.clients.add(q)
        return q

    def unsubscribe(self, q: queue.Queue) -> None:
        with self.lock:
            self.clients.discard(q)

    def publish(self, event: str, data: Any) -> None:
        message = f"event: {event}\ndata: {json.dumps(data)}\n\n"
        with self.lock:
            targets = list(self.clients)
        for q in targets:
            try:
                q.put_nowait(message)
            except queue.Full:
                pass  # A stalled browser must not block the bulbs.

    def start_polling(self) -> None:
        def loop() -> None:
            while not self._stop.wait(self.poll_seconds):
                # Bulbs can also be changed from the Home app or the wall
                # switch, so re-read periodically to keep the UI honest.
                with self.lock:
                    if not self.clients:
                        continue
                try:
                    self.publish("state", self.controller.refresh_all())
                except Exception:
                    pass

        threading.Thread(target=loop, daemon=True).start()

    def stop(self) -> None:
        self._stop.set()


def make_handler(controller: Controller, hub: Hub):
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"
        server_version = "voicelights"

        # -- helpers -------------------------------------------------------
        def _send(self, code: int, body: bytes, content_type: str, extra=None) -> None:
            self.send_response(code)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            for key, value in (extra or {}).items():
                self.send_header(key, value)
            self.end_headers()
            self.wfile.write(body)

        def _json(self, payload: Any, code: int = 200) -> None:
            self._send(code, json.dumps(payload).encode(), "application/json")

        def _body(self) -> dict:
            length = int(self.headers.get("Content-Length", 0))
            if not length:
                return {}
            try:
                return json.loads(self.rfile.read(length).decode())
            except ValueError:
                return {}

        def _static(self, name: str) -> None:
            path = (WEB_ROOT / name).resolve()
            if not path.is_file() or WEB_ROOT.resolve() not in path.parents:
                self._send(404, b"not found", "text/plain")
                return
            ctype = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
            self._send(200, path.read_bytes(), ctype)

        # -- routes --------------------------------------------------------
        def do_GET(self):  # noqa: N802
            route = urlparse(self.path).path
            if route in ("/", "/index.html"):
                self._static("index.html")
            elif route == "/api/state":
                self._json({"devices": controller.snapshot()})
            elif route == "/api/refresh":
                self._json({"devices": controller.refresh_all()})
            elif route == "/api/vocab":
                self._json(controller.vocabulary())
            elif route == "/api/events":
                self._events()
            elif route.startswith("/static/"):
                self._static(route[len("/static/") :])
            else:
                self._send(404, b"not found", "text/plain")

        def do_POST(self):  # noqa: N802
            route = urlparse(self.path).path
            if route == "/api/say":
                text = (self._body().get("text") or "").strip()
                if not text:
                    self._json({"ok": False, "reply": "Nothing was said."}, 400)
                    return
                result = controller.say(text)
                hub.publish("state", result["devices"])
                self._json(result)
            elif route == "/api/command":
                self._json(self._direct(self._body()))
            else:
                self._send(404, b"not found", "text/plain")

        def _direct(self, body: dict) -> dict:
            """Button/slider taps from the UI, bypassing the grammar."""
            from .intents import Command

            action = body.get("action")
            targets = body.get("targets") or list(controller.bulbs)
            if action not in {
                "on", "off", "toggle", "brightness", "brightness_delta",
                "rgb", "temperature", "scene", "status",
            }:
                return {"ok": False, "reply": f"Unknown action {action!r}"}
            command = Command(action, targets, body.get("value"), said=action)
            result = controller.execute([command])
            hub.publish("state", result["devices"])
            return result

        def _events(self) -> None:
            q = hub.subscribe()
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Connection", "keep-alive")
            self.end_headers()
            try:
                first = f"event: state\ndata: {json.dumps(controller.snapshot())}\n\n"
                self.wfile.write(first.encode())
                self.wfile.flush()
                while True:
                    try:
                        message = q.get(timeout=20)
                    except queue.Empty:
                        message = ": keepalive\n\n"
                    self.wfile.write(message.encode())
                    self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError, OSError):
                pass
            finally:
                hub.unsubscribe(q)

        def log_message(self, fmt, *args):
            if self.path.startswith("/api/say"):
                print(f"  {self.address_string()} {fmt % args}", flush=True)

    return Handler


def build(controller: Controller, host: str, port: int) -> tuple[ThreadingHTTPServer, Hub]:
    hub = Hub(controller)
    server = ThreadingHTTPServer((host, port), make_handler(controller, hub))
    server.daemon_threads = True
    return server, hub


def run(
    controller: Controller,
    host: str = "0.0.0.0",
    port: int = 8099,
    certfile: str | None = None,
    keyfile: str | None = None,
) -> None:
    server, hub = build(controller, host, port)
    scheme = "http"
    if certfile:
        import ssl

        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(certfile, keyfile or certfile)
        server.socket = context.wrap_socket(server.socket, server_side=True)
        scheme = "https"

    hub.start_polling()
    shown = "localhost" if host in ("0.0.0.0", "") else host
    print(f"\n  Voice lights on {scheme}://{shown}:{port}")
    if host in ("0.0.0.0", ""):
        from .discovery import local_subnet

        try:
            lan_ip = str(local_subnet().network_address).rsplit(".", 1)[0]
            print(f"  On your phone: {scheme}://{lan_ip}.X:{port}  (this machine's LAN IP)")
        except Exception:
            pass
    if scheme == "http" and host not in ("127.0.0.1", "localhost"):
        print("  Note: browsers only allow the microphone on https:// or localhost.")
        print("        Start with --tls to use it from your phone.")
    print("  Ctrl-C to stop.\n")

    try:
        server.serve_forever(poll_interval=0.2)
    except KeyboardInterrupt:
        print("\n  stopping")
    finally:
        hub.stop()
        server.shutdown()
        server.server_close()
