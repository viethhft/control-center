# Control Center Monorepo Architecture

```text
control-center/
├── app.py                 # API quản trị, phân quyền và process manager
├── projects.json          # Registry/command/port của project con
├── static/                # Control Center UI
├── setup/                 # Script dựng môi trường workstation/container
├── projects/              # Toàn bộ ứng dụng nghiệp vụ
│   ├── text-to-speech-app/
│   ├── genarate-image/
│   ├── story-research-lab/
│   ├── emotion-markup-studio/
│   └── remote-device-hub/
├── data/                  # Database và dữ liệu quản trị trung tâm
├── logs/                  # Log process do Control Center khởi chạy
└── runtime/               # PID và trạng thái runtime
```

## Quy ước

- `projects.json` là nguồn cấu hình duy nhất để Control Center tìm và chạy ứng dụng con.
- Mỗi project tự quản lý source, static assets, dữ liệu nghiệp vụ và requirements riêng.
- Project có thể dùng virtualenv riêng; các app nhỏ hiện dùng chung virtualenv của
  `genarate-image` theo command đã khai báo.
- Script cài workstation/GPU/container nằm trong `setup`, không nằm trong project con.
- URL public và health URL không phụ thuộc vị trí vật lý của source.

## Thêm project mới

1. Tạo thư mục trong `projects/<project-id>`.
2. Khai báo `directory`, `command`, `url`, `health_url` và `features` trong
   `projects.json`.
3. Cấp quyền project/feature cho user trong giao diện quản trị.
