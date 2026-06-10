#!/usr/bin/env sh
set -eu

VERSION="${1:-1.0}"
ROOT_DIR="$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"
mkdir -p "$ROOT_DIR/dist"

CGO_ENABLED=0 GOOS=linux GOARCH=amd64 go build \
  -trimpath \
  -ldflags "-s -w -X github.com/kaishzz/kepagent/internal/version.Version=$VERSION" \
  -o "$ROOT_DIR/dist/kepagent-linux-amd64" \
  "$ROOT_DIR/cmd/kepagent"

echo "Built $ROOT_DIR/dist/kepagent-linux-amd64"
