"""Tests for the phone-battery answer. Run:  venv\\Scripts\\python.exe -m unittest jarvis_addons.tests.test_phone_battery -v"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from jarvis_addons import phone_battery as pb  # noqa: E402

USUAL = "Workstation running solid, Boss. CPU is at 9 percent, and RAM is at 80 percent."


def host(level):
    h = {"log_debug": lambda c, e: None, "get_real_phone_battery": lambda: level,
         "get_system_metrics": lambda: USUAL + (f" Your phone battery is at {level} percent." if level is not None else ""),
         "nlu_command": lambda intent, slots, raw, body=None: h["check_dynamic_rules"]("battery status") or h["get_system_metrics"](),
         "check_dynamic_rules": lambda cmd: False}
    return h


class TestPhoneBattery(unittest.TestCase):
    def test_kind_of_question(self):
        self.assertEqual(pb.kind(["phone ki battery kitni hai"]), "phone")
        self.assertEqual(pb.kind(["mobile ka charge kitna hai"]), "phone")
        self.assertEqual(pb.kind(["फोन की बैटरी कितनी है"]), "phone")
        self.assertEqual(pb.kind(["battery kitni bachi hai"]), "battery")
        self.assertEqual(pb.kind(["system status"]), "")
        self.assertEqual(pb.kind(["phone lock karo"]), "")
        self.assertEqual(pb.kind([]), "")

    def test_phone_question_gets_the_phone_answer_or_a_clear_not_connected(self):
        h = host(None)
        self.assertTrue(pb.install(h))
        self.assertFalse(pb.install(h))                                          # only once
        self.assertEqual(h["nlu_command"]("battery", {}, "phone ki battery kitni hai"), pb.NOT_CONNECTED)
        h = host(73)
        pb.install(h)
        self.assertEqual(h["nlu_command"]("battery", {}, "phone ki battery kitni hai"), "Your phone battery is at 73 percent.")
        h["check_dynamic_rules"]("phone battery")                                # the short form that skips the language step
        self.assertEqual(h["get_system_metrics"](), "Your phone battery is at 73 percent.")

    def test_everything_else_stays_as_it_was(self):
        h = host(None)
        pb.install(h)
        self.assertEqual(h["nlu_command"]("battery", {}, "system ka status batao"), USUAL)
        self.assertEqual(h["get_system_metrics"](), USUAL)                       # morning briefing: no question at all
        self.assertEqual(h["nlu_command"]("battery", {}, "battery kitni bachi hai"), USUAL + " " + pb.NOT_CONNECTED)
        h = host(50)
        pb.install(h)
        self.assertEqual(h["nlu_command"]("battery", {}, "battery kitni bachi hai"), USUAL + " Your phone battery is at 50 percent.")

    def test_an_older_phone_command_does_not_leak_into_a_later_status_question(self):
        h = host(None)
        pb.install(h)
        h["check_dynamic_rules"]("phone lock karo")
        self.assertEqual(h["nlu_command"]("battery", {}, "system status"), USUAL)
        pb.install({})                                                           # a host without JARVIS's functions: nothing breaks


if __name__ == "__main__":
    unittest.main()
