"""Settings from config.json. Run:  venv\\Scripts\\python.exe -m unittest jarvis_addons.tests.test_config_file -v"""
import contextlib
import io
import json
import os
import sqlite3
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from jarvis_addons import config_file as cf  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class TestConfigFile(unittest.TestCase):
    def test_the_published_template_has_only_placeholders(self):
        with open(os.path.join(ROOT, "config.example.json"), encoding="utf-8") as f:
            raw = json.load(f)
        for k in cf.SECRET:
            self.assertTrue(cf.is_placeholder(raw[k]), k)                        # no real key can ever sit in the template
        self.assertEqual(sorted(k for k in raw if not k.startswith("_")), sorted(cf.KEYS))

    def test_placeholders_are_skipped_and_real_values_saved_without_being_printed(self):
        with tempfile.TemporaryDirectory() as d:
            path, db = os.path.join(d, "config.json"), os.path.join(d, "j.db")
            with open(path, "w", encoding="utf-8") as f:
                json.dump({"owner_name": "Riya", "gemini_api_key": "your-gemini-api-key", "groq_keys": "gsk_TESTONLY123", "unknown_setting": "x", "dry_run": "1", "city": ""}, f)
            values, skipped = cf.read(path)
            self.assertEqual(values, {"owner_name": "Riya", "groq_keys": "gsk_TESTONLY123", "dry_run": "1"})
            self.assertEqual(sorted(skipped), ["city", "gemini_api_key"])
            self.assertEqual(cf.apply(values, db), ["dry_run", "groq_keys", "owner_name"])
            con = sqlite3.connect(db)
            rows = dict(con.execute("SELECT key, val FROM config").fetchall())
            con.close()
            self.assertEqual(rows["groq_keys"], "gsk_TESTONLY123")
            self.assertNotIn("unknown_setting", rows)                            # only settings JARVIS knows are ever written
            self.assertEqual(cf.have(db), ["dry_run", "groq_keys", "owner_name"])
            keep, cf.DB = cf.DB, db
            self.addCleanup(lambda: setattr(cf, "DB", keep))
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                cf.main(["--apply", path])
                cf.main(["--check"])
            self.assertNotIn("gsk_TESTONLY123", out.getvalue())                  # names only, never a value
            self.assertIn("groq_keys", out.getvalue())


if __name__ == "__main__":
    unittest.main()
