"""
main.py
Entry point for Quark Audio Player.

Dependencies:
    pip install PyQt6 python-vlc soundfile mutagen
    sudo apt install vlc
"""
# ── Minimal imports first — before any heavy modules ──────────────────
import os
import sys
import socket
import threading
import time
import fcntl

SINGLE_INSTANCE_PORT = 47847

BASE_DIR   = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
ASSETS_DIR = os.path.join(BASE_DIR, "assets")

# Lock file path — created as soon as the first instance starts
LOCK_FILE  = os.path.join(os.path.expanduser("~"), ".config", "quark_audio_player.lock")


def _acquire_lock():
    """
    Try to acquire an exclusive lock on LOCK_FILE.
    Returns the open file handle on success (lock held as long as handle is open),
    or None if another instance already holds the lock.
    Uses fcntl.LOCK_EX | LOCK_NB so it never blocks.
    """
    os.makedirs(os.path.dirname(LOCK_FILE), exist_ok=True)
    try:
        fh = open(LOCK_FILE, "w")
        fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
        fh.write(str(os.getpid()))
        fh.flush()
        return fh
    except OSError:
        try:
            fh.close()
        except Exception:
            pass
        return None


def _try_send_to_existing(paths: list) -> bool:
    """
    Forward paths to the running instance via socket.
    Retries for up to 2 s in case the listener thread hasn't started yet.
    """
    for attempt in range(8):
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(0.5)
            sock.connect(("127.0.0.1", SINGLE_INSTANCE_PORT))
            for path in paths:
                sock.sendall((path + "\n").encode())
            sock.close()
            return True
        except (ConnectionRefusedError, OSError):
            if attempt < 7:
                time.sleep(0.25)
    return False


def _start_listener(window) -> None:
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind(("127.0.0.1", SINGLE_INSTANCE_PORT))
    server.listen(5)
    while True:
        conn, _ = server.accept()
        data = conn.recv(4096).decode()
        conn.close()
        for path in data.strip().split("\n"):
            if path and os.path.isfile(path):
                window._socket_file_received.emit(path)


def main() -> None:
    args     = sys.argv[1:]
    path_arg = " ".join(args) if args else None

    # ── Single-instance check via lock file (instantaneous, no race condition) ──
    lock_fh = _acquire_lock()
    if lock_fh is None:
        # Another instance is running — forward the file and exit immediately
        if path_arg:
            _try_send_to_existing([path_arg])
        return

    # ── We are the first instance — keep lock_fh open for the lifetime of the app ──

    import vlc_setup  # must come before `import vlc` inside MainWindow

    from PyQt6.QtGui     import QIcon
    from PyQt6.QtWidgets import QApplication
    from ui.main_window  import MainWindow
    from config.settings import is_audio

    app = QApplication(sys.argv)
    app.setApplicationName("Quark Audio Player")
    icon_path = os.path.join(ASSETS_DIR, "icon_app.png")
    if os.path.exists(icon_path):
        app.setWindowIcon(QIcon(icon_path))

    window = MainWindow()
    window.show()

    if path_arg and os.path.isfile(path_arg) and is_audio(path_arg):
        window._ingestion.add_file(path_arg, play_when_ready=True)

    t = threading.Thread(target=_start_listener, args=(window,), daemon=True)
    t.start()

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
