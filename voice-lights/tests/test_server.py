"""HTTP surface tests: the routes the browser actually calls."""

import http.client
import json
import os
import sys
import threading
import unittest
import urllib.error
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tools"))

from mock_bulb import FakeBulb, serve

from voicelights import server as server_mod
from voicelights.config import DEFAULT_SCENES
from voicelights.controller import Controller

KEY = "servertestkey"


class ServerTest(unittest.TestCase):
    def setUp(self):
        self.bulbs, self.bulb_servers, devices = {}, [], []
        for name in ("torch", "lamp", "mushroom"):
            bulb = FakeBulb(name, KEY)
            srv = serve(bulb, 0, quiet=True)
            self.bulbs[name] = bulb
            self.bulb_servers.append(srv)
            devices.append(
                {"id": name, "label": f"{name.title()} Light",
                 "ip": f"127.0.0.1:{srv.server_address[1]}"}
            )

        self.controller = Controller(
            {"key": KEY, "devices": devices, "scenes": DEFAULT_SCENES,
             "wake_words": ["lights"], "timeout": 3.0}
        )
        self.server, self.hub = server_mod.build(self.controller, "127.0.0.1", 0)
        self.port = self.server.server_address[1]
        threading.Thread(
            target=lambda: self.server.serve_forever(poll_interval=0.02), daemon=True
        ).start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        for srv in self.bulb_servers:
            srv.shutdown()
            srv.server_close()

    # -- helpers -----------------------------------------------------------
    @property
    def base(self):
        return f"http://127.0.0.1:{self.port}"

    def get(self, path):
        with urllib.request.urlopen(self.base + path, timeout=5) as resp:
            return resp.status, resp.read()

    def post(self, path, body):
        request = urllib.request.Request(
            self.base + path,
            data=json.dumps(body).encode(),
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(request, timeout=5) as resp:
            return json.loads(resp.read())

    # -- static ------------------------------------------------------------
    def test_serves_the_app(self):
        status, body = self.get("/")
        self.assertEqual(status, 200)
        self.assertIn(b"<title>Studio Lights", body)
        for asset in ("/static/app.js", "/static/styles.css"):
            status, body = self.get(asset)
            self.assertEqual(status, 200, asset)
            self.assertTrue(body)

    def test_path_traversal_is_refused(self):
        with self.assertRaises(urllib.error.HTTPError) as caught:
            self.get("/static/../../config.py")
        self.assertEqual(caught.exception.code, 404)

    def test_unknown_route_404s(self):
        with self.assertRaises(urllib.error.HTTPError) as caught:
            self.get("/api/nope")
        self.assertEqual(caught.exception.code, 404)

    # -- api ---------------------------------------------------------------
    def test_say_drives_the_bulbs(self):
        result = self.post("/api/say", {"text": "turn on the torch"})
        self.assertTrue(result["ok"], result["reply"])
        self.assertTrue(self.bulbs["torch"].on)
        self.assertIsInstance(result["ms"], int)

    def test_say_with_empty_text_is_rejected(self):
        with self.assertRaises(urllib.error.HTTPError) as caught:
            self.post("/api/say", {"text": "   "})
        self.assertEqual(caught.exception.code, 400)

    def test_unknown_action_is_refused(self):
        result = self.post("/api/command", {"action": "reboot", "targets": ["lamp"]})
        self.assertFalse(result["ok"])
        self.assertFalse(self.bulbs["lamp"].on)

    def test_command_endpoint_drives_sliders(self):
        self.post("/api/command", {"action": "brightness", "targets": ["lamp"], "value": 77})
        self.assertEqual(self.bulbs["lamp"].luminance, 77)

    def test_vocab_describes_the_grammar(self):
        _, body = self.get("/api/vocab")
        vocab = json.loads(body)
        self.assertIn("movie", vocab["scenes"])
        self.assertIn("hot pink", vocab["colors"])
        self.assertIn("torch", vocab["devices"])

    def test_state_and_refresh(self):
        self.bulbs["mushroom"].on = True
        self.bulbs["mushroom"].luminance = 42
        _, body = self.get("/api/refresh")
        devices = {d["id"]: d for d in json.loads(body)["devices"]}
        self.assertTrue(devices["mushroom"]["state"]["on"])
        self.assertEqual(devices["mushroom"]["state"]["luminance"], 42)

    # -- events ------------------------------------------------------------
    def test_events_stream_pushes_state(self):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        conn.request("GET", "/api/events")
        resp = conn.getresponse()
        self.assertEqual(resp.getheader("Content-Type"), "text/event-stream")

        first = resp.fp.readline() + resp.fp.readline()
        self.assertIn(b"event: state", first)
        self.assertIn(b"Torch Light", first)

        self.post("/api/say", {"text": "all on"})
        pushed = resp.fp.readline() + resp.fp.readline()
        self.assertIn(b"event: state", pushed)
        conn.close()


if __name__ == "__main__":
    unittest.main(verbosity=2)
