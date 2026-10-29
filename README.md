# Lumi OpenUI

Framework độc lập được triển khai trong thư mục này. Không import hoặc đọc tài nguyên từ framework gốc khi chạy.

## Tiến độ

CP1–CP6 đã triển khai/build. CP6 dùng `editMode: true` cho Task Manager sinh patch, merge bằng OpenUI vào chương trình đã hoàn thành. Hỗ trợ sửa nội dung/grid, thêm ảnh/widget, xóa/thay hoạt động; giữ nội dung, state và identity còn hiệu lực. Khi task/Assembly hoặc một lượt lập kế hoạch còn chạy, trả `busy`, không nhận hay xếp hàng chỉnh sửa. Kiểm tra trong `LumiMultiAgent`; provider/transport giả lập, chưa xác minh Live/microphone thật. Chi tiết tại [báo cáo CP6](docs/CP6_REPORT.md) và [CP6_VERIFICATION.json](docs/CP6_VERIFICATION.json). Các báo cáo [CP1](docs/CP1_REPORT.md), [CP2](docs/CP2_REPORT.md), [CP3](docs/CP3_REPORT.md), [CP4](docs/CP4_REPORT.md), [CP5](docs/CP5_REPORT.md) giữ nguyên lịch sử.

Frontend có 8 widget React/OpenUI và 6 effect React, giữ grid 16×10, Shadow DOM, state/anchor/interaction và xác nhận render theo revision. Task Manager tạo/chỉnh sửa chương trình bằng parser/evaluator/merge OpenUI chính thức; Python giữ preflight/ASCII Map/task scheduling/barrier. Assembly chỉ điền phần chưa hoàn thành, tổng hợp trước một cập nhật và giữ source đồng bộ. Đã kiểm tra bằng provider/WebSocket giả lập và PCM im lặng; chưa xác minh phiên Gemini Live/provider hoặc microphone thật.

Assembly hiện nhận toàn bộ source/khung cùng kết quả task trong một request, trả một
patch phối hợp các slot còn thiếu. Backend giữ kiểm tra phạm vi, evidence theo từng
slot, layout, anchor và nội dung đã hoàn thành. Chỉ commit sau khi patch hợp lệ;
chưa bật streaming render. Xem [báo cáo Assembly phối hợp](docs/COORDINATED_ASSEMBLY_REPORT.md).

## Tổ chức code task agent

```text
task_agents/                   # Agent gọi model và prompt của từng task
  search_information/
    agent.py
    prompts.py
  search_image/
    agent.py
    prompts.py
  draw_image/
    agent.py
    prompts.py
    contracts.py               # Kết quả vẽ ảnh và kiểm tra cấu hình
agent_contracts/               # Request, context, tool schema dùng chung
remote_tasks/                  # Chạy agent service qua HTTP theo manifest
executors/                     # Nối task runtime với agent và artifact
knowledge/                     # Catalog, kho dữ liệu, công cụ local search
surface/assembly.py            # Assembly điền nội dung vào surface
```

`agent_contracts/` thay cho tên cũ `agent_tasks/`; `remote_tasks/` thay cho
`async_tasks/`. Các agent tìm thông tin/tìm ảnh/vẽ ảnh đã chuyển khỏi `knowledge/`
và `media/`. Prompt và logic được giữ nguyên khi di chuyển. Chi tiết đường dẫn cũ
và mới tại [báo cáo tổ chức code](docs/TASK_LAYOUT_REPORT.md).

## Môi trường chạy

Đã dọn các hàm tương thích cũ không còn được gọi; xem [báo cáo dọn code](docs/CLEANUP_REPORT.md).

- Python/backend: Conda `LumiMultiAgent`.
- Frontend: dependency cục bộ tại `frontend/node_modules`, phiên bản khóa trong `frontend/package-lock.json`.
- Cấu hình riêng: `.env` tại thư mục này; không commit file này.
- Chroma/media dùng kho độc lập `D:\RAG_ManageAgent_Lumi\knowledge_storage`, dùng chung với
  `final_framework`. `.env` cấu hình `KNOWLEDGE_CHROMA_PATH` và `KNOWLEDGE_MEDIA_PATH`;
  không còn mặc định tạo kho trong framework. Dữ liệu Hồ Chí Minh đã được sao chép và
  kiểm tra từ kho cũ, giữ nguyên bản gốc. Xem [hướng dẫn kho chung](../../knowledge_storage/README.md).
- Riêng Lumi: search agent đọc catalog dữ liệu đã nhập, chọn tất cả kho phù hợp và tìm
  local một lượt đồng thời; kết quả trả lời đúng request thì dùng local, không trả lời
  request thì chuyển Google grounding. Không có kho phù hợp thì grounding trực tiếp.
  [Báo cáo triển khai và giới hạn kiểm tra](docs/SHARED_KNOWLEDGE_REPORT.md).

## Build frontend

```powershell
conda activate LumiMultiAgent
cd D:\RAG_ManageAgent_Lumi\open-ui-lang\lumi_openui\frontend
npm ci --no-audit --no-fund
npm run build
```

Build sinh frontend, Node processor, ánh xạ extension, JSON Schema props cho Python, library spec/schema và prompt trong `build/`. Backend cần Node đã có trong PATH; manifest hash chặn artifact thiếu/cũ. Các manifest tự được phát hiện. Widget dùng `component.tsx`; source props là Zod, contract Python giữ policy runtime. Effect dùng ba file `manifest.json`, `effect.tsx`, `styles.css`; export `effect = defineEffect({component})`. Sửa source/schema/manifest cần build lại trước khi khởi động backend. Hướng dẫn API effect tại [báo cáo CP3](docs/CP3_REPORT.md).

Npm cache/log nằm trong `frontend/.npm-cache`; dependency nằm trong `frontend/node_modules`. Không cần cài npm global. Chạy `python -m unittest discover -s ../tests -v` và `npm run test:cp5` tại `frontend` trong môi trường `LumiMultiAgent`. `npm run test:cp2` chỉ chạy hồi quy widget; `npm run test:cp4` giữ phạm vi CP2–CP4; `npm run test:effect-discovery` kiểm tra effect thứ bảy rồi khôi phục build sáu effect. Browser test dùng Chromium đã có sẵn, không mở microphone hay gọi provider.

## Khởi động khi cần kiểm tra ở checkpoint tiếp theo

Chạy từ thư mục cha chứa package `lumi_openui`:

```powershell
conda activate LumiMultiAgent
cd D:\RAG_ManageAgent_Lumi\open-ui-lang
python -m uvicorn lumi_openui.web_app:app --host 127.0.0.1 --port 8002
```

Mở `http://127.0.0.1:8002`. Cần build trước khi khởi động. Đây là hướng dẫn chạy, chưa phải xác nhận backend/Live đã hoạt động.
