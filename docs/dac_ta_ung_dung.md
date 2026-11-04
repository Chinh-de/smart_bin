# Đặc tả ứng dụng: Thùng rác phân loại trên Raspberry Pi + Web server

> Đã chốt: **webcam USB**, **web server chạy ngay trên Pi** (Pi app gửi tới `http://127.0.0.1:5000`; đổi `server_url` là chuyển server sang máy khác được), có chế độ giả lập. Code: [../smartbin_pi/](../smartbin_pi/README.md)

## 1. Kịch bản sử dụng

1. Người dùng đặt rác lên khay.
2. Cảm biến khoảng cách phát hiện có vật trong vùng khay.
3. Pi bật đèn, chụp ảnh, chạy mô hình phân loại 10 lớp.
4. Pi quyết định thùng (theo `bins.json`) hoặc từ chối (pin).
5. Pi gửi **ảnh + thông tin** lên web server để lưu log.
6. Servo 1 xoay máng tới góc của thùng, servo 2 lật khay thả rác, rồi cả hai về vị trí nghỉ.
7. Web server hiển thị lịch sử, ảnh và thống kê trên trình duyệt.

Bước 5 chạy **song song** với bước 6: mạng chậm hay mất mạng không làm thùng rác đứng.

## 2. Kiến trúc tổng thể

```
┌──────────────────── Raspberry Pi 4 ────────────────────┐        ┌──────── Web server ────────┐
│                                                        │        │                            │
│  HC-SR04 ──► Detector ──► Controller (máy trạng thái)  │  HTTP  │  Flask API                 │
│                              │         │               │ ─────► │   POST /api/events         │
│  Camera ──► Capture ──► Classifier (TFLite)            │  POST  │   (ảnh + JSON)             │
│                              │                         │        │        │                   │
│                         Decider (bins.json)            │        │   SQLite + thư mục ảnh     │
│                              │                         │        │        │                   │
│  Servo máng, servo khay ◄─ Actuator                    │        │   Trang web: lịch sử,      │
│  LED, buzzer            ◄─ Indicator                   │        │   ảnh, thống kê, độ đầy    │
│                              │                         │        │                            │
│                         Uploader (hàng đợi offline) ───┼────────┘                            │
│                         Log cục bộ (CSV + ảnh)         │        └────────────────────────────┘
└────────────────────────────────────────────────────────┘
```

**Web server chạy ở đâu** (đã chọn B):

| Phương án | Ưu | Nhược |
|---|---|---|
| A. Laptop cùng mạng WiFi | Đúng mô hình IoT (thiết bị → server), Pi nhẹ việc, demo xem trên laptop/điện thoại | Phải bật laptop khi demo |
| **B. Chạy ngay trên Pi** ✔ | Gọn, chỉ 1 thiết bị | Không thể hiện được phần "gửi lên server" |
| C. Server cloud (Render, VPS) | Xem từ mọi nơi | Phụ thuộc Internet, mất thời gian triển khai |

Code server giống nhau cho cả 3; chỉ đổi địa chỉ trong `config`.

## 3. Phần cứng và chân GPIO

| Thiết bị | Chân | Ghi chú |
|---|---|---|
| HC-SR04 TRIG | GPIO23 | |
| HC-SR04 ECHO | GPIO24 | **Qua cầu chia áp 1 kΩ / 2 kΩ** (ECHO ra 5 V, GPIO Pi chịu 3,3 V) |
| Servo máng (MG996R) | GPIO12 | PWM phần cứng |
| Servo khay (MG996R) | GPIO13 | PWM phần cứng |
| LED chiếu sáng buồng chụp | GPIO27 | Qua MOSFET/relay |
| LED xanh (OK) | GPIO5 | |
| LED đỏ (từ chối / lỗi) | GPIO6 | |
| Buzzer | GPIO26 | |
| Camera | USB | Webcam USB (OpenCV) |

Servo cấp nguồn 5–6 V riêng, chung GND với Pi.

## 4. Luồng xử lý chi tiết (máy trạng thái)

