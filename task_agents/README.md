# Task agents

Mỗi agent có implementation trong `agent.py`, prompt trong `prompts.py`.

| Task | Implementation | Prompt |
| --- | --- | --- |
| `search_information_task` | [search_information/agent.py](search_information/agent.py) | [search_information/prompts.py](search_information/prompts.py) |
| `search_image_task` | [search_image/agent.py](search_image/agent.py) | [search_image/prompts.py](search_image/prompts.py) |
| `draw_image_task` | [draw_image/agent.py](draw_image/agent.py) | [draw_image/prompts.py](draw_image/prompts.py) |

Agent tìm kiếm nhận catalog và công cụ từ `knowledge/`; agent tự chọn local hoặc
Google theo prompt. `agent_contracts/` định nghĩa request dùng chung; `executors/`
chuyển request/kết quả giữa agent và task runtime. `remote_tasks/` dành cho dịch vụ
agent bên ngoài chạy qua HTTP, không chứa implementation của ba agent này.

Task Manager nằm ở `task_manager_agent/`. Assembly vẫn ở `surface/assembly.py`:
Assembly hoàn thiện nội dung surface, không phải agent tìm kiếm hay vẽ ảnh.
