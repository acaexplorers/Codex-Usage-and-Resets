#!/bin/zsh
emulate -L zsh
set -u

SCRIPT_DIR="${0:A:h}"

printf '\033]0;Codex Usage Report\007'
clear

echo
echo "Starting local Codex usage report..."
/usr/bin/python3 "$SCRIPT_DIR/codex-reset-expiry.py" --serve --open
exit_code=$?

echo
if [ "$exit_code" -eq 0 ]; then
  echo "Usage report stopped."
else
  echo "Could not start the usage report."
fi
echo
read -k 1 "?Press any key to close..."
exit "$exit_code"
