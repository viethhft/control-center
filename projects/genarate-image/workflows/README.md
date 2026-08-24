# Workflow ComfyUI tùy chỉnh

Trong ComfyUI, bật Dev Mode rồi chọn **Save (API Format)**. Lưu JSON vào thư mục này và đặt `COMFYUI_WORKFLOW=workflows/custom_api.json` trong `.env`.

Thay giá trị tại các node tương ứng bằng placeholder:

- `{{PROMPT}}`, `{{NEGATIVE_PROMPT}}`
- `{{WIDTH}}`, `{{HEIGHT}}`, `{{STEPS}}`, `{{SEED}}`
- `{{CHECKPOINT}}`, `{{FILENAME_PREFIX}}`

Placeholder đứng độc lập sẽ giữ đúng kiểu số. Cách này cho phép bổ sung IP-Adapter, ControlNet hoặc workflow giữ khuôn mặt mà không sửa backend.
