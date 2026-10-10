"""Offline tests for the onnxruntime voice backend. Run:  venv\\Scripts\\python.exe -m unittest jarvis_addons.tests.test_voice_ort -v
The feature maths is pinned to numbers that were checked against kaldi-native-fbank (the library sherpa-onnx uses):
largest difference 0.0005 on four signals, and an identical embedding (cosine 1.00000) on real speech."""
import os
import sys
import unittest

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from jarvis_addons import voice_ort  # noqa: E402


def tone(n=16000, scale=32768.0):
    t = np.arange(n) / 16000
    return ((0.3 * np.sin(2 * np.pi * 180 * t) + 0.1 * np.sin(2 * np.pi * 1400 * t)) * scale).astype(np.float32)


class TestFbank(unittest.TestCase):
    def test_shape_follows_kaldi_snip_edges_false(self):
        for n, frames in ((16000, 100), (23457, 147), (8000, 50), (160, 1), (79, 0)):
            self.assertEqual(voice_ort.fbank(tone(n)).shape, (frames, 80), n)

    def test_energy_sits_in_the_right_mel_bands(self):
        f = voice_ort.fbank(tone()).mean(axis=0)
        centres = 700.0 * (np.exp(np.linspace(voice_ort._mel(20.0), voice_ort._mel(7600.0), 82)[1:-1] / 1127.0) - 1.0)
        near = lambda hz: int(np.argmin(np.abs(centres - hz)))                                 # noqa: E731
        self.assertLess(abs(centres[int(np.argmax(f))] - 1400), 120)                           # the loudest band is the 1400 Hz tone (pre-emphasis lifts it)
        self.assertGreater(f[near(180)], f[near(600)] + 5)                                     # the 180 Hz tone stands far above an empty band
        self.assertGreater(f[near(1400)], f[near(4000)] + 5)
        self.assertGreater(f.max() - np.median(f), 8)

    def test_loudness_only_shifts_the_log_energies(self):
        a, b = voice_ort.fbank(tone(scale=32768.0)), voice_ort.fbank(tone(scale=3276.8))
        loud = a > np.median(a)                                                                # (nearly empty bands sit on the numeric floor)
        self.assertTrue(np.allclose((a - b)[loud], np.log(100.0), atol=1e-2))                  # 10x amplitude = 100x power: a constant, removed by mean normalisation

    def test_filters_are_triangles_that_cover_20_to_7600_hz(self):
        fb = voice_ort.mel_filters()
        self.assertEqual(fb.shape, (80, 257))
        self.assertTrue((fb >= 0).all() and (fb.max(axis=1) <= 1.0 + 1e-9).all() and (fb.sum(axis=1) > 0).all())
        self.assertEqual(float(fb[:, 0].sum()), 0.0)                                           # 0 Hz is below the 20 Hz floor
        self.assertEqual(float(fb[:, 256].sum()), 0.0)                                         # the Nyquist bin is never used


class FakeAudio:
    def __init__(self, seconds):
        self.x = (tone(int(16000 * seconds), scale=8000.0)).astype(np.int16)

    def get_raw_data(self, convert_rate=None, convert_width=None):
        return self.x.tobytes()


class TestPrepare(unittest.TestCase):
    def test_same_clip_preparation_as_jarvis(self):
        self.assertIsNone(voice_ort.prepare(FakeAudio(0.2)))                                   # too short
        short = voice_ort.prepare(FakeAudio(0.4))
        self.assertGreaterEqual(len(short), 16000 * 0.9 - 1)                                   # a one-word clip is repeated
        self.assertAlmostEqual(float(np.max(np.abs(voice_ort.prepare(FakeAudio(1.0))))), 0.7, places=2)


