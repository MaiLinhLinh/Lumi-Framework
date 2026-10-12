# object_group

Supports two rendering modes:

- `asset_id + count`: repeats one catalog asset and exposes a group anchor plus
  one anchor for each rendered item.
- `media_id + count`: renders one trusted, already-composed image of the whole
  group. `count` remains semantic data and the widget exposes only the group
  anchor; it does not duplicate the image.

Exactly one of `asset_id` or `media_id` is required.
