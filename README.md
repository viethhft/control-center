# MMO Control Center

Server tổng để đăng ký, chạy/dừng và phân quyền các ứng dụng con.

## Chạy

```bat
cd control-center
set CONTROL_ADMIN_PASSWORD=mat-khau-rat-manh
run.bat
```

Mở `http://127.0.0.1:7999`. Nếu không đặt biến môi trường ở lần chạy đầu, mật khẩu admin ngẫu nhiên được in trong terminal. Database SQLite nằm ở `control-center.db` và không nên commit/chia sẻ.

## Money Printer Turbo

Money Printer Turbo được quản lý trong danh sách ứng dụng, với nút khởi động/dừng
và mở WebUI tại `http://127.0.0.1:8050`. Làm mới Control Center để thấy ứng dụng.

Cài trên Windows từ thư mục `control-center` (tự tải Python 3.11 và `uv` vào `runtime`):

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File setup/install-money-printer.ps1
```

Hoặc cài môi trường riêng bằng Python 3.11 và dependencies đã khóa nếu đã có `uv`:

```powershell
cd projects/money-printer-turbo
uv sync --frozen --no-dev --python 3.11
```

Lệnh này cũng áp dụng trên Linux; Control Center sử dụng `.venv/bin/python`.
Không dùng môi trường Python 3.10 của Control Center cho ứng dụng này.
Sau khi cài, nhấn **Khởi động ứng dụng** trong Control Center và cấu hình nhà
cung cấp AI/API key trong WebUI trước khi tạo video. Log nằm tại
`logs/money-printer-turbo.log`; health check dùng `/_stcore/health`.
Control Center giữ cổng 8050 cố định, không dùng cơ chế tự đổi cổng của `webui.bat`.
Quyền điều khiển là `money-printer-turbo:*`. Các quyền chức năng chỉ là metadata
của Control Center, chưa được cưỡng chế bên trong WebUI Streamlit.

## Thêm hoặc bớt dự án

Sửa `projects.json`. Mỗi dự án có thư mục chạy, command dạng mảng, URL, health URL và danh sách chức năng. Không nhận command từ API để tránh biến giao diện quản trị thành nơi thực thi lệnh tùy ý.

Quyền `project:*` cho phép bật/tắt dự án. Quyền `project:feature` mô tả chức năng người dùng được phép sử dụng. `allowed_ips` trống nghĩa là cho phép mọi IP; nếu có dữ liệu, IP client phải khớp chính xác một mục.

## Lưu ý khi đưa lên Internet

Control Center hiện là MVP quản trị process trên một máy. Trước khi public, đặt nó sau Caddy/Nginx với HTTPS, firewall chỉ mở cổng 443, và không public trực tiếp cổng 8000/8010. Để cưỡng chế quyền đến từng API chức năng, các app con cần gọi `/api/authorize/{project}/{feature}` qua cơ chế service-to-service hoặc được đặt sau một API gateway; chỉ ẩn nút trên giao diện là chưa đủ an toàn.
