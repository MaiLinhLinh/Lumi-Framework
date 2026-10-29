# Thống kê model: test request “kể chuyện về bác hồ”

Nguồn: log mới từ 10:01:38.007 đến 10:02:16.899. Chỉ thống kê các lời gọi model của Task Manager Agent và Assembly Agent trong request này.

- Request: `kể chuyện về bác hồ`.
- Plan: `aeb84b7c3d63`.
- Run: `run_a2f2f4721b904b099064a74976c212c1`.
- Model: `qwen-3.8-27b`; API: Cerebras.
- Output token **không bao gồm thinking**, lấy trực tiếp trường `output_tokens`.
- Dấu chấm là dấu thập phân; dấu phẩy phân cách hàng nghìn.

## 1. Tổng hợp

| Agent | Lượt gọi model | Retry do lỗi | Tổng thời gian gọi → nhận xong | Input token | Thinking token | Output token, không tính thinking |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Task Manager | **3** | **0** | **11.8835 s** | **37,046** | **12,765** | **688** |
| Assembly | **1** | **0** | **2.1405 s** | **7,065** | **1,188** | **239** |
| **Tổng hai agent** | **4** | **0** | **14.0240 s** | **44,111** | **13,953** | **927** |

Thời gian lấy trực tiếp từ `LLM:CALL_COMPLETE elapsed_ms`, không dùng `PLAN_READY duration_ms` hoặc `ASSEMBLY:READY`. Không cộng validation, thực thi công cụ sau response, điều phối task, chờ vẽ ảnh/tìm thông tin hay render.

## 2. Thời gian đến output đầu tiên và nhận xong từng lượt

- **Gọi → output đầu:** `LLM:FIRST_OUTPUT_CHUNK ttft_ms`, thời gian từ khi bắt đầu gọi API đến chunk `content` không rỗng đầu tiên; bỏ qua chunk chỉ có thinking.
- **Gọi → nhận xong:** `LLM:CALL_COMPLETE elapsed_ms`, thời gian đến lúc đọc hết stream response, gồm cả usage cuối stream.
- **Output đầu → nhận xong:** hiệu của hai số trên. Đây là khoảng nhận phần response còn lại, không phải thời gian suy luận thuần hoặc thời gian sinh riêng output của provider.
- Các cột thời gian dưới đây tính bằng **ms**.

| Agent / lượt | Mốc MODEL_CALL | Nhận chunk output đầu tiên | Nhận xong (CALL_COMPLETE) | Gọi → output đầu | Output đầu → nhận xong | Gọi → nhận xong |
| --- | --- | --- | --- | ---: | ---: | ---: |
| Task Manager / 1 | 10:01:38.106 | 10:01:39.537 | 10:01:39.626 | **1,430.9** | **88.4** | **1,519.3** |
| Task Manager / 2 | 10:01:39.636 | 10:01:46.925 | 10:01:46.946 | **7,288.2** | **21.3** | **7,309.5** |
| Task Manager / 3 | 10:01:46.948 | 10:01:49.835 | 10:01:50.003 | **2,885.8** | **168.9** | **3,054.7** |
| **Cộng Task Manager** | | | | **11,604.9** | **278.6** | **11,883.5** |
| Assembly / attempt 1 | 10:01:58.808 | 10:02:00.841 | 10:02:00.949 | **2,032.7** | **107.8** | **2,140.5** |
| **Cộng hai agent** | | | | **13,637.6** | **386.4** | **14,024.0** |

Tổng cột “Gọi → output đầu” là cộng thời gian chờ đầu ra của từng lời gọi, không phải TTFT của một lời gọi duy nhất hay thời gian chờ liên tục từ đầu request. Các timestamp hiển thị được làm tròn đến ms; ưu tiên các giá trị `ttft_ms` và `elapsed_ms` đo bằng đồng hồ monotonic trong code thay vì trừ timestamp hiển thị.

### Text đầu tiên và tool call của Task Manager

| Lượt | Kết quả model | Mốc nhận tool call đầu tiên | Gọi → tool call đầu tiên |
| --- | --- | --- | ---: |
| 1 | Gọi `describe_widget` cho `text`, `image`, `timeline`, `flashcard` | 10:01:39.624 | **1,517.5 ms** |
| 2 | Gọi `describe_widget` cho `choice` | 10:01:46.945 | **7,308.1 ms** |
| 3 | Sinh candidate chương trình OpenUI, được chấp nhận | Không có tool call | — |

**Lượt 1 và 2 có text chunk xuất hiện trước tool call.** Do đó mốc `FIRST_OUTPUT_CHUNK` của hai lượt này là text trong lượt khám phá công cụ, không phải chunk đầu của chương trình OpenUI được chấp nhận. Chunk đầu tiên của candidate xuất hiện tại **10:01:49.835**, sau **2,885.8 ms tính từ lúc gọi lượt 3**.

Hai lượt khám phá công cụ là các bước trong cùng request, không phải retry do lỗi. Log không có `VALIDATION_REJECTED`; Task Manager sinh một candidate ở lượt 3 và được chấp nhận ngay.

## 3. Token từng lượt

