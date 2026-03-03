# Quark Audio Player v0.2

A PyQt6 audio player with real-time visualisations, equalizer, and full playlist management.

## Features

- 6 visualisation modes: Spectrum, Spectrogram, Oscilloscope, Lissajous, Spectral Flux, VU Meter
- 10-band equalizer with presets
- Playlist with duplicate detection, search, undo-delete, drag & drop
- Album art and metadata display
- Configurable colours, font, FPS, shortcuts
- File browser with natural sort and context menu
- Open audio files directly from your file manager

## Dependencies
```bash
pip install PyQt6 python-vlc soundfile mutagen numpy
sudo apt install vlc    # or equivalent for your distro
```

## Running
```bash
python main.py
```

## Building
```bash
python -m venv .venv
source .venv/bin/activate  # or .venv/bin/activate.fish on fish shell
pip install PyQt6 python-vlc soundfile mutagen numpy pyinstaller
pyinstaller --onefile --windowed --name quark-player \
  --add-data "assets:assets" \
  --add-binary "/usr/lib/libvlc.so.5:." \
  --add-binary "/usr/lib/libvlccore.so.9:." \
  --add-data "/usr/lib/vlc/plugins:vlc/plugins" \
  main.py
```

> VLC library paths vary by distro. Arch: `/usr/lib/`. Ubuntu/Mint: `/usr/lib/x86_64-linux-gnu/`.

## Project structure
```
quark_audio_player/
│
├── main.py                        # Entry point
├── vlc_setup.py                   # VLC path setup for packaged builds
│
├── config/
│   └── settings.py                # Defaults, load/save config
│
├── audio/
│   └── engine.py                  # Metadata, album art, FFT, sample loader
│
├── ui/
│   ├── main_window.py             # MainWindow — wires everything together
│   ├── playlist.py                # PlaylistWidget (O(1) duplicate check)
│   ├── visualizations.py          # 6 visualisation widgets
│   ├── dialogs.py                 # SettingsDialog, EqualizerDialog
│   ├── widgets.py                 # ClickableSlider, DropArea, ShortcutField
│   └── style.py                   # QSS stylesheet builder
│
└── assets/                        # Icons (PNG) and app icon
```

## Supported formats

MP3, FLAC, OGG, WAV, AAC, M4A, Opus, WMA
