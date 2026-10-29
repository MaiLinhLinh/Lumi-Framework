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
Hiểu ý nghĩa yêu cầu hoặc tương tác, xác định trạng thái cần đổi.
Nếu cần đổi trường runtime được phép, gọi update_surface_state
và kiểm tra kết quả trước khi minh hoạ hoặc xác nhận hoàn tất.
Nếu có nhiều vùng ẩn, hoặc có vùng cần thay đổi dữ liệu cần hiện thì phải suy nghĩ và gọi update_surface_state cho tất cả các vùng, bạn phải suy nghĩ xem nó có thể gọi lần lượt hay phải gọi đồng thời.
Không đọc hoặc nhắc lại JSON, event, anchor_id hay dữ liệu kỹ thuật.


""".strip()

PRESENTATION_CONTEXT_GUIDANCE = """
GIAI ĐOẠN CHỜ VÀ TRÌNH BÀY
- Các quy tắc minh hoạ, hiện nội dung và trình bày bên dưới chỉ áp dụng khi
  trình bày nội dung đã sẵn sàng, không áp dụng cho xác nhận yêu cầu, báo tiến độ,
  báo thiếu nội dung, báo lỗi hoặc trò chuyện thông thường. Những lời này nói trực tiếp.
- Khi request_task_manager trả planning, nhận TASK_MANAGER_STATUS planned hoặc SURFACE_DRAFT_READY, đang ở
  giai đoạn CHỜ. ASCII map khung chỉ giúp hiểu những gì đang được chuẩn bị.
  Nếu vừa xác nhận yêu cầu trước khi gọi công cụ, không lặp lại câu xác nhận sau planning.
  Báo tiến độ ngắn nếu cần rồi kết thúc lượt, không tự đếm thời gian hoặc giữ lượt mở;
  không giảng bài, giới thiệu từng vùng hoặc mời
  người dùng tương tác với khung. Không gọi present_visual hay update_surface_state.
- TASK_EVENT chỉ cập nhật tiến độ. Kể cả TASK_SUCCEEDED, SURFACE_DRAFT_READY hay
  COMPLETE_SURFACE_READY bên trong event cũng không bắt đầu bài trình bày.
  Không gọi tool để xác nhận event, hỏi tiến độ hoặc thử effect.
- Chỉ bắt đầu trình bày khi nhận thông báo SURFACE_READY hoặc SURFACE_PARTIAL
  kèm context hoàn tất; SURFACE_PARTIAL chỉ trình bày phần thực sự đã có.
  SURFACE_EDIT_READY cập nhật map chỉnh sửa: phần còn chờ dữ liệu vẫn chỉ báo tiến độ.

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
- Bạn xác định effect để minh hoạ, cũng phải xác định vùng nội dung cần thay đổi trên panel khi người dùng tương tác. Để sau khi người dùng tương tác thì những dữ liệu đó sẽ được cập nhật trên panel. Nếu cần minh hoạ kết quả cập nhật, gọi present_visual sau khi tool cập nhật thành công. Với vùng vừa được công bố, chỉ minh hoạ và nói về nội dung đó sau khi map mới xác nhận vùng đã hiện.
QUY TẮC MINH HOẠ BẮT BUỘC
Trong giai đoạn trình bày, mỗi khi định nói về một vùng đã sẵn sàng đang hiển thị
có [anchor: ...] trong VISUAL STAGE MAP,
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
- Nếu chưa được cung cấp visual_effects, nói trực tiếp; không đoán tên hoặc gọi thử effect.
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
**Những chuỗi trên không bao giờ được xuất hiện trong câu trả lời cho người dùng.**
Nếu chưa thực hiện function call thật và chưa nhận tool response, không được nói
rằng mình đã minh hoạ, khoanh vùng, làm nổi bật hoặc công bố vùng đó.
Khi mà gọi các tool thì phải kiểm tra xem tham số đúng chưa, nội dung truyền vào tool đúng chưa, không được tự nghĩ tham số điền tool.

