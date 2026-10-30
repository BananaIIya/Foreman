#!/bin/bash
# Prompts for the Daytona API key without echoing it and stores it where launch.py reads it. The key is never printed.
set -e
dir="$HOME/.config/fusion-bench"
mkdir -p "$dir"
chmod 700 "$dir"
read -rsp 'Paste DAYTONA_API_KEY (input hidden), then press Enter: ' key
printf '\n'
if [ -z "$key" ]; then
  echo 'Empty input; nothing written.'
  exit 1
fi
umask 077
printf 'DAYTONA_API_KEY=%s\n' "$key" > "$dir/daytona.env"
chmod 600 "$dir/daytona.env"
echo "Saved to $dir/daytona.env (mode 600)."
