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

SINGLE_INSTANCE_PORT = 47847

BASE_DIR   = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
ASSETS_DIR = os.path.join(BASE_DIR, "assets")


def _try_send_to_existing(paths: list[str]) -> bool:
    """Try to forward paths to a running instance. Returns True on success."""
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(0.5)
        sock.connect(("127.0.0.1", SINGLE_INSTANCE_PORT))
        for path in paths:
            sock.sendall((path + "\n").encode())
        sock.close()
        return True
    except (ConnectionRefusedError, OSError):
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

    # ── Early exit: forward to existing instance without loading anything heavy
    if path_arg and _try_send_to_existing([path_arg]):
        return

    # ── Only import heavy modules if we're actually starting the app ──
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

    # If launched with a file path (and no existing instance), add and play it.
    # _add_file is now async — play_when_ready handles the sequencing.
    if path_arg and os.path.isfile(path_arg) and is_audio(path_arg):
        window._add_file(path_arg, play_when_ready=True)

    t = threading.Thread(target=_start_listener, args=(window,), daemon=True)
    t.start()

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
