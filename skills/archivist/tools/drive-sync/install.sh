#!/bin/bash
# Build + install the drive-sync menu-bar app for one git working tree.
# All host-specific values come from flags — nothing is hard-coded.
#
# Usage:
#   ./install.sh --repo "/abs/path/to/working/tree" [options]
# Options:
#   --name NAME         menu-bar app name (default: DriveSync). Pick a distinct
#                       name to run several instances for several repos.
#   --remote REMOTE     git remote to track (default: origin)
#   --branch BRANCH     branch to track + stay on (default: main)
#   --interval SECONDS  sync period (default: 900)
#
# Re-runnable: rebuilds, rewrites config, reloads the login agent.
set -euo pipefail

REPO="" NAME="DriveSync" REMOTE="origin" BRANCH="main" INTERVAL="900"
while [[ $# -gt 0 ]]; do
  case "$1" in
    --repo) REPO="$2"; shift 2 ;;
    --name) NAME="$2"; shift 2 ;;
    --remote) REMOTE="$2"; shift 2 ;;
    --branch) BRANCH="$2"; shift 2 ;;
    --interval) INTERVAL="$2"; shift 2 ;;
    *) echo "unknown option: $1" >&2; exit 2 ;;
  esac
done

[[ -n "$REPO" ]] || { echo "error: --repo is required" >&2; exit 2; }
[[ "$REPO" = /* ]] || { echo "error: --repo must be an absolute path" >&2; exit 2; }
command -v swiftc >/dev/null || { echo "error: swiftc not found (install Xcode Command Line Tools: xcode-select --install)" >&2; exit 1; }
git -C "$REPO" rev-parse --git-dir >/dev/null 2>&1 || { echo "error: $REPO is not a git working tree" >&2; exit 1; }

SRCDIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SWIFT="$SRCDIR/drive-sync.swift"
APP="$HOME/Applications/$NAME.app"
BUNDLE_ID="local.drivesync.$(echo "$NAME" | tr '[:upper:]' '[:lower:]' | tr -cd '[:alnum:].-')"
PLIST="$HOME/Library/LaunchAgents/$BUNDLE_ID.plist"
CONFDIR="$HOME/Library/Application Support/$NAME"
BIN="$APP/Contents/MacOS/$NAME"
UID_="$(id -u)"

echo "Building $NAME → $APP"
echo "  repo: $REPO   tracking: $REMOTE/$BRANCH   every ${INTERVAL}s"

# 1. compile
TMPBIN="$(mktemp -t drivesync)"
swiftc -O -o "$TMPBIN" "$SWIFT" -framework Cocoa -framework UserNotifications

# 2. assemble app bundle
rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources"
mv "$TMPBIN" "$BIN"
chmod +x "$BIN"
cat > "$APP/Contents/Info.plist" <<PLISTEOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>CFBundleName</key><string>$NAME</string>
    <key>CFBundleDisplayName</key><string>$NAME</string>
    <key>CFBundleIdentifier</key><string>$BUNDLE_ID</string>
    <key>CFBundleVersion</key><string>1.0</string>
    <key>CFBundleShortVersionString</key><string>1.0</string>
    <key>CFBundleExecutable</key><string>$NAME</string>
    <key>CFBundlePackageType</key><string>APPL</string>
    <key>LSMinimumSystemVersion</key><string>11.0</string>
    <key>LSUIElement</key><true/>
</dict>
</plist>
PLISTEOF
printf 'APPL????' > "$APP/Contents/PkgInfo"

# 3. write per-user config
mkdir -p "$CONFDIR"
REPO="$REPO" REMOTE="$REMOTE" BRANCH="$BRANCH" INTERVAL="$INTERVAL" \
python3 - "$CONFDIR/config.json" <<'PY'
import json, os, sys
json.dump({
    "repo": os.environ["REPO"],
    "remote": os.environ["REMOTE"],
    "branch": os.environ["BRANCH"],
    "intervalSeconds": int(os.environ["INTERVAL"]),
}, open(sys.argv[1], "w"), indent=2)
PY

# 4. ad-hoc code-sign (stable identity unless rebuilt; no FDA tied to it)
codesign --force --deep -s - "$APP"

# 5. install the login agent (Aqua session = native access to cloud folders, no FDA)
cat > "$PLIST" <<PLISTEOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key><string>$BUNDLE_ID</string>
    <key>ProgramArguments</key><array><string>$BIN</string></array>
    <key>RunAtLoad</key><true/>
    <key>KeepAlive</key><dict><key>SuccessfulExit</key><false/></dict>
    <key>LimitLoadToSessionType</key><string>Aqua</string>
</dict>
</plist>
PLISTEOF

# 6. (re)load
pkill -f "$BIN" 2>/dev/null || true
launchctl bootout "gui/$UID_/$BUNDLE_ID" 2>/dev/null || true
launchctl bootstrap "gui/$UID_" "$PLIST"

echo "Installed. Menu-bar icon should appear shortly."
echo "  status:    cat \"$HOME/Library/Logs/$NAME.status\""
echo "  log:       $HOME/Library/Logs/$NAME.log"
echo "  uninstall: $SRCDIR/uninstall.sh --name \"$NAME\""
