"""
audio/engine.py
Audio utilities: metadata extraction, FFT helpers, playlist serialisation.
"""

import os
import threading
from typing import Optional

import numpy as np
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


def _shrink_image_bytes(data: bytes, max_px: int = 800) -> bytes:
    """Resize oversized image bytes using Pillow — no Qt, safe in any thread."""
    try:
        from PIL import Image
        import io
        img = Image.open(io.BytesIO(data))
        if img.width <= max_px and img.height <= max_px:
            return data
        img.thumbnail((max_px, max_px), Image.LANCZOS)
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=90)
        return buf.getvalue()
    except Exception:
        return data


def read_album_art(path: str) -> tuple[Optional[bytes], Optional[bytes]]:
    """
    Return (original_bytes, display_bytes) of the embedded album art.
    original_bytes: full-resolution bytes for saving.
    display_bytes:  shrunk copy for display (same as original if small enough).
    Both are None if no art found.
    """
    try:
        meta = MutagenFile(path)
        if meta is None:
            return None, None
        raw = None
        if hasattr(meta, "tags") and meta.tags:
            for tag in meta.tags.values():
                if isinstance(tag, APIC):
                    raw = tag.data
                    break
        if raw is None and isinstance(meta, MP4):
            covers = meta.tags.get("covr", [])
            if covers:
                raw = bytes(covers[0])
        if raw is None and hasattr(meta, "pictures") and meta.pictures:
            raw = meta.pictures[0].data
        if raw is None:
            return None, None
        display = _shrink_image_bytes(raw) if len(raw) > 300_000 else raw
        return raw, display
    except Exception:
        pass
    return None, None


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

    Also tracks the measured VLC audio latency so that _compute_sync_pos()
    can return a position that is aligned with what the DAC is actually
    outputting rather than what the VLC decoder has reached.
    """

    def __init__(self) -> None:
        self.samples:     Optional[np.ndarray] = None
        self.sample_rate: int   = 0
        self.duration:    float = 0.0   # seconds, set when samples are loaded
        self._thread:     Optional[threading.Thread] = None
        self._cancel_evt  = threading.Event()

    def load(self, path: str) -> None:
        """Start (or restart) background loading for *path*."""
        self._cancel_evt.set()          # signal any running thread to stop
        self._cancel_evt = threading.Event()   # fresh event for new thread
        self.samples     = None
        self.sample_rate = 0
        self.duration    = 0.0
        self._thread = threading.Thread(
            target=self._run, args=(path, self._cancel_evt), daemon=True
        )
        self._thread.start()

    FALLBACK_RATE = 44_100
    FALLBACK_CH   = 2

    def _run(self, path: str, cancel: threading.Event) -> None:
        # Primary: soundfile
        try:
            import soundfile as sf
            samples, rate = sf.read(path, dtype="float32", always_2d=True)
            if not cancel.is_set():
                self.samples     = samples
                self.sample_rate = rate
                self.duration    = len(samples) / rate
            return
        except Exception as e:
            print(f"[SampleLoader] soundfile failed ({e}), trying ffmpeg...")
        self._run_ffmpeg(path, cancel)

    def _run_ffmpeg(self, path: str, cancel: threading.Event) -> None:
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
            if not cancel.is_set():
                self.samples     = samples
                self.sample_rate = self.FALLBACK_RATE
                self.duration    = len(samples) / self.FALLBACK_RATE
        except Exception as e:
            print(f"[SampleLoader] ffmpeg fallback failed: {e}")


def compute_sync_pos(vlc_ms: int, duration_s: float, vlc_audio_delay_ms: int = 0) -> float:
    """
    Convert VLC's reported playback position (milliseconds) to a normalised
    [0, 1] position that is compensated for the VLC audio output delay.

    VLC's get_time() returns the position of the *decoder*, which is ahead of
    what the DAC is actually playing by roughly the audio buffer latency.
    libvlc_media_player_get_aout_delay() (exposed as player.audio_get_delay()
    in python-vlc) returns this offset in microseconds — we subtract it so
    the visualisation window is centred on the sample the user actually hears.

    Parameters
    ----------
    vlc_ms            : player.get_time()  (milliseconds, may be -1)
    duration_s        : total duration of the loaded samples (seconds)
    vlc_audio_delay_ms: player.audio_get_delay() // 1000  (milliseconds)
                        Pass 0 if you cannot read it (safe fallback).
    """
    if vlc_ms < 0 or duration_s <= 0:
        return 0.0
    compensated_ms = max(0, vlc_ms - vlc_audio_delay_ms)
    return min(compensated_ms / 1000.0 / duration_s, 1.0)


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
