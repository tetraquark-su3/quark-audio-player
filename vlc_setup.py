"""
vlc_setup.py
Must be imported before any other module that imports vlc.
Handles VLC library path setup for both frozen (PyInstaller) and dev builds,
on both Linux and Windows.
"""
import os
import sys

if getattr(sys, "frozen", False):
    _bundle = sys._MEIPASS
    if sys.platform == "win32":
        # Sur Windows, les DLL VLC sont bundlées à la racine du bundle
        os.environ["PYTHON_VLC_LIB_PATH"]    = os.path.join(_bundle, "libvlc.dll")
        os.environ["PYTHON_VLC_MODULE_PATH"] = os.path.join(_bundle, "plugins")
    else:
        # Linux : .so bundlé via --add-binary
        os.environ["PYTHON_VLC_LIB_PATH"]    = os.path.join(_bundle, "libvlc.so.5")
        os.environ["PYTHON_VLC_MODULE_PATH"] = os.path.join(_bundle, "vlc")
else:
    # Mode développement sur Windows : VLC installé au chemin standard
    if sys.platform == "win32":
        _vlc_default = r"C:\Program Files\VideoLAN\VLC"
        if os.path.isdir(_vlc_default):
            os.environ["PYTHON_VLC_LIB_PATH"]    = os.path.join(_vlc_default, "libvlc.dll")
            os.environ["PYTHON_VLC_MODULE_PATH"] = os.path.join(_vlc_default, "plugins")
            # Nécessaire pour que Windows trouve les DLL dépendantes
            os.add_dll_directory(_vlc_default)