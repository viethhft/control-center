#!/usr/bin/env bash
set -Eeuo pipefail

# StoryFrame one-command installer for Ubuntu 22.04/24.04 + NVIDIA GPU.
# Optional overrides:
#   STORY_MODEL=qwen3.5:27b APP_PORT=8010 bash control-center/setup/setup-ubuntu.sh

SUDO=""
if [[ "${EUID}" -ne 0 ]]; then
  command -v sudo >/dev/null || { echo "Máy chưa có sudo." >&2; exit 1; }
  SUDO="sudo"
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CONTROL_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
APP_DIR="$(realpath -m "$SCRIPT_DIR/../projects/genarate-image")"

if [[ ! -f "$APP_DIR/requirements.txt" ]] || [[ ! -f "$APP_DIR/app.py" ]]; then
  printf '\n[Lỗi] StoryFrame source chưa đầy đủ tại: %s\n' "$APP_DIR" >&2
  printf 'Cần có ít nhất app.py và requirements.txt. Hãy đồng bộ toàn bộ control-center/projects trước khi setup.\n' >&2
  exit 1
fi

# Docker, WSL cũ và nhiều GPU cloud image không chạy systemd ở PID 1. Trong
# trường hợp đó phải dùng process supervisor nhẹ của setup-container.sh.
INIT_PROCESS="$(ps -p 1 -o comm= 2>/dev/null | tr -d '[:space:]' || true)"
if [[ "$INIT_PROCESS" != "systemd" ]] || [[ ! -d /run/systemd/system ]]; then
  printf '\n[StoryFrame] Không phát hiện systemd (PID 1: %s). Chuyển sang chế độ container.\n' "${INIT_PROCESS:-unknown}"
  if [[ "${EUID}" -ne 0 ]]; then
    exec sudo -E bash "$SCRIPT_DIR/setup-container.sh"
  fi
  exec bash "$SCRIPT_DIR/setup-container.sh"
fi

INSTALL_ROOT="${STORYFRAME_INSTALL_ROOT:-$HOME/storyframe-runtime}"
COMFY_DIR="$INSTALL_ROOT/ComfyUI"
STORY_MODEL="${STORY_MODEL:-qwen3.5:27b}"
APP_PORT="${APP_PORT:-8010}"
COMFY_PORT="${COMFY_PORT:-8188}"
QWEN_MODEL="qwen_image_distill_full_fp8_e4m3fn.safetensors"
QWEN_EDIT_MODEL="qwen_image_edit_2509_fp8_e4m3fn.safetensors"
QWEN_ENCODER="qwen_2.5_vl_7b_fp8_scaled.safetensors"
QWEN_VAE="qwen_image_vae.safetensors"

log() { printf '\n\033[1;36m[StoryFrame]\033[0m %s\n' "$*"; }
fail() { printf '\n\033[1;31m[Lỗi]\033[0m %s\n' "$*" >&2; exit 1; }

if ! command -v nvidia-smi >/dev/null; then
  fail "Không thấy NVIDIA Driver/nvidia-smi. Hãy chọn máy thuê có NVIDIA GPU và cài driver trước."
fi
log "GPU được phát hiện"
nvidia-smi --query-gpu=name,memory.total,driver_version --format=csv,noheader
VRAM_MB="$(nvidia-smi --query-gpu=memory.total --format=csv,noheader,nounits | head -n1 | tr -d ' ')"
[[ "$VRAM_MB" -ge 24000 ]] || fail "Qwen-Image cần GPU tối thiểu khoảng 24 GB VRAM; phát hiện ${VRAM_MB} MB."
AVAILABLE_GB="$(df -Pk "$APP_DIR" | awk 'NR==2 {print int($4/1024/1024)}')"
[[ "$AVAILABLE_GB" -ge 80 ]] || fail "Cần tối thiểu 80 GB trống cho hai model Qwen-Image; hiện còn ${AVAILABLE_GB} GB."

log "Cài gói hệ thống"
$SUDO apt-get update
$SUDO env DEBIAN_FRONTEND=noninteractive apt-get install -y git git-lfs curl wget ffmpeg libgl1 libglib2.0-0 python3 python3-venv python3-pip python3-dev build-essential gcc g++ adb libgl1-mesa-dev libx11-dev libxtst-dev

log "Cài và khởi động Ollama"
if ! command -v ollama >/dev/null; then
  curl -fsSL https://ollama.com/install.sh | sh
fi
# Một số image máy thuê có binary Ollama nhưng không có systemd unit.
# Tạo unit dùng chính user đang setup để model được lưu đúng HOME.
if ! systemctl cat ollama.service >/dev/null 2>&1; then
  OLLAMA_BIN="$(command -v ollama)"
  CURRENT_USER="$(id -un)"
  CURRENT_HOME="$HOME"
  cat > /tmp/ollama.service <<EOF
[Unit]
Description=Ollama Service
After=network-online.target

