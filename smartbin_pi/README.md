# Smartbin trên Raspberry Pi 4

Thùng rác 4 ngăn (thiết kế: `../smartbin_4thung.html`). Đặt rác lên khay qua cửa trước → HC-SR04 phát hiện, chờ vật nằm yên → bật đèn, webcam chụp 5 khung → model TFLite phân loại 10 lớp → servo A xoay khay sang 45°/135°, servo B lật khay đổ rác vào 1 trong 4 thùng → khay về nghỉ, siêu âm kiểm tra khay đã trống (còn vật thì lật lại 1 lần, vẫn còn thì báo lỗi kẹt) → ghi log và gửi ảnh + thông tin lên web server (chạy ngay trên Pi).

| Thùng | Loại | Lớp | Servo A (ngang) | Servo B (lật) |
|---|---|---|---|---|
| 1 | Giấy | paper | 45° | `tilt_rest − tilt_delta` |
| 2 | Nhựa | plastic | 135° | `tilt_rest − tilt_delta` |
| 3 | Rác thải | trash | 45° | `tilt_rest + tilt_delta` |
| 4 | Khác | các lớp còn lại, **không chắc chắn** | 135° | `tilt_rest + tilt_delta` |

Đặc tả: [../docs/dac_ta_ung_dung.md](../docs/dac_ta_ung_dung.md)

```
smartbin_pi/
├── main.py                 # chương trình điều khiển thùng rác
├── config/                 # app.json, hardware.json (chân GPIO, góc, thời gian), bins.json (lớp → thùng)
├── models/                 # chép model_int8.tflite + labels.json từ Kaggle vào đây
├── smartbin/               # classifier, decider, camera, servo (2 servo PWM phần cứng), hardware (siêu âm, đèn, LED, còi, cửa), controller, uploader, mock
├── tools/                  # test_image.py, calibrate.py, servo_test.py, loadcell_test.py
├── server/                 # web server Flask + SQLite
└── systemd/                # tự khởi động khi bật nguồn
```

## 1. Nối dây

| Thiết bị | Chân Pi (BCM) |
|---|---|
| HC-SR04 VCC / GND | 5V / GND |
| HC-SR04 TRIG | GPIO23 |
| HC-SR04 ECHO | GPIO24 **qua cầu chia áp**: ECHO → 1 kΩ → GPIO24 → 2 kΩ → GND |
| Servo A, xoay ngang (tín hiệu) | GPIO12 (PWM0 phần cứng) |
| Servo B, lật khay (tín hiệu) | GPIO13 (PWM1 phần cứng) |
| Đèn LED trần buồng chụp (qua 2N2222) | GPIO27 |
| LED xanh / LED đỏ (qua điện trở 330 Ω) | GPIO5 / GPIO6 |
| Buzzer | GPIO26 |
| Công tắc cửa hông (tùy chọn) | GPIO16 ↔ GND, đặt `"door": 16` trong `hardware.json`. Nhiều cửa thì nối các reed **nối tiếp** (cửa nào mở cũng làm hở mạch) |
| HX711 VCC / GND | 3V3 (chân 1) / GND (chân 6) — **dùng 3.3 V, không dùng 5 V** |
| HX711 DT / SCK | GPIO20 (chân 38) / GPIO21 (chân 40) |
| Load cell → HX711 | đỏ → E+, đen → E−, trắng → A−, xanh lá → A+ (B+/B− bỏ trống) |
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

# buồng chụp: đo khoảng cách khay, bật đèn, chụp thử khung hình, đọc công tắc cửa
.venv/bin/python tools/calibrate.py

# cơ cấu 2 servo (ngang chọn bên 45°/135° + dọc nghiêng đổ) cho 4 thùng: nhập 1..4 để thử
.venv/bin/python tools/servo_test.py          # --pan-ms 800 --tilt-ms 600 --easing sine để thử chuyển động khác, --mock để thử không cần Pi

