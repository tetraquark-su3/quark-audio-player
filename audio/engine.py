"""
audio/engine.py
Audio utilities: metadata extraction, FFT helpers, playlist serialisation.
"""

import os
import threading
from typing import Optional

import numpy as np
import soundfile as sf
from mutagen import File as MutagenFile
from mutagen.id3 import APIC
from mutagen.mp4 import MP4


# ---------------------------------------------------------------------------
# Metadata
# ---------------------------------------------------------------------------

def read_metadata(path: str) -> dict:
    """
    Return a dict with keys: title, artist, album, track, duration_str,
    bitrate, sample_rate, channels, format.
    All values are strings.  Falls back to safe defaults on error.
    """
    filename = os.path.basename(path)
    result = {
        "title":       filename,
        "artist":      "Unknown",
        "album":       "",
        "track":       "",
        "year":        "",
        "duration_str": "0:00",
        "bitrate":     "",
        "sample_rate": "",
        "channels":    "",
        "format":      "",
    }
    try:
        meta = MutagenFile(path, easy=True)
        if meta is None:
            return result
        if meta.tags:
            def tag(key: str, fallback: str = "") -> str:
                values = meta.tags.get(key, [fallback])
                return values[0] if values else fallback

            result["title"]  = tag("title",  filename)
            result["artist"] = tag("artist", "Unknown")
            result["album"]  = tag("album",  "")
            raw_track = tag("tracknumber", "")
            result["track"]  = raw_track.split("/")[0] if raw_track else ""
            raw_year = tag("date", "") or tag("year", "")
            result["year"]   = raw_year[:4] if raw_year else ""

        if meta.info:
            dur = int(meta.info.length)
            result["duration_str"] = f"{dur // 60}:{dur % 60:02d}"

            br = getattr(meta.info, "bitrate",     0) or 0
            sr = getattr(meta.info, "sample_rate", 0) or 0
            ch = getattr(meta.info, "channels",    0) or 0

            if br:
                result["bitrate"]     = f"{br // 1000} kbps"
            if sr:
                result["sample_rate"] = f"{sr} Hz"
            if ch:
                result["channels"]    = "Mono" if ch == 1 else f"{ch} ch"

            result["format"] = meta.info.__class__.__name__.replace("Info", "")
    except Exception:
        pass
    return result


def read_album_art(path: str) -> Optional[bytes]:
    """Return raw bytes of the embedded album art, or None."""
    try:
        meta = MutagenFile(path)
        if meta is None:
            return None
        if hasattr(meta, "tags") and meta.tags:
            for tag in meta.tags.values():
                if isinstance(tag, APIC):
                    return tag.data
        if isinstance(meta, MP4):
            covers = meta.tags.get("covr", [])
            if covers:
                return bytes(covers[0])
        if hasattr(meta, "pictures") and meta.pictures:
            return meta.pictures[0].data
    except Exception:
        pass
    return None


def build_detail_text(path: str) -> str:
    """Return a human-readable multi-line string of all tags + tech info."""
    try:
        meta = MutagenFile(path, easy=True)
        if not meta:
            return "No metadata available."
        lines = []
        if meta.tags:
            for key, value in meta.tags.items():
                lines.append(f"{key.capitalize()}: {', '.join(str(v) for v in value)}")
        if meta.info:
            dur = int(meta.info.length)
            lines += ["", "=== Technical Info ===",
                      f"Duration: {dur // 60}:{dur % 60:02d}"]
            br = getattr(meta.info, "bitrate",     0) or 0
            sr = getattr(meta.info, "sample_rate", 0) or 0
            ch = getattr(meta.info, "channels",    0) or 0
            if br:
                lines.append(f"Bitrate: {br // 1000} kbps")
            if sr:
                lines.append(f"Sample rate: {sr} Hz")
            if ch:
                lines.append(f"Channels: {'Mono' if ch == 1 else ch}")
            lines.append(f"Format: {meta.info.__class__.__name__.replace('Info', '')}")
        return "\n".join(lines)
    except Exception as e:
        return f"Error reading metadata: {e}"


# ---------------------------------------------------------------------------
# Sample loading (background thread)
# ---------------------------------------------------------------------------

