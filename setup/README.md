# Thiết lập toàn bộ Control Center

Thư mục này quản lý môi trường và vòng đời của toàn hệ thống, không thuộc riêng
StoryFrame.

## Container Ubuntu/GPU cloud

```bash
bash setup/setup-container.sh
```

Script sẽ cài Control Center, môi trường Python của cả 5 project, Ollama, ComfyUI,
model render và khởi động toàn bộ dịch vụ.

Sau lần cài đầu tiên:

```bash
bash setup/start-container.sh
bash setup/stop-container.sh
```

## Ubuntu có systemd

```bash
bash setup/setup-ubuntu.sh
```

Các service được tạo:

- `control-center` — cổng 7999
- `text-to-speech` — cổng 8000
- `storyframe` — cổng 8010
- `story-research` — cổng 8020
- `emotion-markup` — cổng 8030
- `remote-device-hub` — cổng 8040
- `storyframe-comfyui` — cổng nội bộ 8188
- `ollama` — cổng nội bộ 11434

## Chỉ cài lại dependency project

```bash
bash setup/install-project-envs.sh
```

## Log container

```bash
tail -F logs/*.log
```

PID của project được đặt tại `runtime/pids`, đúng với process manager của Control
Center. Mỗi project giữ `requirements.txt` riêng và được cài vào virtualenv Linux
riêng; StoryFrame dùng `.venv-app` để tách dependency CUDA/render.