| Agent / lượt | Input token | Thinking token | Output token, không tính thinking | Tổng token |
| --- | ---: | ---: | ---: | ---: |
| Task Manager / 1 | 10,335 | 1,021 | 114 | 11,470 |
| Task Manager / 2 | 13,186 | 8,529 | 29 | 21,744 |
| Task Manager / 3 | 13,525 | 3,215 | 545 | 17,285 |
| **Tổng Task Manager** | **37,046** | **12,765** | **688** | **50,499** |
| Assembly / attempt 1 | 7,065 | 1,188 | 239 | 8,492 |
| **Tổng hai agent** | **44,111** | **13,953** | **927** | **58,991** |

Đối chiếu: `44,111 + 13,953 + 927 = 58,991` token. Token input cộng usage của từng lời gọi, bao gồm ngữ cảnh được gửi lại; không phải số token nội dung duy nhất. Output của lượt trả tool cũng nằm trong usage của model, không chỉ text chương trình OpenUI.

## 4. Kết quả của từng agent và giới hạn phép đo

- **Task Manager:** 2 lượt khám phá widget và 1 lượt sinh candidate; tổng gọi model **11.8835 s**, **0 retry**. Lượt 2 có 8,529 thinking token và thời gian chờ text đầu tiên 7.2882 s; log không tách được bao nhiêu trong đó là tính toán, mạng hay hàng đợi provider.
- **Assembly:** 1 lượt, **0 retry**; chờ output đầu tiên **2.0327 s**, từ output đầu đến đọc hết response **0.1078 s**, tổng **2.1405 s**. Model sinh patch điền cả ảnh và timeline.
- Thời gian sau `CALL_COMPLETE` đến validation/READY được loại khỏi bảng; riêng Assembly không cộng đoạn 10:02:00.949 → 10:02:01.000.
- Các số đo phản ánh thời gian gọi API từ ứng dụng, gồm mạng, chờ provider và đọc stream. Chúng không xác định chính xác thời gian suy luận thuần, thời gian thinking riêng hay tốc độ sinh token nội bộ của model; chunk có thể chứa nhiều token.
- Không cộng token hoặc thời gian gọi model của Gemini Live, `draw_image` và `search_information`.

---

# Báo cáo final_framework: request “kể chuyện về bác hồ”

Nguồn: log người dùng cung cấp từ 10:28:06.694 đến 10:28:59.868. Phần này chỉ thống kê Task Manager Agent và Assembly Agent cho đúng request `kể chuyện về bác hồ`; không cộng request khác.

- Plan: `b33e37459907`.
- Run: `run_4537a1f39c7d4d3cbeeddbe4e112d655`.
- Model Task Manager: **`qwen-3.8-27b`**.
- Model Assembly: **`gpt-oss-120b`**.
- API: Cerebras.
- Output token luôn **không bao gồm thinking**.
- Thời gian dùng các trường `ttft_ms` và `elapsed_ms` của log; không cộng xử lý code sau response, validation, tìm thông tin, vẽ ảnh, render hoặc Gemini Live.

## 1. Tổng hợp

| Agent | Lượt gọi model | Retry do lỗi validation | Tổng thời gian gọi → nhận xong | Input token | Thinking token | Output token, không tính thinking |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Task Manager | 4 | 2 | **4.3210 s** | 36,184 | 1,165 | 1,868 |
| Assembly | 2 | 0 | **1.2163 s** | 2,716 | 346 | 606 |
| Tổng hai agent | 6 | 2 | **5.5373 s** | 38,900 | 1,511 | 2,474 |

Assembly có **2 lời gọi độc lập cho 2 slot**, mỗi slot thành công ngay `attempt=1`: đây **không phải retry**. Hai dòng usage có cùng `run` và `attempt=1` vẫn phải cộng cả hai vì thuộc hai lời gọi khác nhau, nhận diện qua các dòng `ASSEMBLY:SLOT` và thứ tự log.

## 2. Thời gian đến output đầu tiên và nhận xong từng lượt

| Agent / lượt | Gọi → output đầu | Output đầu → nhận xong | Gọi → nhận xong |
| --- | ---: | ---: | ---: |
| Task Manager / lượt 1 | 964.7 ms | 79.8 ms | 1,044.5 ms |
| Task Manager / lượt 2 | 916.1 ms | 362.9 ms | 1,279.0 ms |
| Task Manager / lượt 3 | 1,004.2 ms | 193.5 ms | 1,197.7 ms |
| Task Manager / lượt 4 | 620.3 ms | 179.5 ms | 799.8 ms |
| Assembly / portrait_image / attempt 1 | 487.8 ms | 57.3 ms | 545.1 ms |
| Assembly / life_timeline / attempt 1 | 545.7 ms | 125.5 ms | 671.2 ms |
| **Cộng Task Manager** | **3,505.3 ms** | **815.7 ms** | **4,321.0 ms = 4.3210 s** |
| **Cộng Assembly** | **1,033.5 ms** | **182.8 ms** | **1,216.3 ms = 1.2163 s** |
| **Cộng hai agent** | **4,538.8 ms** | **998.5 ms** | **5,537.3 ms = 5.5373 s** |

