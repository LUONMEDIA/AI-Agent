# Phân tích Fotoreto và hướng cải tiến

Nguồn: thông tin công khai (trang giới thiệu trên SourceForge, Slashdot, topai.tools, AIChief) và `Info.plist` của bản macOS 1.0.34. Không dùng mã, tài sản hay dịch ngược sản phẩm gốc. Mọi con số giá/tính năng cần đối chiếu lại tại fotoreto.com trước khi dùng cho quyết định kinh doanh (trạng thái: NEEDS CHECK).

## 1. Fotoreto là gì
- Trình chỉnh ảnh **hàng loạt**, chạy trong trình duyệt, xử lý cục bộ, có thể dùng offline; làm bởi một thợ retouch chân dung chuyên nghiệp (Hoang Phuc Photo, Việt Nam, thành lập 2018).
- Khách hàng mục tiêu: nhiếp ảnh gia cưới/sự kiện/chân dung/studio, người chỉnh cả buổi chụp.
- Điểm bán được nêu: tự động lọc ảnh, retouch chân dung (xóa mụn, làm mịn da), 111 phong cách màu, xử lý cục bộ, không tính phí theo ảnh.
- Giá công bố trên các trang tổng hợp: gói năm khoảng 39,99 USD, mua đứt khoảng 79,99 USD; một trang khác ghi giá khởi điểm 12,99 USD/năm. Các nguồn lệch nhau → cần kiểm tra.
- Bản macOS 1.0.34: gói Tauri (WebKit) đa kiến trúc x86_64 + arm64, đọc JPEG/PNG/TIFF/HEIC/AVIF/WebP và nhiều định dạng RAW (Canon, Nikon, Sony, Fuji, Panasonic, Olympus, Pentax, Samsung).

## 2. Điểm mạnh đáng học
1. Định vị rõ: "cả buổi chụp", không phải "một tấm ảnh".
2. Riêng tư theo thiết kế: ảnh không bị tải lên; cực kỳ quan trọng với ảnh cưới, ảnh trẻ em.
3. Mô hình giá không tính theo ảnh.
4. Có cả bản web và bản desktop.

## 3. Góp ý / khoảng trống có thể khai thác
| # | Quan sát | Hệ quả với người dùng | Cách bản Lượn xử lý |
|---|---|---|---|
| 1 | Điểm bán là "tự động lọc" nhưng ngưỡng cứng dễ sai với ống kính/độ phân giải khác nhau | Gắn nhầm ảnh nét là mờ | Ngưỡng **tương đối theo trung vị cả buổi chụp**; ảnh cháy/tối được xét trước ảnh mờ |
| 2 | Đồng bộ chỉnh sửa cả loạt dễ làm lệch phơi sáng từng khung | Phải chỉnh lại từng ảnh | **Cân sáng theo ảnh tham chiếu**, giữ nguyên các chỉnh khác của từng ảnh |
| 3 | Làm mịn da dễ làm "nhựa" da | Mất chi tiết mắt, môi, tóc | Làm mịn **chỉ trên vùng da**, giảm lực ở nơi có cạnh mạnh |
| 4 | Xuất hàng loạt nhiều ảnh lớn | Treo trình duyệt, mất kết quả | Xuất **tuần tự**, có thanh tiến trình, ghi lỗi từng ảnh, tên file không trùng |
| 5 | Thao tác lọc có thể xóa nhầm | Mất ảnh quý | "Bỏ ảnh" chỉ gỡ khỏi danh sách xuất, **không đụng file gốc**, có hộp xác nhận |
| 6 | Thiếu giải thích vì sao ảnh bị loại | Khó tin tưởng | Mỗi ảnh có nhãn Tốt/Mờ/Cháy sáng/Quá tối |
| 7 | Giữ chuột để xem ảnh gốc | So sánh trước/sau nhanh | Đã có |

## 4. Những gì bản này CHƯA làm (nói thẳng)
- Chưa đọc RAW/HEIC/TIFF/AVIF (cần thư viện giải mã; xuất JPG trước).
- Mặt nạ da theo màu (YCbCr) là heuristic: có thể bắt nhầm vật thể màu da, bỏ sót da rất tối/rất sáng. Chưa có nhận diện khuôn mặt hay xóa mụn bằng AI.
- Chưa có hồ sơ màu (ICC), chưa lưu preset do người dùng tạo, chưa có bản desktop.
- Chưa thử trên tập ảnh thật nhiều người; ngưỡng lọc cần hiệu chỉnh với ảnh thực tế.
- Xuất ZIP không nén (JPEG vốn đã nén) và nằm trong bộ nhớ: buổi chụp rất lớn nên chia lô.

## 5. Lộ trình đề xuất
1. **v0.2**: lưu/nạp preset (localStorage), sao chép cài đặt từ ảnh này sang ảnh khác, phím tắt.
2. **v0.3**: Web Worker + OffscreenCanvas để không đơ giao diện; xuất theo lô.
3. **v0.4**: đọc HEIC/RAW bằng thư viện WASM đã kiểm tra giấy phép.
4. **v0.5**: nhận diện khuôn mặt cục bộ để giới hạn vùng làm mịn; đóng gói PWA offline.
5. **Sau đó**: thị trường (giá, kênh phân phối, so với Fotoreto/Photopea/Lightroom) cần dữ liệu thật; chưa có nên chưa đề xuất giá.
