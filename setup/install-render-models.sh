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

echo "[1/4] Tải Qwen-Image distilled FP8"
[[ -s "$DIFFUSION_DIR/$MODEL_NAME" ]] || wget --continue --output-document="$DIFFUSION_DIR/$MODEL_NAME" "$MODEL_URL"
echo "[2/4] Tải Qwen-Image-Edit-2509 FP8 để khóa nhân vật"
[[ -s "$DIFFUSION_DIR/$EDIT_MODEL_NAME" ]] || wget --continue --output-document="$DIFFUSION_DIR/$EDIT_MODEL_NAME" "$EDIT_MODEL_URL"
echo "[3/4] Tải text encoder và VAE"
[[ -s "$ENCODER_DIR/$ENCODER_NAME" ]] || wget --continue --output-document="$ENCODER_DIR/$ENCODER_NAME" "$ENCODER_URL"
[[ -s "$VAE_DIR/$VAE_NAME" ]] || wget --continue --output-document="$VAE_DIR/$VAE_NAME" "$VAE_URL"

echo "[4/4] Cập nhật cấu hình StoryFrame"
touch "$APP_DIR/.env"
sed -i '/^COMFYUI_CHECKPOINT=/d;/^COMFYUI_PHOTO_CHECKPOINT=/d;/^COMFYUI_ANIME_CHECKPOINT=/d;/^QWEN_IMAGE_MODEL=/d;/^QWEN_IMAGE_EDIT_MODEL=/d;/^QWEN_IMAGE_ENCODER=/d;/^QWEN_IMAGE_VAE=/d' "$APP_DIR/.env"
printf '\nQWEN_IMAGE_MODEL=%s\nQWEN_IMAGE_EDIT_MODEL=%s\nQWEN_IMAGE_ENCODER=%s\nQWEN_IMAGE_VAE=%s\n' "$MODEL_NAME" "$EDIT_MODEL_NAME" "$ENCODER_NAME" "$VAE_NAME" >> "$APP_DIR/.env"

echo "Hoàn tất. Hãy restart bằng: bash $SCRIPT_DIR/start-container.sh"
