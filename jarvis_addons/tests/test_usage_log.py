"""Privacy tests for the usage log. Run:  venv\\Scripts\\python.exe -m unittest jarvis_addons.tests.test_usage_log -v"""
import json
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from jarvis_addons import usage_log  # noqa: E402

PRIVATE = ["Rahul", "rahul", "Mummy", "9876501234", "98765", "late tonight", "love", "secret123", "gsk_ABCDEF", "C:\\Users\\riya", "riya",
           "192.168.1.20", "friend@mail.test", "pizza", "hunter2"]


class FakeJarvis(dict):
    """The few functions of JARVIS the usage log hooks into, with a router that behaves like the real one."""

    def __init__(self, folder, cfg=None):
        super().__init__()
        self.cfg = dict(cfg or {})
        self.audits, self.debugs = [], []
        self.update(JARVIS_DIR=folder, CLI_MODE=True, get_config=lambda k, d=None: self.cfg.get(k, d), set_config=self.cfg.__setitem__,
                    note_mood=lambda text: "neutral", care_after=lambda mood: None,
                    log_audit=lambda action, risk_level="LOW", status="SUCCESS": self.audits.append((action, status)),
                    log_debug=lambda context, exc: self.debugs.append((context, exc)), run_silent_cmd=lambda *a, **k: None, spk_ready=lambda: False)

    def say(self, text, body):
        """What route_and_execute does: note_mood -> the command's own work -> care_after (skipped when it crashes)."""
        mood = self["note_mood"](text)
        body()
        self["care_after"](mood)


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="jarvis_usage_test_")
        self.addCleanup(lambda: shutil.rmtree(self.tmp, ignore_errors=True))
        self.path = os.path.join(self.tmp, usage_log.FILE_NAME)

    def rows(self):
        return usage_log._read(self.path)

    def raw(self):
        return open(self.path, encoding="utf-8").read() if os.path.exists(self.path) else ""


class TestSanitisers(unittest.TestCase):
    def test_shape_keeps_command_words_and_nothing_after_the_first_private_word(self):
        self.assertEqual(usage_log.shape("Send message to Rahul saying call me when you open the phone"), ("send message to _ _ _ _ _", 12))
        self.assertEqual(usage_log.shape("open calculator on phone"), ("open calculator on phone", 4))
        self.assertEqual(usage_log.shape("call 9876501234"), ("call _", 2))
        self.assertEqual(usage_log.shape("mummy ko message bhejo ki main late hoon"), ("_ _ _ _ _ _ _ _", 8))
        self.assertEqual(usage_log.shape(""), ("", 0))

    def test_audit_text_is_reduced_to_a_fixed_category(self):
        self.assertEqual(usage_log.audit_category("WhatsApp sent to Rahul: I love pizza"), "WhatsApp sent")
        self.assertEqual(usage_log.audit_category("Phone Call to 9876501234"), "Phone Call")
        self.assertEqual(usage_log.audit_category("Introduced Rahul as friend/guest"), "Introduced")
        self.assertEqual(usage_log.audit_category("Guest 'Rahul' blocked from: unlock phone"), "Guest")
        self.assertEqual(usage_log.audit_category("Rahul did something never seen before"), "other")

    def test_error_keeps_only_an_identifier_and_the_type(self):
        known = {"run_silent_cmd", "send_whatsapp"}.__contains__
        self.assertEqual(usage_log.error_where("run_silent_cmd timeout ['C:\\\\Users\\\\riya\\\\adb.exe', '-s', '192.168.1.20:5555']", known), "run_silent_cmd")
        self.assertEqual(usage_log.error_where("send_whatsapp/typing", known), "send_whatsapp")
        self.assertEqual(usage_log.error_where("Rahul could not be called", known), "other")          # a context that starts with a name
        self.assertEqual(usage_log.error_where("anything"), "other")
        self.assertEqual(usage_log.error_type(ValueError("no number for Rahul 9876501234")), "ValueError")
        self.assertEqual(usage_log.error_type("some text about Rahul"), "message")


