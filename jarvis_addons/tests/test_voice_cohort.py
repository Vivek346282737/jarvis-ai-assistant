"""The comparison voices ("cohort") are only saved when the recording is real. Found for real on 2026-10-02: with the
speaker volume low the microphone heard near-silence, and the cohort saved on 2026-09-21 was 11 copies of the same vector.
Run:  venv\\Scripts\\python.exe -m unittest jarvis_addons.tests.test_voice_cohort -v"""
import os
import sys
import unittest

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from jarvis_addons import voice_setup  # noqa: E402


def unit(v):
    v = np.asarray(v, dtype=np.float32)
    return v / np.linalg.norm(v)


class FakeL:
    _COHORT_VOICES = ["v%d" % i for i in range(8)]

    def __init__(self, ready=True, others=()):
        self.saved, self._ready, self._others = None, ready, list(others)

    def spk_ready(self):
        return self._ready

    def other_jarvis_processes(self):
        return self._others

    def spk_save(self, name, embs, replace=False):
        self.saved = (name, len(embs), replace)


def hearing(loudness, distinct=True, fail=()):
    rng = np.random.default_rng(0)
    base = rng.standard_normal(32)
    played = []

    def hear(L, voice, sentence):
        played.append(voice)
        if voice in fail:
            return None, 0.0
        e = unit(rng.standard_normal(32)) if distinct else unit(base + 0.01 * rng.standard_normal(32))
        return e, loudness
    hear.played = played
    return hear


class TestCohortChecks(unittest.TestCase):
    def test_real_recordings_are_saved(self):
        L = FakeL()
        ok, msg = voice_setup.record_cohort_checked(L, hear_fn=hearing(800))
        self.assertTrue(ok, msg)
        self.assertEqual(L.saved, ("__cohort__", 8, True))

    def test_quiet_speakers_stop_after_one_voice_and_save_nothing(self):
        L = FakeL()
        h = hearing(17)                                                                      # the loudness really measured with the volume low
        ok, msg = voice_setup.record_cohort_checked(L, hear_fn=h)
        self.assertFalse(ok)
        self.assertIn("volume", msg)
        self.assertEqual((len(h.played), L.saved), (1, None))

    def test_recordings_that_are_all_alike_are_refused(self):
        L = FakeL()
        ok, msg = voice_setup.record_cohort_checked(L, hear_fn=hearing(800, distinct=False))
        self.assertFalse(ok)
        self.assertIn("all alike", msg)
        self.assertIsNone(L.saved)

    def test_too_few_voices_are_refused(self):
        L = FakeL()
        ok, msg = voice_setup.record_cohort_checked(L, hear_fn=hearing(800, fail=("v1", "v2", "v3", "v4")))
        self.assertFalse(ok)
        self.assertIn("only 4 voices", msg)
        self.assertIsNone(L.saved)

    def test_never_plays_voices_while_another_jarvis_is_listening(self):
        L = FakeL(others=[1234])
        h = hearing(800)
        ok, msg = voice_setup.record_cohort_checked(L, hear_fn=h)
        self.assertFalse(ok)
        self.assertIn("close it first", msg)
        self.assertEqual(h.played, [])

    def test_model_not_usable_is_refused(self):
        self.assertFalse(voice_setup.record_cohort_checked(FakeL(ready=False), hear_fn=hearing(800))[0])

    def test_the_broken_cohort_shape_is_recognised(self):
        same = [unit(np.ones(16))] * 11
        self.assertIn("all alike", voice_setup.cohort_problem(same, [500] * 11))
        self.assertIsNone(voice_setup.cohort_problem([unit(np.eye(16)[i]) for i in range(8)], [500] * 8))


if __name__ == "__main__":
    unittest.main()
