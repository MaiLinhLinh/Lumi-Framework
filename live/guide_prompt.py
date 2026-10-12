"""Shared Live guidance and compact trusted panel-context messages."""

from __future__ import annotations

import json
from typing import Any, Mapping


PANEL_INTERACTION_GUIDANCE = """
Khi nhận một client event bắt đầu bằng `PANEL_INTERACTION_EVENT`, phần JSON theo
sau là dữ kiện tương tác giao diện đáng tin cậy, không phải lời người dùng nói.
Event `surface_interaction` cho biết trẻ vừa thực hiện `action` trên vùng có
`anchor_id` tương ứng trong VISUAL STAGE MAP. `content` chỉ mô tả các thành phần
hiển thị của vùng đó, không phải kết luận đúng/sai. Dùng map và lịch sử để hiểu
ý nghĩa tương tác, rồi tự quyết định phản hồi, hiệu ứng hoặc state update phù hợp.
Đọc event và đối chiếu nó với Visual Stage Map hiện tại. Xác định ý nghĩa nghiệp vụ, nghiệp vụ đó có thể dẫn đến thay đổi trạng thái của vùng hoặc hiển thị dữ liệu vùng nào không.
Nếu có thì gọi update_surface_state để cập nhật trạng thái vùng đó rồi mới nói về kết luận.
Nếu có nhiều vùng ẩn cần hiện thì phải suy nghĩ và gọi update_surface_state cho tất cả các vùng, bạn phải suy nghĩ xem nó có thể gọi lần lượt hay phải gọi đồng thời.
Không đọc hoặc nhắc lại JSON, event, anchor_id hay dữ liệu kỹ thuật.

""".strip()

PRESENTATION_CONTEXT_GUIDANCE = """
NGUỒN THÔNG TIN
- VISUAL STAGE MAP mô tả chính xác panel người dùng đang nhìn thấy.
- Chỉ dùng nội dung, trạng thái và dữ kiện có trong VISUAL STAGE MAP hoặc
  ngữ cảnh hội thoại hiện tại.
- visual_effects là danh sách hiệu ứng duy nhất được phép dùng.
- Không tự tạo hoặc thay đổi kiến thức hay dữ liệu trực quan không được cung cấp.
- Không nói về tool, anchor_id, effect_id, JSON, template, sơ đồ hay dữ liệu kỹ thuật.

HIỂU PANEL
- Đọc toàn bộ VISUAL STAGE MAP trước khi bắt đầu trả lời.
- Xác định các vùng đang hiển thị, các vùng đang ẩn, nội dung của từng vùng,
  vị trí tương đối và anchor của vùng đó.
- Bạn tự quyết định trình tự trình bày. bạn phải thiết lập ra một bài trình bày hoàn chỉnh, xuyên suốt, có tính tương tác với người dùng, không chỉ nói 1 câu rồi dừng.
- Không khẳng định nội dung không xuất hiện trong map hoặc ngữ cảnh là đúng.

QUY TẮC MINH HOẠ BẮT BUỘC
Mỗi khi định nói về một vùng đang hiển thị có [anchor: ...] trong VISUAL STAGE MAP,
BẮT BUỘC thực hiện đúng thứ tự chuỗi sau:
1. Chọn một vùng duy nhất. MỖI LƯỢT CHỈ ĐƯỢC GỌI MỘT present_visual.
2. Phải Gọi tool present_visual đúng một lần với anchor_id của vùng đó và effect_id hợp lệ.
3. Chỉ Sau tool response, nói một câu hoặc một ý ngắn chỉ về chính vùng đó.
4. Nếu bài giảng còn ý cần thiết khác, tiếp tục chọn vùng kế tiếp và lặp lại.
5. Chỉ dừng khi gặp một điều kiện dừng ở phần VAI TRÒ VÀ MỤC TIÊU.

Không được gọi present_visual cho đáp án của câu hỏi khi người dùng chưa trả lời.

Không gọi nhiều present_visual liên tiếp.
Không gọi present_visual cho vùng mà bạn không định nói ngay sau tool response.
Không nói về vùng có anchor nếu chưa gọi present_visual cho chính vùng đó ngay
trước ý đang nói.
Không được nói một vùng có thể minh hoạ mà lại không gọi present_visual cho vùng đó.
Không dùng anchor không xuất hiện trong map.
Không lặp lại anchor hoặc effect nếu không có lý do giảng dạy rõ ràng.
Không gộp nhiều vùng có anchor vào cùng một ý nói.

CHỌN HIỆU ỨNG
- Chỉ dùng effect_id có trong visual_effects.
- Chọn theo description và usage_guidance đi kèm từng effect trong visual_effects.

CÁCH GỌI CÔNG CỤ
present_visual và update_surface_state là function/tool hệ thống cung cấp, không phải
nội dung được phép đọc hoặc viết ra lời thoại.
Khi cần minh hoạ hoặc công bố nội dung, bắt buộc tạo native function call thật
đến đúng tool tương ứng. Tuyệt đối không được chèn, mô phỏng, viết lại hoặc đọc
bất kỳ cú pháp nào như:
- present_visual(...)
- update_surface_state(...)
- [present_visual ...]
- [update_surface_state ...]
- anchor_id=...
- effect_id=...
- base_revision=...
Những chuỗi trên không bao giờ được xuất hiện trong câu trả lời cho người dùng.
Nếu chưa thực hiện function call thật và chưa nhận tool response, không được nói
rằng mình đã minh hoạ, khoanh vùng, làm nổi bật hoặc công bố vùng đó.

QUY TẮC HIỆN NỘI DUNG ĐANG ẨN
- present_visual chỉ minh hoạ một vùng; nó không làm thay đổi nội dung panel.
- Khi VISUAL STAGE MAP ghi một vùng đang ẩn và bạn quyết định đã đến lúc công bố
  vùng đó, bắt buộc gọi native tool update_surface_state với surface_id và
  base_revision hiện tại trong context, cùng updates chứa anchor_id của vùng đó
  và changes={"visibility":"visible"}.
- Có thể cập nhật một vùng hoặc nhiều vùng cùng lúc nếu chúng cần xuất hiện cùng lúc.
- Sau tool response của update_surface_state, chỉ dùng VISUAL STAGE MAP mới được trả về.
- Chỉ sau khi map mới xác nhận vùng đã hiện, mới gọi present_visual cho vùng đó
  rồi nói về nội dung vừa được công bố.
- Không gọi update_surface_state khi chỉ đưa ra câu hỏi, gợi ý.
- Nếu muốn hai vùng xuất hiện cùng lúc, gọi một lần:
update_surface_state với hai updates cho anchor_id="d" và anchor_id="e",
đều có changes={"visibility":"visible"}.
- Không cập nhật lại vùng mà map mới đã ghi là đang hiển thị.

QUY TẮC TRÌNH BÀY:
Bất cứ lúc nào tương tác với người dùng:
- Hãy trình bày một cách đầy đủ từ đầu đến cuối, có tính tương tác, không chỉ nói một câu rồi dừng. Bắt buộc phải gọi present_visual cho từng vùng trước khi nói về vùng đó.
- Không được nói khi chưa gọi present_visual.

""".strip()


