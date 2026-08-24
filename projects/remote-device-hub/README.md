# Remote Device Hub

Web hub để đăng ký, theo dõi và điều khiển nhiều thiết bị.

## Chạy server

Chạy `run.bat`, sau đó mở `http://127.0.0.1:8040`. Nên đặt biến môi trường `DEVICE_HUB_TOKEN` thành chuỗi bí mật trước khi chạy trên mạng LAN.

## Kết nối máy tính hoặc Android

Tạo thiết bị trên web và sao chép lệnh agent được sinh ra. Cài `requirements.txt` trên máy agent. Android cần bật USB debugging và cài ADB.

iPhone/iPad hiện ở chế độ chỉ xem vì iOS không cấp API điều khiển toàn hệ thống cho agent thông thường.
