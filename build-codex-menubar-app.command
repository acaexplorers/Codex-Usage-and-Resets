#!/bin/zsh
emulate -L zsh
set -u

SCRIPT_DIR="${0:A:h}"
APP_PATH="$SCRIPT_DIR/Codex Usage.app"
BUILD_DIR="$SCRIPT_DIR/.build/codex-usage-menubar"
SOURCE="$SCRIPT_DIR/CodexUsageMenuBar.swift"
HELPER="$SCRIPT_DIR/codex-reset-expiry.py"

mkdir -p "$BUILD_DIR" "$APP_PATH/Contents/MacOS" "$APP_PATH/Contents/Resources"

build_arch() {
  local arch="$1"
  local output="$BUILD_DIR/CodexUsageMenuBar-$arch"
  echo "Building $arch..." >&2
  swiftc "$SOURCE" \
    -target "$arch-apple-macosx13.0" \
    -framework AppKit \
    -framework Foundation \
    -o "$output"
  echo "$output"
}

binary_paths=()
native_arch="$(uname -m)"
if [[ "$native_arch" != "arm64" && "$native_arch" != "x86_64" ]]; then
  native_arch="arm64"
fi

if native_binary=$(build_arch "$native_arch" 2>/tmp/codex-menubar-native.log); then
  binary_paths+=("$native_binary")
else
  cat /tmp/codex-menubar-native.log
fi

if [[ "${UNIVERSAL:-0}" == "1" && "$native_arch" != "arm64" ]]; then
  if arm_binary=$(build_arch arm64 2>/tmp/codex-menubar-arm64.log); then
    binary_paths+=("$arm_binary")
  else
    cat /tmp/codex-menubar-arm64.log
  fi
fi

if [[ "${UNIVERSAL:-0}" == "1" && "$native_arch" != "x86_64" ]]; then
  if intel_binary=$(build_arch x86_64 2>/tmp/codex-menubar-x86_64.log); then
    binary_paths+=("$intel_binary")
  else
    cat /tmp/codex-menubar-x86_64.log
  fi
fi

if [[ "${#binary_paths[@]}" -eq 0 ]]; then
  echo "Could not build the menu-bar app."
  exit 1
elif [[ "${#binary_paths[@]}" -eq 1 ]]; then
  cp "${binary_paths[1]}" "$APP_PATH/Contents/MacOS/CodexUsageMenuBar"
else
  lipo -create "${binary_paths[@]}" -output "$APP_PATH/Contents/MacOS/CodexUsageMenuBar"
fi

cp "$HELPER" "$APP_PATH/Contents/Resources/codex-reset-expiry.py"
chmod +x "$APP_PATH/Contents/MacOS/CodexUsageMenuBar" "$APP_PATH/Contents/Resources/codex-reset-expiry.py"

cat > "$APP_PATH/Contents/Info.plist" <<'PLIST'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleExecutable</key>
  <string>CodexUsageMenuBar</string>
  <key>CFBundleIdentifier</key>
  <string>local.codex.usage-menubar</string>
  <key>CFBundleName</key>
  <string>Codex Usage</string>
  <key>CFBundleDisplayName</key>
  <string>Codex Usage</string>
  <key>CFBundlePackageType</key>
  <string>APPL</string>
  <key>CFBundleShortVersionString</key>
  <string>0.2.1</string>
  <key>CFBundleVersion</key>
  <string>3</string>
  <key>LSMinimumSystemVersion</key>
  <string>13.0</string>
  <key>LSUIElement</key>
  <true/>
  <key>NSHighResolutionCapable</key>
  <true/>
</dict>
</plist>
PLIST

codesign --force --deep --sign - "$APP_PATH" >/dev/null 2>&1 || true

echo
echo "Built native menu-bar app:"
echo "$APP_PATH"
echo
echo "Open it to show Codex usage in your menu bar."
if [[ "${UNIVERSAL:-0}" != "1" ]]; then
  echo "Built for this Mac's architecture ($native_arch). Set UNIVERSAL=1 before running to try a universal build."
fi
echo
if [[ -t 0 ]]; then
  read -k 1 "?Press any key to close..."
fi
