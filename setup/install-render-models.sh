#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP_DIR="$(cd "$SCRIPT_DIR/../projects/genarate-image" && pwd)"
COMFY_DIR="${COMFY_DIR:-/root/ComfyUI}"
DIFFUSION_DIR="$COMFY_DIR/models/diffusion_models"
ENCODER_DIR="$COMFY_DIR/models/text_encoders"
VAE_DIR="$COMFY_DIR/models/vae"
MODEL_NAME="qwen_image_distill_full_fp8_e4m3fn.safetensors"
EDIT_MODEL_NAME="qwen_image_edit_2509_fp8_e4m3fn.safetensors"
ENCODER_NAME="qwen_2.5_vl_7b_fp8_scaled.safetensors"
VAE_NAME="qwen_image_vae.safetensors"
MODEL_URL="https://huggingface.co/Comfy-Org/Qwen-Image_ComfyUI/resolve/main/non_official/diffusion_models/$MODEL_NAME"
EDIT_MODEL_URL="https://huggingface.co/Comfy-Org/Qwen-Image-Edit_ComfyUI/resolve/main/split_files/diffusion_models/$EDIT_MODEL_NAME"
ENCODER_URL="https://huggingface.co/Comfy-Org/Qwen-Image_ComfyUI/resolve/main/split_files/text_encoders/$ENCODER_NAME"
VAE_URL="https://huggingface.co/Comfy-Org/Qwen-Image_ComfyUI/resolve/main/split_files/vae/$VAE_NAME"

command -v wget >/dev/null || { echo "Thiếu wget" >&2; exit 1; }
[[ -d "$COMFY_DIR" ]] || { echo "Không thấy ComfyUI tại $COMFY_DIR" >&2; exit 1; }
[[ -d "$COMFY_DIR/.git" ]] && git -C "$COMFY_DIR" pull --ff-only
mkdir -p "$DIFFUSION_DIR" "$ENCODER_DIR" "$VAE_DIR"

valid_safetensors(){
	local path="$1"
	[[ -s "$path" ]] || return 1
	"$COMFY_DIR/.venv/bin/python" - "$path" <<'PY'
import sys
from safetensors import safe_open

try:
		with safe_open(sys.argv[1], framework="pt", device="cpu") as model:
				next(iter(model.keys()), None)
except Exception:
		raise SystemExit(1)
PY
}

download_model(){
	local destination="$1" url="$2"
	if valid_safetensors "$destination"; then
		return 0
	fi
	rm -f "$destination"
	wget --continue --output-document="$destination" "$url"
	valid_safetensors "$destination" || { echo "File model bị hỏng: $destination" >&2; exit 1; }
}

echo "[1/4] Tải Qwen-Image distilled FP8"
download_model "$DIFFUSION_DIR/$MODEL_NAME" "$MODEL_URL"
echo "[2/4] Tải Qwen-Image-Edit-2509 FP8 để khóa nhân vật"
download_model "$DIFFUSION_DIR/$EDIT_MODEL_NAME" "$EDIT_MODEL_URL"
echo "[3/4] Tải text encoder và VAE"
download_model "$ENCODER_DIR/$ENCODER_NAME" "$ENCODER_URL"
download_model "$VAE_DIR/$VAE_NAME" "$VAE_URL"

echo "[4/4] Cập nhật cấu hình StoryFrame"
touch "$APP_DIR/.env"
sed -i '/^COMFYUI_CHECKPOINT=/d;/^COMFYUI_PHOTO_CHECKPOINT=/d;/^COMFYUI_ANIME_CHECKPOINT=/d;/^QWEN_IMAGE_MODEL=/d;/^QWEN_IMAGE_EDIT_MODEL=/d;/^QWEN_IMAGE_ENCODER=/d;/^QWEN_IMAGE_VAE=/d' "$APP_DIR/.env"
printf '\nQWEN_IMAGE_MODEL=%s\nQWEN_IMAGE_EDIT_MODEL=%s\nQWEN_IMAGE_ENCODER=%s\nQWEN_IMAGE_VAE=%s\n' "$MODEL_NAME" "$EDIT_MODEL_NAME" "$ENCODER_NAME" "$VAE_NAME" >> "$APP_DIR/.env"

echo "Hoàn tất. Hãy restart bằng: bash $SCRIPT_DIR/start-container.sh"