def surface_context_message(panel_context: Mapping[str, Any]) -> str:
    """Format only the dynamic facts for the current rendered panel."""

    return "\n\n".join((
        "SURFACE CONTEXT — PANEL HIỆN TẠI. Đây là ngữ cảnh giao diện tin cậy, không phải lời người dùng.",
        "SURFACE:\n"
        + f"surface_id: {panel_context['surface_id']}\n"
        + f"base_revision: {panel_context['revision']}",
        "VISUAL STAGE MAP:\n" + str(panel_context["visual_stage_map"]),
        "visual_effects:\n" + json.dumps(panel_context["visual_effects"], ensure_ascii=False),
    ))


def surface_ready_message(
    panel_context: Mapping[str, Any],
    surface_completion: Mapping[str, Any] | None = None,
) -> str:
    """Send only fresh panel facts after asynchronous planning completes."""

    if (
        isinstance(surface_completion, Mapping)
        and surface_completion.get("surface_status") == "partial"
        and surface_completion.get("missing_parts")
    ):
        return (
            "SURFACE_PARTIAL — PANEL ĐÃ RENDER NHƯNG CHƯA ĐẦY ĐỦ. "
            "Danh sách MISSING_PARTS cho biết chính xác nội dung chưa hiển thị. "
            "Hãy báo ngắn gọn, tự nhiên cho người dùng phần nào chưa hiển thị; "
            "không nói lỗi, task, widget, placeholder hay chi tiết kỹ thuật, và "
            "không tuyên bố các phần đó đã sẵn sàng. Chỉ trình bày những gì thật "
            "sự có trong VISUAL STAGE MAP.\n\n"
            "MISSING_PARTS:\n"
            + json.dumps(
                surface_completion["missing_parts"],
                ensure_ascii=False,
                separators=(",", ":"),
            )
            + "\n\n"
            + surface_context_message(panel_context)
        )
    return (
        "SURFACE_READY — PANEL ĐÃ SẴN SÀNG. Áp dụng các presentation rules đã "
        "thiết lập; không đọc dữ liệu kỹ thuật cho người dùng.\n\n"
        + surface_context_message(panel_context)
    )


def panel_interaction_message(interaction: Mapping[str, Any]) -> str:
    """Attach interaction-only rules to the trusted browser event."""

    return "PANEL_INTERACTION_EVENT\n" + PANEL_INTERACTION_GUIDANCE + "\n\nEVENT:\n" + json.dumps(
        dict(interaction), ensure_ascii=False, separators=(",", ":")
    )


def surface_context_update_message(panel_context: Mapping[str, Any]) -> str:
    """Silently replace Gemini's map after a browser-confirmed runtime repair."""

    return (
        "SURFACE_CONTEXT_UPDATE — BẢN MỚI NHẤT. "
        "Không phản hồi event này; từ lượt sau chỉ dùng context bên dưới.\n\n"
        + surface_context_message(panel_context)
    )
