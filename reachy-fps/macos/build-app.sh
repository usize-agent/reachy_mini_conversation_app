#!/bin/sh
# Build "Reachy Mini Daemon.app", a background app that owns the macOS
# camera/microphone permission for the Reachy Mini daemon.
set -eu
cd "$(dirname "$0")/.."
APP="dist/Reachy Mini Daemon.app"
rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS"
go build -trimpath -o "$APP/Contents/MacOS/daemon-launcher" ./cmd/daemon-launcher
cat > "$APP/Contents/Info.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleIdentifier</key><string>local.reachy-fps.daemon</string>
  <key>CFBundleName</key><string>Reachy Mini Daemon</string>
  <key>CFBundleExecutable</key><string>daemon-launcher</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>CFBundleShortVersionString</key><string>1.0</string>
  <key>CFBundleVersion</key><string>1</string>
  <key>LSMinimumSystemVersion</key><string>12.0</string>
  <key>LSUIElement</key><true/>
  <key>NSCameraUsageDescription</key><string>Streams Reachy Mini's camera to the teleoperation page.</string>
  <key>NSMicrophoneUsageDescription</key><string>Streams Reachy Mini's microphone to the teleoperation page.</string>
</dict>
</plist>
PLIST
codesign --force --sign - "$APP"
echo "built $APP"