# load cell + HX711: đọc thô, tare, hiệu chuẩn bằng vật biết khối lượng (lưu config/loadcell.json)
.venv/bin/python tools/loadcell_test.py       # --mock để thử không cần Pi
```

`loadcell_test.py`: gõ `t` khi cân trống (tare), đặt vật đã biết khối lượng rồi `c <gram>` (vd `c 500`), sau đó `w`/`r` để đọc gram, `n` để xem độ nhiễu. Số đọc âm khi đặt vật thì đảo dây A+ ↔ A−. Màu dây load cell mỗi hãng một kiểu; nếu `t`/`c` báo chênh lệch ≈ 0 thì kiểm tra lại E+/E−/A+/A−.

`servo_test.py` dùng chung cấu hình với `main.py`: mục `servo` trong `config/hardware.json` (`pan_rest`, `tilt_rest`, `tilt_delta`, `tilt_sign` đảo chiều dọc, `easing` (`linear`/`quad`/`cubic`/`sine`), `pan_ms`/`tilt_ms` thời gian mỗi lần xoay ngang/lật dọc, `update_hz`, `settle_s`, `hold_s`) và `pan`/`tilt_dir` của từng thùng trong `config/bins.json`. Servo dùng PWM phần cứng (xung ổn định, không rung) nên cần bật overlay một lần: thêm dòng sau vào `/boot/firmware/config.txt` rồi `sudo reboot` (GPIO12 = kênh 0, GPIO13 = kênh 1):

```
dtoverlay=pwm-2chan,pin=12,func=4,pin2=13,func2=4
```

Chuyển động servo theo cơ chế ServoEaser (thử nghiệm ở `tools/servo_lab/easer.py`): mỗi lần quay tới góc trong đúng
`pan_ms`/`tilt_ms` (không theo tốc độ), góc theo hàm easing kiểu Robert Penner, mặc định `cubic` (easeInOutCubic).

Khay về nghỉ lệch một tí về một bên (thả thùng bên này về đúng, bên kia về thiếu) là do vùng chết + độ rơ bánh răng
của servo: chỗ dừng phụ thuộc chiều đi tới. Đặt `pan_approach` / `tilt_approach` để servo luôn về nghỉ từ phía về đúng:
`-1` = từ phía góc lớn (vd thả thùng 135° rồi về thì đúng), `1` = từ phía góc nhỏ (thùng 45° về đúng), `0` = tắt.
Đang ở phía kia thì servo quay vượt góc nghỉ `approach_deg` độ (mặc định 8) rồi mới quay về. Chỉnh được ở trang **Cấu hình**.

Kiểm tra: `ls /sys/class/pwm/` phải thấy `pwmchip0`. Nếu báo không ghi được `/sys/class/pwm`, chạy `sudo usermod -aG gpio $USER` rồi đăng nhập lại (hoặc chạy bằng `sudo`). Không chạy `servo_test.py` cùng lúc với `main.py`.

Với `calibrate.py`:
1. `d` khi khay trống, rồi `d` khi đặt một vật nhỏ. Đặt `trigger_cm` nằm giữa hai giá trị, `clear_cm` lớn hơn `trigger_cm` 2–3 cm.
2. `s` chụp thử `calib.jpg` (có bật đèn) để chỉnh camera: khay nằm trong ô vuông đỏ (vùng model nhìn thấy), nền trắng, ảnh nét.

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
- **Thống kê**: theo thùng, lớp, giờ, ngày; số lần không chắc chắn, lỗi kẹt rác.

- **Log**: log trực tiếp của chương trình trên Pi (lọc theo mức, theo chữ, tạm dừng).
- **Cấu hình**: chỉnh thông số servo (easing, thời gian, góc nghỉ, biên độ lật, xung), góc từng thùng, ngưỡng siêu âm,
  ngưỡng phân loại; bấm **Lưu & áp dụng** là Pi áp dụng ngay (không cần khởi động lại) và lưu vào `config/local.json`.
  File này không commit (có trong `.gitignore`), lúc khởi động được đè lên `hardware.json` / `bins.json`, nên `git pull`
  trên Pi không bị xung đột; xoá nó để về cấu hình gốc, muốn đưa giá trị đã chỉnh vào git thì chép sang file gốc. Giá trị sai (vd góc lật ra ngoài 0..180) bị từ chối, không đổi gì. Nút **Thả thùng N** / **Về nghỉ** để
  thử servo, chỉ chạy khi Pi đang chờ vật và khay trống.

Pi đồng bộ log + cấu hình với server mỗi giây (`smartbin/remote.py`, `POST /api/sync/<device_id>`); tắt bằng `--no-remote`
(hoặc `--no-upload`). Log và cấu hình chờ áp dụng chỉ giữ trong RAM của server. Trang Cấu hình chưa có đăng nhập:
ai vào được server đều chỉnh được thông số và chạy thử servo.

API cho phần mở rộng: `GET /api/events`, `GET /api/stats`, `GET /api/logs/<device_id>?after=<seq>`, `GET/POST /api/config/<device_id>`.

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
Trên Pi thật, thêm `--keyboard` để nhấn **Enter** chạy nhận diện ngay (ngoài siêu âm), tiện test khi chưa gắn hoặc chưa chỉnh cảm biến:
`python main.py --keyboard --no-upload`. Enter bấm lúc đang xử lý sẽ bị bỏ, không dồn thành lượt sau.

Chưa nối siêu âm/servo mà chỉ muốn thử nhận diện bằng webcam thật: `python main.py --bench --no-upload`
(webcam + model thật; siêu âm, servo, đèn, còi được giả lập và in ra màn hình; nhấn **Enter** để chụp; không cần gpiozero).


**Cân:** `main.py` đọc load cell HX711 (chân `hx711_dt`/`hx711_sck`) ở luồng nền, đổi ra gram theo `config/loadcell.json`
(tạo bằng `tools/loadcell_test.py`: `t` rồi `c <gram>`), gửi lên server mỗi giây; trang chủ hiện số gram cạnh video.
Cân lỗi/chưa nối thì thùng rác vẫn chạy bình thường. Tắt bằng `--no-scale`. Không chạy `loadcell_test.py` cùng lúc với `main.py`.

**Video trực tiếp:** xem ngay ở trang chủ `http://<ip server>:5000/`. Pi đẩy từng khung JPEG lên server (`smartbin/streamer.py`),
server phát lại dạng MJPEG. Pi chỉ mã hoá + gửi khi có người đang xem. Chỉnh trong `config/app.json`, mục `stream`
(`enabled`, `fps`, `width`, `quality`). Không chạy khi dùng `--no-upload`. Trang chủ chưa có đăng nhập, ai vào được server đều xem được.