“Output đầu → nhận xong” = `CALL_COMPLETE elapsed_ms - FIRST_OUTPUT_CHUNK ttft_ms`; gồm phần response còn lại và usage cuối stream. Tổng cột “Gọi → output đầu” là cộng các khoảng chờ của từng lượt, không phải TTFT của một lời gọi duy nhất.

### Text đầu tiên và tool call của Task Manager

| Lượt | Loại kết quả | Gọi → tool call đầu tiên | Trạng thái |
| --- | --- | ---: | --- |
| 1 | Describe `timeline`, `image`, `choice`, `text` | 1,038.5 ms | Khám phá widget, có text chunk trước tool call |
| 2 | Candidate JSON đầu tiên | — | Bị từ chối: `every slot_role must be targeted by a surface plan task: quiz_option_1, quiz_option_2, quiz_option_3.` |
| 3 | Candidate JSON sửa lần 1 | — | Bị từ chối: `tasks use a task_type absent from task_catalog: search_image_task.` |
| 4 | Candidate JSON sửa lần 2 | — | Được chấp nhận, 7 blocks và 2 tasks |

Task Manager có **1 lượt khám phá widget + 3 lượt sinh candidate**, trong đó **2 candidate bị từ chối**, tương ứng **2 lần retry sửa candidate**. Hai lượt retry (lượt 3 và 4) dùng tổng **1.9975 s**, đã nằm trong tổng 4.3210 s.

Chunk text đầu tiên của lượt 1 không phải chunk đầu của chương trình JSON cuối cùng. Candidate được chấp nhận ở lượt 4 có thời gian đến chunk đầu **620.3 ms**, nhận xong trong **799.8 ms** tính từ lúc gọi lượt 4.

## 3. Token từng lượt

| Agent / lượt | Input token | Thinking token | Output token, không tính thinking | Tổng token |
| --- | ---: | ---: | ---: | ---: |
| Task Manager / lượt 1 | 7,634 | 424 | 113 | 8,171 |
| Task Manager / lượt 2 | 8,814 | 418 | 635 | 9,867 |
| Task Manager / lượt 3 | 9,548 | 275 | 554 | 10,377 |
| Task Manager / lượt 4 | 10,188 | 48 | 566 | 10,802 |
| Assembly / portrait_image / attempt 1 | 1,245 | 150 | 155 | 1,550 |
| Assembly / life_timeline / attempt 1 | 1,471 | 196 | 451 | 2,118 |
| **Tổng Task Manager** | **36,184** | **1,165** | **1,868** | **39,217** |
| **Tổng Assembly** | **2,716** | **346** | **606** | **3,668** |
| **Tổng hai agent** | **38,900** | **1,511** | **2,474** | **42,885** |

Đối chiếu: `38,900 + 1,511 + 2,474 = 42,885` token. Tổng input cộng ngữ cảnh gửi lại theo từng lời gọi; output của lượt trả tool cũng nằm trong usage.

## 4. Kết quả từng agent và giới hạn phép đo

- **Task Manager:** tổng thời gian gọi model **4.3210 s**, 4 lượt gọi, 2 retry. Tổng thinking **1,165 token**; tổng output không tính thinking **1,868 token**.
- **Assembly:** xử lý riêng `portrait_image` và `life_timeline`, tổng thời gian gọi model **1.2163 s**, 2 lượt, 0 retry. Ảnh nhận output đầu sau **487.8 ms**, nhận xong sau **545.1 ms**; timeline tương ứng **545.7 ms** và **671.2 ms**.
- Thời gian code trước `MODEL_CALL` và sau `CALL_COMPLETE` không nằm trong bảng. Không dùng khoảng `ASSEMBLY:START → READY` để tính thời gian model.
- Số đo từ ứng dụng vẫn gồm mạng, chờ provider và đọc stream; không tách được suy luận thuần hay thời gian thinking riêng. Không suy ra tốc độ sinh token nội bộ chỉ từ khoảng output đầu → nhận xong.

---

# So sánh hiệu năng hai framework cho request “kể chuyện về bác hồ”

So sánh phần `lumi_openui` ở đầu file (test 10:01, plan `aeb84b7c3d63`) với phần `final_framework` ở trên (test 10:28, plan `b33e37459907`). Chỉ gồm Task Manager và Assembly của đúng request này.

## 1. Điều kiện và phạm vi so sánh

| Hạng mục | lumi_openui | final_framework |
| --- | --- | --- |
| Request | kể chuyện về bác hồ | kể chuyện về bác hồ |
| Model Task Manager | qwen-3.8-27b | qwen-3.8-27b |
| Model Assembly | **qwen-3.8-27b** | **gpt-oss-120b** |
| Đầu ra Task Manager | Chương trình/patch OpenUI Lang | Surface Retrieval Plan JSON |
| Lượt Task Manager | 3: 2 khám phá + 1 candidate | 4: 1 khám phá + 3 candidate |
| Candidate Task Manager bị từ chối / retry | 0 / 0 | 2 / 2 |
| Số blocks của plan | 8 | 7 |
| Task được tạo | 1 vẽ ảnh + 1 tìm thông tin | 1 vẽ ảnh + 1 tìm thông tin |
| Số mốc timeline được yêu cầu | 4 | 5 |
| Cách Assembly gọi model | 1 lời gọi phối hợp ảnh + timeline | 2 lời gọi riêng: ảnh, timeline |
| Retry Assembly | 0 | 0 |

