from ui.playlist_stats import (
    compute_playlist_stats,
    format_stats_html,
    format_total,
    parse_duration,
)


def T(artist, album, dur):
    return {"artist": artist, "album": album, "duration": dur,
            "title": "x", "track": "", "path": "/x"}


def test_parse_duration():
    assert parse_duration("3:05") == 185
    assert parse_duration("75:03") == 4503
    assert parse_duration("") == 0
    assert parse_duration("abc") == 0
    assert parse_duration("3") == 0


def test_format_total():
    assert format_total(185) == "3:05"
    assert format_total(3600) == "1:00:00"
    assert format_total(4503) == "1:15:03"
    assert format_total(-5) == "0:00"


def test_empty():
    s = compute_playlist_stats([])
    assert s["count"] == 0 and s["total_seconds"] == 0
    assert s["top_artists"] == []
    assert "empty" in format_stats_html(s)


def test_aggregates():
    tracks = [
        T("A", "Alb1", "3:00"),
        T("A", "Alb1", "2:00"),
        T("B", "Alb2", "10:00"),
        T("Unknown", "", "1:00"),
        T("", "", "bad"),
    ]
    s = compute_playlist_stats(tracks)
    assert s["count"] == 5
    assert s["total_seconds"] == 180 + 120 + 600 + 60
    assert s["artist_count"] == 2
    assert s["album_count"] == 2
    assert s["unknown_artist_tracks"] == 2
    assert s["top_artists"] == [("A", 2, 300), ("B", 1, 600)]


def test_top_n_limit():
    tracks = [T(f"Artist{i}", "", "1:00") for i in range(15)]
    assert len(compute_playlist_stats(tracks, top_n=10)["top_artists"]) == 10


def test_html_escapes_artist():
    s = compute_playlist_stats([T("<b>x</b>", "", "1:00")])
    html = format_stats_html(s)
    assert "<b>x</b>" not in html.split("Top artists")[1]
    assert "&lt;b&gt;" in html
