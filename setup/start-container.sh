#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CONTROL_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
APP_DIR="$(cd "$SCRIPT_DIR/../projects/genarate-image" && pwd)"
COMFY_DIR="${COMFY_DIR:-/root/ComfyUI}"
LOG_DIR="$APP_DIR/runtime/logs"
PID_DIR="$APP_DIR/runtime/pids"
mkdir -p "$LOG_DIR" "$PID_DIR"

stop_pid(){
 local file="$1"
 if [[ -f "$file" ]]; then
  local pid;pid="$(cat "$file")"
  if kill -0 "$pid" 2>/dev/null; then kill "$pid" 2>/dev/null || true; sleep 2; fi
  rm -f "$file"
 fi
}

stop_pid "$PID_DIR/comfyui.pid"
stop_pid "$PID_DIR/storyframe.pid"
mkdir -p "$CONTROL_DIR/logs" "$CONTROL_DIR/runtime"
stop_pid "$CONTROL_DIR/runtime/control-center.pid"

cd "$COMFY_DIR"
VRAM_MB="$(nvidia-smi --query-gpu=memory.total --format=csv,noheader,nounits | head -n1 | tr -d ' ')"
COMFY_MEMORY_ARG="--normalvram"
[[ "$VRAM_MB" -lt 40000 ]] && COMFY_MEMORY_ARG="--lowvram"
nohup env CC=/usr/bin/gcc CXX=/usr/bin/g++ \
  .venv/bin/python main.py --listen 127.0.0.1 --port 8188 "$COMFY_MEMORY_ARG" \
  >"$LOG_DIR/comfyui.log" 2>&1 &
echo $! > "$PID_DIR/comfyui.pid"

cd "$APP_DIR"
nohup .venv-app/bin/python -m uvicorn app:app --host 127.0.0.1 --port 8010 \
  >"$LOG_DIR/storyframe.log" 2>&1 &
echo $! > "$PID_DIR/storyframe.pid"

cd "$CONTROL_DIR"
nohup .venv-linux/bin/python -m uvicorn app:app --host 127.0.0.1 --port 7999 \
  >"$CONTROL_DIR/logs/control-center.log" 2>&1 &
echo $! > "$CONTROL_DIR/runtime/control-center.pid"

for _ in {1..90}; do
 if curl -fsS http://127.0.0.1:8010/api/health >/tmp/storyframe-health.json 2>/dev/null \
   && curl -fsS http://127.0.0.1:8188/system_stats >/tmp/comfyui-health.json 2>/dev/null \
   && curl -fsS http://127.0.0.1:7999/ >/tmp/control-center-health.html 2>/dev/null; then break; fi
 sleep 2
done

if ! curl -fsS http://127.0.0.1:8010/api/health \
  || ! curl -fsS http://127.0.0.1:8188/system_stats >/dev/null \
  || ! curl -fsS http://127.0.0.1:7999/ >/dev/null; then
 echo "Control Center, StoryFrame hoặc ComfyUI chưa sẵn sàng."
 tail -n 100 "$CONTROL_DIR/logs/control-center.log" "$LOG_DIR/storyframe.log" "$LOG_DIR/comfyui.log" || true
 exit 1
fi

echo
echo "Control Center đang chạy nội bộ tại cổng 7999."
echo "StoryFrame đang chạy tại cổng 8010."
echo "ComfyUI đang chạy nội bộ tại cổng 8188."
