#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CONTROL_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
PROJECTS_DIR="$CONTROL_DIR/projects"
COMFY_DIR="${COMFY_DIR:-/root/ComfyUI}"
LOG_DIR="$CONTROL_DIR/logs"
PID_DIR="$CONTROL_DIR/runtime/pids"
mkdir -p "$LOG_DIR" "$PID_DIR" "$CONTROL_DIR/runtime"

if [[ -f "$CONTROL_DIR/.env.system" ]]; then
  set -a
  source "$CONTROL_DIR/.env.system"
  set +a
fi

log(){ printf '\n\033[1;36m[Control Center]\033[0m %s\n' "$*"; }
fail(){ printf '\n\033[1;31m[Lỗi]\033[0m %s\n' "$*" >&2; exit 1; }

stop_pid(){
  local file="$1"
  [[ -f "$file" ]] || return 0
  local pid; pid="$(cat "$file" 2>/dev/null || true)"
  if [[ "$pid" =~ ^[0-9]+$ ]] && kill -0 "$pid" 2>/dev/null; then
    kill "$pid" 2>/dev/null || true
    for _ in {1..20}; do kill -0 "$pid" 2>/dev/null || break; sleep .25; done
    kill -9 "$pid" 2>/dev/null || true
  fi
  rm -f "$file"
}

start_app(){
  local id="$1" directory="$2" python="$3" port="$4"
  [[ -f "$directory/app.py" ]] || fail "$id thiếu app.py tại $directory"
  [[ -x "$directory/$python" ]] || fail "$id chưa có môi trường: $directory/$python"
  stop_pid "$PID_DIR/$id.pid"
  log "Khởi động $id tại cổng $port"
  (
    cd "$directory"
    nohup "$python" -m uvicorn app:app --host 127.0.0.1 --port "$port" \
      >"$LOG_DIR/$id.log" 2>&1 &
    echo $! > "$PID_DIR/$id.pid"
  )
}

stop_pid "$PID_DIR/comfyui.pid"
stop_pid "$CONTROL_DIR/runtime/control-center.pid"
# Dọn PID theo cấu trúc StoryFrame cũ trong lần nâng cấp đầu tiên.
stop_pid "$PROJECTS_DIR/genarate-image/runtime/pids/comfyui.pid"
stop_pid "$PROJECTS_DIR/genarate-image/runtime/pids/storyframe.pid"

[[ -x "$COMFY_DIR/.venv/bin/python" ]] || fail "ComfyUI chưa có môi trường tại $COMFY_DIR/.venv"
VRAM_MB="$(nvidia-smi --query-gpu=memory.total --format=csv,noheader,nounits | head -n1 | tr -d ' ')"
COMFY_MEMORY_ARG="--normalvram"
[[ "$VRAM_MB" -lt 40000 ]] && COMFY_MEMORY_ARG="--lowvram"
log "Khởi động ComfyUI tại cổng 8188"
(
  cd "$COMFY_DIR"
  nohup env CC=/usr/bin/gcc CXX=/usr/bin/g++ .venv/bin/python main.py \
    --listen 127.0.0.1 --port 8188 "$COMFY_MEMORY_ARG" \
    >"$LOG_DIR/comfyui.log" 2>&1 &
  echo $! > "$PID_DIR/comfyui.pid"
)

start_app "text-to-speech" "$PROJECTS_DIR/text-to-speech-app" ".venv-linux/bin/python" 8000
start_app "story-frame" "$PROJECTS_DIR/genarate-image" ".venv-app/bin/python" 8010
start_app "story-research" "$PROJECTS_DIR/story-research-lab" ".venv-linux/bin/python" 8020
start_app "emotion-markup" "$PROJECTS_DIR/emotion-markup-studio" ".venv-linux/bin/python" 8030
start_app "remote-device-hub" "$PROJECTS_DIR/remote-device-hub" ".venv-linux/bin/python" 8040

[[ -x "$CONTROL_DIR/.venv-linux/bin/python" ]] || fail "Control Center chưa có .venv-linux"
log "Khởi động Control Center tại cổng 7999"
(
  cd "$CONTROL_DIR"
  nohup .venv-linux/bin/python -m uvicorn app:app --host 127.0.0.1 --port 7999 \
    >"$LOG_DIR/control-center.log" 2>&1 &
  echo $! > "$CONTROL_DIR/runtime/control-center.pid"
)

declare -A HEALTH=(
  [control-center]="http://127.0.0.1:7999/"
  [text-to-speech]="http://127.0.0.1:8000/"
  [story-frame]="http://127.0.0.1:8010/api/health"
  [story-research]="http://127.0.0.1:8020/api/health"
  [emotion-markup]="http://127.0.0.1:8030/api/health"
  [remote-device-hub]="http://127.0.0.1:8040/api/health"
  [comfyui]="http://127.0.0.1:8188/system_stats"
)

for _ in {1..120}; do
  remaining=0
  for name in "${!HEALTH[@]}"; do
    curl -fsS --max-time 3 "${HEALTH[$name]}" >/dev/null 2>&1 || remaining=$((remaining + 1))
  done
  [[ "$remaining" -eq 0 ]] && break
  sleep 2
done

failed=0
for name in "${!HEALTH[@]}"; do
  if curl -fsS --max-time 5 "${HEALTH[$name]}" >/dev/null 2>&1; then
    printf '[OK]   %-20s %s\n' "$name" "${HEALTH[$name]}"
  else
    printf '[FAIL] %-20s %s\n' "$name" "${HEALTH[$name]}" >&2
    failed=1
  fi
done

if [[ "$failed" -ne 0 ]]; then
  printf '\nCó dịch vụ chưa sẵn sàng. Xem log trong %s\n' "$LOG_DIR" >&2
  exit 1
fi

printf '\nToàn bộ hệ thống đã chạy. Control Center: http://127.0.0.1:7999\n'