class TestRecorderEndToEnd(Base):
    def _host(self, cfg=None):
        h = FakeJarvis(self.tmp, cfg)
        self.assertTrue(usage_log.install(h))
        self.assertFalse(usage_log.install(h))                                               # idempotent
        return h

    def test_a_command_is_recorded_without_any_personal_content(self):
        h = self._host()
        h["message_commands"] = lambda cmd, raw: True
        h["call_commands"] = lambda cmd, raw: False

        def work():
            h["call_commands"]("x", "x")
            h["message_commands"]("x", "x")
            h["log_audit"]("WhatsApp sent to Rahul: I will be late tonight, love you 9876501234", "MEDIUM", "SUCCESS")
            h["log_debug"]("run_silent_cmd timeout ['C:\\Users\\riya\\adb.exe', '192.168.1.20:5555']", TimeoutError("friend@mail.test secret123 hunter2"))
        h.say("Send message to Rahul saying I will be late tonight love you 9876501234", work)
        row = self.rows()[-1]
        self.assertEqual((row["kind"], row["src"], row["shape"], row["handlers"]), ("command", "cli", "send message to _ _ _ _ _", ["message_commands"]))
        self.assertEqual(row["actions"], [{"what": "WhatsApp sent", "status": "SUCCESS"}])
        self.assertEqual(row["errors"], [{"where": "run_silent_cmd", "type": "TimeoutError"}])
        self.assertEqual(row["status"], "problem")
        self.assertEqual(h.audits[0][0], "WhatsApp sent to Rahul: I will be late tonight, love you 9876501234")          # JARVIS's own audit is untouched
        raw = self.raw()
        for secret in PRIVATE:
            self.assertNotIn(secret, raw, secret)

    def test_nothing_private_survives_a_storm_of_inputs(self):
        h = self._host()
        for i, s in enumerate(PRIVATE):
            h.say(f"call {s} and tell {s} that {s}", lambda s=s: (h["log_audit"](f"Phone Call to {s}", "MEDIUM", "FAIL"), h["log_audit"](f"{s} unknown action {s}"),
                                                                     h["log_debug"](f"ctx {s}", RuntimeError(s)), h["log_debug"](s, s)))
        raw = self.raw()
        for secret in PRIVATE:
            self.assertNotIn(secret.lower(), raw.lower(), secret)
        self.assertEqual(len([r for r in self.rows() if r["kind"] == "command"]), len(PRIVATE))

    def test_a_crashed_command_is_marked_and_the_next_one_is_still_recorded(self):
        h = self._host()

        def boom():
            raise RuntimeError("Rahul 9876501234")
        with self.assertRaises(RuntimeError):
            h.say("lock phone", boom)
        h.say("what time is it", lambda: None)
        rows = [r for r in self.rows() if r["kind"] == "command"]
        self.assertEqual([(r["shape"], r["status"]) for r in rows], [("lock phone", "no_end"), ("what time is it", "ok")])

    def test_switched_off_writes_nothing_and_background_errors_are_rate_limited(self):
        h = self._host({"usage_log": "off"})
        h.say("lock phone", lambda: None)
        self.assertEqual(self.raw(), "")
        h.cfg["usage_log"] = "on"
        for _ in range(5):
            h["log_debug"]("spk_ready", ImportError("DLL load failed C:\\Users\\riya"))
        h["log_audit"]("Auto-replied to Rahul", "MEDIUM", "SUCCESS")
        rows = self.rows()
        self.assertEqual([(r["kind"], r.get("where") or r.get("what")) for r in rows], [("background_error", "spk_ready"), ("background", "Auto-replied")])

    def test_install_id_is_random_and_stable_and_the_file_has_only_known_fields(self):
        h = self._host()
        iid = h.cfg["usage_install_id"]
        self.assertRegex(iid, r"^[0-9a-f]{12}$")
        h.say("open camera on phone", lambda: None)
        row = self.rows()[-1]
        self.assertEqual(row["id"], iid)
        self.assertEqual(set(row), {"t", "id", "kind", "src", "shape", "words", "script", "handlers", "actions", "errors", "ms", "status"})
        json.dumps(usage_log.summary(self.rows()))

    def test_the_wrapped_handlers_keep_their_results_and_markers(self):
        h = self._host()
        fn = lambda cmd, raw: "RESULT"                                                       # noqa: E731
        fn._jarvis_agent = True
        h["project_builder_commands"] = fn
        h.say("build website", lambda: None)                                                 # first command -> the handlers get wrapped
        self.assertEqual(h["project_builder_commands"]("a", "b"), "RESULT")
        self.assertTrue(h["project_builder_commands"]._jarvis_agent)


class TestFirstRunNotice(Base):
    """Setup explains the usage log and asks. If setup was skipped, JARVIS itself must say so - once."""

    def _host(self, cfg=None, cli=False):
        h = FakeJarvis(self.tmp, cfg)
        h.printed, h.spoken = [], []
        h.update(CLI_MODE=cli, safe_print=h.printed.append, speak=h.spoken.append, file_log=lambda m: None)
        self.assertTrue(usage_log.install(h))
        return h

    def test_setup_skipped_voice_mode_prints_once_and_says_it_once_after_the_first_command(self):
        h = self._host()
        self.assertEqual(len(h.printed), 1)
        self.assertIn("usage log", h.printed[0])
        self.assertIn("--off", h.printed[0])
        self.assertEqual(h.spoken, [])                                                       # not spoken during start-up
        h.say("what time is it", lambda: None)
        h.say("lock phone", lambda: None)
        self.assertEqual(len(h.spoken), 1)
        self.assertIn("usage log", h.spoken[0])
        self.assertEqual(h.cfg["usage_log_notice"], "shown")
        h2 = self._restart(h.cfg)                                                            # next start of the same installation: silent
        h2.say("what time is it", lambda: None)
        self.assertEqual((h2.printed, h2.spoken), ([], []))

    def _restart(self, cfg):
        h = FakeJarvis(self.tmp, cfg)
        h.printed, h.spoken = [], []
        h.update(CLI_MODE=False, safe_print=h.printed.append, speak=h.spoken.append, file_log=lambda m: None)
        usage_log.install(h)
        return h

    def test_setup_done_means_no_notice_because_setup_already_asked(self):
        for choice in ("on", "off"):
            h = self._host({"usage_log": choice})
            h.say("what time is it", lambda: None)
            self.assertEqual((h.printed, h.spoken), ([], []), choice)

    def test_a_quiet_tool_load_neither_shows_nor_uses_up_the_notice(self):
        os.environ["JARVIS_ADDON_TOOL"] = "1"
        try:
            h = self._host()
        finally:
            os.environ.pop("JARVIS_ADDON_TOOL", None)
        self.assertEqual(h.printed, [])
        self.assertNotIn("usage_log_notice", h.cfg)                                          # still pending for the owner's next real start
        h2 = self._restart(h.cfg)
        self.assertEqual(len(h2.printed), 1)

    def test_cli_mode_prints_but_never_speaks(self):
        h = self._host(cli=True)
        h.say("what time is it", lambda: None)
        self.assertEqual((len(h.printed), h.spoken), (1, []))


if __name__ == "__main__":
    unittest.main()