Đây là **kết quả quan sát của hai lần chạy**, không phải phép đo với đầu vào hoàn toàn giống nhau. Assembly khác model; hai bản khác prompt/ngữ cảnh, định dạng đầu ra, số blocks, số mốc timeline và số lời gọi. Vì vậy không thể quy toàn bộ chênh lệch cho kiến trúc framework hoặc kết luận model nào nhanh hơn nói chung.

## 2. So sánh thời gian thật chi tiết

Chênh lệch = `final_framework - lumi_openui`. Tỷ lệ = `(final_framework / lumi_openui - 1) × 100%`. Giá trị âm nghĩa là final_framework dùng ít thời gian hơn trong lần test này. Thời gian trong bảng tính bằng **ms**, không phải mốc giờ.

| Agent | Chỉ số | lumi_openui | final_framework | Chênh lệch | Thay đổi |
| --- | --- | ---: | ---: | ---: | ---: |
| Task Manager | Cộng thời gian gọi → output đầu | 11,604.9 ms | 3,505.3 ms | −8,099.6 ms | −69.79% |
| Task Manager | Cộng thời gian output đầu → nhận xong | 278.6 ms | 815.7 ms | +537.1 ms | +192.79% |
| Task Manager | Tổng thời gian gọi → nhận xong | 11,883.5 ms | 4,321.0 ms | −7,562.5 ms | −63.64% |
| Task Manager | Trung bình gọi → output đầu mỗi lượt | 3,868.3 ms | 876.3 ms | −2,992.0 ms | −77.35% |
| Task Manager | Trung bình gọi → nhận xong mỗi lượt | 3,961.2 ms | 1,080.3 ms | −2,880.9 ms | −72.73% |
| Assembly | Cộng thời gian gọi → output đầu | 2,032.7 ms | 1,033.5 ms | −999.2 ms | −49.16% |
| Assembly | Cộng thời gian output đầu → nhận xong | 107.8 ms | 182.8 ms | +75.0 ms | +69.57% |
| Assembly | Tổng thời gian gọi → nhận xong | 2,140.5 ms | 1,216.3 ms | −924.2 ms | −43.18% |
| Assembly | Trung bình gọi → output đầu mỗi lượt | 2,032.7 ms | 516.8 ms | −1,516.0 ms | −74.58% |
| Assembly | Trung bình gọi → nhận xong mỗi lượt | 2,140.5 ms | 608.2 ms | −1,532.4 ms | −71.59% |
| Tổng hai agent | Cộng thời gian gọi → output đầu | 13,637.6 ms | 4,538.8 ms | −9,098.8 ms | −66.72% |
| Tổng hai agent | Cộng thời gian output đầu → nhận xong | 386.4 ms | 998.5 ms | +612.1 ms | +158.41% |
| Tổng hai agent | Tổng thời gian gọi → nhận xong | 14,024.0 ms | 5,537.3 ms | −8,486.7 ms | −60.52% |

Tổng thời gian gọi hai agent: **14.0240 s → 5.5373 s**, giảm **8.4867 s (60.52%)** trong hai lần chạy này. Mỗi tổng là cộng các lời gọi model thực tế, gồm cả các lượt retry; không tính thời gian toàn bộ request đến panel hoàn chỉnh.

### Đối chiếu loại lượt gọi Task Manager

| Loại lượt | lumi_openui | final_framework |
| --- | --- | --- |
| Khám phá widget | 2 lượt, tổng 8.8288 s | 1 lượt, 1.0445 s |
| Sinh candidate, gồm cả candidate bị từ chối | 1 lượt, 3.0547 s | 3 lượt, tổng 3.2765 s |
| Riêng lượt sinh candidate được chấp nhận: gọi → output đầu | 2.8858 s | 0.6203 s |
| Riêng lượt sinh candidate được chấp nhận: gọi → nhận xong | 3.0547 s | 0.7998 s |
| Tổng | **11.8835 s** | **4.3210 s** |

Không ghép cứng lượt 2 của hai framework để coi là cùng thao tác: ở lumi_openui, lượt 2 còn khám phá `choice`; ở final_framework, lượt 2 đã sinh candidate đầu tiên. Phần lớn chênh lệch thời gian Task Manager quan sát được nằm ở các lượt khám phá: **8.8288 s so với 1.0445 s**. Điều này không chứng minh nguyên nhân là riêng thao tác khám phá; lượng thinking và ngữ cảnh của các lời gọi cũng khác nhau.

## 3. So sánh token thật chi tiết

Chênh lệch và tỷ lệ vẫn lấy `final_framework` so với `lumi_openui`. Output trong bảng luôn **không bao gồm thinking**.

