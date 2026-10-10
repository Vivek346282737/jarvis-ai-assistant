"""Tests for the performance add-on. Run:  venv\\Scripts\\python.exe -m unittest jarvis_addons.tests.test_perf -v"""
import os
import sys
import threading
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from jarvis_addons import perf  # noqa: E402


def pc(ram, cores, gpu=("Intel(R) UHD Graphics",)):
    return {"ram_gb": ram, "cores": cores, "threads": cores * 2, "gpu": list(gpu), "dedicated_gpu": perf.has_dedicated_gpu(gpu)}


class TestMode(unittest.TestCase):
    def test_mode_is_chosen_from_the_machine_and_can_be_forced(self):
        self.assertEqual(perf.choose_mode(pc(7.8, 4))[0], "low")                 # a normal 8 GB laptop
        self.assertEqual(perf.choose_mode(pc(15.5, 8))[0], "normal")             # the owner's Galaxy Book
        self.assertEqual(perf.choose_mode(pc(15.9, 2))[0], "low")                # plenty of RAM but an old 2-core CPU
        self.assertEqual(perf.choose_mode(pc(31.8, 8), "low"), ("low", "set by you"))
        self.assertEqual(perf.choose_mode(pc(7.8, 4), "normal")[0], "normal")
        self.assertEqual(perf.choose_mode(pc(7.8, 4), "auto")[0], "low")         # 'auto' / nothing saved = decide by itself

    def test_gpu_detection(self):
        self.assertFalse(perf.has_dedicated_gpu(["Intel(R) Arc(TM) 140V GPU (8GB)", "Intel(R) UHD Graphics"]))
        self.assertTrue(perf.has_dedicated_gpu(["Intel(R) UHD Graphics", "NVIDIA GeForce RTX 4060 Laptop GPU"]))
        self.assertTrue(perf.has_dedicated_gpu(["AMD Radeon RX 6600M"]))
        self.assertFalse(perf.has_dedicated_gpu([]))

    def test_this_machine_is_readable(self):
        m = perf.machine()
        self.assertGreater(m["ram_gb"], 1)
        self.assertGreaterEqual(m["cores"], 1)


class TestRules(unittest.TestCase):
    def test_background_study_only_when_the_pc_is_really_free(self):
        self.assertEqual(perf.may_learn(600, 10, 6.0, False), (True, ""))
        self.assertEqual(perf.may_learn(600, 10, 6.0, True)[1], "on battery")
        self.assertTrue(perf.may_learn(600, 10, 6.0, True, allow_battery=True)[0])
        self.assertEqual(perf.may_learn(20, 10, 6.0, False)[1], "the PC is being used")
        self.assertEqual(perf.may_learn(600, 85, 6.0, False)[1], "the CPU is busy")
        self.assertEqual(perf.may_learn(600, 10, 1.2, False)[1], "little free RAM")

    def test_wait_after_windows_starts(self):
        self.assertEqual(perf.boot_wait_left(1000, 990, 45), 35)                 # desktop up for 10 s -> wait 35 more
        self.assertEqual(perf.boot_wait_left(1000, 100, 45), 0)                  # up for a long time -> start at once
        self.assertEqual(perf.boot_wait_left(1000, None, 45), 0)
        self.assertEqual(perf.boot_wait_left(1000, 1005, 45), 45)                # never more than the full wait

    def test_reconnect_backoff_grows_and_resets(self):
        b = perf.Backoff(30)
        gaps = []
        for _ in range(6):
            b.result(False, 100.0)
            gaps.append(b.next_ts - 100.0)
        self.assertEqual(gaps, [6, 12, 24, 30, 30, 30])
        self.assertTrue(b.skip(110.0))
        b.result(True, 110.0)
        self.assertFalse(b.skip(110.0))


class TestPace(unittest.TestCase):
    def test_pace_follows_power_use_and_heat(self):
        self.assertEqual(perf.choose_pace(False, 10, False)[0], "normal")        # plugged in, you are working
        self.assertEqual(perf.choose_pace(False, 600, False)[0], "full")         # plugged in, nobody at the laptop
        self.assertEqual(perf.choose_pace(True, 600, False)[0], "eco")           # on battery, even when unused
        self.assertEqual(perf.choose_pace(False, 600, True)[0], "eco")           # hot, even on the charger

    def test_hot_by_sensor_or_by_a_minute_of_load_and_no_flapping(self):
        self.assertFalse(perf.is_hot(False, 60, 20))
        self.assertTrue(perf.is_hot(False, perf.HOT_C, 20))
        self.assertTrue(perf.is_hot(False, None, perf.BUSY_AVG))                 # a laptop without a readable sensor: load decides
        self.assertTrue(perf.is_hot(True, perf.HOT_C - 3, 20))                   # still warm: stays hot
        self.assertTrue(perf.is_hot(True, 50, perf.CALM_AVG + 5))
        self.assertFalse(perf.is_hot(True, perf.COOL_C - 1, 20))
        self.assertFalse(perf.is_hot(True, None, 20))
        self.assertEqual(perf.may_learn(600, 10, 6.0, False, hot=True), (False, "the laptop is hot or under heavy load"))

    def test_cpu_threads_and_gpu_decision(self):
        self.assertEqual([perf.cpu_threads({"cores": c}) for c in (1, 2, 4, 6, 8, 16)], [1, 2, 2, 3, 4, 8])
        self.assertEqual(perf.gpu_decision((7.1, 0.0), (19.1, 4.4))[0], "on")
        self.assertEqual(perf.gpu_decision((9.0, 0.0), (9.5, 4.4))[0], "cpu")    # not clearly faster: not worth it
        self.assertEqual(perf.gpu_decision((9.0, 0.0), (30.0, 0.0))[0], "cpu")   # nothing went to the GPU
        self.assertEqual(perf.gpu_decision((9.0, 0.0), None)[0], "cpu")          # Ollama did not come back / crashed
        self.assertEqual(perf.gpu_decision(None, (12.0, 4.0))[0], "on")

    def test_this_laptops_temperature_is_a_number_or_none(self):
        t = perf.temperature()
        self.assertTrue(t is None or 5 < t < 130)


