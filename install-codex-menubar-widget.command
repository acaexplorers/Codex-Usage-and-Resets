#!/bin/zsh
emulate -L zsh
set -u

SCRIPT_DIR="${0:A:h}"

target_dirs=()
target_dirs+=("${SWIFTBAR_PLUGIN_DIR:-$HOME/Library/Application Support/SwiftBar/Plugins}")

if [[ -n "${XBAR_PLUGIN_DIR:-}" ]]; then
  target_dirs+=("$XBAR_PLUGIN_DIR")
elif [[ -d "$HOME/Library/Application Support/xbar/plugins" ]]; then
  target_dirs+=("$HOME/Library/Application Support/xbar/plugins")
fi

echo
echo "Installing Codex reset widget plugin..."
for plugin_dir in "$target_dirs[@]"; do
  mkdir -p "$plugin_dir"
  cp "$SCRIPT_DIR/codex-reset-widget.5m.py" "$plugin_dir/codex-reset-widget.5m.py"
  cp "$SCRIPT_DIR/codex-reset-expiry.py" "$plugin_dir/codex-reset-expiry.py"
  cp "$SCRIPT_DIR/open-codex-reset-expiry-dashboard.command" "$plugin_dir/open-codex-reset-expiry-dashboard.command"
  chmod +x "$plugin_dir/codex-reset-widget.5m.py" "$plugin_dir/codex-reset-expiry.py" "$plugin_dir/open-codex-reset-expiry-dashboard.command"
  echo "$plugin_dir/codex-reset-widget.5m.py"
done
echo
swiftbar_app=""
xbar_app=""
for candidate in "/Applications/SwiftBar.app" "$HOME/Applications/SwiftBar.app"; do
  [[ -d "$candidate" ]] && swiftbar_app="$candidate" && break
done
for candidate in "/Applications/xbar.app" "$HOME/Applications/xbar.app"; do
  [[ -d "$candidate" ]] && xbar_app="$candidate" && break
done

if [[ -n "$swiftbar_app" ]]; then
  echo "Opening SwiftBar..."
  open "$swiftbar_app"
elif [[ -n "$xbar_app" ]]; then
  echo "Opening xbar..."
  open "$xbar_app"
else
  echo "Next step: install SwiftBar or xbar."
  echo
  if command -v brew >/dev/null 2>&1; then
    echo "You can install SwiftBar with:"
    echo "  brew install --cask swiftbar"
  else
    echo "Download SwiftBar:"
    echo "  https://github.com/swiftbar/SwiftBar/releases"
  fi
  echo
  echo "After installing it, open SwiftBar and choose this plugin folder:"
  echo "  ${target_dirs[1]}"
fi
echo
echo "The menu-bar item refreshes every 5 minutes once SwiftBar or xbar is running."
echo
open "${target_dirs[1]}" >/dev/null 2>&1 || true
read -k 1 "?Press any key to close..."
