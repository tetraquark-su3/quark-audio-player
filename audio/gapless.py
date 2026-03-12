"""
audio/gapless.py
GaplessEngine — seamless track transitions via sounddevice PCM streaming.
"""

from __future__ import annotations

import threading
from typing import Callable, Optional

import numpy as np

HANDOFF_THRESHOLD = 3.0   # seconds before end to trigger handoff
BLOCK_SIZE        = 2048
TARGET_RATE       = 44_100


def _to_stereo(samples: np.ndarray) -> np.ndarray:
    if samples.ndim == 1:
        samples = samples[:, np.newaxis]
    if samples.shape[1] == 1:
        samples = np.concatenate([samples, samples], axis=1)
    elif samples.shape[1] > 2:
        samples = samples[:, :2]
    return samples.astype(np.float32, copy=False)


def _resample(samples: np.ndarray, src_rate: int, dst_rate: int) -> np.ndarray:
    if src_rate == dst_rate:
        return samples
    n_src       = len(samples)
    n_dst       = int(n_src * dst_rate / src_rate)
    src_indices = np.linspace(0, n_src - 1, n_dst)
    lo          = np.floor(src_indices).astype(int)
    hi          = np.clip(lo + 1, 0, n_src - 1)
    frac        = (src_indices - lo)[:, np.newaxis]
    return (samples[lo] * (1.0 - frac) + samples[hi] * frac).astype(np.float32)


def is_available() -> bool:
    """Return True if sounddevice + PortAudio are usable."""
    try:
        import sounddevice as sd
        sd.query_devices()  # raises if PortAudio missing
        return True
    except Exception:
        return False


class GaplessEngine:
    def __init__(self, on_track_end: Callable[[], None]) -> None:
        self._on_track_end       = on_track_end
        self._lock               = threading.Lock()
        self._stream             = None
        self._buffer             = None
        self._pos                = 0
        self._active             = False
        self._next_start         = 0
        self._next_fired         = False
        self._on_next_cb: Optional[Callable[[], None]] = None

    @property
    def is_active(self) -> bool:
        with self._lock:
            return self._active

    def handoff(
        self,
        cur_samples:    np.ndarray,
        cur_rate:       int,
        cur_frame:      int,
        nxt_samples:    np.ndarray,
        nxt_rate:       int,
        on_next_started: Callable[[], None],
    ) -> bool:
        try:
            import sounddevice as sd
        except Exception as e:
            print(f"[Gapless] sounddevice unavailable: {e}")
            return False

        self.stop()

        try:
            cur   = _to_stereo(_resample(cur_samples, cur_rate, TARGET_RATE))
            nxt   = _to_stereo(_resample(nxt_samples, nxt_rate, TARGET_RATE))
            start = max(0, int(cur_frame * TARGET_RATE / cur_rate))
            tail  = cur[start:]
            chain = np.concatenate([tail, nxt], axis=0)
        except Exception as e:
            print(f"[Gapless] buffer preparation failed: {e}")
            return False

        with self._lock:
            self._buffer       = chain
            self._pos          = 0
            self._next_start   = len(tail)
            self._active       = True
            self._next_fired   = False
            self._on_next_cb   = on_next_started

        try:
            self._stream = sd.OutputStream(
                samplerate        = TARGET_RATE,
                channels          = 2,
                dtype             = "float32",
                blocksize         = BLOCK_SIZE,
                callback          = self._callback,
                finished_callback = self._on_finished,
            )
            self._stream.start()
            print(f"[Gapless] started — tail={len(tail)} nxt={len(nxt)} frames")
            return True
        except Exception as e:
            print(f"[Gapless] stream open failed: {e}")
            with self._lock:
                self._active = False
            return False

    def stop(self) -> None:
        with self._lock:
            self._active = False
        if self._stream is not None:
            try:
                self._stream.stop()
                self._stream.close()
            except Exception:
                pass
            self._stream = None

    # ------------------------------------------------------------------
    # Audio thread callback
    # ------------------------------------------------------------------

    def _callback(self, outdata: np.ndarray, frames: int, time, status) -> None:
        import sounddevice as sd
        with self._lock:
            if not self._active or self._buffer is None:
                outdata[:] = 0
                raise sd.CallbackStop()

            pos   = self._pos
            buf   = self._buffer
            avail = len(buf) - pos

            if avail <= 0:
                outdata[:] = 0
                raise sd.CallbackStop()

            chunk = min(frames, avail)
            outdata[:chunk]  = buf[pos:pos + chunk]
            outdata[chunk:]  = 0
            self._pos        = pos + chunk

            # Fire on_next_started exactly once when crossing into next track
            if (not self._next_fired
                    and pos <= self._next_start < pos + chunk
                    and self._on_next_cb is not None):
                self._next_fired = True
                cb = self._on_next_cb
                # Schedule on Qt thread — can't call Qt from audio thread
                threading.Thread(target=cb, daemon=True).start()

    def _on_finished(self) -> None:
        with self._lock:
            self._active = False
        self._on_track_end()