| Trạng thái | Việc làm | Chuyển sang |
|---|---|---|
| **IDLE** | Đo khoảng cách mỗi 100 ms; LED chiếu tắt; servo ở vị trí nghỉ | Khoảng cách < `trigger_cm` liên tục ≥ `stable_ms` → SETTLE |
| **SETTLE** | Chờ `settle_ms` cho vật nằm yên | → CAPTURE |
| **CAPTURE** | Bật LED chiếu, chờ 200 ms (camera chỉnh sáng), chụp `n_frames` khung | → CLASSIFY |
| **CLASSIFY** | Tiền xử lý từng khung, chạy TFLite, **trung bình softmax** | → DECIDE |
| **DECIDE** | Áp logic `bins.json`: pin ≥ 0.4 → từ chối; cộng xác suất theo thùng; < 0.6 → thùng Khác | Thùng → ROUTE; Pin → REJECT |
| **ROUTE** | Servo máng xoay tới `angle` của thùng, chờ `route_ms` | → DROP |
| **DROP** | Servo khay lật `tilt_angle`, giữ `drop_ms`, trả về; đo lại khoảng cách | Hết vật → LOG; còn vật → lắc lại 1 lần; vẫn còn → ERROR |
| **REJECT** | Không lật khay; LED đỏ + buzzer theo mẫu cảnh báo | Vật được lấy ra (khoảng cách > `clear_cm`) → LOG |
| **LOG** | Ghi log cục bộ + đưa sự kiện vào hàng đợi upload; servo máng về vị trí nghỉ; LED xanh nháy 1 lần | → COOLDOWN |
| **COOLDOWN** | Chờ `cooldown_ms` để không xử lý lại chính vật vừa thả | → IDLE |
| **ERROR** | LED đỏ nhấp nháy; ghi log lỗi | Khay trống ≥ 3 s → IDLE |

Mỗi trạng thái có thời gian tối đa (`timeout_s`); quá hạn → ERROR.

**Chống báo giả của HC-SR04:** lấy trung vị 5 lần đo; bỏ giá trị ngoài 2–200 cm.

## 5. Mô hình và quyết định

- File model: `models/model_int8.tflite` (hoặc fp16, chọn trong config), `models/labels.json`.
- Tiền xử lý: cắt vuông giữa → resize 224×224 → RGB float32 thang 0..255.
- Thư viện: `ai-edge-litert`, 4 luồng.
- Logic theo `config/bins.json`:

```json
{
  "bins": [
    {"id": 1, "name": "Rác tái chế",   "angle": 0,   "classes": ["plastic", "metal", "paper", "cardboard"]},
    {"id": 2, "name": "Rác thực phẩm", "angle": 90,  "classes": ["biological"]},
    {"id": 3, "name": "Rác thải khác", "angle": 180, "classes": ["trash", "clothes", "shoes", "glass"]}
  ],
  "reject": [
    {"class": "battery", "threshold": 0.4, "message": "Pin là rác nguy hại, vui lòng bỏ vào điểm thu gom pin"}
  ],
  "confident_threshold": 0.6,
  "uncertain_bin": 3
}
```

## 6. Dữ liệu một sự kiện (Pi gửi lên server)

`POST /api/events` dạng `multipart/form-data`: trường `image` (JPEG khung giữa, 640 px) + trường `meta` (JSON):

```json
{
  "event_id": "8f2c1e0a-...",            // UUID do Pi tạo, server dùng để chống trùng khi gửi lại
  "device_id": "smartbin-01",
  "timestamp": "2026-11-04T14:32:05+07:00",
  "result": "BIN",                        // BIN | REJECT | ERROR
  "bin_id": 1,
  "bin_name": "Rác tái chế",
  "top_class": "plastic",
  "top_prob": 0.91,
  "top3": [["plastic", 0.91], ["glass", 0.05], ["trash", 0.02]],
  "bin_probs": {"1": 0.93, "2": 0.01, "3": 0.06},
  "uncertain": false,
  "distance_cm": 7.4,
  "inference_ms": 38,
  "total_ms": 2150,
  "model": "model_int8.tflite"
}
```

Ngoài ra Pi gửi **heartbeat** mỗi 30 s (`POST /api/heartbeat`: device_id, uptime, nhiệt độ CPU, số sự kiện đang chờ gửi) để trang web biết thiết bị đang online.

## 7. Web server

**Công nghệ:** Python Flask + SQLite, ảnh lưu thư mục `uploads/YYYY-MM-DD/`. Không cần đăng nhập ở bản demo; Pi gửi kèm header `X-API-Key` đơn giản.

**API:**

| Phương thức | Đường dẫn | Mô tả |
|---|---|---|
| POST | `/api/events` | Nhận sự kiện + ảnh (trùng `event_id` → bỏ qua, trả 200) |
| POST | `/api/heartbeat` | Nhận trạng thái thiết bị |
| GET | `/api/events?limit=&bin=&class=&date=` | Danh sách sự kiện (JSON) |
| GET | `/api/stats` | Thống kê theo thùng, theo lớp, theo ngày |
| GET | `/uploads/<path>` | Ảnh |

