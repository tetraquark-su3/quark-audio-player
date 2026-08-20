#!/usr/bin/env bash
# Build the standalone Linux quark-player executable inside the
# ubuntu:22.04 container defined by ./Dockerfile — a checked-in
# reproduction of a pipeline that used to live only in ad hoc shell
# history (see README's "Building a standalone Linux executable").
#
# Usage:
#   ./docker-build.sh [distpath]
#
# distpath defaults to dist-linux-docker/ — NOT dist/ — so this never
# touches the existing dist/quark-player. Move the new build into dist/
# yourself once you've confirmed it works.
set -euo pipefail
cd "$(dirname "$0")"

DISTPATH="${1:-dist-linux-docker}"
IMAGE_TAG="quark-player-builder:22.04"

# All the docker invocations found in history ran under sudo (this
# machine's user isn't in the docker group). Override with DOCKER=docker
# if that ever changes.
DOCKER="${DOCKER:-sudo docker}"

$DOCKER build -t "$IMAGE_TAG" .

# --user matches the invoking user's uid/gid: the historical raw `docker
# run` commands (mounting $(pwd) and building as root) are why dist/,
# build/, and quark-player.spec are root-owned on disk today. Passing
# --user avoids reproducing that wart — nothing in the image needs root
# at run time, the system/pip packages were already installed at image
# build time.
#
# --add-data vlc/plugins:vlc (single-level "vlc" destination, not
# "vlc/plugins"): this must match vlc_setup.py's frozen-Linux branch,
# which sets PYTHON_VLC_MODULE_PATH to <bundle>/vlc. A "vlc/plugins"
# destination (as briefly tried once in this project's history) would
# land the plugin .so files one level too deep and VLC would fail to
# find them at runtime.
$DOCKER run --rm \
  --user "$(id -u):$(id -g)" \
  -v "$(pwd)":/app \
  -w /app \
  "$IMAGE_TAG" \
  pyinstaller --onefile --name quark-player \
    --distpath "$DISTPATH" \
    --workpath build-linux-docker \
    --specpath build-linux-docker \
    --add-data '/app/assets:assets' \
    --add-binary '/usr/lib/x86_64-linux-gnu/libvlc.so.5:.' \
    --add-binary '/usr/lib/x86_64-linux-gnu/libvlccore.so.9:.' \
    --add-data '/usr/lib/x86_64-linux-gnu/vlc/plugins:vlc' \
    main.py

echo "Built: $DISTPATH/quark-player"
echo "dist/quark-player was NOT touched — copy the new build over yourself once you've verified it."
