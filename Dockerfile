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
# and the plugin directory PyInstaller bundles below; binutils provides
# objdump, which PyInstaller requires on Linux to build the executable —
# it fails immediately at run time without it ("On Linux, objdump is
# required"). The original shell-history pipeline never installed
# binutils explicitly either; that gap only surfaced now, when
# reconstructing this Dockerfile and actually running it end to end.
# Whether it was pulled in transitively by something else on the
# ubuntu:22.04 image at the time, or this is a requirement that appeared
# in a newer PyInstaller release than whatever was in use back then,
# doesn't matter here — either way, declaring it explicitly is the right
# call rather than depending on it being present implicitly.
#
# libpython3.10 provides libpython3.10.so.1.0 — PyInstaller links the
# executable against it and fails ("Python shared library
# ('libpython3.10.so.1.0') was not found!") without it. Same situation as
# binutils above: not installed explicitly by the original shell-history
# pipeline either, only surfaced by actually running this Dockerfile end
# to end — declared explicitly here for the same reason.
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
        binutils \
        libpython3.10 \
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
