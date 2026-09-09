# Story Video Studio

Project độc lập cho truyện + audio đã thu → video minh họa. Không cần MoneyPrinterTurbo hay StoryFrame chạy; dùng chung Ollama và ComfyUI/model đã cài. Không gọi API trả phí, không lấy footage mạng, không tạo lại giọng đọc.

## Cài và chạy

Python 3.10+; FFmpeg và FFprobe trong PATH. FFmpeg cần libx264 và libass (subtitles).

```powershell
cd E:\Viet\MMO\control-center\projects\story-video-studio
python -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt
Copy-Item .env.example .env
.\run.bat
```

Linux: `python3 -m venv .venv`, `.venv/bin/pip install -r requirements.txt`, `.venv/bin/python -m uvicorn app:app --host 127.0.0.1 --port 8060`.

Mở http://127.0.0.1:8060. Nhập truyện và audio, tùy chọn nhạc, rồi chọn thể loại hình ảnh trong nhóm Animated Storytelling hoặc Cinematic Slideshow. Nhấn Tạo video; kết quả có MP4 720p, SRT và manifest. Mặc định Whisper nhận diện tiếng Việt trên CPU; lần đầu tải model. Chế độ estimate không cần Whisper nhưng thời gian chỉ là ước lượng, đặc biệt dễ lệch khi có im lặng/đọc khác truyện.

Ollama phải có model trong OLLAMA_MODEL. ComfyUI cần Qwen-Image distilled, Qwen-Image-Edit-2509, encoder và VAE như .env.example; cần node TextEncodeQwenImageEditPlus. Không tự tải các model ảnh lớn. Dùng cấu hình ComfyUI hiện có của StoryFrame.

## Cách hoạt động

Whisper lấy timestamp theo audio; captions giữ lời nhận diện thực tế, không giả vờ là forced alignment nguyên văn. Truyện cung cấp character bible tích lũy theo chunk. Model lập cảnh từ lời đọc, mô tả ảnh bằng tiếng Anh sau khi giải nghĩa tiếng Việt. Từng nhân vật có một ảnh reference tái sử dụng; cảnh có người dùng Image Edit. Hiện ảnh reference được chọn tự động; chưa có màn hình duyệt/sửa. Chưa tự kiểm tra mọi sai lệch danh tính, tuổi hay trang phục.

FFmpeg tạo pan/zoom 24fps, cache clip theo nội dung ảnh/thời gian/chuyển động, ghép tuần tự, giữ audio gốc và trộn nhạc có ducking. Khi không burn subtitle, đoạn ghép cuối stream-copy video. Khi burn subtitle cần encode thêm một lần. Đây là lựa chọn giới hạn RAM cho truyện dài, không phải một filtergraph khổng lồ. Chưa có AI video, parallax, ambience/SFX tự động.

Các lựa chọn hình ảnh hiện được lưu trong `project.json` và `manifest.json`: Animated Storytelling gồm Illustrated Storytelling với Hand-drawn Whiteboard, Motion Comic, Cutout Animation; ngoài ra có Cinematic Slideshow. Pipeline hiện vẫn dùng renderer ảnh tĩnh chung; các lựa chọn này là hợp đồng cấu hình để renderer chuyên biệt có thể được thêm tiếp theo.

Mỗi job lưu tại data/ID: project.json, captions.json, bible.json, manifest.json, ảnh và cache clip. Lỗi có thể nhấn Tiếp tục; job gián đoạn sau restart không tự chạy lại. Một worker tránh các job tranh GPU. ComfyUI prompt ID được lưu ngay sau khi nhận response để tiếp tục polling; nếu ComfyUI mất history sau restart, cần kiểm tra job và xóa receipt tương ứng trước retry. Các job của ứng dụng khác vẫn có thể tranh GPU.

Muốn chỉnh nâng cao: khi job đã dừng, sửa prompt_en/motion trong manifest.json rồi tiếp tục. Thay nội dung nguồn/audio nên tạo project mới vì cache phân tích thuộc project. Không sửa file khi job đang chạy. Dữ liệu cục bộ không được xóa tự động.

API: GET /api/health, GET /api/projects, POST /api/projects (multipart), POST /api/projects/ID/resume; download qua /api/projects/ID/files/final.mp4. Chạy một process uvicorn; chưa hỗ trợ multi-worker. Chỉ bind localhost; đây là app cá nhân chưa có đăng nhập.

Tài liệu kỹ thuật: [FFmpeg filters](https://ffmpeg.org/ffmpeg-filters.html), [Ollama chat](https://docs.ollama.com/api/chat).
