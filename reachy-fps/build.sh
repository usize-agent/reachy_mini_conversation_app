#!/bin/sh
# Cross-compile static reachy-fps binaries into dist/.
set -eu
cd "$(dirname "$0")"
mkdir -p dist
for target in darwin/arm64 darwin/amd64 linux/amd64 linux/arm64 windows/amd64; do
  os=${target%/*}
  arch=${target#*/}
  ext=""
  [ "$os" = windows ] && ext=".exe"
  out="dist/reachy-fps-$os-$arch$ext"
  CGO_ENABLED=0 GOOS=$os GOARCH=$arch go build -trimpath -ldflags="-s -w" -o "$out" .
  echo "built $out"
done
