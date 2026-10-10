"""Voice messages, 'call me' and important-alert calls (local fake Telegram; speech-to-text, text-to-speech and the call
service are scripted). Run:  venv\\Scripts\\python.exe -m unittest jarvis_addons.tests.test_telegram_voice -v"""
import os
import sys
import threading
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from jarvis_addons import callmebot, telegram_bot as tg  # noqa: E402
from jarvis_addons.tests.test_telegram_bot import Base, OWNER, STRANGER, TOKEN  # noqa: E402

OGG = b"OggS-fake-voice-bytes"


class VoiceBase(Base):
    def setUp(self):
        super().setUp()
        self.bridge.release_speech()
        self.heard, self.spoken, self.calls = [], [], []

        def stt(audio, name):
            self.heard.append((audio, name))
            return self.stt_says

        def tts(text):
            self.spoken.append(text)
            return b"ID3-fake-mp3-" + text.encode("utf-8")[:20]
        self.stt_says = "what time is it"
        self.bridge = tg.Bridge(self.host, tg.Api(TOKEN, self.tg.base), OWNER, stt=stt, tts=tts,
                                caller=lambda text: (self.calls.append(text), (True, "queued"))[1])
        self.bridge.capture_speech()
        self.addCleanup(self.bridge.release_speech)


class TestVoiceMessages(VoiceBase):
    def test_voice_message_is_heard_run_and_answered_with_text_and_voice(self):
        self.tg.message("", audio=OGG)
        self.run_once()
        self.assertEqual(self.heard[0][0], OGG)                                           # the real bytes were downloaded and passed on
        self.assertEqual(self.host.ran, [("what time is it", "Riya")])
        self.assertEqual(self.texts(), ["🎙 what time is it", "It's 2:17 PM, Boss."])     # what was understood, then the answer as text
        self.assertEqual(self.spoken, ["It's 2:17 PM, Boss."])                            # ...and the same answer as a voice message
        self.assertEqual(len(self.tg.voices), 1)
        self.assertIn(b"ID3-fake-mp3-", self.tg.voices[0])
        self.assertIn(b'name="chat_id"\r\n\r\n%d' % OWNER, self.tg.voices[0])

    def test_text_message_is_answered_with_text_only(self):
        self.tg.message("what time is it")
        self.run_once()
        self.assertEqual((len(self.tg.sent), self.tg.voices, self.spoken), (1, [], []))

    def test_unclear_too_long_and_strangers_voice_never_run_anything(self):
        self.stt_says = ""
        self.tg.message("", audio=OGG)
        self.tg.message("", audio=OGG, duration=tg.MAX_VOICE_SECONDS + 1)
        self.tg.message("", audio=OGG, sender=STRANGER)
        self.run_once()
        self.assertEqual(self.host.ran, [])
        self.assertEqual(len(self.heard), 1)                                              # the long one and the stranger's were never even downloaded
        self.assertIn("could not make out", self.texts()[0])
        self.assertIn("longer than", self.texts()[1])
        self.assertEqual(self.tg.voices, [])

    def test_destructive_request_spoken_in_a_voice_message_is_still_refused(self):
        self.stt_says = "shutdown the laptop"
        self.tg.message("", audio=OGG)
        self.run_once()
        self.assertEqual(self.host.ran, [])

    def test_a_voice_answer_that_cannot_be_made_still_leaves_the_text_answer(self):
        self.bridge.tts = lambda text: None
        self.tg.message("", audio=OGG)
        self.run_once()
        self.assertEqual((self.texts()[-1], self.tg.voices), ("It's 2:17 PM, Boss.", []))


class TestCallMe(VoiceBase):
    def test_call_me_in_text_voice_hinglish_and_devanagari_places_a_call_not_a_phone_command(self):
        for said in ("call me", "JARVIS call kar", "jarvis, mujhe call karo", "Jarvis, call me now."):
            self.tg.message(said)
        self.stt_says = "जार्विस कॉल करो"
        self.tg.message("", audio=OGG)
        self.run_once()
        self.assertEqual(len(self.calls), 5)
        self.assertEqual(self.host.ran, [])                                               # never sent to JARVIS's own 'call <contact>' command
        self.assertIn("Calling you now.", self.texts())

    def test_calling_someone_else_is_left_to_jarvis(self):
        for said in ("call mummy", "call didi and tell her I am late", "papa ko call karo"):
            self.assertIsNone(tg.CALL_ME.search(tg.normalize(said)), said)

    def test_a_failed_call_is_reported_honestly(self):
        self.bridge.caller = lambda text: (False, "user not authorized")
        self.tg.message("call me")
        self.run_once()
        self.assertIn("did not go through: user not authorized", self.texts()[-1])
        self.bridge.caller = None
        self.tg.message("call me")
        self.run_once()
        self.assertIn("not set up", self.texts()[-1])


