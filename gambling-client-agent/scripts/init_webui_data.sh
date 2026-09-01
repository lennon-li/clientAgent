#!/usr/bin/env bash
# Create state/webui-data for the gambling-webui container and seed it with
# the model caches that are baked into the image.
#
# Why seeding is needed: the image ships ~265 MB of sentence-transformers and
# whisper model files under /app/backend/data, which is exactly the path the
# bind mount covers up. Without this, the directory starts empty and Open WebUI
# would try to download those models on first use -- which OFFLINE_MODE=true
# then refuses. Copying them in once keeps the instance fully offline.
#
# Idempotent: does nothing if the directory already has content.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DATA_DIR="$HERE/state/webui-data"
IMAGE="ghcr.io/open-webui/open-webui:0.10.2@sha256:9fcea9c6e32ab60b0498f3986c6cdf651ddbe61db48d2213a3d28048ddd673d4"

if [[ -e "$DATA_DIR/webui.db" || -d "$DATA_DIR/cache" ]]; then
  echo "$DATA_DIR already initialised; leaving it alone."
  exit 0
fi

mkdir -p "$DATA_DIR"
chmod 700 "$DATA_DIR"

echo "Seeding model caches from the pinned image (this copies ~265 MB)..."
cid="$(docker create "$IMAGE")"
trap 'docker rm -f "$cid" >/dev/null 2>&1 || true' EXIT
docker cp "$cid:/app/backend/data/cache" "$DATA_DIR/cache"

echo "Done. $DATA_DIR now holds:"
ls -la "$DATA_DIR"