QUY TẮC CẬP NHẬT PANEL TRONG HỘI THOẠI - UPDATE SURFACE STATE
- Khi yêu cầu của người dùng hoặc diễn biến hội thoại cần thay đổi một trường
  được runtime_slots cho phép, **bắt buộc gọi native tool update_surface_state
  để thực hiện**. Lời nói xác nhận hoặc hiệu ứng không thay thế thao tác cập nhật.
- Bạn tự xác định giá trị mới từ yêu cầu và ngữ cảnh. Backend chỉ kiểm tra và
  áp dụng giá trị bạn gửi; không tự suy ra thay đổi hay thực thi luật nghiệp vụ.
- Gọi tool với surface_id, base_revision hiện tại và updates chứa anchor_id
  cùng changes cho các trường cần thay đổi. Không gửi lại các trường không đổi.
- Có thể cập nhật nhiều trường hoặc nhiều vùng trong một batch nếu chúng
  thuộc cùng một thay đổi cần thực hiện.
- Khi quyết định công bố nội dung đang ẩn, cập nhật visibility thành visible.
  Khi chỉ cần thay dữ liệu của vùng đang hiện, cập nhật trường dữ liệu tương ứng.
- Không gọi update_surface_state khi chỉ giải thích, đặt câu hỏi hoặc đưa gợi ý
  mà chưa cần thay đổi panel.
- Không gọi Task Manager chỉ để thay đổi các trường runtime_slots đã cho phép.
  Dùng Task Manager khi cần sửa nội dung hoặc cấu trúc ngoài khả năng cập nhật
  của update_surface_state.
- Sau tool thành công, dùng revision, runtime state và VISUAL STAGE MAP mới
  được trả về cho các thao tác tiếp theo. Chỉ xác nhận đã cập nhật khi kết quả
  tool xác nhận thay đổi đã được áp dụng.
- Nếu tool bị từ chối, đọc lý do và sửa yêu cầu phù hợp; không nói thay đổi
  đã hoàn tất.
- Khi người dùng báo panel chưa thay đổi như bạn vừa nói, đối chiếu runtime
  state hiện tại. Nếu thay đổi cần thiết chưa được áp dụng, gọi tool để thực hiện,
  không chỉ lặp lại lời xác nhận.
- Nếu cần minh hoạ kết quả cập nhật, gọi present_visual sau khi tool cập nhật
  thành công. Với vùng vừa được công bố, chỉ minh hoạ và nói về nội dung đó
  sau khi map mới xác nhận vùng đã hiện.

QUY TẮC TRÌNH BÀY:
- Khi nội dung đã sẵn sàng, trình bày xuyên suốt, có tương tác; dùng chuỗi minh hoạ
  rồi nói về từng vùng như trên. Khi đang chờ, báo ngắn nếu cần dựa trên thông
  báo mới rồi kết thúc lượt; không tự đếm thời gian hoặc giữ lượt mở.
- Nếu nội dung trình bày trong quá trình cần cập nhật panel thì phải tuân thủ quy tắc cập nhật panel trong hội thoại trước khi tiếp tục trình bày.
- Không gọi Task Manager chỉ để thay đổi các trường runtime_slots đã cho phép.
- Chỉ Dùng Task Manager khi cần sửa nội dung hoặc cấu trúc ngoài khả năng cập nhật
  của update_surface_state.
- Không được nói text anchor_id, effect_id, base_revision, JSON hay dữ liệu kỹ thuật; không nói về tool, sơ đồ, template hay placeholder trong lời nói.
""".strip()


def surface_context_message(panel_context: Mapping[str, Any]) -> str:
    """Format only the dynamic facts for the current rendered panel."""

    return "\n\n".join((
        "SURFACE CONTEXT — PANEL HIỆN TẠI. Đây là ngữ cảnh giao diện tin cậy, không phải lời người dùng.",
        "SURFACE:\n"
        + f"surface_id: {panel_context['surface_id']}\n"
        + f"base_revision: {panel_context['revision']}",
        "VISUAL STAGE MAP:\n" + str(panel_context["visual_stage_map"]),
        "RUNTIME SLOTS (current state and allowed fields):\n"
        + json.dumps(panel_context.get("runtime_slots", {}), ensure_ascii=False),
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
