"""Self-extending capabilities. Run:  venv\\Scripts\\python.exe -m unittest jarvis_addons.tests.test_capability -v"""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from jarvis_addons import capability as cap  # noqa: E402

GOOD = '''NAME: dice_roll
DESCRIPTION: Rolls a dice.
TRIGGERS: roll a dice | roll the dice | dice roll | pasa phenko
EXAMPLE: roll a dice
```python
import random
NAME = "dice_roll"
DESCRIPTION = "Rolls a dice."
TRIGGERS = ["roll a dice", "roll the dice", "dice roll", "pasa phenko"]
EXAMPLE = "roll a dice"

def run(text, ctx):
    return "You rolled a %d." % random.randint(1, 6)

def self_test():
    for _ in range(20):
        assert run(EXAMPLE, None).startswith("You rolled a ")
```
'''


def variant(old, new):
    return GOOD.replace(old, new)


class Sandbox(unittest.TestCase):
    """Every test works in its own empty capabilities folder - never in the real one."""

    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        keep = {k: getattr(cap, k) for k in ("CAP_DIR", "REGISTRY", "ACTIVITY", "PENDING")}
        self.addCleanup(lambda: [setattr(cap, k, v) for k, v in keep.items()])
        cap.CAP_DIR = self.dir.name
        cap.REGISTRY, cap.ACTIVITY, cap.PENDING = (os.path.join(self.dir.name, n) for n in ("registry.json", "activity.jsonl", "PENDING.md"))
        cap._LOADED.clear()

    def files(self):
        return sorted(f for f in os.listdir(self.dir.name) if f.endswith(".py"))


class TestRules(Sandbox):
    def test_matching_needs_the_whole_phrase_and_a_short_sentence(self):
        t = ["roll a dice", "dice roll"]
        self.assertTrue(cap.matches("Jarvis, please roll a dice", t))
        self.assertTrue(cap.matches("dice roll karo", t))
        self.assertFalse(cap.matches("roll the window down", t))
        self.assertFalse(cap.matches("dice", t))
        self.assertFalse(cap.matches("what time is it", t))
        self.assertFalse(cap.matches("roll", ["roll"]))                          # a one-word trigger never matches anything
        self.assertFalse(cap.matches("i was thinking we could roll a dice later to decide who among the four of us pays", t))

    def test_what_counts_as_a_build_request(self):
        for s in ("add a feature that flips a coin", "coin flip wala feature add karo", "naya feature banao jo joke sunaye", "create a capability to roll a dice"):
            self.assertTrue(cap.BUILD_RX.search(s), s)
        for s in ("what time is it", "learn a new skill", "phone ki battery kitni hai", "open notepad", "send a message to mom"):
            self.assertFalse(cap.BUILD_RX.search(s), s)

    def test_requests_about_deleting_money_or_passwords_are_denied(self):
        for s in ("add a feature that deletes old files", "add a feature to pay my bills by upi", "feature add karo jo wifi password bataye", "add a feature to transfer money"):
            self.assertTrue(cap.denied(s), s)
        for s in ("add a feature that flips a coin", "add a feature that tells a joke", "add a feature that converts celsius to fahrenheit"):
            self.assertIsNone(cap.denied(s), s)

    def test_code_check(self):
        code = GOOD.split("```python\n")[1].split("```")[0]
        self.assertIsNone(cap.check_code(code))
        bad = {"import os": "import of 'os'", "import subprocess": "subprocess", "from urllib import request": "not allowed", "import socket": "socket",
               "x = open('a.txt', 'w')": "'open'", "eval('1')": "'eval'", "y = ().__class__": "__class__", "import urllib.request": "not allowed"}
        for line, why in bad.items():
            self.assertIn(why, cap.check_code(line + "\n" + code) or "", line)
        self.assertIn("missing", cap.check_code("import random\nNAME = 'x'\n"))
        self.assertIn("syntax error", cap.check_code("def run(:\n"))
        self.assertIsNone(cap.check_code("import urllib.parse\nfrom datetime import datetime\n" + code))


