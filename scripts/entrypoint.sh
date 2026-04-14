#!/bin/bash
set -e

# ---
# 此脚本为修复启动服务器时报错
# ---

# === Fix libv8*.so ===
SRC_DIR="/cs2/game/bin/linuxsteamrt64"
DST_DIR="/cs2/game/csgo/bin/linuxsteamrt64"

mkdir -p "$DST_DIR"
cd "$SRC_DIR"

shopt -s nullglob
for lib in libv8*.so; do
  ln -sf "$(pwd)/$lib" "$DST_DIR/$lib"
  echo "[entrypoint] linked $lib"
done
shopt -u nullglob

# === Fix steamclient.so ===
STEAM_SDK_DIR="/root/.steam/sdk64"
STEAMCMD_CLIENT="/steamcmd/linux64/steamclient.so"

mkdir -p "$STEAM_SDK_DIR"
if [ -f "$STEAMCMD_CLIENT" ]; then
  ln -sf "$STEAMCMD_CLIENT" "$STEAM_SDK_DIR/steamclient.so"
  echo "[entrypoint] steamclient.so linked"
else
  echo "[entrypoint] WARNING: steamclient.so not found"
fi

echo "[entrypoint] done"

# === VERY IMPORTANT PART ===
if [ "$#" -gt 0 ]; then
  echo "[entrypoint] exec passed command: $@"
  exec "$@"
else
  echo "[entrypoint] no command passed, entering shell"
  exec bash
fi