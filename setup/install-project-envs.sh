#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
PROJECTS_DIR="$ROOT_DIR/projects"

log(){ printf '\n\033[1;36m[Control Center]\033[0m %s\n' "$*"; }
fail(){ printf '\n\033[1;31m[Lỗi]\033[0m %s\n' "$*" >&2; exit 1; }

command -v python3 >/dev/null || fail "Không tìm thấy python3"

install_env(){
  local directory="$1" env_name="$2" label="$3"
  [[ -f "$directory/app.py" ]] || fail "$label thiếu app.py tại $directory"
  [[ -f "$directory/requirements.txt" ]] || fail "$label thiếu requirements.txt tại $directory"
  log "Cài môi trường $label"
  python3 -m venv "$directory/$env_name"
  "$directory/$env_name/bin/python" -m pip install --upgrade pip wheel setuptools
  "$directory/$env_name/bin/pip" install -r "$directory/requirements.txt"
}

install_env "$ROOT_DIR" ".venv-linux" "Control Center"
install_env "$PROJECTS_DIR/text-to-speech-app" ".venv-linux" "Text-to-Speech"
install_env "$PROJECTS_DIR/story-video-studio" ".venv-linux" "Story Video Studio"

# StoryFrame có môi trường CUDA riêng do setup Ubuntu/container tạo. Chỉ tạo
# fallback khi người dùng chạy installer môi trường độc lập.
if [[ ! -x "$PROJECTS_DIR/genarate-image/.venv-app/bin/python" ]]; then
  install_env "$PROJECTS_DIR/genarate-image" ".venv-app" "StoryFrame"
fi

install_env "$PROJECTS_DIR/story-research-lab" ".venv-linux" "Story Research"
install_env "$PROJECTS_DIR/emotion-markup-studio" ".venv-linux" "Emotion Markup"
install_env "$PROJECTS_DIR/remote-device-hub" ".venv-linux" "Remote Device Hub"

log "Đã cài xong môi trường Python cho toàn bộ hệ thống"
