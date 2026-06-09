#!/bin/bash
# Remove a git-autosync instance. Usage: ./uninstall.sh [--name NAME] [--keep-logs]
set -euo pipefail
NAME="GitSync" KEEP_LOGS=0
while [[ $# -gt 0 ]]; do
  case "$1" in
    --name) NAME="$2"; shift 2 ;;
    --keep-logs) KEEP_LOGS=1; shift ;;
    *) echo "unknown option: $1" >&2; exit 2 ;;
  esac
done

BUNDLE_ID="local.gitautosync.$(echo "$NAME" | tr '[:upper:]' '[:lower:]' | tr -cd '[:alnum:].-')"
UID_="$(id -u)"
APP="$HOME/Applications/$NAME.app"
BIN="$APP/Contents/MacOS/$NAME"

launchctl bootout "gui/$UID_/$BUNDLE_ID" 2>/dev/null || true
pkill -f "$BIN" 2>/dev/null || true
rm -f "$HOME/Library/LaunchAgents/$BUNDLE_ID.plist"
rm -rf "$APP"
rm -rf "$HOME/Library/Application Support/$NAME"
if [[ "$KEEP_LOGS" -eq 0 ]]; then
  rm -f "$HOME/Library/Logs/$NAME.log" "$HOME/Library/Logs/$NAME.status"
fi
echo "Removed $NAME (agent, app, config$([[ $KEEP_LOGS -eq 0 ]] && echo ', logs'))."
