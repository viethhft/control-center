# Text-to-Speech Studio — VieNeu-TTS Local

Ứng dụng chuyển văn bản và truyện phân vai thành một file MP3 hoàn chỉnh bằng
VieNeu v3 Turbo. Engine ONNX int8 chạy trực tiếp trên CPU, không dùng API key,
không bị giới hạn quota và không chiếm kết nối WebSocket tới dịch vụ TTS ngoài.

## Giọng đọc

Ứng dụng cung cấp 10 giọng tiếng Việt Bắc/Nam. Mặc định:

- vieneu-ngoc-linh: giọng nữ Bắc, phong cách đọc truyện.
- vieneu-thanh-binh: giọng nam Bắc, dùng cho vai nam.
- Các giọng còn lại có thể chọn trực tiếp trên giao diện.

### Tạo giọng tùy chỉnh

Trong mục **Tạo giọng của tôi**, tải từ 1 đến 8 file WAV, MP3, FLAC hoặc OGG. Mỗi
file nên có 3–8 giây giọng nói rõ ràng của cùng một người, không nhạc nền và ít
tiếng vang. Ứng dụng kiểm tra và lưu riêng từng mẫu, trích đặc trưng người nói bằng
VieNeu v3 Turbo, tự loại mẫu lệch giọng rõ rệt và tổng hợp các mẫu còn lại thành
một profile. Profile được lưu tại `data/custom_voices`, xuất hiện trong cả chế độ
đọc thường và phân vai, đồng thời vẫn dùng được sau khi khởi động lại.

Với giọng đã tạo, bấm nút **micro +** trong thẻ giọng để bổ sung thêm 1–8 file mỗi
lần (tối đa 24 mẫu/profile). Mỗi lần cập nhật, ứng dụng đối chiếu lại toàn bộ mẫu,
gộp speaker embedding của các mẫu phù hợp và dùng đoạn tốt nhất làm tham chiếu.
Nên dùng các câu khác nhau nhưng cùng người nói, cùng kiểu thu và chất lượng tương
đối đồng đều. Số mẫu thực sự được dùng/tổng số mẫu được hiển thị ngay trong thẻ.

Đây là zero-shot voice cloning có tổng hợp nhiều speaker embedding, không phải
fine-tune model. VieNeu chỉ dùng tối đa 8 giây từ mỗi file. Fine-tune LoRA từ bộ dữ
liệu có transcript là một pipeline GPU riêng và không chạy trên backend ONNX/CPU
hiện tại.

## Cài đặt

Yêu cầu Python 3.10:

    python -m venv .venv
    .\.venv\Scripts\python.exe -m pip install -r requirements.txt

VieNeu tự tải model từ Hugging Face trong lần tổng hợp đầu tiên. Các lần chạy
sau sử dụng model đã lưu trong cache trên máy.

## Chạy ứng dụng

    .\run.bat

Mở http://127.0.0.1:8000.

## Phân vai bằng AI local (tùy chọn)

Chế độ **Nhanh** chạy hoàn toàn trên máy và không cần cấu hình. Để dùng chế độ
**AI phân vai & biểu cảm**, cài Ollama trên Windows rồi tải model Qwen 2.5 3B:

    ollama pull qwen3.5:27b
    .\run.bat

Mặc định ứng dụng kết nối `http://127.0.0.1:11434` và không gửi truyện ra ngoài
máy. Có thể đổi model qua biến `OLLAMA_ROLE_MODEL` hoặc địa chỉ máy chủ qua
`OLLAMA_HOST`. Nếu Ollama chưa chạy, thiếu model, timeout hoặc trả dữ liệu không
hợp lệ, ứng dụng tự động dùng chế độ Nhanh để người dùng vẫn tiếp tục tạo audio.

AI trả về cả người đọc, cảm xúc, cường độ và chỉ dẫn diễn xuất cho từng đoạn. Ứng
dụng chuyển các kết quả này thành tốc độ, cao độ, âm lượng và khoảng nghỉ thực tế;
nhãn cảm xúc không bị đọc thành lời. Với máy chỉ dùng CPU, mặc định xử lý 8 đoạn mỗi
lượt. Có thể chỉnh `OLLAMA_ROLE_BATCH_SIZE` (1-16) và `OLLAMA_ROLE_TIMEOUT` (giây).

## Kiểm tra các giọng

Khởi động server trước, sau đó mở terminal khác:

    .\.venv\Scripts\python.exe verify_all_voices.py

## Pipeline

1. Chuẩn hóa Markdown, ký tự ẩn và các đoạn không phù hợp cho TTS.
2. Phân tích lời dẫn/hội thoại, giữ nguyên nội dung và thứ tự.
3. Chia văn bản theo ranh giới câu để xử lý ổn định.
4. Tổng hợp PCM 48 kHz bằng VieNeu v3 Turbo ONNX int8.
5. Mã hóa toàn bộ bằng một encoder MP3 để tạo file liền mạch.
6. Stream kết quả để nghe và đồng thời lưu file trong generated_audio.

Thời gian trên CPU phụ thuộc độ dài audio. Lần đầu cần thêm thời gian nạp model;
giao diện hiển thị khoảng ước tính dựa trên số từ và tốc độ đọc.