| Agent | Loại token | lumi_openui | final_framework | Chênh lệch token | Thay đổi |
| --- | --- | ---: | ---: | ---: | ---: |
| Task Manager | Input | 37,046 | 36,184 | −862 | −2.33% |
| Task Manager | Thinking | 12,765 | 1,165 | −11,600 | −90.87% |
| Task Manager | Output, không tính thinking | 688 | 1,868 | +1,180 | +171.51% |
| Task Manager | Tổng token | 50,499 | 39,217 | −11,282 | −22.34% |
| Assembly | Input | 7,065 | 2,716 | −4,349 | −61.56% |
| Assembly | Thinking | 1,188 | 346 | −842 | −70.88% |
| Assembly | Output, không tính thinking | 239 | 606 | +367 | +153.56% |
| Assembly | Tổng token | 8,492 | 3,668 | −4,824 | −56.81% |
| Tổng hai agent | Input | 44,111 | 38,900 | −5,211 | −11.81% |
| Tổng hai agent | Thinking | 13,953 | 1,511 | −12,442 | −89.17% |
| Tổng hai agent | Output, không tính thinking | 927 | 2,474 | +1,547 | +166.88% |
| Tổng hai agent | Tổng token | 58,991 | 42,885 | −16,106 | −27.30% |

### Phân bổ token theo loại lượt Task Manager

| Framework / loại lượt | Input | Thinking | Output không tính thinking | Tổng token |
| --- | ---: | ---: | ---: | ---: |
| lumi_openui / khám phá widget, lượt 1–2 | 23,521 | 9,550 | 143 | 33,214 |
| lumi_openui / candidate được chấp nhận, lượt 3 | 13,525 | 3,215 | 545 | 17,285 |
| final_framework / khám phá widget, lượt 1 | 7,634 | 424 | 113 | 8,171 |
| final_framework / candidate bị từ chối, lượt 2–3 | 18,362 | 693 | 1,189 | 20,244 |
| final_framework / candidate được chấp nhận, lượt 4 | 10,188 | 48 | 566 | 10,802 |

Hai lần retry của final_framework là lượt 3 và lượt 4, dùng **19,736 input + 323 thinking + 1,120 output = 21,179 token**. Bảng “candidate bị từ chối” cộng lượt 2–3 theo kết quả, còn “retry” cộng lượt 3–4 theo vai trò sửa lỗi; đây là hai cách phân nhóm khác nhau, không được cộng chồng.

## 4. Diễn giải kết quả quan sát

- **Task Manager:** final_framework dùng ít hơn **7.5625 s (63.64%)** dù có thêm một lượt gọi và 2 retry. Input chỉ giảm **2.33%**, trong khi thinking giảm **90.87%**; output tăng **171.51%**. Không thể dùng riêng số lượt gọi để dự đoán độ trễ.
- **Assembly:** final_framework cộng 2 lời gọi vẫn dùng ít hơn **0.9242 s (43.18%)**. Input giảm **61.56%**, thinking giảm **70.88%**, output tăng **153.56%**. Tuy nhiên final_framework dùng **gpt-oss-120b** còn lumi_openui dùng **qwen-3.8-27b**, nên kết quả này chưa tách được ảnh hưởng model khỏi cách chia lời gọi và định dạng output.
- **Tổng hai agent:** final_framework dùng **42,885 token** so với **58,991 token**, giảm **16,106 token (27.30%)**. Riêng output không tính thinking tăng từ **927 lên 2,474 token**; mức giảm tổng token chủ yếu đến từ thinking, không phải đầu ra ngắn hơn.
- Các con số đã bao gồm lượt thất bại/retry thực tế của từng bản và loại thời gian code ngoài lời gọi. Không đánh giá thời gian của Gemini Live, tìm thông tin, vẽ ảnh, render hoặc chất lượng nội dung trong bảng hiệu năng model này.
- Một lần chạy mỗi bản chưa cho biết mức dao động hay hiệu năng điển hình. Để so sánh riêng tác động framework cần chạy lặp lại với cùng model cho từng agent, cùng cấu hình và ngữ cảnh đầu vào được kiểm soát.

---

# Bổ sung: lumi_openui chạy lại request “kể chuyện về bác hồ” lúc 10:42

Nguồn: log từ 10:42:49.917 đến 10:43:37.980. Đây là một lần chạy mới độc lập; các số liệu phần trước vẫn thuộc lần 10:01 và không bị thay thế.

- Request: `kể chuyện về bác hồ`.
- Plan: `ac449ce55686`.
- Run: `run_23748335988942ac83d3e5b4fd41f3b5`.
- Model Task Manager và Assembly: `qwen-3.8-27b`; API Cerebras.
- Phạm vi: chỉ các lời gọi model của Task Manager và Assembly cho request này. Không cộng request trái cây, Gemini Live, search_information, draw_image, thời gian validation, xử lý code ngoài lời gọi hoặc render.
- Output token luôn **không bao gồm thinking**.

## 1. Tổng hợp lần chạy mới

