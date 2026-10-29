# spotlight

Làm tối phần còn lại của panel và giữ anchor Runtime đã xác minh ở vùng sáng. `run()` tạo một
SVG path trên overlay rồi trả cleanup để `AnimationController` gỡ nó khi effect bị thay, lượt bị
ngắt hoặc panel đổi revision.