[Service]
Type=simple
User=$CURRENT_USER
Environment=HOME=$CURRENT_HOME
ExecStart=$OLLAMA_BIN serve
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
EOF
  $SUDO install -m 0644 /tmp/ollama.service /etc/systemd/system/ollama.service
  rm -f /tmp/ollama.service
  $SUDO systemctl daemon-reload
fi
$SUDO systemctl enable --now ollama
for _ in {1..30}; do
  if curl -fsS http://127.0.0.1:11434/api/tags >/dev/null 2>&1; then break; fi
  sleep 1
done
curl -fsS http://127.0.0.1:11434/api/tags >/dev/null || fail "Ollama service chưa sẵn sàng."
log "Tải model $STORY_MODEL (có thể mất nhiều thời gian)"
ollama pull "$STORY_MODEL"

log "Cài ComfyUI"
mkdir -p "$INSTALL_ROOT"
if [[ ! -d "$COMFY_DIR/.git" ]]; then
  git clone --depth 1 https://github.com/Comfy-Org/ComfyUI.git "$COMFY_DIR"
else
  git -C "$COMFY_DIR" pull --ff-only
fi
python3 -m venv "$COMFY_DIR/.venv"
"$COMFY_DIR/.venv/bin/python" -m pip install --upgrade pip
"$COMFY_DIR/.venv/bin/pip" install torch==2.7.1 torchvision==0.22.1 torchaudio==2.7.1 --index-url https://download.pytorch.org/whl/cu126
"$COMFY_DIR/.venv/bin/pip" install -r "$COMFY_DIR/requirements.txt"
CC=/usr/bin/gcc CXX=/usr/bin/g++ "$COMFY_DIR/.venv/bin/python" -c "import torch; assert torch.cuda.is_available(), 'PyTorch không nhận CUDA'; print('CUDA OK:', torch.cuda.get_device_name(0))"

log "Tải Qwen-Image, Qwen-Image-Edit-2509, text encoder và VAE"
mkdir -p "$COMFY_DIR/models/diffusion_models" "$COMFY_DIR/models/text_encoders" "$COMFY_DIR/models/vae"
[[ -s "$COMFY_DIR/models/diffusion_models/$QWEN_MODEL" ]] || wget --continue --output-document="$COMFY_DIR/models/diffusion_models/$QWEN_MODEL" "https://huggingface.co/Comfy-Org/Qwen-Image_ComfyUI/resolve/main/non_official/diffusion_models/$QWEN_MODEL"
[[ -s "$COMFY_DIR/models/diffusion_models/$QWEN_EDIT_MODEL" ]] || wget --continue --output-document="$COMFY_DIR/models/diffusion_models/$QWEN_EDIT_MODEL" "https://huggingface.co/Comfy-Org/Qwen-Image-Edit_ComfyUI/resolve/main/split_files/diffusion_models/$QWEN_EDIT_MODEL"
[[ -s "$COMFY_DIR/models/text_encoders/$QWEN_ENCODER" ]] || wget --continue --output-document="$COMFY_DIR/models/text_encoders/$QWEN_ENCODER" "https://huggingface.co/Comfy-Org/Qwen-Image_ComfyUI/resolve/main/split_files/text_encoders/$QWEN_ENCODER"
[[ -s "$COMFY_DIR/models/vae/$QWEN_VAE" ]] || wget --continue --output-document="$COMFY_DIR/models/vae/$QWEN_VAE" "https://huggingface.co/Comfy-Org/Qwen-Image_ComfyUI/resolve/main/split_files/vae/$QWEN_VAE"

log "Cài môi trường StoryFrame"
python3 -m venv "$APP_DIR/.venv-app"
"$APP_DIR/.venv-app/bin/python" -m pip install --upgrade pip
"$APP_DIR/.venv-app/bin/pip" install -r "$APP_DIR/requirements.txt"
cat > "$APP_DIR/.env" <<EOF
OLLAMA_URL=http://127.0.0.1:11434
OLLAMA_STORY_MODEL=$STORY_MODEL
COMFYUI_URL=http://127.0.0.1:$COMFY_PORT
QWEN_IMAGE_MODEL=$QWEN_MODEL
QWEN_IMAGE_EDIT_MODEL=$QWEN_EDIT_MODEL
QWEN_IMAGE_ENCODER=$QWEN_ENCODER
QWEN_IMAGE_VAE=$QWEN_VAE
COMFYUI_WORKFLOW=
COMFYUI_MAX_CONCURRENCY=1
COMFYUI_RENDER_RETRIES=2
COMFYUI_TIMEOUT_SECONDS=1800
COMFYUI_POLL_SECONDS=1
EOF

log "Cài môi trường cho Control Center và toàn bộ project con"
bash "$SCRIPT_DIR/install-project-envs.sh"

cat > "$CONTROL_DIR/.env.system" <<EOF
OLLAMA_URL=http://127.0.0.1:11434
OLLAMA_HOST=http://127.0.0.1:11434
OLLAMA_STORY_MODEL=$STORY_MODEL
STORY_LAB_MODEL=$STORY_MODEL
EMOTION_MODEL=$STORY_MODEL
OLLAMA_ROLE_MODEL=$STORY_MODEL
EOF