class TestCallMeBot(unittest.TestCase):
    def test_username_and_number_forms(self):
        self.assertEqual(callmebot.clean_user("@Vivek_01"), "@Vivek_01")
        self.assertEqual(callmebot.clean_user("vivek_01"), "@vivek_01")
        self.assertEqual(callmebot.clean_user("+919876501234"), "+919876501234")
        for bad in ("", None, "ab", "has space", "9876501234", "@1starts_with_digit"):
            self.assertIsNone(callmebot.clean_user(bad), bad)

    def test_request_and_answer(self):
        seen = []
        ok, detail = callmebot.call("vivek_01", "Hello  there\nBoss " + "x" * 400, repeat=1, opener=lambda url, t: (seen.append(url), (200, "<html><b>Call queued</b> for @vivek_01</html>"))[1])
        self.assertTrue(ok)
        self.assertEqual(detail, "Call queued for @vivek_01")
        self.assertTrue(seen[0].startswith("http://api.callmebot.com/start.php?user=%40vivek_01&text=Hello+there+Boss+"))
        self.assertLessEqual(len(seen[0]), 60 + 20 + callmebot.MAX_TEXT + 20)             # the text was cut to the service's limit
        self.assertFalse(callmebot.call("vivek_01", "hi", opener=lambda u, t: (200, "ERROR: user not authorized. Please send /start"))[0])
        self.assertFalse(callmebot.call("vivek_01", "hi", opener=lambda u, t: (_ for _ in ()).throw(TimeoutError("timed out")))[0])
        self.assertEqual(callmebot.call("no", "hi")[0], False)                            # no request is even made
        self.assertEqual(callmebot.call("vivek_01", "   ")[0], False)


class TestImportantAlerts(Base):
    def setUp(self):
        super().setUp()
        for k, v in (("telegram_token", TOKEN), ("telegram_owner_id", OWNER), ("telegram_api", self.tg.base), ("telegram_username", "@vivek_01")):
            tg.cfg_set(k, v, self.db)
        tg._last_alert_call["ts"] = 0.0
        self.calls = []
        self.caller = lambda user, text, lang=None: (self.calls.append((user, text)), (True, "queued"))[1]

    def test_details_go_to_telegram_and_the_call_carries_no_personal_content(self):
        sent, called = tg.alert("Important email from HR <hr@bigcorp.com>: Interview at 3 PM", db=self.db, now=1000.0, caller=self.caller)
        self.assertEqual((sent, called), (True, True))
        self.assertEqual(self.texts(), ["Important email from HR <hr@bigcorp.com>: Interview at 3 PM"])
        self.assertEqual(self.calls[0][0], "@vivek_01")
        for private in ("HR", "bigcorp", "Interview", "3 PM"):
            self.assertNotIn(private, self.calls[0][1])

    def test_only_one_call_per_window_but_every_text_is_sent(self):
        for i, t in enumerate((1000.0, 1100.0, 1000.0 + tg.CALL_ALERT_EVERY + 1)):
            tg.alert(f"alert {i}", db=self.db, now=t, caller=self.caller)
        self.assertEqual((len(self.texts()), len(self.calls)), (3, 2))

    def test_no_username_or_switched_off_means_text_only(self):
        tg.cfg_set("telegram_alert_call", "off", self.db)
        self.assertEqual(tg.alert("x", db=self.db, now=1.0, caller=self.caller), (True, None))
        self.assertEqual(self.calls, [])

    def test_hook_keeps_jarvis_own_alert_and_never_fires_from_a_terminal_run(self):
        done = threading.Event()
        keep_alert, keep_conf = tg.alert, tg.configured
        fired = []
        tg.alert = lambda text, *a, **k: (fired.append(text), done.set())
        tg.configured = lambda db=None: (object(), OWNER)
        try:
            host = {"notify_whatsapp": lambda text: "WA-RESULT", "CLI_MODE": False, "log_debug": lambda c, e: None}
            self.assertTrue(tg.install(host))
            self.assertFalse(tg.install(host))
            self.assertEqual(host["notify_whatsapp"]("Important email"), "WA-RESULT")       # JARVIS gets exactly what it got before
            self.assertTrue(done.wait(3))
            self.assertEqual(fired, ["Important email"])
            cli = {"notify_whatsapp": lambda text: "WA-RESULT", "CLI_MODE": True, "log_debug": lambda c, e: None}
            tg.install(cli)
            fired.clear()
            self.assertEqual(cli["notify_whatsapp"]("from --full-test"), "WA-RESULT")
            self.assertEqual(fired, [])                                                    # --say / --full-test never ring the owner
        finally:
            tg.alert, tg.configured = keep_alert, keep_conf


if __name__ == "__main__":
    unittest.main()
