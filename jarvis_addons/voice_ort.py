"""Strong voice lock without sherpa-onnx (add-on).

JARVIS's speaker model is an ONNX file that used to be run through sherpa-onnx. On PCs where Windows Smart App Control
blocks sherpa-onnx's unsigned DLLs, the strong lock silently falls back to the weak built-in one. This add-on runs the
SAME model file through Microsoft's own, signed `onnxruntime`, and computes the model's input features (Kaldi fbank)
in plain numpy, exactly the way sherpa-onnx does - so the voice prints already saved keep working.

It only steps in when sherpa-onnx cannot load; whenever sherpa-onnx works, JARVIS's original code runs untouched.
The fingerprints are the same as sherpa-onnx's (verified by tests/test_voice_ort.py on any PC where both load).

    venv\\Scripts\\python.exe -m jarvis_addons.voice_ort --status     what is installed / active
    venv\\Scripts\\python.exe -m jarvis_addons.voice_ort --off        never use it  (--on to allow it again)"""
import os
import sqlite3
import sys

import numpy as np

SAMPLE_RATE = 16000
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


# ------------------------------------------------------------------ Kaldi-compatible fbank, as sherpa-onnx configures it
def _mel(f):
    return 1127.0 * np.log(1.0 + f / 700.0)


def mel_filters(num_bins=80, n_fft=512, sr=SAMPLE_RATE, low=20.0, high=-400.0):
    high = sr / 2.0 + high if high <= 0 else high
    edges = np.linspace(_mel(low), _mel(high), num_bins + 2)
    mels = _mel(np.arange(n_fft // 2) * sr / n_fft)                     # Kaldi leaves the Nyquist bin out
    fb = np.zeros((num_bins, n_fft // 2 + 1))
    for b in range(num_bins):
        left, centre, right = edges[b], edges[b + 1], edges[b + 2]
        up = (mels - left) / (centre - left)
        down = (right - mels) / (right - centre)
        fb[b, :n_fft // 2] = np.where((mels > left) & (mels <= centre), up, np.where((mels > centre) & (mels < right), down, 0.0))
    return fb


_FB = {}


def fbank(samples, sr=SAMPLE_RATE, num_bins=80, frame_ms=25, shift_ms=10, preemph=0.97):
    """Log mel filterbank energies [frames, num_bins]: Kaldi defaults with dither 0 and snip_edges false (frames are centred and
    the signal is mirrored at both ends), povey window, DC removed, power spectrum, 512-point FFT."""
    x = np.asarray(samples, dtype=np.float64)
    win, shift = sr * frame_ms // 1000, sr * shift_ms // 1000
    n_fft = 1
    while n_fft < win:
        n_fft *= 2
    n = len(x)
    frames = (n + shift // 2) // shift
    if frames <= 0:
        return np.zeros((0, num_bins), dtype=np.float32)
    idx = (np.arange(frames) * shift + shift // 2 - win // 2)[:, None] + np.arange(win)[None, :]
    for _ in range(4):                                                  # mirror out-of-range samples back into the signal (Kaldi's reflection)
        idx = np.where(idx < 0, -idx - 1, idx)
        idx = np.where(idx >= n, 2 * n - 1 - idx, idx)
    idx = np.clip(idx, 0, n - 1)
    w = x[idx]
    w = w - w.mean(axis=1, keepdims=True)
    w = np.concatenate([w[:, :1] * (1 - preemph), w[:, 1:] - preemph * w[:, :-1]], axis=1)
    w = w * np.power(0.5 - 0.5 * np.cos(2 * np.pi * np.arange(win) / (win - 1)), 0.85)
    power = np.abs(np.fft.rfft(w, n=n_fft, axis=1)) ** 2
    key = (num_bins, n_fft, sr)
    if key not in _FB:
        _FB[key] = mel_filters(num_bins, n_fft, sr)
    energies = power @ _FB[key].T
    return np.log(np.maximum(energies, np.finfo(np.float32).eps)).astype(np.float32)


# ------------------------------------------------------------------ the model
class Embedder:
    def __init__(self, model_path):
        import onnxruntime as ort
        so = ort.SessionOptions()
        so.intra_op_num_threads = 1
        so.log_severity_level = 3
        self.sess = ort.InferenceSession(model_path, sess_options=so, providers=["CPUExecutionProvider"])
        meta = self.sess.get_modelmeta().custom_metadata_map or {}
        self.meta = dict(meta)
        self.normalize_samples = str(meta.get("normalize_samples", "1")).strip() not in ("0", "false", "False")
        self.feature_normalize = str(meta.get("feature_normalize_type", "")).strip()
        # Deliberately NOT "improved": sherpa-onnx normalises the features only when the model's metadata asks for it, and this
        # backend must give the same fingerprints as sherpa-onnx or the saved voice prints would stop matching.
        # Measured on this PC against sherpa-onnx itself, same clips: cosine 1.0000 on 6 of 6 (speech, a one-word clip, noise).
        self.sample_rate = int(meta.get("sample_rate", SAMPLE_RATE) or SAMPLE_RATE)
        self.input_name = self.sess.get_inputs()[0].name
        dim = self.sess.get_inputs()[0].shape[-1]
        self.feat_dim = dim if isinstance(dim, int) else 80

    def features(self, samples):
        x = np.asarray(samples, dtype=np.float32)
        if not self.normalize_samples:                                   # the model was trained on int16-range audio
            x = x * 32768.0
        f = fbank(x, sr=self.sample_rate, num_bins=self.feat_dim)
        if self.feature_normalize == "global-mean":
            f = f - f.mean(axis=0, keepdims=True)
        elif self.feature_normalize == "global-mean-variance":
            f = (f - f.mean(axis=0, keepdims=True)) / (f.std(axis=0, keepdims=True) + 1e-5)
        return f.astype(np.float32)

    def embed(self, samples):
        """Unit-length voice fingerprint of float samples in [-1, 1] at 16 kHz, or None."""
        f = self.features(samples)
        if len(f) < 10:
            return None
        e = np.asarray(self.sess.run(None, {self.input_name: f[None, :, :]})[0]).reshape(-1).astype(np.float32)
        n = float(np.linalg.norm(e))
        return e / n if n > 0 else None


def prepare(audio):
    """Exactly the clip preparation JARVIS's own spk_embed() does before the model: 16 kHz floats, a very short clip repeated,
    loudness evened out."""
    x = np.frombuffer(audio.get_raw_data(convert_rate=SAMPLE_RATE, convert_width=2), dtype=np.int16).astype(np.float32) / 32768.0
    if len(x) < SAMPLE_RATE * 0.3:
        return None
    if len(x) < SAMPLE_RATE * 0.6:
        x = np.tile(x, int(np.ceil(SAMPLE_RATE * 0.9 / len(x))))
    peak = float(np.max(np.abs(x))) or 1.0
    return np.clip(x * min(20.0, 0.7 / peak), -1.0, 1.0)


def install(host):
    need = ("spk_ready", "spk_embed", "spk_model_path", "get_config", "log_debug", "_SPK")
    if any(k not in host for k in need) or getattr(host["spk_ready"], "_voice_ort", False):
        return False
    orig_ready, orig_embed = host["spk_ready"], host["spk_embed"]
    state = {"emb": None, "tried": False}

    def backend():
        """The onnxruntime embedder, or None. Only when sherpa-onnx itself is not working (and the owner did not switch this off)."""
        if host["_SPK"].get("ex") is not None or host["get_config"]("spk_backend") == "off":
            return None
        if state["emb"] is None and not state["tried"]:
            state["tried"] = True
            try:
                path = host["spk_model_path"]()
                if path:
                    state["emb"] = Embedder(path)
            except Exception as e:
                host["log_debug"]("voice_ort", e)
        return state["emb"]

    def spk_ready():
        if orig_ready():
            return True                                                  # sherpa-onnx works on this PC: nothing changes
        try:
            return backend() is not None
        except Exception as e:
            host["log_debug"]("voice_ort", e)
            return False

    def spk_embed(audio):
        if host["_SPK"].get("ex") is not None:
            return orig_embed(audio)
        try:
            b = backend()
            if b is None:
                return orig_embed(audio)
            x = prepare(audio)
            return None if x is None else b.embed(x)
        except Exception as e:
            host["log_debug"]("voice_ort", e)
            return None

    spk_ready._voice_ort = True
    host["spk_ready"], host["spk_embed"] = spk_ready, spk_embed
    return True


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    db = os.path.join(ROOT, "jarvis_memory.db")
    if "--on" in argv or "--off" in argv:
        con = sqlite3.connect(db)
        con.execute("CREATE TABLE IF NOT EXISTS config (key TEXT PRIMARY KEY, val TEXT)")
        if "--on" in argv:
            con.execute("DELETE FROM config WHERE key='spk_backend'")          # on is the default
        else:
            con.execute("INSERT OR REPLACE INTO config (key, val) VALUES ('spk_backend', 'off')")
        con.commit()
        con.close()
        print("Voice model through onnxruntime when sherpa-onnx is blocked: " + ("ON (the default)." if "--on" in argv else "OFF (when sherpa-onnx is blocked, the built-in lock is used, as before)."))
        return 0
    try:
        import onnxruntime
        ort = f"installed ({onnxruntime.__version__})"
    except Exception as e:
        ort = f"NOT usable: {type(e).__name__}"
    from ._host import load
    L = load()
    print(f"onnxruntime (signed by Microsoft) : {ort}")
    print(f"speaker model file                : {L.spk_model_path() or 'missing (run --setup-voiceid)'}")
    print(f"sherpa-onnx on this PC            : {'works' if L._SPK.get('ex') is not None or _sherpa_ok() else 'blocked / not installed'}")
    print(f"fallback to onnxruntime           : {'OFF' if L.get_config('spk_backend') == 'off' else 'on (used only when sherpa-onnx cannot load)'}")
    print(f"strong voice lock usable right now: {bool(L.spk_ready())}")
    return 0


def _sherpa_ok():
    try:
        import sherpa_onnx  # noqa: F401
        return True
    except Exception:
        return False


if __name__ == "__main__":
    sys.exit(main())
