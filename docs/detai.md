# Thùng rác phân loại rác thông minh áp dụng Raspberry Pi

## 1. Mục tiêu

Xây dựng mô hình thùng rác chi phí thấp: người dùng đặt rác lên khay, camera chụp ảnh, Raspberry Pi 4 nhận diện loại rác bằng mô hình học sâu chạy tại chỗ, sau đó cơ cấu servo đưa rác vào đúng thùng. Riêng pin (rác nguy hại) bị từ chối kèm cảnh báo.

Yêu cầu chính:
- Phân loại tự động, chạy hoàn toàn trên Raspberry Pi 4 (không cần Internet).
- Chi phí phần cứng thấp (khoảng 2–2,5 triệu đồng, phần lớn là Raspberry Pi).
- Cách phân loại bám theo Luật Bảo vệ môi trường 2020 (Điều 75).

## 2. Cách phân loại

Mô hình phân loại **10 lớp**. Sau khi có nhãn, ứng dụng trên Pi gom lớp vào thùng theo file cấu hình `bins.json`, nên đổi cách gom không cần huấn luyện lại.

| Kết quả | Các lớp | Hành động |
|---|---|---|
| **Thùng 1 – Rác tái chế** (servo 0°) | plastic, metal, paper, cardboard | Thả rác |
| **Thùng 2 – Rác thực phẩm** (servo 90°) | biological | Thả rác |
| **Thùng 3 – Rác thải khác** (servo 180°) | trash, clothes, shoes, glass, **và mọi trường hợp không chắc chắn** | Thả rác |
| **Từ chối – Pin** | battery | Không lật khay; đèn đỏ + còi: "Pin là rác nguy hại, vui lòng bỏ vào điểm thu gom pin" |

**Logic quyết định:**
1. `p(battery) ≥ 0.4` → từ chối (pin). Kiểm tra đầu tiên vì để lọt pin là nguy hiểm nhất.
2. Cộng xác suất các lớp theo từng thùng, chọn thùng cao nhất.
3. Nếu thùng cao nhất < 0.6 (không chắc chắn) → **Thùng 3 – Rác thải khác**.

**Lý do lựa chọn:**
- 3 thùng đúng 3 nhóm rác sinh hoạt của Luật BVMT 2020: tái chế, thực phẩm, rác thải khác.
- Pin là chất thải nguy hại, phải thu gom riêng.
- Thủy tinh theo luật thuộc nhóm tái chế, nhưng mảnh vỡ làm bẩn giấy/nhựa và gây nguy hiểm cho người thu gom, nên xếp vào **rác thải khác** thay vì thùng tái chế. Thùng 3 lót đệm (mút/vải dày) ở đáy và máng hạ sát miệng thùng để giảm nguy cơ vỡ khi rơi.
- Vật không chắc chắn đưa vào rác thải khác: đây là nhóm "còn lại" theo luật, và tránh làm bẩn thùng tái chế/thực phẩm khi đoán sai.
- Quần áo, giày dép còn dùng được nên quyên góp; hệ thống chỉ nhận vật nhỏ, hư hỏng (tất, vải vụn) và xếp vào rác thải khác.

**Ghi chú mở:**
- Có thể chuyển glass sang từ chối (hướng dẫn bỏ ngăn thủy tinh riêng / bán ve chai) nếu thùng 3 không đủ an toàn khi thả.
- Có thể chuyển clothes, shoes sang từ chối nếu muốn nhấn mạnh tái sử dụng.
- Vật lạ không phải rác (điện thoại, chìa khóa) cũng sẽ rơi vào thùng 3 khi không chắc chắn — chấp nhận cho demo; có thể đổi `uncertain_action` thành từ chối sau.

## 3. Thiết kế phần cứng

### 3.1 Cơ cấu: máng xoay + khay lật (3 thùng cố định)

```
      [Camera + LED]          ← buồng chụp kín, nền trắng
      ┌──────────┐
      │ Khay lật │ ← Servo 2: lật khay thả rác
      └────┬─────┘
           ▼
        ╲ máng ╱   ← Servo 1: xoay máng 0° / 90° / 180°
         ╲    ╱
   ┌──────┬──────┬──────┐
   │  T1  │  T2  │  T3  │  ← 3 thùng đứng yên, xếp hình quạt 180°
   └──────┴──────┴──────┘
```