class Host(dict):
    def __init__(self, cfg=None):
        super().__init__()
        self.cfg, self.logs, self.ensure_calls, self.dumps, self.connected = dict(cfg or {}), [], [], [], False
        self.update(CLI_MODE=True, ADDON={"last_activity": time.time()}, get_config=lambda k, d=None: self.cfg.get(k, d), log_debug=lambda c, e: self.logs.append(c),
                    file_log=self.logs.append, _DISC={"switched": False}, _ollama_warm=lambda: self.logs.append("WARMED 4.7 GB"),
                    ensure_adb_connected=lambda: (self.ensure_calls.append(threading.current_thread().name), self.connected)[1],
                    _adb_dump=lambda *a, **k: (self.dumps.append(time.time()), "DUMP")[1], _ollama_text=lambda *a, **k: "answer")
        for name in perf.STAGGER:
            self[name] = (lambda n: lambda: self.logs.append("RAN " + n))(name)


def in_thread(name, fn):
    out = []
    t = threading.Thread(target=lambda: out.append(fn()), name=name)
    t.start()
    t.join(10)
    return out[0]


class TestInstall(unittest.TestCase):
    def setUp(self):
        self.keep = perf.machine
        perf.machine = lambda: pc(15.5, 8)
        self.addCleanup(lambda: setattr(perf, "machine", self.keep))

    def test_installs_once_and_reports_the_mode(self):
        h = Host()
        self.assertTrue(perf.install(h))
        self.assertFalse(perf.install(h))
        self.assertEqual(h["_perf"]["mode"], "normal")
        h2 = Host({"perf_mode": "low"})
        perf.install(h2)
        self.assertEqual(h2["_perf"]["mode"], "low")
        self.assertFalse(perf.install({"log_debug": lambda c, e: None}))         # a host without JARVIS's functions: nothing breaks

    def test_local_model_is_not_loaded_at_start_and_the_adb_restart_is_skipped(self):
        h = Host()
        perf.install(h)
        h["_ollama_warm"]()
        self.assertNotIn("WARMED 4.7 GB", h.logs)
        self.assertEqual(h["_ollama_warm"].__name__, "_ollama_warm")             # JARVIS names its threads after the function
        self.assertTrue(h["_DISC"]["switched"])

    def test_loops_keep_their_names_and_still_run_just_later(self):
        h = Host()
        perf.install(h)
        self.assertEqual(h["_call_loop"].__name__, "_call_loop")
        keep, slept = perf.time.sleep, []
        perf.time.sleep = slept.append
        try:
            h["_call_loop"]()
            h["_announcer_loop"]()
        finally:
            perf.time.sleep = keep
        self.assertEqual(slept, [perf.STAGGER["_call_loop"]])                    # the announcer starts at once, the call watcher 6 s later
        self.assertIn("RAN _call_loop", h.logs)
        self.assertIn("RAN _announcer_loop", h.logs)

    def test_a_command_always_tries_the_phone_but_background_retries_back_off(self):
        h = Host()
        perf.install(h)
        for _ in range(5):
            self.assertFalse(in_thread("_adb_keeper_loop", h["ensure_adb_connected"]))
        self.assertEqual(len(h.ensure_calls), 1)                                 # 5 background attempts in a row = ONE real adb attempt
        self.assertFalse(in_thread("MainThread-command", h["ensure_adb_connected"]))
        self.assertEqual(len(h.ensure_calls), 2)                                 # the owner's own command is never skipped
        h.connected = True
        self.assertTrue(in_thread("MainThread-command", h["ensure_adb_connected"]))
        self.assertTrue(in_thread("_adb_keeper_loop", h["ensure_adb_connected"]))    # connected again -> back to normal at once

    def test_phone_polling_is_only_slowed_when_idle_and_never_for_commands(self):
        h = Host({"perf_mode": "low"})
        perf.install(h)
        keep, slept = perf.time.sleep, []
        perf.time.sleep = slept.append
        try:
            self.assertEqual(h["_adb_dump"]("dumpsys"), "DUMP")                  # a command thread: no wait at all
            in_thread("_notification_loop", lambda: h["_adb_dump"]("dumpsys"))   # recent activity
            h["ADDON"]["last_activity"] = time.time() - 900
            in_thread("_notification_loop", lambda: h["_adb_dump"]("dumpsys"))   # nothing going on
        finally:
            perf.time.sleep = keep
        self.assertEqual(slept, [2.0, 4.0])

    def test_pace_changes_phone_polling_and_cpu_only_ai_gets_half_the_cores(self):
        h = Host()
        sent = []
        h.update(OLLAMA_URL="http://x", _post_json=lambda url, data, timeout=0: sent.append((url, data)) or {})
        perf.install(h)
        keep, slept = perf.time.sleep, []
        perf.time.sleep = slept.append
        try:
            h["ADDON"]["last_activity"] = time.time() - 900
            for pace in ("normal", "eco", "full"):
                h["_perf"]["pace"] = pace
                in_thread("_notification_loop", lambda: h["_adb_dump"]("dumpsys"))
            h["_perf"]["pace"] = "eco"
            self.assertEqual(h["_adb_dump"]("dumpsys"), "DUMP")                  # a command: never waits, in any pace
        finally:
            perf.time.sleep = keep
        self.assertEqual(slept, [2.0, perf.ECO_POLL])                            # normal 2 s, eco 4 s, full: no wait at all
        body = {"model": "m", "options": {"num_predict": 5}}
        h["_perf"]["ai_gpu"] = False
        h["_post_json"]("http://x/api/generate", body, timeout=5)
        self.assertEqual(sent[-1][1]["options"], {"num_predict": 5, "num_thread": 4})
        self.assertEqual(body["options"], {"num_predict": 5})                    # the caller's own data is not touched
        h["_post_json"]("https://api.groq.com/x", body, timeout=5)
        self.assertNotIn("num_thread", sent[-1][1]["options"])                   # only the local AI
        h["_perf"]["ai_gpu"] = True
        h["_post_json"]("http://x/api/generate", body, timeout=5)
        self.assertNotIn("num_thread", sent[-1][1]["options"])                   # on a GPU the CPU is not the limit

    def test_mdns_search_backs_off_in_the_background_and_its_line_is_logged_once(self):
        h = Host()
        found = []
        h["adb_discover"] = lambda: (found.append(1), h["file_log"](perf.MDNS_LINE + " on the network"), False)[2]
        perf.install(h)
        for _ in range(5):
            self.assertFalse(in_thread("_adb_keeper_loop", h["adb_discover"]))
        self.assertEqual(len(found), 1)                                          # 5 background searches in a row = ONE real search
        for _ in range(3):
            h["adb_discover"]()                                                  # a command: always searched
        self.assertEqual(len(found), 4)
        self.assertEqual(len([x for x in h.logs if x.startswith(perf.MDNS_LINE)]), 1)    # 4 searches, one line in the log
        h["file_log"]("[ADB] found the phone at 1.2.3.4:5555")
        self.assertIn("[ADB] found the phone at 1.2.3.4:5555", h.logs)           # other lines are untouched
        keep = perf.MDNS_LOG_EVERY
        perf.MDNS_LOG_EVERY = 0
        try:
            h["adb_discover"]()
        finally:
            perf.MDNS_LOG_EVERY = keep
        self.assertTrue(h.logs[-1].endswith("[3 more times since the last such line]"))

    def test_local_model_use_is_remembered_and_unloaded_models_are_reported(self):
        h = Host()
        perf.install(h)
        self.assertEqual(h["_ollama_text"]("sys", "prompt"), "answer")
        self.assertGreater(h["_perf"]["last_local_ai"], 0)
        posted = []
        import datetime
        iso = lambda ts: datetime.datetime.fromtimestamp(ts, datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S") + ".123456789Z"          # Ollama sends nanoseconds
        now = 1_800_000_000.0
        loaded = [{"name": "qwen2.5:7b", "expires_at": iso(now - 400 + perf.KEEP_ALIVE)},          # last used 400 s ago
                  {"name": "qwen2.5vl:3b", "expires_at": iso(now - 30 + perf.KEEP_ALIVE)}]         # used 30 s ago: still needed
        h.update(OLLAMA_URL="http://x", _http_json=lambda url, timeout=0: {"models": loaded}, _post_json=lambda url, data, timeout=0: posted.append(data) or {})
        self.assertAlmostEqual(perf.idle_seconds_of(loaded[0]["expires_at"], now), 400, delta=1)
        self.assertEqual(perf.unload_local_models(h, older_than=300, now=now), ["qwen2.5:7b"])
        self.assertEqual(posted, [{"model": "qwen2.5:7b", "keep_alive": 0}])
        self.assertIsNone(perf.idle_seconds_of("garbage", now))
        h["_http_json"] = lambda url, timeout=0: (_ for _ in ()).throw(OSError("ollama not running"))
        self.assertEqual(perf.unload_local_models(h), [])


if __name__ == "__main__":
    unittest.main()
