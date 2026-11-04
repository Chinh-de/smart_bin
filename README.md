# smart_bin

Thùng rác phân loại rác thông minh áp dụng Raspberry Pi 4: camera nhận diện rác bằng mô hình MobileNetV3 (10 lớp, TFLite), servo đưa rác vào 3 thùng theo Luật BVMT 2020 (tái chế / thực phẩm / rác thải khác), pin bị từ chối kèm cảnh báo; mỗi lần xử lý được ghi log kèm ảnh lên web server.

| Thư mục | Nội dung |
|---|---|
| [docs/](docs/) | [Kế hoạch đề tài](docs/detai.md), [đặc tả ứng dụng](docs/dac_ta_ung_dung.md) |
| [Model_training/](Model_training/) | Notebook Kaggle: EDA, tiền xử lý, huấn luyện, đánh giá, xuất TFLite |
| [smartbin_pi/](smartbin_pi/) | Ứng dụng trên Raspberry Pi + web server — hướng dẫn cài đặt trong [README](smartbin_pi/README.md) |

Model (`model_int8.tflite`, `labels.json`) không nằm trong repo: tải từ output notebook Kaggle rồi chép vào `smartbin_pi/models/`.