class SampleLoader:
    """
    Loads an audio file's samples in a background daemon thread.
    Access .samples and .sample_rate after the thread completes.
    """

    def __init__(self) -> None:
        self.samples: Optional[np.ndarray] = None
        self.sample_rate: int = 0
        self._thread: Optional[threading.Thread] = None
        self._cancelled = False

    def load(self, path: str) -> None:
        """Start (or restart) background loading for *path*."""
        self._cancelled = True   # cancel all pending threads
        self.samples     = None
        self.sample_rate = 0
        self._cancelled  = False  # repart à zéro pour le nouveau thread
        self._thread = threading.Thread(target=self._run, args=(path,), daemon=True)
        self._thread.start()

    FALLBACK_RATE = 44_100
    FALLBACK_CH   = 2

    def _run(self, path: str) -> None:
        # Primary: soundfile
        try:
            samples, rate = sf.read(path, dtype="float32", always_2d=True)
            if not self._cancelled:   # checking before writting
                self.samples     = samples
                self.sample_rate = rate
            return
        except Exception as e:
            print(f"[SampleLoader] soundfile failed ({e}), trying ffmpeg...")
        # Fallback: ffmpeg -> raw f32le PCM
        self._run_ffmpeg(path)

    def _run_ffmpeg(self, path: str) -> None:
        import subprocess, shutil
        ffmpeg = shutil.which("ffmpeg") or shutil.which("avconv")
        if ffmpeg is None:
            print("[SampleLoader] ffmpeg not found; visualisations disabled.")
            return
        cmd = [
            ffmpeg, "-hide_banner", "-loglevel", "error",
            "-i", path,
            "-f", "f32le", "-acodec", "pcm_f32le",
            "-ar", str(self.FALLBACK_RATE),
            "-ac", str(self.FALLBACK_CH),
            "pipe:1",
        ]
        try:
            result = subprocess.run(cmd, stdout=subprocess.PIPE,
                                    stderr=subprocess.PIPE, timeout=120)
            raw = result.stdout
            if not raw:
                print(f"[SampleLoader] ffmpeg no output: {result.stderr.decode()[:200]}")
                return
            flat    = np.frombuffer(raw, dtype=np.float32)
            samples = flat.reshape(-1, self.FALLBACK_CH)
            if not self._cancelled:   # checking before writing
                self.samples     = samples
                self.sample_rate = self.FALLBACK_RATE
        except Exception as e:
            print(f"[SampleLoader] ffmpeg fallback failed: {e}")


# ---------------------------------------------------------------------------
# FFT helpers
# ---------------------------------------------------------------------------

FFT_BLOCK = 2048
FFT_BINS  = 512


def compute_fft_frame(
    samples: np.ndarray,
    position: float,
    bar_count: int,
) -> Optional[dict]:
    """
    Given a float32 (N, channels) array and a normalised playback position
    [0, 1], return a dict with keys:
        bars        - list[float] of length bar_count  (log-spaced, normalised)
        left        - list[float] mono normalised waveform (left channel)
        right       - list[float] mono normalised waveform (right channel)
        mono        - list[float] averaged waveform
    Returns None if the block cannot be extracted.
    """
    if samples is None or len(samples) < FFT_BLOCK:
        return None
    if not (0.0 <= position <= 1.0):
        return None

    n = len(samples)
    idx = min(int(position * n), n - FFT_BLOCK)
    idx = max(0, idx)
    block = samples[idx : idx + FFT_BLOCK]

    if block.shape[0] < FFT_BLOCK:
        return None

    # Ensure stereo
    if block.ndim < 2 or block.shape[1] < 2:
        ch = block[:, 0] if block.ndim == 2 else block
        block = np.column_stack([ch, ch])

    left  = block[:, 0]
    right = block[:, 1]
    mono  = (left + right) * 0.5

    # FFT
    fft = np.abs(np.fft.rfft(mono))[:FFT_BINS]
    fft = np.log1p(fft)
    mx  = fft.max()
    if mx > 0:
        fft /= mx

    # Log-spaced bar indices
    indices = np.logspace(0, np.log10(len(fft) - 1), bar_count, dtype=int)
    indices = np.clip(indices, 0, len(fft) - 1)
    bars = fft[indices].tolist()

    def norm(arr: np.ndarray) -> list:
        mx = np.abs(arr).max()
        return (arr / mx).tolist() if mx > 0 else arr.tolist()

    return {
        "bars":  bars,
        "left":  norm(left),
        "right": norm(right),
        "mono":  norm(mono),
    }