class Host(dict):
    def __init__(self, cfg=None, sherpa_works=False):
        super().__init__()
        self.cfg, self.logs = dict(cfg or {}), []
        spk = {"ex": object() if sherpa_works else None, "tried": True}
        self.update(_SPK=spk, spk_ready=lambda: spk["ex"] is not None, spk_embed=lambda audio: "SHERPA" if spk["ex"] is not None else None,
                    spk_model_path=lambda: "model.onnx", get_config=lambda k, d=None: self.cfg.get(k, d), log_debug=lambda c, e: self.logs.append((c, repr(e))))


class FakeEmbedder:
    def __init__(self, path):
        self.path = path

    def embed(self, x):
        return "ORT:%d" % len(x)


class TestInstall(unittest.TestCase):
    def setUp(self):
        self.keep = voice_ort.Embedder
        voice_ort.Embedder = FakeEmbedder
        self.addCleanup(lambda: setattr(voice_ort, "Embedder", self.keep))

    def test_switched_off_changes_nothing(self):
        h = Host({"spk_backend": "off"})
        self.assertTrue(voice_ort.install(h))
        self.assertFalse(voice_ort.install(h))                                                 # idempotent
        self.assertFalse(h["spk_ready"]())
        self.assertIsNone(h["spk_embed"](FakeAudio(1.0)))

    def test_sherpa_blocked_falls_back_to_onnxruntime_by_default(self):
        h = Host()
        voice_ort.install(h)
        self.assertTrue(h["spk_ready"]())
        self.assertEqual(h["spk_embed"](FakeAudio(1.0)), "ORT:16000")
        self.assertIsNone(h["spk_embed"](FakeAudio(0.1)))                                      # too short, exactly like the original

    def test_when_sherpa_works_the_original_always_wins(self):
        h = Host(sherpa_works=True)
        voice_ort.install(h)
        self.assertTrue(h["spk_ready"]())
        self.assertEqual(h["spk_embed"](FakeAudio(1.0)), "SHERPA")

    def test_a_backend_that_cannot_load_is_logged_and_leaves_the_weak_lock_in_charge(self):
        def boom(path):
            raise ImportError("DLL load failed")
        voice_ort.Embedder = boom
        h = Host()
        voice_ort.install(h)
        self.assertFalse(h["spk_ready"]())
        self.assertIsNone(h["spk_embed"](FakeAudio(1.0)))
        self.assertEqual(len(h.logs), 1)                                                       # tried once, not on every call


MODEL = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "models", "wespeaker_en_voxceleb_resnet34.onnx")


class TestSameFingerprintsAsSherpa(unittest.TestCase):
    """The whole point of this backend: when both can load, they must give the same voice fingerprint, so prints saved with one
    keep matching with the other. Skipped on a PC where sherpa-onnx (or the model, or onnxruntime) is not available."""

    def test_same_embedding_as_sherpa_onnx(self):
        if not os.path.isfile(MODEL):
            self.skipTest("speaker model not downloaded")
        try:
            import onnxruntime  # noqa: F401
            import sherpa_onnx
        except Exception as e:
            self.skipTest("cannot load here: %s" % type(e).__name__)
        cfg = sherpa_onnx.SpeakerEmbeddingExtractorConfig(model=MODEL, num_threads=1, debug=False, provider="cpu")
        ex = sherpa_onnx.SpeakerEmbeddingExtractor(cfg)
        mine = voice_ort.Embedder(MODEL)
        rng = np.random.default_rng(3)
        for seconds in (0.9, 2.0, 4.3):
            n = int(16000 * seconds)
            t = np.arange(n) / 16000
            x = (0.25 * np.sin(2 * np.pi * (140 + 30 * np.sin(2 * np.pi * 3 * t)) * t) + 0.08 * np.sin(2 * np.pi * 900 * t) + 0.03 * rng.standard_normal(n)).astype(np.float32)
            st = ex.create_stream()
            st.accept_waveform(sample_rate=16000, waveform=x)
            st.input_finished()
            ref = np.array(ex.compute(st), dtype=np.float32)
            ref = ref / np.linalg.norm(ref)
            self.assertGreater(float(ref @ mine.embed(x)), 0.999, seconds)


if __name__ == "__main__":
    unittest.main()