- Thùng đứng yên, chỉ máng và khay (bằng carton, rất nhẹ) chuyển động, nên không cần vòng bi hay động cơ bước.
- Servo 180° quay đến đúng góc của từng thùng, không cần cảm biến vị trí.
- Vỏ, buồng chụp, máng, thùng làm bằng carton; mặt khay dán thêm mica/nhựa mỏng chống ẩm.

### 3.2 Linh kiện

| Linh kiện | Vai trò | Giá ước tính (VNĐ) |
|---|---|---|
| Raspberry Pi 4 | Xử lý, chạy mô hình | 1.500.000–1.800.000 |
| Pi Camera v2 hoặc webcam USB | Chụp ảnh rác | 150.000–300.000 |
| 2 × servo MG996R | Xoay máng, lật khay | 120.000 |
| Cảm biến IR / HC-SR04 (hoặc nút bấm) | Phát hiện có rác trên khay | 20.000 |
| Dải LED trắng + MOSFET/relay | Chiếu sáng ổn định khi chụp | 50.000 |
| LED đỏ, LED xanh, buzzer | Báo hiệu | 20.000 |
| Nguồn 5–6 V riêng cho servo + nguồn Pi | Cấp điện | 150.000 |
| Carton, keo, vít, mica | Khung vỏ | 50.000–100.000 |

Servo dùng nguồn riêng, nối chung GND với Pi; không lấy nguồn servo từ chân 5 V của Pi.

## 4. Mô hình nhận diện

### 4.1 Dữ liệu

- **Dataset:** Garbage Classification V2 (Kaggle, sumn2u), 10 lớp: battery, biological, cardboard, clothes, glass, metal, paper, plastic, shoes, trash.
- Lý do chọn: đây là bộ phổ biến duy nhất có sẵn cả lớp hữu cơ (biological) lẫn pin (battery); thủy tinh đã gộp sẵn thành 1 lớp.
- Hạn chế: ảnh lấy từ web, nền lộn xộn, khác ảnh chụp trong thùng → sẽ fine-tune thêm bằng ảnh tự chụp.

### 4.2 Huấn luyện (notebook `Model_training/train_garbage.ipynb`, chạy trên Kaggle GPU)

1. **EDA:** phân bố lớp, ảnh lỗi, kích thước, ảnh trùng (MD5), ảnh mẫu, độ sáng.
2. **Tiền xử lý:** sửa hướng EXIF, chuyển RGB (PNG trong suốt ghép nền trắng), loại ảnh cạnh < 64 px, đệm vuông nền trắng, resize 256×256.
3. **Chia dữ liệu:** tối đa 2.500 ảnh/lớp, chia stratified 70/15/15.
4. **Mô hình:** MobileNetV3-Large pretrained ImageNet, augmentation (lật, xoay, zoom, độ sáng, tương phản), class_weight cân bằng.
5. **Huấn luyện 2 giai đoạn:** (1) đóng băng backbone, train đầu phân loại, lr 1e-3; (2) mở khóa ~40% lớp cuối (giữ BatchNorm đóng băng), lr 1e-4. Mỗi giai đoạn có callback riêng; EarlyStopping, checkpoint và giảm learning rate đều theo val_loss.
6. **Đánh giá:** classification report, ma trận nhầm lẫn, biểu đồ train/val, ví dụ dự đoán ngẫu nhiên theo lớp, phân tích ảnh sai tự tin nhất.
7. **Xuất mô hình:** `model.keras`, `model_fp16.tflite`, `model_int8.tflite` (batch cố định 1; nếu XNNPACK không chạy được bản full-int8 thì tự chuyển sang dynamic-range), `labels.json`.

### 4.3 Kết quả hiện tại

| Mô hình | Accuracy test (10 lớp) | Ghi chú |
|---|---|---|
| Keras gốc (lần 2, đã fine-tune) | **0.9369** (macro F1 0.929) | Epoch 12 (chọn theo val_loss). Lớp yếu: trash (P 0.76), plastic (R 0.85), metal (P 0.88). Battery R 0.965 |
| Keras gốc (lần 1) | 0.9048 | Lỗi callback dùng chung: thực chất là model chưa fine-tune |
| TFLite FP16 | *chờ số lần 2* | Lần 1: ~4 ms/ảnh trên CPU Kaggle |
| TFLite INT8 | *chờ chạy lại* | Lần đầu lỗi XNNPACK với batch động; đã sửa notebook |

## 5. Ứng dụng trên Raspberry Pi

> Đặc tả chi tiết (cảm biến, web server, API, dữ liệu log): [dac_ta_ung_dung.md](dac_ta_ung_dung.md)

