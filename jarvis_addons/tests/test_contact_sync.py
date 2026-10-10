"""Contact sync and renamed contacts. Run:  venv\\Scripts\\python.exe -m unittest jarvis_addons.tests.test_contact_sync -v"""
import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from jarvis_addons import contact_sync as cs  # noqa: E402

PHONE_OUT = """Row: 0 display_name=Rohan, data1=+91 90000 00101
Row: 1 display_name=Rahul Office, data1=9000000102
Row: 2 display_name=Mom, data1=9000000111
Row: 3 display_name=Mummy, data1=090000 00111
Row: 4 display_name=Rohan, data1=9000000999
Row: 5 display_name=, data1=9000000777
garbage line
"""
PHONE = [("Rohan", "+919000000101"), ("Rahul Office", "9000000102"), ("Mom", "9000000111"), ("Mummy", "09000000111")]


class TestRules(unittest.TestCase):
    def test_phone_output_is_parsed_one_number_per_name(self):
        self.assertEqual(cs.parse_phone_rows(PHONE_OUT), PHONE)
        self.assertEqual(cs.parse_phone_rows(""), [])
        self.assertEqual(cs.key10("+91 90000 00101"), cs.key10("09000000101"))

    def test_plan(self):
        local = [("rahul", "9000000101", True), ("rahul office", "9000000102", False), ("Only In Jarvis", "9000000555", False), ("mom", "9000000111", True), ("maa", "9000000111", False)]
        p = cs.plan(local, PHONE)
        self.assertEqual(p["renamed"], [("rahul", "Rohan")])                      # the real case: Rahul was renamed to Rohan on the phone
        self.assertEqual(p["new"], ["Rohan", "Mummy"])
        self.assertEqual(cs.plan(local, PHONE, keep={"rahul"})["renamed"], [])     # a name the owner restored is his own: kept for ever
        # 'Only In Jarvis' (its number is not on the phone) and 'maa' (two phone names share that number) are never touched
        self.assertEqual(cs.plan([("x", "12345", False)], PHONE)["renamed"], [])  # short codes are not compared


class TestDatabase(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.db = os.path.join(self.dir.name, "j.db")
        con = sqlite3.connect(self.db)
        con.execute("CREATE TABLE contacts (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT UNIQUE, phone TEXT, relation TEXT, notes TEXT, email TEXT)")
        con.executemany("INSERT INTO contacts (name, phone, relation, notes, email) VALUES (?,?,?,?,?)",
                        [("rahul", "9000000101", "friend", "likes cricket", "a@b.com"), ("rahul office", "9000000102", None, None, None), ("Only In Jarvis", "9000000555", "boss", None, None)])
        con.commit()
        con.close()

    def rows(self, sql="SELECT name, phone, relation, notes, email FROM contacts ORDER BY id"):
        con = sqlite3.connect(self.db)
        try:
            return con.execute(sql).fetchall()
        finally:
            con.close()

    def test_rename_before_jarvis_synced_the_new_name(self):
        self.assertEqual(cs.follow_renames(PHONE, self.db), [("rahul", "Rohan")])
        self.assertEqual(self.rows()[0], ("Rohan", "9000000101", "friend", "likes cricket", "a@b.com"))        # same person, new name, nothing lost
        self.assertEqual(len(self.rows()), 3)
        self.assertEqual(self.rows("SELECT name, replaced_by FROM contacts_retired"), [("rahul", "Rohan")])
        self.assertEqual(cs.follow_renames(PHONE, self.db), [])                                                  # doing it again changes nothing

    def test_rename_after_jarvis_added_the_new_name_keeps_the_persons_data(self):
        con = sqlite3.connect(self.db)
        con.execute("INSERT INTO contacts (name, phone, email) VALUES ('Rohan', '+919000000101', 'new@x.com')")
        con.commit()
        con.close()
        self.assertEqual(cs.follow_renames(PHONE, self.db), [("rahul", "Rohan")])
        names = [r[0] for r in self.rows()]
        self.assertNotIn("rahul", names)
        nee = [r for r in self.rows() if r[0] == "Rohan"][0]
        self.assertEqual(nee, ("Rohan", "+919000000101", "friend", "likes cricket", "new@x.com"))               # filled in, never overwritten
        self.assertIn("Only In Jarvis", names)                                                                   # a JARVIS-only contact is untouched

    def test_restore_brings_a_name_back_and_keeps_it(self):
        cs.follow_renames(PHONE, self.db)
        self.assertFalse(cs.restore("nobody", self.db))
        self.assertTrue(cs.restore("rahul", self.db))
        self.assertIn(("rahul", "9000000101", "friend", "likes cricket", "a@b.com"), self.rows())
        self.assertEqual(cs.follow_renames(PHONE, self.db), [])                                                  # not retired a second time
        self.assertFalse(cs.restore("rahul", self.db))


class TestHook(unittest.TestCase):
    def test_the_spoken_sync_also_follows_renames_and_an_unreachable_phone_changes_nothing(self):
        with tempfile.TemporaryDirectory() as d:
            db = os.path.join(d, "j.db")
            con = sqlite3.connect(db)
            con.execute("CREATE TABLE contacts (name TEXT UNIQUE, phone TEXT, relation TEXT, notes TEXT, email TEXT)")     # the owner's real, older table: no 'id' column
            con.execute("INSERT INTO contacts (name, phone) VALUES ('rahul', '9000000101')")
            con.commit()
            con.close()
            keep, cs.DB = cs.DB, db
            self.addCleanup(lambda: setattr(cs, "DB", keep))
            logs, state = [], {"192.168.1.23:5555": "device"}
            host = {"sync_contacts_from_phone": lambda announce=True: 7, "_adb_state": lambda: dict(state), "ADB_BIN": "adb", "TARGET": "192.168.1.23:5555", "CLI_MODE": True,
                    "run_silent_cmd": lambda cmd, timeout=None: subprocess.CompletedProcess(cmd, 0, stdout=PHONE_OUT, stderr=""), "log_debug": lambda c, e: logs.append(repr(e)), "file_log": logs.append}
            self.assertTrue(cs.install(host))
            self.assertFalse(cs.install(host))
            state.clear()                                                         # phone not connected
            self.assertEqual(host["sync_contacts_from_phone"](announce=False), 7)
            con = sqlite3.connect(db)
            self.assertEqual(con.execute("SELECT name FROM contacts").fetchall(), [("rahul",)])
            con.close()
            state["192.168.1.23:5555"] = "device"
            self.assertEqual(host["sync_contacts_from_phone"](announce=False), 7)                 # JARVIS's own result is passed on unchanged
            con = sqlite3.connect(db)
            self.assertEqual(con.execute("SELECT name FROM contacts").fetchall(), [("Rohan",)])
            con.close()
            self.assertTrue(any("'rahul' is now 'Rohan'" in s for s in logs), logs)


if __name__ == "__main__":
    unittest.main()
