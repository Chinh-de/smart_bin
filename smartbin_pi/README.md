# Smartbin trên Raspberry Pi 4

Đặt rác lên khay → HC-SR04 phát hiện → webcam chụp → model TFLite phân loại 10 lớp → servo đưa vào thùng (pin: từ chối + cảnh báo) → ghi log và gửi ảnh + thông tin lên web server (chạy ngay trên Pi).

Đặc tả: [../docs/dac_ta_ung_dung.md](../docs/dac_ta_ung_dung.md)

```
smartbin_pi/
├── main.py                 # chương trình điều khiển thùng rác
├── config/                 # app.json, hardware.json (chân GPIO, góc, thời gian), bins.json (lớp → thùng)
├── models/                 # chép model_int8.tflite + labels.json từ Kaggle vào đây
├── smartbin/               # classifier, decider, camera, hardware, uploader, controller, mock
├── tools/                  # test_image.py, calibrate.py
├── server/                 # web server Flask + SQLite
└── systemd/                # tự khởi động khi bật nguồn
```

## 1. Nối dây

| Thiết bị | Chân Pi (BCM) |
|---|---|
| HC-SR04 VCC / GND | 5V / GND |
| HC-SR04 TRIG | GPIO23 |
| HC-SR04 ECHO | GPIO24 **qua cầu chia áp**: ECHO → 1 kΩ → GPIO24 → 2 kΩ → GND |
| Servo máng (tín hiệu) | GPIO12 |
| Servo khay (tín hiệu) | GPIO13 |
| Đèn chiếu (qua MOSFET/relay) | GPIO27 |
| LED xanh / LED đỏ (qua điện trở 330 Ω) | GPIO5 / GPIO6 |
| Buzzer | GPIO26 |
| Webcam | USB |

**Servo dùng nguồn 5–6 V riêng (≥ 2 A)**, nối GND nguồn servo với GND của Pi. Không lấy nguồn servo từ chân 5 V của Pi.
Đổi chân thì sửa `config/hardware.json`.

## 2. Cài đặt (Raspberry Pi OS 64-bit Bookworm)

```bash
sudo apt update
sudo apt install -y python3-venv python3-gpiozero python3-lgpio
# chép thư mục smartbin_pi vào /home/pi/smartbin_pi, rồi:
cd ~/smartbin_pi
python3 -m venv --system-site-packages .venv     # dùng lại gpiozero/lgpio của apt
.venv/bin/pip install -r requirements.txt
```

Nếu `ai-edge-litert` không cài được: `.venv/bin/pip install tflite-runtime` (code tự nhận).

Chép từ output Kaggle vào `models/`: `model_int8.tflite`, `labels.json` (và `model_fp16.tflite` nếu muốn so sánh).

## 3. Kiểm tra từng phần

```bash
# model: phân loại ảnh có sẵn, in top-3, thùng và thời gian suy luận
.venv/bin/python tools/test_image.py anh_mau/

# phần cứng: chỉnh góc servo từng thùng, góc lật khay, đo khoảng cách khay
.venv/bin/python tools/calibrate.py
```

Với `calibrate.py`:
1. `d` khi khay trống, rồi `d` khi đặt một vật nhỏ. Đặt `trigger_cm` nằm giữa hai giá trị, `clear_cm` lớn hơn `trigger_cm` 2–3 cm.
2. `c <góc>` tới khi máng chỉ đúng từng thùng → ghi vào `angle` trong `config/bins.json`; góc nghỉ → `chute_rest_angle`.
3. `t <góc>` để tìm góc khay nằm ngang (`tray_rest_angle`) và góc đủ để rác trượt xuống (`tray_tilt_angle`).
4. `s` chụp thử `calib.jpg` để chỉnh vị trí webcam/đèn: vật nằm giữa khung, nền trắng.

## 4. Chạy

```bash
# cửa sổ 1: web server
cd ~/smartbin_pi/server && ../.venv/bin/python app.py
# cửa sổ 2: thùng rác
cd ~/smartbin_pi && .venv/bin/python main.py
```

Mở web từ điện thoại/laptop cùng WiFi: `http://<ip-của-pi>:5000` (xem IP: `hostname -I`).

- **Tổng quan**: thiết bị online/offline, số rác hôm nay theo thùng, sự kiện mới nhất (tự làm mới 5 s).
- **Lịch sử**: lọc theo thùng / kết quả / lớp / ngày, bấm vào xem ảnh lớn và xác suất.
- **Thống kê**: theo thùng, lớp, giờ, ngày; số lần từ chối, không chắc chắn, lỗi kẹt rác.

API cho phần mở rộng: `GET /api/events`, `GET /api/stats`.

## 5. Tự khởi động khi bật nguồn

```bash
sudo cp systemd/*.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now smartbin-server smartbin
journalctl -u smartbin -f        # xem log
```

Đổi khoá API: sửa `Environment=SMARTBIN_API_KEY=...` trong `smartbin-server.service` **và** `api_key` trong `config/app.json`.

## 6. Chạy thử trên laptop (không cần Pi)

```bash
pip install numpy opencv-python-headless requests flask
python server/app.py
python main.py --mock --images thu_muc_anh/
```

Nhấn **Enter** = đặt / lấy vật trên khay; mỗi lần "chụp" lấy 1 ảnh trong thư mục; servo, đèn, còi in ra màn hình.
Chưa có model trong `models/` thì dùng bộ phân loại ngẫu nhiên (chỉ để thử luồng). Có model thì cần thêm `pip install ai-edge-litert` (hoặc `tensorflow`).

## 7. Dữ liệu tạo ra

| Ở đâu | Nội dung |
|---|---|
| `logs/events.csv` | Mỗi lần xử lý 1 dòng: thời gian, kết quả, thùng, top-3, khoảng cách, thời gian |
| `logs/app.log` | Log chương trình |
| `captures/<lớp dự đoán>/<id>.jpg` | Ảnh đã chụp — **dùng làm dữ liệu fine-tune** (sửa lại nhãn sai trước khi dùng) |
| `queue/` | Sự kiện chưa gửi được lên server (tự gửi bù); `queue/failed/` là sự kiện bị server từ chối |
| `server/smartbin.db`, `server/uploads/` | Dữ liệu của web server |

## 8. Đổi cách phân loại

Chỉ sửa `config/bins.json`, không cần train lại. Ví dụ đưa thủy tinh ra khỏi thùng 3 và từ chối:

```json
"reject": [
  {"class": "battery", "threshold": 0.4, "message": "Pin là rác nguy hại, vui lòng bỏ vào điểm thu gom pin"},
  {"class": "glass",   "threshold": 0.4, "message": "Vui lòng bỏ thủy tinh vào ngăn riêng"}
]
```
(và xoá `"glass"` khỏi `classes` của thùng 3).
