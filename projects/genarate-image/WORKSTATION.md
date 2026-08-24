# Triển khai StoryFrame trên máy trạm

## Thành phần bắt buộc

- FastAPI StoryFrame: điều phối job và lưu project.
- Ollama: phân tích truyện và tạo prompt tiếng Việt.
- ComfyUI: runtime/API thực thi workflow Qwen-Image.
- Qwen-Image Distilled FP8, Qwen 2.5 VL text encoder và Qwen Image VAE.

ComfyUI không phải model tạo ảnh. Nó là engine chạy Qwen-Image; vì vậy không được gỡ phần cài hoặc khởi động ComfyUI nếu vẫn dùng endpoint `/api/generate` hiện tại.

## Cấu hình khuyến nghị

- NVIDIA GPU 24 GB VRAM trở lên; máy mục tiêu V100 32 GB phù hợp nhưng FP8 không được tăng tốc phần cứng như GPU đời mới.
- RAM tối thiểu 64 GB, khuyến nghị 94 GB.
- SSD trống tối thiểu 80 GB.
- Chỉ render một ảnh cùng lúc với `COMFYUI_MAX_CONCURRENCY=1`.

## Cài đặt

Từ thư mục workspace, trong Docker Ubuntu dùng `bash control-center/setup/setup-container.sh`. Trên Ubuntu có systemd dùng `bash control-center/setup/setup-ubuntu.sh`.

Nâng cấp một môi trường đã có ComfyUI:

```bash
chmod +x control-center/setup/*.sh
bash control-center/setup/install-render-models.sh
bash control-center/setup/start-container.sh
```

## Kiểm tra

```bash
curl -s http://127.0.0.1:8000/api/health | python3 -m json.tool
tail -F runtime/logs/storyframe.log runtime/logs/comfyui.log
```

Health hợp lệ cần có `comfyui: true` và `qwen_models_ready: true`.

Trạng thái phân tích được lưu trong `runtime/analysis_jobs`; ảnh hoàn tất được ghi ngay vào `projects/<project-id>/images`.
