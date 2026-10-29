# object_group

Both `asset_id` and `media_id` display the entire image exactly `count` times.
`count` is the number of image instances, not the number of objects inside an image.
Each instance has its own anchor, reserved even while media is pending, plus a group anchor.

Task Manager must coordinate image instructions and count:
objects per image × count = intended total objects. For counting exercises, prefer
an image containing exactly one object and set count to the intended total.
An image containing three apples with count 1 shows three apples; with count 3
it shows nine apples. Assembly fills the image ID and preserves count.

Exactly one of `asset_id` or `media_id` is required.
