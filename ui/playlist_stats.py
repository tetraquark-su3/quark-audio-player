"""
Playlist statistics — pure Python, no Qt/VLC dependency.

Operates on the list-of-dicts format produced by ``PlaylistWidget.to_list()``
(keys: track, artist, album, title, duration, path), where ``duration`` is a
"m:ss" string with unbounded minutes (e.g. "75:03").
"""
from __future__ import annotations

from collections import defaultdict

UNKNOWN_ARTIST = "Unknown"


def parse_duration(s: str) -> int:
    """Parse "m:ss" into seconds. Returns 0 for anything malformed."""
    parts = (s or "").split(":")
    try:
        return int(parts[0]) * 60 + int(parts[1])
    except (ValueError, IndexError):
        return 0


def format_total(seconds: int) -> str:
    """Format seconds as "h:mm:ss" (or "m:ss" under an hour)."""
    h, rem = divmod(max(0, int(seconds)), 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def compute_playlist_stats(tracks: list[dict], top_n: int = 10) -> dict:
    """
    Return aggregate stats for a playlist:
      count, total_seconds, artist_count, album_count,
      top_artists: [(artist, track_count, seconds)] sorted by track count
      desc, then seconds desc, then name — limited to ``top_n``.
    Tracks with an empty/"Unknown" artist are excluded from artist_count and
    top_artists (reported separately as ``unknown_artist_tracks``). Empty
    albums are excluded from album_count.
    """
    total = 0
    albums: set[str] = set()
    per_artist: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    unknown = 0

    for t in tracks:
        dur = parse_duration(t.get("duration", ""))
        total += dur
        album = (t.get("album") or "").strip()
        if album:
            albums.add(album)
        artist = (t.get("artist") or "").strip()
        if not artist or artist == UNKNOWN_ARTIST:
            unknown += 1
            continue
        entry = per_artist[artist]
        entry[0] += 1
        entry[1] += dur

    top = sorted(
        ((a, c, s) for a, (c, s) in per_artist.items()),
        key=lambda x: (-x[1], -x[2], x[0].lower()),
    )[:top_n]

    return {
        "count": len(tracks),
        "total_seconds": total,
        "artist_count": len(per_artist),
        "album_count": len(albums),
        "unknown_artist_tracks": unknown,
        "top_artists": top,
    }


def format_stats_html(stats: dict) -> str:
    """Render stats as a small HTML fragment for a QTextEdit."""
    from html import escape

    if stats["count"] == 0:
        return "<p>The playlist is empty.</p>"

    rows = [
        f"<b>Tracks:</b> {stats['count']}",
        f"<b>Total duration:</b> {format_total(stats['total_seconds'])}",
        f"<b>Artists:</b> {stats['artist_count']}",
        f"<b>Albums:</b> {stats['album_count']}",
    ]
    if stats["unknown_artist_tracks"]:
        rows.append(
            f"<b>Tracks without artist:</b> {stats['unknown_artist_tracks']}"
        )
    out = "<p>" + "<br>".join(rows) + "</p>"

    if stats["top_artists"]:
        out += "<p><b>Top artists</b></p><ol>"
        for artist, count, secs in stats["top_artists"]:
            word = "track" if count == 1 else "tracks"
            out += (f"<li>{escape(artist)} — {count} {word}, "
                    f"{format_total(secs)}</li>")
        out += "</ol>"
    return out
