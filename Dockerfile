# syntax=docker/dockerfile:1
#
# Build environment for the standalone Linux `quark-player` executable.
#
# This does NOT bake the app source into the image — it only sets up the
# system packages and Python deps PyInstaller needs. The actual repo is
# volume-mounted at build time (see scripts/build-linux-docker.sh), the
# same way it was always done manually before this Dockerfile existed:
# build the image once, reuse it across every release.
#
# ubuntu:22.04, not a newer tag: ubuntu:24.04 was tried first and produced
# a PyInstaller binary linked against a glibc too new to run on at least
# one user's system (elementaryOS 7, itself based on Ubuntu 22.04).
# ubuntu:20.04 was also tried around the same time but never became the
# version actually used for a release build — ubuntu:22.04 is what every
# real build since has run on. See README's "Building a standalone Linux
# executable" section for the fuller history.
FROM ubuntu:22.04

ENV DEBIAN_FRONTEND=noninteractive

# python3-pip pulls in python3; vlc provides libvlc.so.5/libvlccore.so.9
# and the plugin directory PyInstaller bundles below.
#
# Note: unlike the Windows build (see README), this does NOT install or
# bundle ffmpeg. ffmpeg bundling (apt package + --add-binary) was tried
# exactly once, in an isolated one-off command — never adopted for any
# of the real version-numbered release builds that came before or after
# it (v0.6 through v0.7 in the build history all skip it).
#
# This doesn't remove the fallback decoder's capability, just the image
# used to build it: SampleLoader._run_ffmpeg (audio/engine.py) resolves
# ffmpeg via shutil.which() at runtime, on the machine running the
# binary — not the one that built it. So the fallback works fine on this
# build as long as the end user's machine has ffmpeg installed, exactly
# like running `python main.py` directly. Not bundling it here just
# means this build doesn't guarantee that for the user, the way the
# Windows build's --add-binary does.
RUN apt-get update && apt-get install -y --no-install-recommends \
        python3 \
        python3-pip \
        vlc \
    && rm -rf /var/lib/apt/lists/*

RUN pip3 install --no-cache-dir \
        PyQt6 \
        python-vlc \
        soundfile \
        mutagen \
        numpy \
        Pillow \
        pyinstaller

WORKDIR /app