Log siêu âm: lúc chờ vật in khoảng cách mỗi `distance_log_s` giây (mặc định 1, đặt 0 để tắt) kèm từng lần ping thô,
vd `Siêu âm: 31.9 cm (xa, ngưỡng < 12) | 9 lần/1s [32.1 31.8 mất 31.9 ...], hỏng: mất 1`.
Lần ping hỏng: `mất` = không có tiếng vang (dây TRIG/ECHO, nguồn 5V, cầu chia áp, hoặc vật mềm/nghiêng hút sóng),
`ngắn` = xung vang quá ngắn không bắt kịp (vật rất sát cảm biến), `bận` = chân ECHO kẹt mức cao.
Khoảng cách = trung vị các lần đo hợp lệ trong `distance_window_ms` gần nhất; không có lần nào thì coi khay trống và in cảnh báo.
`lost_as_object` (mặc định `false`): `true` thì trong cửa sổ đo, số lần `mất`/`ngắn` nhiều hơn số lần đo được -> coi là có vật
(chỉ dùng cho vật mềm/nghiêng hút sóng; chỉ áp dụng khi cảm biến đã từng đo được số hợp lệ).

Cách kích hoạt nhận diện (`trigger_mode` trong `config/hardware.json`):
- `"hand"` (mặc định): rác nhỏ, siêu âm không thấy rác mà chỉ thấy **tay** lúc đặt vào. Thấy tay (< `trigger_cm`) -> chờ rút tay ra
  (> `clear_cm` liên tục `stable_ms`) -> chờ `settle_ms` -> chụp. Rác đủ to để siêu âm thấy (sau `near_hold_ms` vẫn chưa > `clear_cm`)
  thì cũng nhận diện, không phải chờ rút tay. Đặt `near_hold_ms` = 0 để tắt nhánh này.
- `"object"`: cách cũ, rác nằm yên < `trigger_cm` liên tục `stable_ms` thì nhận diện (rác phải đủ to để siêu âm thấy).
Thêm `--show-distance` để in mỗi lần đọc (~`poll_ms`).

Khởi động: servo về nghỉ từng cái một, rồi chạy thử 1 lượt xoay + lật vào thùng 1 (Giấy) và về nghỉ,
để kết thúc bằng đúng đường về nghỉ như lúc chạy thật (chỉ nhảy thẳng về nghỉ thì khay không về đúng chỗ).
Lỡ trên khay còn vật lúc bật máy thì vật rơi vào thùng này.

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

Chỉ sửa `config/bins.json`, không cần train lại:
- `classes` của từng thùng: lớp nào vào thùng nào (mỗi lớp chỉ ở 1 thùng). Lớp không thuộc thùng nào tính vào `uncertain_bin`.
- `priority`: lớp nguy hiểm vượt `threshold` là vào thẳng `bin`, kiểm tra trước mọi thứ khác (mặc định pin ≥ 0.4 → thùng 4).
- `confident_threshold`: tổng xác suất của thùng thắng thấp hơn ngưỡng này thì đưa vào `uncertain_bin` (thùng 4).
- `pan` / `tilt_dir`: vị trí thùng (góc servo A, chiều lật servo B), thử bằng `tools/servo_test.py` hoặc trang web **Cấu hình**.
