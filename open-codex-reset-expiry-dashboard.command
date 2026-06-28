#!/bin/zsh
emulate -L zsh
set -u

SCRIPT_DIR="${0:A:h}"

printf '\033]0;Codex Reset Dashboard\007'
clear

echo
echo "Starting local Codex reset dashboard..."
/usr/bin/python3 "$SCRIPT_DIR/codex-reset-expiry.py" --serve --open
exit_code=$?

echo
if [ "$exit_code" -eq 0 ]; then
  echo "Dashboard stopped."
else
  echo "Could not start dashboard."
fi
echo
read -k 1 "?Press any key to close..."
exit "$exit_code"
