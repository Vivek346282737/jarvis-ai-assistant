"""Notes, expenses, converter and habits: rules and storage, using a temporary folder (no real data is touched).
Run:  venv\\Scripts\\python.exe -m unittest jarvis_addons.tests.test_everyday -v"""
import os
import sys
import tempfile
import unittest
from datetime import date, datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from jarvis_addons import _rules, converter, expenses, habits, notes  # noqa: E402


class Tmp(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.TemporaryDirectory()
        for m, n in ((notes, "notes.json"), (expenses, "expenses.json"), (habits, "habits.json")):
            self.addCleanup(setattr, m, "FILE", m.FILE)
            m.FILE = os.path.join(self.d.name, n)
        self.addCleanup(self.d.cleanup)


class NotesTest(Tmp):
    def test_add_list_last_clear(self):
        self.assertIn("1 note", notes.handle("note kar: milk lena"))
        self.assertIn("2 notes", notes.handle("take a note call mom"))
        self.assertIn("call mom", notes.handle("last note"))
        self.assertIn("milk lena", notes.handle("show my notes"))
        notes.handle("clear notes")
        self.assertIn("no notes", notes.handle("show my notes"))

    def test_unrelated_is_ignored(self):
        self.assertIsNone(notes.handle("open chrome"))
        self.assertIsNone(notes.handle("what time is it"))


class ExpensesTest(Tmp):
    def test_log_and_summaries(self):
        now = datetime(2026, 10, 10, 12, 0)
        self.assertIn("200", expenses.handle("spent 200 on lunch", now))
        expenses.handle("kharcha 50 chai", now)
        expenses.handle("spent 30.5 on chai", now)
        r = expenses.handle("aaj kitna kharch hua", now)
        self.assertIn("280.50", r)
        self.assertIn("lunch 200", r)
        self.assertIn("280.50", expenses.handle("this month expenses", now))
        self.assertIn("No expenses", expenses.handle("today expenses", datetime(2026, 10, 11)))

    def test_unrelated_is_ignored(self):
        self.assertIsNone(expenses.handle("open spotify"))


class ConverterTest(unittest.TestCase):
    def test_conversions(self):
        self.assertAlmostEqual(converter.convert(5, "km", "miles"), 3.10686, 4)
        self.assertAlmostEqual(converter.convert(100, "c", "f"), 212)
        self.assertAlmostEqual(converter.convert(32, "fahrenheit", "celsius"), 0)
        self.assertIsNone(converter.convert(1, "kg", "km"))
        self.assertIn("3.1069", converter.handle("convert 5 km to miles"))
        self.assertIn("2.2046", converter.handle("1 kg to pounds"))

    def test_calculator(self):
        self.assertIn("88", converter.handle("calculate 12*(3+4)+4"))
        self.assertIn("8", converter.handle("calculate 2 ^ 3"))
        self.assertIn("90", converter.handle("20 percent of 450"))
        self.assertIn("zero", converter.handle("calculate 1/0"))

    def test_calculator_is_not_eval(self):
        for bad in ("calculate __import__('os').system('x')", "calculate open('f')", "calculate 9**9**9", "calculate abc"):
            self.assertIsNone(converter.handle(bad))

    def test_unrelated_is_ignored(self):
        self.assertIsNone(converter.handle("open chrome"))
        self.assertIsNone(converter.handle("5 apples to eat"))


class HabitsTest(Tmp):
    def test_water(self):
        t = date(2026, 10, 10)
        self.assertIn("1 glass today", habits.handle("paani piya", t))
        self.assertIn("3 glasses", habits.handle("drank 2 glasses of water", t))
        self.assertIn("3 of 8", habits.handle("water kitna piya", t))
        self.assertIn("1 glass today", habits.handle("paani piya", t + timedelta(days=1)))

    def test_streak(self):
        t = date(2026, 10, 10)
        for i in (2, 1, 0):
            habits.handle("habit done gym", t - timedelta(days=i))
        self.assertIn("3 day streak", habits.handle("habit streak gym", t))
        self.assertIn("3 day streak", habits.handle("habit streak gym", t + timedelta(days=1)))   # today not yet done: streak survives
        self.assertIn("No streak", habits.handle("habit streak gym", t + timedelta(days=3)))

    def test_unrelated_is_ignored(self):
        self.assertIsNone(habits.handle("open chrome"))


class HookTest(unittest.TestCase):
    def test_hook_chains_and_survives_errors(self):
        said, host = [], {"speak": lambda s: said.append(s), "check_dynamic_rules": lambda c, *a, **k: False, "log_debug": lambda *a: None}
        self.assertTrue(_rules.hook(host, "_f", lambda c: "hi" if c == "x" else None, "t"))
        self.assertFalse(_rules.hook(host, "_f", lambda c: None, "t"))               # installed once only
        self.assertTrue(host["check_dynamic_rules"]("x"))
        self.assertEqual(said, ["hi"])
        self.assertFalse(host["check_dynamic_rules"]("y"))                           # falls through to the original rule
        host2 = {"check_dynamic_rules": lambda c: "orig", "log_debug": lambda *a: None}
        _rules.hook(host2, "_g", lambda c: 1 / 0, "t")
        self.assertEqual(host2["check_dynamic_rules"]("z"), "orig")                  # a crashing handler never breaks the pipeline


if __name__ == "__main__":
    unittest.main()
