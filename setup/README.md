# Control Center Environment Setup

Các script dựng môi trường dùng chung được quản lý tập trung tại đây. Script tự xác định
StoryFrame ở `../projects/genarate-image`; toàn bộ ứng dụng con được quản lý trong
`control-center/projects`.

## Windows workstation

```bat
control-center\setup\run-workstation.bat
```

Kiểm tra các dịch vụ:

```powershell
powershell -ExecutionPolicy Bypass -File control-center\setup\check-workstation.ps1
```

## Ubuntu có systemd

```bash
bash control-center/setup/setup-ubuntu.sh
```

## GPU container

```bash
bash control-center/setup/setup-container.sh
```

Tải/cập nhật riêng model render và khởi động lại:

```bash
bash control-center/setup/install-render-models.sh
bash control-center/setup/start-container.sh
```

`requirements.txt` và launcher hằng ngày vẫn nằm trong từng project vì Control Center
dùng chúng làm dependency/entry point riêng cho mỗi ứng dụng.
