#!/usr/bin/env bash
# Install the Daily Notes Emailer as a macOS LaunchAgent (runs daily at 8 AM)
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PLIST_DIR="$HOME/Library/LaunchAgents"
PYTHON="$(command -v python3 || echo '/usr/bin/python3')"

# ---- config.json ----
if [ ! -f "$SCRIPT_DIR/config.json" ]; then
  echo ""
  echo "=== Gmail App Password Setup ==="
  echo ""
  echo "To send email, the script needs a Gmail App Password."
  echo "Steps:"
  echo "  1. Go to https://myaccount.google.com/security"
  echo "  2. Enable 2-Step Verification if not already on"
  echo "  3. Search for 'App passwords' and create one (name it 'Daily Notes')"
  echo "  4. You'll get a 16-character password like 'abcd efgh ijkl mnop'"
  echo ""
  read -rp "Enter your Gmail address: " GMAIL_ADDR
  read -rp "Enter the App Password (spaces OK): " APP_PASS
  read -rp "Send the daily email to [$GMAIL_ADDR]: " RECIPIENT
  RECIPIENT="${RECIPIENT:-$GMAIL_ADDR}"
  DEFAULT_LABEL="com.$USER.dailynotes"
  read -rp "LaunchAgent label [$DEFAULT_LABEL]: " LABEL
  LABEL="${LABEL:-$DEFAULT_LABEL}"
  cat > "$SCRIPT_DIR/config.json" <<JSON
{
  "sender_email": "$GMAIL_ADDR",
  "app_password": "$APP_PASS",
  "recipient_email": "$RECIPIENT",
  "launchd_label": "$LABEL"
}
JSON
  echo "config.json created."
else
  echo "config.json already exists, skipping."
fi

# ---- LaunchAgent plist ----
PLIST_LABEL="$("$PYTHON" -c 'import json, sys; print(json.load(open(sys.argv[1]))["launchd_label"])' "$SCRIPT_DIR/config.json")"
PLIST_PATH="$PLIST_DIR/$PLIST_LABEL.plist"
mkdir -p "$PLIST_DIR"
cat > "$PLIST_PATH" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>$PLIST_LABEL</string>
  <key>ProgramArguments</key>
  <array>
    <string>$PYTHON</string>
    <string>$SCRIPT_DIR/daily_notes_emailer.py</string>
  </array>
  <key>StartCalendarInterval</key>
  <dict>
    <key>Hour</key>
    <integer>8</integer>
    <key>Minute</key>
    <integer>0</integer>
  </dict>
  <key>StandardOutPath</key>
  <string>$SCRIPT_DIR/launchd.out</string>
  <key>StandardErrorPath</key>
  <string>$SCRIPT_DIR/launchd.err</string>
  <key>RunAtLoad</key>
  <false/>
</dict>
</plist>
PLIST

# Unload existing agent if present (ignore errors)
launchctl unload "$PLIST_PATH" 2>/dev/null || true
launchctl load "$PLIST_PATH"

echo ""
echo "=== Done ==="
echo "LaunchAgent installed: $PLIST_PATH"
echo "Will run daily at 8:00 AM."
echo ""
echo "Test right now with:"
echo "  python3 $SCRIPT_DIR/daily_notes_emailer.py --dry-run"
echo ""
echo "To run a real send immediately:"
echo "  python3 $SCRIPT_DIR/daily_notes_emailer.py"
echo ""
echo "Logs: $SCRIPT_DIR/emailer.log"