class TestBuild(Sandbox):
    def test_a_good_capability_is_written_tested_and_registered(self):
        said = []
        name, entry = cap.build("add a feature that rolls a dice", lambda system, prompt: GOOD, say=said.append)
        self.assertEqual((name, entry["status"], entry["tries"]), ("dice_roll", "active", 1))
        self.assertEqual(self.files(), ["dice_roll.py"])
        self.assertEqual(cap.find("please roll a dice"), "dice_roll")
        ok, out = cap.run("dice_roll", "roll a dice", {})
        self.assertTrue(ok and out.startswith("You rolled a "))
        self.assertEqual(cap.registry()["dice_roll"]["uses"], 1)
        states = [a["state"] for a in cap.recent_activity()]
        self.assertEqual([s for s in states if s in ("CODING", "TESTING", "SUCCESS")][-3:], ["CODING", "TESTING", "SUCCESS"])

    def test_a_failing_answer_is_fixed_on_the_next_attempt_with_the_error_given_back(self):
        prompts = []
        answers = [variant("random.randint(1, 6)", "random.randint(1, 6) / 0"), "sorry, here you go", GOOD]
        name, entry = cap.build("add a feature that rolls a dice", lambda system, prompt: (prompts.append(prompt), answers[len(prompts) - 1])[1])
        self.assertEqual((name, entry["tries"]), ("dice_roll", 3))
        self.assertIn("the test failed: TypeError", prompts[1])                           # the real error went back to the AI
        self.assertIn("required layout", prompts[2])

    def test_three_failures_leave_nothing_behind(self):
        for bad in (variant("import random", "import os"), variant("random.randint(1, 6)", "random.randint(1, 6) / 0"),
                    variant('return "You rolled a %d." % random.randint(1, 6)', "while True: pass")):
            keep, cap.TEST_TIMEOUT = cap.TEST_TIMEOUT, 3
            try:
                name, why = cap.build("add a feature that rolls a dice", lambda system, prompt: bad, tries=2 if "while True" in bad else 3)
            finally:
                cap.TEST_TIMEOUT = keep
            self.assertIsNone(name, bad[:40])
            self.assertEqual(self.files(), [])
            self.assertEqual(cap.registry(), {})

    def test_a_denied_request_is_never_sent_to_the_ai_and_goes_to_the_pending_file(self):
        asked = []
        name, why = cap.build("add a feature that deletes my old downloads", lambda s, p: asked.append(p) or GOOD)
        self.assertEqual((name, why, asked), (None, "denied:deletes" if False else why, []))
        self.assertTrue(why.startswith("denied:"))
        self.assertIn("deletes my old downloads", open(cap.PENDING, encoding="utf-8").read())

    def test_a_capability_that_breaks_later_cannot_break_jarvis_and_can_be_removed(self):
        cap.build("add a feature that rolls a dice", lambda s, p: GOOD)
        path = os.path.join(self.dir.name, "dice_roll.py")
        open(path, "a", encoding="utf-8").write("\ndef run(text, ctx):\n    return 1 / 0\n")
        os.utime(path, (1, 1))
        ok, out = cap.run("dice_roll", "roll a dice", {})
        self.assertFalse(ok)
        self.assertIn("failed", out)
        open(path, "a", encoding="utf-8").write("\nimport os\n")                 # edited by hand into something not allowed: refused
        os.utime(path, (2, 2))
        self.assertFalse(cap.run("dice_roll", "roll a dice", {})[0])
        self.assertTrue(cap.remove("dice roll"))
        self.assertEqual(self.files(), [])
        self.assertIsNone(cap.find("roll a dice"))
        self.assertEqual(cap.registry()["dice_roll"]["status"], "removed")


class TestHooks(Sandbox):
    def host(self):
        self.said, self.old = [], []
        return {"check_dynamic_rules": lambda cmd: self.old.append(("rule", cmd)) or False, "nlu_handle": lambda raw: self.old.append(("nlu", raw)) or False,
                "speak": self.said.append, "gemini_text": lambda contents, system, **k: GOOD, "log_debug": lambda c, e: self.said.append(f"DEBUG {c} {e!r}"),
                "require_owner": lambda what: True, "CLI_MODE": True}

    def test_request_builds_and_runs_it_and_later_the_sentence_alone_is_enough(self):
        h = self.host()
        self.assertTrue(cap.install(h))
        self.assertFalse(cap.install(h))
        self.assertTrue(h["check_dynamic_rules"]("add a feature that rolls a dice"))
        self.assertTrue(any("roll a dice" in s for s in self.said) and any(s.startswith("You rolled a ") for s in self.said), self.said)
        self.assertEqual(self.old, [])                                           # the old rules were not asked for the build request
        del self.said[:]
        self.assertFalse(h["check_dynamic_rules"]("roll a dice"))                # a capability never jumps ahead of JARVIS's own rules ...
        self.assertTrue(h["nlu_handle"]("roll a dice"))                          # ... it answers when none of them wanted the sentence
        self.assertTrue(self.said[0].startswith("You rolled a "))
        self.assertFalse(h["nlu_handle"]("what is the capital of japan"))
        self.assertEqual(self.old[-1], ("nlu", "what is the capital of japan"))
        self.assertTrue(h["check_dynamic_rules"]("list capabilities"))
        self.assertIn("dice roll", self.said[-1])
        self.assertTrue(h["check_dynamic_rules"]("remove capability dice roll"))
        self.assertFalse(h["nlu_handle"]("roll a dice"))

    def test_denied_and_coding_requests(self):
        h = self.host()
        cap.install(h)
        self.assertTrue(h["check_dynamic_rules"]("add a feature that deletes old files"))
        self.assertIn("approval", self.said[-1])
        self.assertEqual(self.files(), [])
        self.assertFalse(h["check_dynamic_rules"]("add a feature to app.py that shows the date"))      # a file name: the coding agent's request
        self.assertEqual(self.old[-1][0], "rule")
        h["require_owner"] = lambda what: False
        self.assertTrue(h["check_dynamic_rules"]("add a feature that rolls a dice"))                   # not the owner: nothing is built
        self.assertEqual(self.files(), [])


if __name__ == "__main__":
    unittest.main()
