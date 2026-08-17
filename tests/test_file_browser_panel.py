"""
tests/test_file_browser_panel.py
Covers only the pure helper `list_mount_roots` from ui.file_browser_panel —
OS drive/mount-point enumeration, with no Qt involved. FileBrowserPanel
itself (QTreeView/QFileSystemModel wiring, context menu, keyPressEvent)
constructs real Qt widgets and requires a live QApplication, which this
project does not bootstrap yet (no pytest-qt, no conftest.py) — it is
intentionally not covered here.
"""

from ui.file_browser_panel import list_mount_roots


def test_windows_lists_only_existing_drive_letters(monkeypatch):
    monkeypatch.setattr("ui.file_browser_panel.sys.platform", "win32")
    monkeypatch.setattr(
        "ui.file_browser_panel.os.path.exists",
        lambda p: p in ("C:\\", "D:\\"),
    )
    assert list_mount_roots() == [("C:\\", "C:\\"), ("D:\\", "D:\\")]


def test_windows_no_drives_returns_empty_list(monkeypatch):
    monkeypatch.setattr("ui.file_browser_panel.sys.platform", "win32")
    monkeypatch.setattr("ui.file_browser_panel.os.path.exists", lambda p: False)
    assert list_mount_roots() == []


def test_linux_always_includes_root(monkeypatch):
    monkeypatch.setattr("ui.file_browser_panel.sys.platform", "linux")
    monkeypatch.setattr("ui.file_browser_panel.os.environ", {"USER": "alice"})
    monkeypatch.setattr("ui.file_browser_panel.os.path.isdir", lambda p: False)
    assert list_mount_roots() == [("Root /", "/")]


def test_linux_lists_mounted_entries_under_run_media_and_mnt(monkeypatch):
    monkeypatch.setattr("ui.file_browser_panel.sys.platform", "linux")
    monkeypatch.setattr("ui.file_browser_panel.os.environ", {"USER": "alice"})
    monkeypatch.setattr(
        "ui.file_browser_panel.os.path.isdir",
        lambda p: p in ("/run/media/alice", "/mnt"),
    )
    monkeypatch.setattr(
        "ui.file_browser_panel.os.listdir",
        lambda base: ["usb1"] if base == "/run/media/alice" else ["data"],
    )
    monkeypatch.setattr("ui.file_browser_panel.os.path.ismount", lambda p: True)
    assert list_mount_roots() == [
        ("Root /", "/"),
        ("usb1", "/run/media/alice/usb1"),
        ("data", "/mnt/data"),
    ]


def test_linux_skips_non_mounted_entries(monkeypatch):
    monkeypatch.setattr("ui.file_browser_panel.sys.platform", "linux")
    monkeypatch.setattr("ui.file_browser_panel.os.environ", {"USER": "alice"})
    monkeypatch.setattr(
        "ui.file_browser_panel.os.path.isdir", lambda p: p == "/run/media/alice"
    )
    monkeypatch.setattr(
        "ui.file_browser_panel.os.listdir", lambda base: ["not_a_mount"]
    )
    monkeypatch.setattr("ui.file_browser_panel.os.path.ismount", lambda p: False)
    assert list_mount_roots() == [("Root /", "/")]
