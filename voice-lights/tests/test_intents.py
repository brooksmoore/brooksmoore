"""Grammar tests.

Every case here is a sentence someone would actually say out loud. If a phrase
gets misheard by the parser in real use, add it here first, watch it fail, then
fix the grammar.
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from voicelights.intents import COLORS, IntentParser

DEVICES = {
    "torch": ["Torch", "torch light", "torch"],
    "lamp": ["Lamp", "lamp light", "lamp"],
    "mushroom": ["Mushroom", "mushroom light", "mushroom", "shroom"],
}
SCENES = {
    "movie": "movie",
    "movie mode": "movie",
    "goodnight": "night",
    "focus": "focus",
}
ALL = {"torch", "lamp", "mushroom"}


class ParserTest(unittest.TestCase):
    def setUp(self):
        self.parser = IntentParser(DEVICES, SCENES)

    def commands(self, text, last=None):
        return self.parser.parse(text, last).commands

    def one(self, text, last=None):
        cmds = self.commands(text, last)
        self.assertEqual(len(cmds), 1, f"{text!r} -> {cmds}")
        return cmds[0]

    # -- power -------------------------------------------------------------
    def test_simple_on(self):
        for phrase in ["turn on the torch", "torch on", "switch on the torch light",
                       "turn the torch on"]:
            cmd = self.one(phrase)
            self.assertEqual(cmd.action, "on", phrase)
            self.assertEqual(cmd.targets, ["torch"], phrase)

    def test_simple_off(self):
        for phrase in ["turn off the lamp", "lamp off", "shut the lamp off",
                       "kill the lamp light"]:
            cmd = self.one(phrase)
            self.assertEqual(cmd.action, "off", phrase)
            self.assertEqual(cmd.targets, ["lamp"], phrase)

    def test_everything_off(self):
        for phrase in ["all off", "lights out", "everything off",
                       "turn off all the lights", "kill the lights"]:
            cmd = self.one(phrase)
            self.assertEqual(cmd.action, "off", phrase)
            self.assertEqual(set(cmd.targets), ALL, phrase)

    def test_toggle(self):
        cmd = self.one("toggle the mushroom")
        self.assertEqual(cmd.action, "toggle")
        self.assertEqual(cmd.targets, ["mushroom"])

    # -- targets -----------------------------------------------------------
    def test_two_targets_joined_by_and(self):
        cmd = self.one("torch and lamp off")
        self.assertEqual(cmd.action, "off")
        self.assertEqual(set(cmd.targets), {"torch", "lamp"})

    def test_two_targets_after_the_verb(self):
        # Needs alias-chain merging: the clause splitter would otherwise cut at
        # "and" and drop the lamp on the floor.
        cmd = self.one("turn off the torch and the lamp")
        self.assertEqual(cmd.action, "off")
        self.assertEqual(set(cmd.targets), {"torch", "lamp"})

    def test_all_three_by_name(self):
        cmd = self.one("torch lamp and mushroom to 30")
        self.assertEqual(cmd.action, "brightness")
        self.assertEqual(set(cmd.targets), ALL)

    def test_nickname(self):
        cmd = self.one("shroom off")
        self.assertEqual(cmd.targets, ["mushroom"])

    def test_unknown_light_name_is_refused_not_broadcast(self):
        # A name the parser does not know must not quietly mean "all of them".
        for phrase in ["kitchen light off", "ceiling light to 50",
                       "desk light red"]:
            cmd = self.one(phrase)
            self.assertEqual(cmd.action, "unknown", phrase)
            self.assertEqual(cmd.targets, [], phrase)

    def test_bare_lights_still_means_everything(self):
        # The guard above must not break the phrasings that legitimately mean
        # every light in the room.
        for phrase in ["lights out", "turn the lights off", "all the lights off",
                       "kill the lights", "dim the lights"]:
            cmd = self.one(phrase)
            self.assertNotEqual(cmd.action, "unknown", phrase)
            self.assertEqual(set(cmd.targets), ALL, phrase)

    def test_known_light_name_still_resolves(self):
        cmd = self.one("torch light off")
        self.assertEqual(cmd.action, "off")
        self.assertEqual(cmd.targets, ["torch"])

    def test_vocabulary_words_before_lights_are_not_names(self):
        # Regression: "which lights are on" once read "which" as a light name.
        for phrase in ["which lights are on", "make the lights warm",
                       "blue lights on"]:
            cmd = self.one(phrase)
            self.assertNotEqual(cmd.action, "unknown", phrase)

    def test_bare_command_targets_everything(self):
        cmd = self.one("50 percent")
        self.assertEqual(set(cmd.targets), ALL)

    def test_pronoun_follows_previous_target(self):
        cmds = self.commands("turn on the lamp then set it to 20")
        self.assertEqual([c.action for c in cmds], ["on", "brightness"])
        self.assertEqual(cmds[1].targets, ["lamp"])
        self.assertEqual(cmds[1].value, 20)

    def test_pronoun_uses_session_memory(self):
        cmd = self.one("make it red", last=["mushroom"])
        self.assertEqual(cmd.action, "rgb")
        self.assertEqual(cmd.targets, ["mushroom"])

    # -- brightness --------------------------------------------------------
    def test_absolute_brightness(self):
        for phrase, value in [
            ("set the lamp to 40 percent", 40),
            ("lamp 40%", 40),
            ("dim the lamp to 20", 20),
            ("lamp to fifty percent", 50),
            ("lamp twenty five percent", 25),
            ("lamp full", 100),
            ("lamp half", 50),
            ("put the lamp at a hundred", 100),
        ]:
            cmd = self.one(phrase)
            self.assertEqual(cmd.action, "brightness", phrase)
            self.assertEqual(cmd.value, value, phrase)

    def test_out_of_range_percent_is_clamped(self):
        # Speech recognition mishears "to 50" as "250" often enough to matter.
        cmd = self.one("torch 250 percent")
        self.assertEqual(cmd.action, "brightness")
        self.assertEqual(cmd.value, 100)

    def test_zero_percent_is_off(self):
        cmd = self.one("torch 0 percent")
        self.assertEqual(cmd.action, "off")

    def test_relative_brightness(self):
        for phrase, delta in [
            ("brighter", 20),
            ("torch brighter", 20),
            ("way brighter", 35),
            ("a bit dimmer", -10),
            ("dim the torch", -20),
            ("turn the torch up", 20),
            ("torch down a little", -10),
        ]:
            cmd = self.one(phrase)
            self.assertEqual(cmd.action, "brightness_delta", phrase)
            self.assertEqual(cmd.value, delta, phrase)

    def test_relative_brightness_with_amount(self):
        cmd = self.one("torch brighter by 30")
        self.assertEqual(cmd.action, "brightness_delta")
        self.assertEqual(cmd.value, 30)

    # -- colour ------------------------------------------------------------
    def test_color(self):
        cmd = self.one("make the torch red")
        self.assertEqual(cmd.action, "rgb")
        self.assertEqual(cmd.value, COLORS["red"])

    def test_multiword_color(self):
        cmd = self.one("mushroom hot pink")
        self.assertEqual(cmd.value, COLORS["hot pink"])

    def test_color_and_brightness_together(self):
        cmds = self.commands("torch purple at 30 percent")
        self.assertEqual([c.action for c in cmds], ["rgb", "brightness"])
        self.assertEqual(cmds[1].value, 30)

    def test_white_is_temperature_not_color(self):
        for phrase in ["warm white", "lamp daylight", "torch candle"]:
            cmd = self.one(phrase)
            self.assertEqual(cmd.action, "temperature", phrase)

    def test_color_word_beats_a_temperature_word(self):
        # "warm red" is red, not warm white.
        cmd = self.one("torch warm red")
        self.assertEqual(cmd.action, "rgb")
        self.assertEqual(cmd.value, COLORS["red"])

    def test_different_colors_for_different_bulbs(self):
        cmds = self.commands("torch red and lamp blue")
        self.assertEqual([c.action for c in cmds], ["rgb", "rgb"])
        self.assertEqual(cmds[0].targets, ["torch"])
        self.assertEqual(cmds[1].targets, ["lamp"])

    # -- scenes ------------------------------------------------------------
    def test_scene(self):
        for phrase in ["movie mode", "goodnight", "focus"]:
            cmd = self.one(phrase)
            self.assertEqual(cmd.action, "scene", phrase)

    def test_scene_survives_a_leading_verb(self):
        cmd = self.one("turn on movie mode")
        self.assertEqual(cmd.action, "scene")

    # -- wake word / noise -------------------------------------------------
    def test_wake_word_is_stripped(self):
        cmd = self.one("hey lights turn off the torch")
        self.assertEqual(cmd.action, "off")
        self.assertEqual(cmd.targets, ["torch"])

    def test_unrelated_speech_is_ignored(self):
        for phrase in ["what time is it", "remind me to call mom", ""]:
            result = self.parser.parse(phrase)
            self.assertFalse(result.understood, phrase)
            self.assertEqual(result.commands, [], phrase)

    def test_status(self):
        for phrase in ["what's on", "status", "which lights are on", "is the torch on"]:
            cmd = self.one(phrase)
            self.assertEqual(cmd.action, "status", phrase)

    def test_questions_that_are_not_about_lights(self):
        # A question word alone must not trigger a light report.
        for phrase in ["what's the weather tomorrow", "what time is it",
                       "how far is it to chicago"]:
            result = self.parser.parse(phrase)
            self.assertFalse(result.understood, phrase)


if __name__ == "__main__":
    unittest.main(verbosity=2)
