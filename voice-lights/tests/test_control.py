"""End-to-end tests: spoken phrase -> signed LAN request -> bulb state.

These run against tools/mock_bulb.py, which implements the same wire protocol
as a real MSL120, so everything except the physical radio is exercised here.
"""

import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tools"))

from mock_bulb import FakeBulb, serve

from voicelights.controller import Controller
from voicelights.meross import rgb_to_int

KEY = "testkey"
SCENES = {
    "movie": {
        "phrases": ["movie", "movie mode"],
        "steps": [
            {"device": "torch", "power": False},
            {"device": "lamp", "power": False},
            {"device": "mushroom", "brightness": 12, "color": "deep blue"},
        ],
    },
    "night": {"phrases": ["goodnight"], "steps": [{"device": "all", "power": False}]},
    "focus": {
        "phrases": ["focus"],
        "steps": [{"device": "all", "brightness": 100, "temperature": "daylight"}],
    },
}


class ControlTest(unittest.TestCase):
    strict_capacity = False

    def setUp(self):
        self.bulbs, self.servers, devices = {}, [], []
        for name in ("torch", "lamp", "mushroom"):
            bulb = FakeBulb(name, KEY, strict_capacity=self.strict_capacity)
            server = serve(bulb, 0, quiet=True)
            port = server.server_address[1]
            self.bulbs[name] = bulb
            self.servers.append(server)
            devices.append(
                {"id": name, "label": f"{name.title()} Light", "ip": f"127.0.0.1:{port}"}
            )
        self.controller = Controller(
            {"key": KEY, "devices": devices, "scenes": SCENES, "timeout": 3.0,
             "wake_words": ["lights"]}
        )

    def tearDown(self):
        for server in self.servers:
            server.shutdown()
            server.server_close()

    def say(self, text):
        result = self.controller.say(text)
        self.assertEqual(result.get("errors", []), [], f"{text!r}: {result['reply']}")
        return result

    # -- power -------------------------------------------------------------
    def test_turn_one_on_and_off(self):
        self.say("turn on the torch")
        self.assertTrue(self.bulbs["torch"].on)
        self.assertFalse(self.bulbs["lamp"].on)
        self.say("torch off")
        self.assertFalse(self.bulbs["torch"].on)

    def test_all_off(self):
        for bulb in self.bulbs.values():
            bulb.on = True
        self.say("lights out")
        self.assertEqual([b.on for b in self.bulbs.values()], [False, False, False])

    def test_two_targets(self):
        self.say("turn on the torch and the lamp")
        self.assertTrue(self.bulbs["torch"].on)
        self.assertTrue(self.bulbs["lamp"].on)
        self.assertFalse(self.bulbs["mushroom"].on)

    # -- brightness --------------------------------------------------------
    def test_brightness_turns_the_bulb_on(self):
        self.assertFalse(self.bulbs["lamp"].on)
        self.say("set the lamp to 40 percent")
        self.assertTrue(self.bulbs["lamp"].on)
        self.assertEqual(self.bulbs["lamp"].luminance, 40)

    def test_relative_brightness_reads_current_state(self):
        self.say("mushroom 50 percent")
        self.say("mushroom brighter")
        self.assertEqual(self.bulbs["mushroom"].luminance, 70)
        self.say("mushroom a bit dimmer")
        self.assertEqual(self.bulbs["mushroom"].luminance, 60)

    def test_relative_brightness_cannot_exceed_bounds(self):
        self.say("torch 95 percent")
        self.say("torch way brighter")
        self.assertEqual(self.bulbs["torch"].luminance, 100)

    def test_brightness_zero_turns_off(self):
        self.say("torch 60 percent")
        self.say("torch 0 percent")
        self.assertFalse(self.bulbs["torch"].on)

    # -- colour ------------------------------------------------------------
    def test_color(self):
        self.say("make the mushroom hot pink")
        self.assertTrue(self.bulbs["mushroom"].on)
        self.assertEqual(self.bulbs["mushroom"].rgb, rgb_to_int(255, 20, 147))
        self.assertEqual(self.bulbs["mushroom"].mode, "rgb")

    def test_temperature(self):
        self.say("lamp daylight")
        self.assertEqual(self.bulbs["lamp"].mode, "temperature")
        self.assertEqual(self.bulbs["lamp"].temperature, 90)

    def test_color_then_brightness_in_one_phrase(self):
        self.say("torch purple at 30 percent")
        self.assertEqual(self.bulbs["torch"].rgb, rgb_to_int(160, 0, 255))
        self.assertEqual(self.bulbs["torch"].luminance, 30)

    def test_pronoun_carries_across_utterances(self):
        self.say("turn on the lamp")
        self.say("make it red")
        self.assertEqual(self.bulbs["lamp"].rgb, rgb_to_int(255, 0, 0))
        self.assertEqual(self.bulbs["torch"].rgb, 0xFFFFFF)

    # -- scenes ------------------------------------------------------------
    def test_scene(self):
        for bulb in self.bulbs.values():
            bulb.on = True
        self.say("movie mode")
        self.assertFalse(self.bulbs["torch"].on)
        self.assertFalse(self.bulbs["lamp"].on)
        self.assertTrue(self.bulbs["mushroom"].on)
        self.assertEqual(self.bulbs["mushroom"].luminance, 12)
        self.assertEqual(self.bulbs["mushroom"].rgb, rgb_to_int(0, 20, 200))

    def test_scene_with_temperature(self):
        self.say("focus")
        for bulb in self.bulbs.values():
            self.assertTrue(bulb.on)
            self.assertEqual(bulb.luminance, 100)
            self.assertEqual(bulb.temperature, 90)

    def test_goodnight(self):
        self.say("focus")
        self.say("goodnight")
        self.assertEqual([b.on for b in self.bulbs.values()], [False, False, False])

    # -- failure modes -----------------------------------------------------
    def test_unparsed_speech_touches_nothing(self):
        result = self.controller.say("what's the weather tomorrow")
        self.assertFalse(result["ok"])
        self.assertEqual(result["commands"], [])
        self.assertEqual([b.on for b in self.bulbs.values()], [False, False, False])

    def test_unknown_light_name_changes_nothing(self):
        result = self.controller.say("kitchen light on")
        self.assertFalse(result["ok"])
        self.assertIn("don't know a light called kitchen", result["reply"])
        # It should say what it does have.
        self.assertIn("Torch Light", result["reply"])
        self.assertEqual([b.on for b in self.bulbs.values()], [False, False, False])

    def test_wrong_key_is_reported_not_swallowed(self):
        self.controller.bulbs["torch"].key = "wrong"
        result = self.controller.say("turn on the torch")
        self.assertFalse(result["ok"])
        self.assertIn("device key", result["reply"])
        self.assertFalse(self.bulbs["torch"].on)

    def test_one_dead_bulb_does_not_block_the_others(self):
        self.controller.bulbs["lamp"].ip = "127.0.0.1:1"  # nothing listening
        self.controller.bulbs["lamp"].retries = 0
        result = self.controller.say("all on")
        self.assertFalse(result["ok"])
        self.assertTrue(self.bulbs["torch"].on)
        self.assertTrue(self.bulbs["mushroom"].on)
        self.assertEqual(len(result["errors"]), 1)

    def test_refresh_reads_state_back_from_the_bulbs(self):
        self.bulbs["torch"].on = True
        self.bulbs["torch"].luminance = 33
        snapshot = {d["id"]: d for d in self.controller.refresh_all()}
        self.assertTrue(snapshot["torch"]["state"]["on"])
        self.assertEqual(snapshot["torch"]["state"]["luminance"], 33)
        self.assertFalse(snapshot["lamp"]["state"]["on"])


class StrictFirmwareTest(ControlTest):
    """Same suite against firmware that rejects combined capacity masks."""

    strict_capacity = True


if __name__ == "__main__":
    unittest.main(verbosity=2)
