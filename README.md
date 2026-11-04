# smart_bin

Thùng rác phân loại rác thông minh áp dụng Raspberry Pi 4: camera nhận diện rác bằng mô hình MobileNetV3 (10 lớp, TFLite), servo đưa rác vào 3 thùng theo Luật BVMT 2020 (tái chế / thực phẩm / rác thải khác), pin bị từ chối kèm cảnh báo; mỗi lần xử lý được ghi log kèm ảnh lên web server.

| Thư mục | Nội dung |
|---|---|
| [docs/](docs/) | [Kế hoạch đề tài](docs/detai.md), [đặc tả ứng dụng](docs/dac_ta_ung_dung.md) |
| [Model_training/](Model_training/) | Notebook Kaggle: EDA, tiền xử lý, huấn luyện, đánh giá, xuất TFLite |
| [smartbin_pi/](smartbin_pi/) | Ứng dụng trên Raspberry Pi + web server — hướng dẫn cài đặt trong [README](smartbin_pi/README.md) |
| [smart_pin_demo_model/](smart_pin_demo_model/) | Demo trên laptop: webcam/ảnh → phân loại → đọc to hành động bằng tiếng Việt |

Model đã huấn luyện (`model_int8.tflite`, `labels.json`) nằm trong [Model_training/models/](Model_training/models/); demo laptop dùng trực tiếp, còn trên Pi thì chép sang `smartbin_pi/models/`.
