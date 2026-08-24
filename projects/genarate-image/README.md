# StoryFrame Studio

Ứng dụng biến truyện thành character bible, storyboard và prompt tiếng Việt, sau đó render bằng Qwen-Image Distilled FP8 thông qua ComfyUI.

## Kiến trúc

`Trình duyệt → FastAPI → Ollama` để phân tích truyện.

`FastAPI → ComfyUI API → Qwen-Image` để render ảnh.

ComfyUI vẫn là runtime/API cần thiết; dự án không còn dùng Stable Diffusion WebUI, SDXL, RealVisXL hay Animagine.

## Cài trong container Ubuntu

```bash
git clone git@github.com:viethhft/generate_image_for_story.git
cd generate_image_for_story
bash control-center/setup/setup-container.sh
```

Model phân tích mặc định là `qwen3.5:27b`. Có thể đổi đồng bộ qua biến `STORY_MODEL` khi chạy script setup.

Nâng cấp container cũ sang Qwen-Image:

```bash
git pull
chmod +x control-center/setup/*.sh
bash control-center/setup/install-render-models.sh
bash control-center/setup/start-container.sh
```

Ba model Qwen được lưu tại:

- `ComfyUI/models/diffusion_models/qwen_image_distill_full_fp8_e4m3fn.safetensors`
- `ComfyUI/models/text_encoders/qwen_2.5_vl_7b_fp8_scaled.safetensors`
- `ComfyUI/models/vae/qwen_image_vae.safetensors`

Kiểm tra bằng `curl http://127.0.0.1:8000/api/health`. Trường `qwen_models_ready` phải là `true`.

Project và ảnh nằm trong `projects/<project-id>`. Checkpoint phân tích nằm trong `runtime/analysis_jobs`.

## Quy trình khóa nhân vật

1. Phân tích truyện và mở tab **Nhân vật cố định**.
2. Nhấn **Tạo 3 ảnh gốc** cho từng nhân vật xuất hiện trong storyboard.
3. Chọn một ảnh phù hợp để khóa nhân vật.
4. Dùng **Render nháp** để duyệt nhanh, sau đó dùng **Render cuối** hoặc render hàng loạt.

Ảnh gốc nằm tại `projects/<project-id>/character-references/<character-id>`. Cảnh có nhân vật dùng Qwen-Image-Edit-2509 và tối đa ba ảnh reference; cảnh không có nhân vật dùng Qwen-Image thường.

Bộ cài yêu cầu NVIDIA GPU tối thiểu 24 GB VRAM và khoảng 80 GB dung lượng trống. Máy 24–32 GB tự chạy `--lowvram`; máy từ 40 GB chạy `--normalvram`.
