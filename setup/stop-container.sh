#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CONTROL_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
PID_DIR="$CONTROL_DIR/runtime/pids"

stop_file(){
  local file="$1" label="$2"
  [[ -f "$file" ]] || { printf '[SKIP] %s\n' "$label"; return; }
  local pid; pid="$(cat "$file" 2>/dev/null || true)"
  if [[ "$pid" =~ ^[0-9]+$ ]] && kill -0 "$pid" 2>/dev/null; then
    kill "$pid" 2>/dev/null || true
    for _ in {1..20}; do kill -0 "$pid" 2>/dev/null || break; sleep .25; done
    kill -9 "$pid" 2>/dev/null || true
  fi
  rm -f "$file"
  printf '[STOP] %s\n' "$label"
}

stop_file "$PID_DIR/text-to-speech.pid" "Text-to-Speech"
stop_file "$PID_DIR/story-frame.pid" "StoryFrame"
stop_file "$PID_DIR/story-research.pid" "Story Research"
stop_file "$PID_DIR/emotion-markup.pid" "Emotion Markup"
stop_file "$PID_DIR/remote-device-hub.pid" "Remote Device Hub"
stop_file "$PID_DIR/comfyui.pid" "ComfyUI"
stop_file "$CONTROL_DIR/runtime/control-center.pid" "Control Center"

echo "Đã dừng toàn bộ hệ thống."