| Agent | Lượt gọi model | Retry do lỗi | Tổng thời gian gọi → nhận xong | Input token | Thinking token | Output không tính thinking | Tổng token |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| Task Manager | 2 | 0 | **6.0430 s** | 22,976 | 3,401 | 659 | 27,036 |
| Assembly | 1 | 0 | **1.6932 s** | 6,754 | 1,744 | 177 | 8,675 |
| **Tổng hai agent** | **3** | **0** | **7.7362 s** | **29,730** | **5,145** | **836** | **35,711** |

## 2. Thời gian đến output đầu và nhận xong từng lượt

Thời gian lấy trực tiếp từ `FIRST_OUTPUT_CHUNK ttft_ms` và `CALL_COMPLETE elapsed_ms`. Không dùng mốc `PLAN_READY` hoặc `ASSEMBLY:READY` để tính thời gian model.

| Agent / lượt | Gọi → chunk output đầu | Chunk output đầu → nhận xong | Gọi → nhận xong |
| --- | ---: | ---: | ---: |
| Task Manager / 1 — discovery | 1,415.3 ms | 96.3 ms | 1,511.6 ms |
| Task Manager / 2 — candidate | 4,277.9 ms | 253.5 ms | 4,531.4 ms |
| **Cộng Task Manager** | **5,693.2 ms** | **349.8 ms** | **6,043.0 ms = 6.0430 s** |
| Assembly / attempt 1 | 1,615.0 ms | 78.2 ms | 1,693.2 ms |
| **Tổng hai agent** | **7,308.2 ms** | **428.0 ms** | **7,736.2 ms = 7.7362 s** |

### Discovery và retry

| Lượt Task Manager | Kết quả | Gọi → tool call đầu |
| --- | --- | ---: |
| 1 | Describe đủ `text`, `image`, `timeline`, `flashcard`, `choice` | 1,508.5 ms |
| 2 | Sinh candidate, được chấp nhận ngay | — |

Task Manager dùng **1 lượt discovery + 1 lượt candidate**, **0 retry**. Text chunk đầu của lượt 1 xuất hiện trước tool call, không phải chương trình OpenUI cuối cùng.

Assembly sinh patch phối hợp điền ảnh và timeline trong một lần gọi, **0 retry**. Timeline lần này có **3 mốc**, so với **4 mốc** của lần lumi_openui cũ và **5 mốc** của final_framework.

## 3. Token từng lượt

| Agent / lượt | Input | Thinking | Output không tính thinking | Tổng token |
| --- | ---: | ---: | ---: | ---: |
| Task Manager / 1 | 9,899 | 476 | 142 | 10,517 |
| Task Manager / 2 | 13,077 | 2,925 | 517 | 16,519 |
| **Tổng Task Manager** | **22,976** | **3,401** | **659** | **27,036** |
| Assembly / attempt 1 | 6,754 | 1,744 | 177 | 8,675 |
| **Tổng hai agent** | **29,730** | **5,145** | **836** | **35,711** |

Đối chiếu: `29,730 + 5,145 + 836 = 35,711` token. Input tính theo usage từng lời gọi, bao gồm ngữ cảnh gửi lại; không phải lượng nội dung input duy nhất.

## 4. Nhận xét lần chạy mới

- Task Manager lấy đủ 5 widget trong lượt discovery đầu, tránh lượt discovery thứ hai từng dùng 8,529 thinking token ở lần 10:01. Tổng thinking Task Manager giảm từ 12,765 xuống 3,401 token.
- Assembly nhanh hơn lần 10:01 nhưng thinking tăng từ 1,188 lên 1,744 token; tổng token Assembly tăng từ 8,492 lên 8,675 token.
- Khoảng đến chunk đầu và nhận hết response là độ trễ quan sát từ ứng dụng, vẫn gồm mạng/chờ provider/đọc stream, chưa phải thời gian suy luận thuần.
- Chất lượng cần xem riêng: quiz “Bác Hồ quê ở đâu?” có các lựa chọn Thái Bình, Đức Thọ–Hà Tĩnh, Hà Nội, trong khi evidence của chính log ghi Nghệ An. Các lựa chọn không chứa đáp án phù hợp với evidence. Candidate qua validation không đồng nghĩa nội dung đã đúng.
- Log không chứng minh có thay đổi prompt/model/config giữa hai lần lumi_openui; ngữ cảnh chương trình cũ và đầu ra thực tế cũng khác. Không quy toàn bộ thay đổi hiệu năng cho một nguyên nhân duy nhất.

---

# So sánh ba lần chạy: lumi_openui cũ, final_framework và lumi_openui mới

Cả ba lần có request **“kể chuyện về bác hồ”**. Mỗi lần được tính riêng theo plan/run, không cộng dồn chúng thành usage của một request.

## 1. Bảng tổng quan đầy đủ