### 5.1 Luồng xử lý (máy trạng thái)

```
IDLE ──(phát hiện rác ≥ 0,5 s)──► SETTLE (chờ vật nằm yên)
  ▲                                   │
  │                                   ▼
  │                         CAPTURE (bật LED, chụp 5 khung)
  │                                   │
  │                                   ▼
  │                         CLASSIFY (trung bình softmax 5 khung)
  │                                   │
  │                                   ▼
  │                         DECIDE (theo bins.json)
  │                          │                    │
  │                    thùng 1/2/3             pin
  │                          ▼                    ▼
  │                ROUTE (xoay máng)      REJECT (đèn đỏ + còi,
  │                          ▼             chờ lấy pin ra)
  │                DROP (lật khay, trả khay)      │
  │                          ▼                    │
  └──────────────────── LOG ◄─────────────────────┘
```

- Tiền xử lý ảnh camera giống lúc train: cắt vuông giữa → 224×224 → RGB float32 thang 0..255.
- Chống kẹt: sau khi lật mà vẫn phát hiện vật → lắc khay lại; vẫn còn → trạng thái lỗi, đèn đỏ nhấp nháy.
- Ghi log mỗi lần xử lý (thời gian, top-3, thùng, thời gian suy luận) vào `logs/events.csv`; lưu ảnh vào `captures/<lớp>/` để làm dữ liệu fine-tune.

### 5.2 Cấu trúc code dự kiến

```
smartbin/
├── config/
│   ├── bins.json        # ánh xạ lớp → thùng, góc servo, ngưỡng
│   └── hardware.json    # chân GPIO, góc khay, thời gian chờ
├── models/              # model_*.tflite, labels.json
├── smartbin/
│   ├── classifier.py    # nạp TFLite (ai-edge-litert), tiền xử lý, predict()
│   ├── decider.py       # softmax → thùng / từ chối
│   ├── camera.py        # picamera2 / OpenCV
│   ├── hardware.py      # servo, cảm biến, LED, còi + chế độ giả lập
│   └── controller.py    # máy trạng thái
├── tools/
│   ├── calibrate.py     # chỉnh góc servo từng thùng
│   └── test_image.py    # phân loại 1 ảnh, không cần phần cứng
└── main.py              # chạy chính, tự khởi động bằng systemd
```

Môi trường: Raspberry Pi OS 64-bit (Bookworm), Python 3.11, `ai-edge-litert`, `numpy`, `opencv-python-headless`, `picamera2`, `gpiozero`.

## 6. Kế hoạch thực hiện

| Giai đoạn | Nội dung | Trạng thái |
|---|---|---|
| 1 | Chọn cách phân loại, dataset, thiết kế cơ cấu | Xong |
| 2 | Notebook EDA, tiền xử lý, huấn luyện, đánh giá, xuất TFLite | Xong (đang chạy lại bước xuất INT8) |
| 3 | Demo nhận diện trên Pi: camera → mô hình → hiển thị kết quả | Tiếp theo |
| 4 | Lắp phần cứng carton, servo, cảm biến; hiệu chỉnh góc servo | |
| 5 | Ứng dụng đầy đủ: máy trạng thái, bins.json, log | |
| 6 | Chụp ảnh trong buồng, fine-tune mô hình, đánh giá lại | |
| 7 | Thử nghiệm thực tế, viết báo cáo | |
| Mở rộng | IoT: gửi thống kê qua MQTT/web, cảm biến đo độ đầy thùng | Tùy thời gian |

## 7. Tiêu chí đánh giá

- Mô hình: accuracy test 10 lớp ≥ 90%; recall lớp battery cao (ưu tiên an toàn).
- Hệ thống: thời gian từ lúc đặt rác đến lúc thả xong < 3 giây.
- Thử 20 vật thật: ≥ 85% vào đúng thùng, không có pin nào lọt vào thùng; thủy tinh rơi vào thùng 3 không vỡ.
- Chạy liên tục 30 phút không treo, tự khởi động lại khi mất điện.

## 8. Việc còn cần chốt

1. Camera: Pi Camera hay webcam USB.
2. Kích hoạt: cảm biến IR, HC-SR04 hay nút bấm.
3. Có làm chế độ giả lập (`--mock`) để thử code trên laptop trước khi lắp mạch không.
4. Làm demo nhận diện trước hay làm thẳng bản đầy đủ có servo.
