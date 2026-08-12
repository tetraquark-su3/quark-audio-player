"""
ui/playlist_persistence.py
Pure-Python JSON load/save for playlist data. No Qt/VLC dependency —
callers own file dialogs / message boxes and any UI-level feedback.
"""

from __future__ import annotations

import json
import os


class PlaylistPersistenceError(Exception):
    """Raised when a playlist JSON file can't be read, parsed, or written."""


class PlaylistPersistence:
    """Reads/writes playlist track lists as flat JSON arrays of dicts."""

    def __init__(self, default_path: str) -> None:
        """default_path: fixed autosave/autoload location."""
        self._default_path = default_path

    @property
    def default_path(self) -> str:
        return self._default_path

    def load_default(self) -> list[dict] | None:
        """Load from default_path. Returns None if the file doesn't exist
        (not an error — first run). Raises PlaylistPersistenceError on any
        read/parse failure."""
        if not os.path.exists(self._default_path):
            return None
        return self.load(self._default_path)

    def save_default(self, tracks: list[dict]) -> None:
        """Save to default_path. Raises PlaylistPersistenceError on failure."""
        self.save(self._default_path, tracks)

    def load(self, path: str) -> list[dict]:
        """Load and parse a playlist JSON file. Raises
        PlaylistPersistenceError wrapping the original exception on any
        read/parse failure."""
        try:
            with open(path) as f:
                return json.load(f)
        except Exception as e:
            raise PlaylistPersistenceError(str(e)) from e

    def save(self, path: str, tracks: list[dict]) -> None:
        """Write tracks as indented JSON. Raises PlaylistPersistenceError on
        any write failure."""
        try:
            with open(path, "w") as f:
                json.dump(tracks, f, indent=2)
        except Exception as e:
            raise PlaylistPersistenceError(str(e)) from e
