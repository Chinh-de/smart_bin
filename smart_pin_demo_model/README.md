# Demo phân loại rác trên laptop (có đọc tiếng Việt)

Webcam (hoặc ảnh có sẵn) → model TFLite 10 lớp → chọn thùng theo `../smartbin_pi/config/bins.json` → hiển thị kết quả và **đọc to hành động**, ví dụ *"Nhựa. Bỏ vào thùng rác tái chế."*, *"Phát hiện pin. Pin là rác nguy hại, vui lòng bỏ vào điểm thu gom pin."*

Dùng chung bộ phân loại và logic chọn thùng với ứng dụng trên Pi (`../smartbin_pi/smartbin`), nên kết quả giống hệt khi chạy trên thùng rác thật.

## Cài đặt (môi trường ảo)

```powershell
cd smart_pin_demo_model
py -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt
```

Model: mặc định đọc `../Model_training/models/model_int8.tflite` + `labels.json` (tải từ output Kaggle, không nằm trong git). Dùng file khác: `--model đường/dẫn/model.tflite`.

## Chạy

```powershell
.venv\Scripts\python demo.py                          # webcam
.venv\Scripts\python demo.py --image anh.jpg          # 1 ảnh
.venv\Scripts\python demo.py --image thu_muc_anh      # cả thư mục, phím bất kỳ = ảnh tiếp
.venv\Scripts\python demo.py --image thu_muc_anh --no-window   # chỉ in kết quả + đọc
```

Phím trong cửa sổ webcam:

| Phím | Tác dụng |
|---|---|
| `SPACE` | Phân loại (trung bình 5 khung hình liên tiếp, giống trên Pi) |
| `A` | Bật/tắt tự động phân loại mỗi 3 s (`--auto-interval`) |
| `M` | Bật/tắt tiếng |
| `Q` / `Esc` | Thoát |

Khung trắng giữa màn hình là vùng model nhìn thấy (ảnh được cắt vuông ở giữa) — đặt vật vào trong khung, nền càng trơn càng tốt.

## Giọng đọc

- Dùng **edge-tts** (giọng neural của Microsoft): `vi-VN-HoaiMyNeural` (nữ, mặc định) hoặc `--voice vi-VN-NamMinhNeural` (nam).
- **Lần chạy đầu cần Internet** để tạo file âm thanh; các câu được lưu trong `audio_cache/` nên lần sau chạy không cần mạng. Lúc khởi động chương trình tạo sẵn tất cả câu có thể đọc (khoảng 11 câu, mất vài chục giây lần đầu) — trong lúc đó vẫn phân loại bình thường.
- Windows không có sẵn giọng tiếng Việt offline, nên không dùng `pyttsx3`.
- Tắt tiếng: `--mute`.

## Tuỳ chọn khác

| Tham số | Mặc định | Ý nghĩa |
|---|---|---|
| `--camera` | 0 | Chỉ số webcam (thử 1 nếu laptop có nhiều camera) |
| `--frames` | 5 | Số khung trung bình mỗi lần phân loại |
| `--bins` | `../smartbin_pi/config/bins.json` | Cách gom lớp vào thùng |