log "Tạo systemd service"
CURRENT_USER="$(id -un)"
cat > /tmp/storyframe-comfyui.service <<EOF
[Unit]
Description=ComfyUI for StoryFrame
After=network-online.target

[Service]
Type=simple
User=$CURRENT_USER
WorkingDirectory=$COMFY_DIR
ExecStart=$COMFY_DIR/.venv/bin/python $COMFY_DIR/main.py --listen 127.0.0.1 --port $COMFY_PORT
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF
$SUDO install -m 0644 /tmp/storyframe-comfyui.service /etc/systemd/system/storyframe-comfyui.service

cat > /tmp/storyframe.service <<EOF
[Unit]
Description=StoryFrame Studio
After=network-online.target ollama.service storyframe-comfyui.service
Requires=storyframe-comfyui.service

[Service]
Type=simple
User=$CURRENT_USER
WorkingDirectory=$APP_DIR
EnvironmentFile=-$CONTROL_DIR/.env.system
ExecStart=$APP_DIR/.venv-app/bin/python -m uvicorn app:app --host 127.0.0.1 --port $APP_PORT
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF
$SUDO install -m 0644 /tmp/storyframe.service /etc/systemd/system/storyframe.service

cat > /tmp/control-center.service <<EOF
[Unit]
Description=Control Center
After=network-online.target

[Service]
Type=simple
User=$CURRENT_USER
WorkingDirectory=$CONTROL_DIR
EnvironmentFile=-$CONTROL_DIR/.env.system
ExecStart=$CONTROL_DIR/.venv-linux/bin/python -m uvicorn app:app --host 127.0.0.1 --port 7999
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF
$SUDO install -m 0644 /tmp/control-center.service /etc/systemd/system/control-center.service

install_project_service(){
  local name="$1" description="$2" directory="$3" port="$4"
  cat > "/tmp/$name.service" <<EOF
[Unit]
Description=$description
After=network-online.target ollama.service

[Service]
Type=simple
User=$CURRENT_USER
WorkingDirectory=$directory
EnvironmentFile=-$CONTROL_DIR/.env.system
ExecStart=$directory/.venv-linux/bin/python -m uvicorn app:app --host 127.0.0.1 --port $port
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF
  $SUDO install -m 0644 "/tmp/$name.service" "/etc/systemd/system/$name.service"
  rm -f "/tmp/$name.service"
}

install_project_service "text-to-speech" "Text-to-Speech Studio" "$CONTROL_DIR/projects/text-to-speech-app" 8000
install_project_service "story-research" "Story Research Lab" "$CONTROL_DIR/projects/story-research-lab" 8020
install_project_service "emotion-markup" "Emotion Markup Studio" "$CONTROL_DIR/projects/emotion-markup-studio" 8030
install_project_service "remote-device-hub" "Remote Device Hub" "$CONTROL_DIR/projects/remote-device-hub" 8040

rm -f /tmp/storyframe-comfyui.service /tmp/storyframe.service /tmp/control-center.service
$SUDO systemctl daemon-reload
$SUDO systemctl enable --now storyframe-comfyui storyframe control-center \
  text-to-speech story-research emotion-markup remote-device-hub

log "Chờ service sẵn sàng"
declare -A SERVICE_HEALTH=(
  [control-center]="http://127.0.0.1:7999/"
  [text-to-speech]="http://127.0.0.1:8000/"
  [storyframe]="http://127.0.0.1:$APP_PORT/api/health"
  [story-research]="http://127.0.0.1:8020/api/health"
  [emotion-markup]="http://127.0.0.1:8030/api/health"
  [remote-device-hub]="http://127.0.0.1:8040/api/health"
  [storyframe-comfyui]="http://127.0.0.1:$COMFY_PORT/system_stats"
)
for _ in {1..120}; do
  remaining=0
  for name in "${!SERVICE_HEALTH[@]}"; do
    curl -fsS --max-time 3 "${SERVICE_HEALTH[$name]}" >/dev/null 2>&1 || remaining=$((remaining + 1))
  done
  [[ "$remaining" -eq 0 ]] && break
  sleep 2
done
for name in "${!SERVICE_HEALTH[@]}"; do
  if curl -fsS --max-time 5 "${SERVICE_HEALTH[$name]}" >/dev/null 2>&1; then
    printf '[OK]   %s\n' "$name"
  else
    $SUDO systemctl --no-pager --full status "$name" || true
    fail "$name chưa sẵn sàng. Xem log: journalctl -u $name -f"
  fi
done

IP_ADDRESS="$(hostname -I | awk '{print $1}')"
printf '\n\033[1;32mCÀI ĐẶT HOÀN TẤT\033[0m\n'
printf 'Mở ứng dụng: http://%s:%s\n' "$IP_ADDRESS" "$APP_PORT"
printf 'Xem log: sudo journalctl -u storyframe -u storyframe-comfyui -f\n'
printf 'Khởi động lại: sudo systemctl restart storyframe storyframe-comfyui ollama\n'