**Trang web:**

| Trang | Nội dung |
|---|---|
| `/` Tổng quan | Trạng thái thiết bị (online/offline, lần cuối thấy), số rác hôm nay theo thùng, 10 sự kiện mới nhất có ảnh thu nhỏ, tự làm mới 5 s |
| `/events` Lịch sử | Bảng có lọc theo thùng/lớp/ngày; bấm vào xem ảnh lớn + top-3 |
| `/stats` Thống kê | Biểu đồ số lượng theo lớp, theo thùng, theo giờ; tỉ lệ từ chối; thời gian suy luận trung bình |

**Tính năng thêm (chưa làm, để sau):**
- Nút **"Gán nhãn đúng"** trên mỗi sự kiện: người xem sửa nhãn nếu máy đoán sai → xuất ra bộ ảnh có nhãn để fine-tune.
- Ước lượng **độ đầy thùng** theo số lần thả (đặt sức chứa mỗi thùng trong config), cảnh báo khi > 80%.

## 8. Hoạt động khi mất mạng

- Mỗi sự kiện luôn được ghi **trước** vào `logs/events.csv` + `captures/<lớp>/<event_id>.jpg` trên Pi.
- Uploader chạy luồng riêng, lấy sự kiện từ hàng đợi (lưu trên đĩa, `queue/`), gửi; lỗi → thử lại sau 5 s, 10 s, 30 s… (tối đa 5 phút).
- Mạng có lại → tự gửi bù; server chống trùng bằng `event_id`.

## 9. Cấu hình

```
config/
├── bins.json        # thùng, góc, lớp, ngưỡng (mục 5)
├── hardware.json    # chân GPIO, góc nghỉ/lật khay, trigger_cm, clear_cm, các thời gian chờ
└── app.json         # server_url, api_key, device_id, model, camera, n_frames
```

Giá trị khởi đầu đề xuất: `trigger_cm` = khoảng cách cảm biến→mặt khay − 2 cm; `stable_ms` 500; `settle_ms` 500; `n_frames` 5; `route_ms` 600; `drop_ms` 800; `cooldown_ms` 1500. Hiệu chỉnh bằng `tools/calibrate.py`.

## 10. Cấu trúc code

```
smartbin/                        # chạy trên Pi
├── config/  models/  logs/  captures/  queue/
├── smartbin/
│   ├── sensors.py        # HC-SR04 (trung vị, lọc nhiễu)
│   ├── camera.py         # picamera2 / OpenCV
│   ├── classifier.py     # TFLite + tiền xử lý
│   ├── decider.py        # bins.json → kết quả
│   ├── actuators.py      # servo, LED, buzzer
│   ├── uploader.py       # hàng đợi + gửi HTTP + heartbeat
│   ├── controller.py     # máy trạng thái
│   └── mock.py           # giả lập cảm biến/servo/camera
├── tools/
│   ├── calibrate.py      # chỉnh góc servo, đo khoảng cách khay
│   └── test_image.py     # phân loại 1 ảnh
├── main.py               # --mock để chạy trên laptop
└── smartbin.service      # systemd tự khởi động

server/                          # chạy trên laptop / server
├── app.py                # Flask: API + trang web
├── db.py                 # SQLite
├── templates/  static/
└── uploads/  smartbin.db
```

## 11. Chế độ giả lập (`--mock`)

- Cảm biến: nhấn Enter = "có vật"; camera: lấy ảnh lần lượt từ một thư mục; servo/LED: in ra màn hình.
- Chạy được cả hệ thống (Pi app + server) trên laptop trước khi lắp mạch, để test luồng và web.

## 12. Tiêu chí hoàn thành

| Tiêu chí | Mục tiêu |
|---|---|
| Đặt vật → thả xong | < 3 s |
| Sự kiện hiện trên web | < 2 s sau khi xử lý (khi có mạng) |
| Mất mạng 5 phút rồi có lại | Không mất sự kiện nào |
| Báo giả từ cảm biến | Không kích hoạt khi khay trống trong 30 phút |
| Chạy liên tục | 30 phút không treo; tự chạy lại sau khi mất điện |

## 13. Ngoài phạm vi bản này

Cảm biến đo độ đầy thật (siêu âm trong từng thùng), loa đọc tiếng Việt, đăng nhập/phân quyền web, MQTT, nhiều thiết bị.

## 14. Còn để ngỏ

- Nút "Gán nhãn đúng" và ước lượng độ đầy thùng (mục 7).
