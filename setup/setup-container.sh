#!/usr/bin/env bash
set -Eeuo pipefail

# One-command setup for an Ubuntu-based GPU container (no systemd).
# Override example: STORY_MODEL=qwen3.5:35b bash control-center/setup/setup-container.sh

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP_DIR="$(realpath -m "$SCRIPT_DIR/../projects/genarate-image")"
if [[ ! -f "$APP_DIR/requirements.txt" ]] || [[ ! -f "$APP_DIR/app.py" ]]; then
  printf '\n[Lỗi] StoryFrame source chưa đầy đủ tại: %s\n' "$APP_DIR" >&2
  printf 'Cần có ít nhất app.py và requirements.txt. Hãy đồng bộ toàn bộ control-center/projects trước khi setup.\n' >&2
  exit 1
fi
COMFY_DIR="${COMFY_DIR:-/root/ComfyUI}"
STORY_MODEL="${STORY_MODEL:-qwen3.5:27b}"
QWEN_MODEL="qwen_image_distill_full_fp8_e4m3fn.safetensors"
QWEN_EDIT_MODEL="qwen_image_edit_2509_fp8_e4m3fn.safetensors"
QWEN_ENCODER="qwen_2.5_vl_7b_fp8_scaled.safetensors"
QWEN_VAE="qwen_image_vae.safetensors"

log(){ printf '\n\033[1;36m[StoryFrame]\033[0m %s\n' "$*"; }
fail(){ printf '\n\033[1;31m[Lỗi]\033[0m %s\n' "$*" >&2; exit 1; }

[[ "${EUID}" -eq 0 ]] || fail "Hãy chạy bằng root bên trong container."
command -v nvidia-smi >/dev/null || fail "Container chưa được cấp NVIDIA GPU."
log "Kiểm tra GPU"
nvidia-smi --query-gpu=name,memory.total,driver_version --format=csv,noheader
VRAM_MB="$(nvidia-smi --query-gpu=memory.total --format=csv,noheader,nounits | head -n1 | tr -d ' ')"
[[ "$VRAM_MB" -ge 24000 ]] || fail "Qwen-Image cần GPU tối thiểu khoảng 24 GB VRAM; phát hiện ${VRAM_MB} MB."

AVAILABLE_GB="$(df -Pk "$APP_DIR" | awk 'NR==2 {print int($4/1024/1024)}')"
[[ "$AVAILABLE_GB" -ge 80 ]] || fail "Cần tối thiểu 80 GB trống cho hai model Qwen-Image; hiện còn ${AVAILABLE_GB} GB."

log "Cài package hệ thống và C compiler cho Triton"
apt-get update
env DEBIAN_FRONTEND=noninteractive apt-get install -y \
  git git-lfs curl wget ca-certificates ffmpeg libgl1 libglib2.0-0 \
  python3 python3-venv python3-pip python3-dev build-essential gcc g++ \
  adb libgl1-mesa-dev libx11-dev libxtst-dev

log "Kiểm tra Ollama"
command -v ollama >/dev/null || fail "Image không có Ollama. Nên tạo container từ ollama/ollama:latest."
if ! curl -fsS http://127.0.0.1:11434/api/tags >/dev/null 2>&1; then
  nohup ollama serve > /tmp/ollama.log 2>&1 &
fi
for _ in {1..30}; do curl -fsS http://127.0.0.1:11434/api/tags >/dev/null 2>&1 && break; sleep 1; done
curl -fsS http://127.0.0.1:11434/api/tags >/dev/null || fail "Ollama không khởi động được. Xem /tmp/ollama.log"
log "Tải $STORY_MODEL"
ollama pull "$STORY_MODEL"

log "Cài ComfyUI"
if [[ ! -d "$COMFY_DIR/.git" ]]; then
  git clone --depth 1 https://github.com/Comfy-Org/ComfyUI.git "$COMFY_DIR"
else
  git -C "$COMFY_DIR" pull --ff-only
fi
python3 -m venv "$COMFY_DIR/.venv"
"$COMFY_DIR/.venv/bin/python" -m pip install --upgrade pip
"$COMFY_DIR/.venv/bin/pip" install \
  torch==2.7.1 torchvision==0.22.1 torchaudio==2.7.1 \
  --index-url https://download.pytorch.org/whl/cu126
"$COMFY_DIR/.venv/bin/pip" install -r "$COMFY_DIR/requirements.txt"
CC=/usr/bin/gcc CXX=/usr/bin/g++ "$COMFY_DIR/.venv/bin/python" -c \
  "import torch; assert torch.cuda.is_available(), 'PyTorch không nhận CUDA'; print('CUDA OK:', torch.cuda.get_device_name(0))"

log "Tải Qwen-Image, Qwen-Image-Edit-2509, text encoder và VAE"
mkdir -p "$COMFY_DIR/models/diffusion_models" "$COMFY_DIR/models/text_encoders" "$COMFY_DIR/models/vae"
[[ -s "$COMFY_DIR/models/diffusion_models/$QWEN_MODEL" ]] || wget --continue --output-document="$COMFY_DIR/models/diffusion_models/$QWEN_MODEL" "https://huggingface.co/Comfy-Org/Qwen-Image_ComfyUI/resolve/main/non_official/diffusion_models/$QWEN_MODEL"
[[ -s "$COMFY_DIR/models/diffusion_models/$QWEN_EDIT_MODEL" ]] || wget --continue --output-document="$COMFY_DIR/models/diffusion_models/$QWEN_EDIT_MODEL" "https://huggingface.co/Comfy-Org/Qwen-Image-Edit_ComfyUI/resolve/main/split_files/diffusion_models/$QWEN_EDIT_MODEL"
[[ -s "$COMFY_DIR/models/text_encoders/$QWEN_ENCODER" ]] || wget --continue --output-document="$COMFY_DIR/models/text_encoders/$QWEN_ENCODER" "https://huggingface.co/Comfy-Org/Qwen-Image_ComfyUI/resolve/main/split_files/text_encoders/$QWEN_ENCODER"
[[ -s "$COMFY_DIR/models/vae/$QWEN_VAE" ]] || wget --continue --output-document="$COMFY_DIR/models/vae/$QWEN_VAE" "https://huggingface.co/Comfy-Org/Qwen-Image_ComfyUI/resolve/main/split_files/vae/$QWEN_VAE"

log "Cài StoryFrame"
python3 -m venv "$APP_DIR/.venv-app"
"$APP_DIR/.venv-app/bin/python" -m pip install --upgrade pip
"$APP_DIR/.venv-app/bin/pip" install -r "$APP_DIR/requirements.txt"
cat > "$APP_DIR/.env" <<EOF
OLLAMA_URL=http://127.0.0.1:11434
OLLAMA_STORY_MODEL=$STORY_MODEL
COMFYUI_URL=http://127.0.0.1:8188
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

log "Khởi động Control Center, ComfyUI và StoryFrame"
bash "$SCRIPT_DIR/start-container.sh"

printf '\n\033[1;32mSETUP HOÀN TẤT\033[0m\n'
printf 'Control Center: http://127.0.0.1:7999\n'
printf 'StoryFrame:    http://127.0.0.1:8010/api/health\n'
printf 'Logs:   tail -F %s/runtime/logs/storyframe.log %s/runtime/logs/comfyui.log\n' "$APP_DIR" "$APP_DIR"
