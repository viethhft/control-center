#!/usr/bin/env bash
set -Eeuo pipefail
sudo systemctl restart ollama storyframe-comfyui storyframe
sudo systemctl --no-pager --full status ollama storyframe-comfyui storyframe
IP_ADDRESS="$(hostname -I | awk '{print $1}')"
echo "StoryFrame: http://${IP_ADDRESS}:8000"