| Lần chạy | Agent | Lượt model | Retry | Thời gian gọi → nhận xong | Input | Thinking | Output không tính thinking | Tổng token |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| lumi_openui cũ (10:01) | Task Manager | 3 | 0 | 11.8835 s | 37,046 | 12,765 | 688 | 50,499 |
| lumi_openui cũ (10:01) | Assembly | 1 | 0 | 2.1405 s | 7,065 | 1,188 | 239 | 8,492 |
| lumi_openui cũ (10:01) | Tổng hai agent | 4 | 0 | 14.0240 s | 44,111 | 13,953 | 927 | 58,991 |
| final_framework (10:28) | Task Manager | 4 | 2 | 4.3210 s | 36,184 | 1,165 | 1,868 | 39,217 |
| final_framework (10:28) | Assembly | 2 | 0 | 1.2163 s | 2,716 | 346 | 606 | 3,668 |
| final_framework (10:28) | Tổng hai agent | 6 | 2 | 5.5373 s | 38,900 | 1,511 | 2,474 | 42,885 |
| lumi_openui mới (10:42) | Task Manager | 2 | 0 | 6.0430 s | 22,976 | 3,401 | 659 | 27,036 |
| lumi_openui mới (10:42) | Assembly | 1 | 0 | 1.6932 s | 6,754 | 1,744 | 177 | 8,675 |
| lumi_openui mới (10:42) | Tổng hai agent | 3 | 0 | 7.7362 s | 29,730 | 5,145 | 836 | 35,711 |

## 2. So sánh thời gian chi tiết

Thời gian trong bảng đều là **khoảng thời gian tính bằng ms**. Cột tổng “gọi → chunk đầu” cộng thời gian chờ của nhiều lời gọi, không phải một TTFT duy nhất.

| Agent | Chỉ số | lumi_openui cũ (10:01) | final_framework (10:28) | lumi_openui mới (10:42) |
| --- | --- | ---: | ---: | ---: |
| Task Manager | Cộng gọi → chunk output đầu | 11,604.9 ms | 3,505.3 ms | 5,693.2 ms |
| Task Manager | Cộng chunk output đầu → nhận xong | 278.6 ms | 815.7 ms | 349.8 ms |
| Task Manager | Tổng gọi → nhận xong | 11,883.5 ms | 4,321.0 ms | 6,043.0 ms |
| Task Manager | Trung bình gọi → chunk output đầu mỗi lượt | 3,868.3 ms | 876.3 ms | 2,846.6 ms |
| Task Manager | Trung bình gọi → nhận xong mỗi lượt | 3,961.2 ms | 1,080.3 ms | 3,021.5 ms |
| Assembly | Cộng gọi → chunk output đầu | 2,032.7 ms | 1,033.5 ms | 1,615.0 ms |
| Assembly | Cộng chunk output đầu → nhận xong | 107.8 ms | 182.8 ms | 78.2 ms |
| Assembly | Tổng gọi → nhận xong | 2,140.5 ms | 1,216.3 ms | 1,693.2 ms |
| Assembly | Trung bình gọi → chunk output đầu mỗi lượt | 2,032.7 ms | 516.8 ms | 1,615.0 ms |
| Assembly | Trung bình gọi → nhận xong mỗi lượt | 2,140.5 ms | 608.2 ms | 1,693.2 ms |
| Tổng hai agent | Cộng gọi → chunk output đầu | 13,637.6 ms | 4,538.8 ms | 7,308.2 ms |
| Tổng hai agent | Cộng chunk output đầu → nhận xong | 386.4 ms | 998.5 ms | 428.0 ms |
| Tổng hai agent | Tổng gọi → nhận xong | 14,024.0 ms | 5,537.3 ms | 7,736.2 ms |

### Phân loại lượt Task Manager để so sánh đúng thao tác

| Loại lượt | lumi_openui cũ | final_framework | lumi_openui mới |
| --- | --- | --- | --- |
| Discovery | 2 lượt, 8.8288 s | 1 lượt, 1.0445 s | 1 lượt, 1.5116 s |
| Sinh candidate, gồm cả candidate bị từ chối | 1 lượt, 3.0547 s | 3 lượt, 3.2765 s | 1 lượt, 4.5314 s |
| Retry sửa candidate | 0 | 2 | 0 |
| Riêng candidate được chấp nhận: gọi → chunk đầu | 2.8858 s | 0.6203 s | 4.2779 s |
| Riêng candidate được chấp nhận: gọi → nhận xong | 3.0547 s | 0.7998 s | 4.5314 s |
| **Tổng Task Manager** | **11.8835 s** | **4.3210 s** | **6.0430 s** |

Lumi_openui mới giảm tổng thời gian Task Manager chủ yếu ở phần discovery. Riêng lượt sinh candidate mới **chậm hơn** lượt sinh candidate của lumi_openui cũ (4.5314 s so với 3.0547 s); không nên hiểu tổng nhanh hơn là mọi lượt đều nhanh hơn.

## 3. So sánh token chi tiết

| Agent | Loại token | lumi_openui cũ (10:01) | final_framework (10:28) | lumi_openui mới (10:42) |
| --- | --- | ---: | ---: | ---: |
| Task Manager | Input | 37,046 | 36,184 | 22,976 |
| Task Manager | Thinking | 12,765 | 1,165 | 3,401 |
| Task Manager | Output không tính thinking | 688 | 1,868 | 659 |
| Task Manager | Tổng token | 50,499 | 39,217 | 27,036 |
| Assembly | Input | 7,065 | 2,716 | 6,754 |
| Assembly | Thinking | 1,188 | 346 | 1,744 |
| Assembly | Output không tính thinking | 239 | 606 | 177 |
| Assembly | Tổng token | 8,492 | 3,668 | 8,675 |
| Tổng hai agent | Input | 44,111 | 38,900 | 29,730 |
| Tổng hai agent | Thinking | 13,953 | 1,511 | 5,145 |
| Tổng hai agent | Output không tính thinking | 927 | 2,474 | 836 |
| Tổng hai agent | Tổng token | 58,991 | 42,885 | 35,711 |

