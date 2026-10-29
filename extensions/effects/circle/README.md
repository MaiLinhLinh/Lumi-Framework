# circle

Khoanh vùng DOM mà Runtime đã xác minh từ `anchor_id`.

`run(context, command)` chỉ dựng ellipse vào `context.overlay` và trả cleanup để
`AnimationController` gỡ nó khi effect bị thay, lượt bị ngắt hoặc revision đổi.
