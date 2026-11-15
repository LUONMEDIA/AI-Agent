# Lượn Photo Batch

Chỉnh ảnh hàng loạt chạy hoàn toàn trong trình duyệt: ảnh không được tải lên đâu cả. Không cần cài đặt, không phụ thuộc thư viện ngoài.

## Tính năng
- Nạp nhiều ảnh JPG/PNG/WebP (kéo thả).
- Chỉnh: phơi sáng, tương phản, bão hòa, nhiệt độ màu, làm mịn da (giữ cạnh); 5 phong cách có sẵn.
- Áp dụng cho ảnh đã chọn / tất cả (Ctrl/⌘/Shift + bấm để chọn nhiều ảnh). Giữ chuột trên ảnh lớn để xem bản gốc.
- **Lọc ảnh**: gắn nhãn Tốt / Mờ / Cháy sáng / Quá tối, theo ngưỡng tương đối so với cả buổi chụp.
- **Cân sáng** các ảnh đã chọn theo ảnh đang xem.
- Xuất ZIP JPG (chất lượng, cạnh dài tối đa tùy chọn). File gốc không bị sửa.

## Chạy
```
npm start          # http://localhost:8080
npm test           # kiểm thử lõi xử lý (Node ≥ 18, cần lệnh unzip)
```
Mở bằng máy chủ tĩnh (ES module không chạy từ `file://`). Có thể đặt lên NAS/Cloudflare Pages như một site tĩnh.

## Cấu trúc
- `src/core.js` — xử lý ảnh, lọc, cân sáng, ZIP (thuần, có test)
- `src/app.js`, `src/style.css`, `index.html` — giao diện
- `docs/ANALYSIS.md` — phân tích Fotoreto, góp ý, giới hạn và lộ trình

## Giới hạn hiện tại
Xem mục 4 trong `docs/ANALYSIS.md` (chưa có RAW/HEIC, mặt nạ da là heuristic, chưa tối ưu cho buổi chụp rất lớn).

Dự án độc lập, không chứa mã hay tài sản của Fotoreto.