### Phân bổ Task Manager theo loại lượt

| Lần chạy / loại lượt | Input | Thinking | Output không tính thinking | Tổng token |
| --- | ---: | ---: | ---: | ---: |
| lumi_openui cũ / discovery, lượt 1–2 | 23,521 | 9,550 | 143 | 33,214 |
| lumi_openui cũ / candidate, lượt 3 | 13,525 | 3,215 | 545 | 17,285 |
| final_framework / discovery, lượt 1 | 7,634 | 424 | 113 | 8,171 |
| final_framework / các candidate, lượt 2–4 | 28,550 | 741 | 1,755 | 31,046 |
| lumi_openui mới / discovery, lượt 1 | 9,899 | 476 | 142 | 10,517 |
| lumi_openui mới / candidate, lượt 2 | 13,077 | 2,925 | 517 | 16,519 |

## 4. Chênh lệch của lumi_openui mới so với hai lần cũ

Công thức: chênh lệch = `lumi_openui mới − bản được so sánh`; tỷ lệ = `(mới / bản được so sánh − 1) × 100%`. Số âm nghĩa là lần mới dùng ít thời gian/token hơn.

| Agent | Chỉ số | Mới so với lumi_openui cũ | Mới so với final_framework |
| --- | --- | ---: | ---: |
| Task Manager | Thời gian gọi → nhận xong | −5.8405 s (−49.15%) | +1.7220 s (+39.85%) |
| Task Manager | Input token | −14,070 (−37.98%) | −13,208 (−36.50%) |
| Task Manager | Thinking token | −9,364 (−73.36%) | +2,236 (+191.93%) |
| Task Manager | Output không tính thinking | −29 (−4.22%) | −1,209 (−64.72%) |
| Task Manager | Tổng token | −23,463 (−46.46%) | −12,181 (−31.06%) |
| Assembly | Thời gian gọi → nhận xong | −0.4473 s (−20.90%) | +0.4769 s (+39.21%) |
| Assembly | Input token | −311 (−4.40%) | +4,038 (+148.67%) |
| Assembly | Thinking token | +556 (+46.80%) | +1,398 (+404.05%) |
| Assembly | Output không tính thinking | −62 (−25.94%) | −429 (−70.79%) |
| Assembly | Tổng token | +183 (+2.15%) | +5,007 (+136.50%) |
| Tổng hai agent | Thời gian gọi → nhận xong | −6.2878 s (−44.84%) | +2.1989 s (+39.71%) |
| Tổng hai agent | Input token | −14,381 (−32.60%) | −9,170 (−23.57%) |
| Tổng hai agent | Thinking token | −8,808 (−63.13%) | +3,634 (+240.50%) |
| Tổng hai agent | Output không tính thinking | −91 (−9.82%) | −1,638 (−66.21%) |
| Tổng hai agent | Tổng token | −23,280 (−39.46%) | −7,174 (−16.73%) |

## 5. Kết quả và phạm vi kết luận

- **So với lumi_openui cũ:** lần mới giảm tổng thời gian gọi model **44.84%**, từ **14.0240 s xuống 7.7362 s**; giảm tổng token **39.46%**, từ **58,991 xuống 35,711**. Tổng thinking giảm **63.13%**. Cả hai lần đều không retry.
- **So với final_framework:** lumi_openui mới vẫn chậm hơn **2.1989 s (39.71%)** khi cộng hai agent, nhưng dùng ít hơn **7,174 token (16.73%)**. Input giảm **23.57%**, output không tính thinking giảm **66.21%**, còn thinking tăng **240.50%**.
- **Task Manager:** cả ba dùng `qwen-3.8-27b`, nhưng prompt, ngữ cảnh, định dạng output và candidate khác nhau. Final_framework có 2 retry vẫn nhanh hơn trong số liệu lần chạy này; không có nghĩa retry không tốn chi phí.
- **Assembly:** hai lần lumi_openui dùng `qwen-3.8-27b`, phối hợp ảnh + timeline trong một lời gọi; final_framework dùng `gpt-oss-120b`, gọi hai slot riêng. Không thể kết luận chênh lệch chỉ do framework.
- Timeline có lần lượt **4 / 5 / 3 mốc**; số blocks là **8 / 7 / 7** cho lumi_openui cũ / final_framework / lumi_openui mới. Khối lượng nội dung và chất lượng chưa được chuẩn hóa, nên kết quả chỉ mô tả các lần test này.
- Việc có thêm một lần lumi_openui cho thấy độ biến động lớn của thinking và thời gian. Các mẫu hiện tại chưa đủ để đưa ra hiệu năng điển hình hoặc khẳng định nguyên nhân.
