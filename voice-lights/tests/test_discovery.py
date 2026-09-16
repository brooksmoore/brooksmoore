"""Discovery tests, including the key-candidate path.

The case that matters: a bulb provisioned through HomeKit rather than the
Meross app often accepts an empty device key, which means no cloud login is
needed at all. Discovery has to find that on its own.
"""

import os
import sys
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tools"))

from mock_bulb import FakeBulb, serve

from voicelights import discovery


def not_a_bulb(port_holder):
    """Something else answering on port 80 -- a router, a printer, a proxy."""

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def do_POST(self):  # noqa: N802
            body = b"426 Upgrade Required"
            self.send_response(426)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(
        target=lambda: server.serve_forever(poll_interval=0.02), daemon=True
    ).start()
    port_holder.append(server)
    return server.server_address[1]


class DiscoveryTest(unittest.TestCase):
    def setUp(self):
        self.servers = []

    def tearDown(self):
        for server in self.servers:
            server.shutdown()
            server.server_close()

    def bulb(self, key):
        bulb = FakeBulb("mushroom", key)
        server = serve(bulb, 0, quiet=True)
        self.servers.append(server)
        return bulb, f"127.0.0.1:{server.server_address[1]}"

    # -- the HomeKit case --------------------------------------------------
    def test_empty_key_bulb_needs_no_cloud_login(self):
        _, addr = self.bulb("")
        found = discovery.probe_keys(addr, ["", "some-cloud-key"])
        self.assertIsNotNone(found)
        self.assertTrue(found.key_ok)
        self.assertEqual(found.key, "")
        self.assertEqual(found.model, "msl120")
        self.assertTrue(found.is_light)

    def test_keyed_bulb_is_found_by_the_right_candidate(self):
        _, addr = self.bulb("realkey")
        found = discovery.probe_keys(addr, ["", "wrong", "realkey"])
        self.assertTrue(found.key_ok)
        self.assertEqual(found.key, "realkey")

    def test_all_keys_rejected_still_reports_the_device(self):
        _, addr = self.bulb("realkey")
        found = discovery.probe_keys(addr, ["", "wrong"])
        self.assertIsNotNone(found, "a Meross device must still be reported")
        self.assertFalse(found.key_ok)

    # -- not a bulb --------------------------------------------------------
    def test_non_meross_responder_is_not_a_device(self):
        port = not_a_bulb(self.servers)
        self.assertIsNone(discovery.probe_keys(f"127.0.0.1:{port}", ["", "k"]))

    def test_nothing_listening(self):
        self.assertIsNone(discovery.probe_keys("127.0.0.1:1", [""], timeout=0.5))

    # -- scan --------------------------------------------------------------
    def test_scan_over_explicit_hosts(self):
        _, a = self.bulb("")
        _, b = self.bulb("realkey")
        dead = "127.0.0.1:1"
        found = discovery.scan([a, b, dead], keys=["realkey"])
        self.assertEqual(len(found), 2)
        by_key = {f.key for f in found}
        self.assertEqual(by_key, {"", "realkey"})
        self.assertTrue(all(f.key_ok for f in found))

    def test_scan_always_tries_the_empty_key(self):
        _, addr = self.bulb("")
        found = discovery.scan([addr], keys=["only-a-cloud-key"])
        self.assertEqual(len(found), 1)
        self.assertTrue(found[0].key_ok)

    def test_host_port_parsing(self):
        self.assertEqual(discovery._split_host("192.168.1.21"), ("192.168.1.21", 80))
        self.assertEqual(discovery._split_host("127.0.0.1:8081"), ("127.0.0.1", 8081))


if __name__ == "__main__":
    unittest.main(verbosity=2)
