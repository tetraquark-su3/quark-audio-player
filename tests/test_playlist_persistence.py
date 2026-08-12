"""
tests/test_playlist_persistence.py
Unit tests for ui.playlist_persistence.PlaylistPersistence — pure
Python/json, no Qt/VLC involved, no conftest.py needed.
"""

import pytest

from ui.playlist_persistence import PlaylistPersistence, PlaylistPersistenceError

TRACKS = [
    {
        "track": "1",
        "artist": "A",
        "album": "B",
        "title": "C",
        "duration": "3:00",
        "path": "/music/a.mp3",
    },
]


def test_save_then_load_round_trip(tmp_path):
    p = PlaylistPersistence(default_path=str(tmp_path / "default.json"))
    target = tmp_path / "out.json"
    p.save(str(target), TRACKS)
    assert p.load(str(target)) == TRACKS


def test_save_default_then_load_default_round_trip(tmp_path):
    default_path = str(tmp_path / "playlist.json")
    p = PlaylistPersistence(default_path=default_path)
    p.save_default(TRACKS)
    assert p.load_default() == TRACKS


def test_load_default_missing_file_returns_none(tmp_path):
    p = PlaylistPersistence(default_path=str(tmp_path / "missing.json"))
    assert p.load_default() is None


def test_load_missing_path_raises(tmp_path):
    p = PlaylistPersistence(default_path=str(tmp_path / "default.json"))
    with pytest.raises(PlaylistPersistenceError):
        p.load(str(tmp_path / "nope.json"))


def test_load_corrupt_json_raises(tmp_path):
    bad = tmp_path / "corrupt.json"
    bad.write_text("{not valid json")
    p = PlaylistPersistence(default_path=str(tmp_path / "default.json"))
    with pytest.raises(PlaylistPersistenceError):
        p.load(str(bad))


def test_save_and_load_empty_playlist(tmp_path):
    p = PlaylistPersistence(default_path=str(tmp_path / "default.json"))
    target = tmp_path / "empty.json"
    p.save(str(target), [])
    assert p.load(str(target)) == []


def test_save_raises_on_unwritable_directory(tmp_path):
    p = PlaylistPersistence(default_path=str(tmp_path / "default.json"))
    unwritable_dir = tmp_path / "does_not_exist" / "sub"
    with pytest.raises(PlaylistPersistenceError):
        p.save(str(unwritable_dir / "out.json"), [])
