#!/bin/zsh
emulate -L zsh
set -u

SCRIPT_DIR="${0:A:h}"
printf '\033]0;Codex Reset Expiry\007'
clear

echo
/usr/bin/python3 "$SCRIPT_DIR/codex-reset-expiry.py" --pretty
exit_code=$?

echo
if [ "$exit_code" -eq 0 ]; then
  echo "Done. Redeem resets only from the Codex app."
else
  echo "Could not check reset expiry."
fi
echo
read -k 1 "?Press any key to close..."
exit "$exit_code"
