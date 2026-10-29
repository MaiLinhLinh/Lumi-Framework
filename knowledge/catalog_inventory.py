"""Describe indexed data to retrieval agents without running a vector search."""
from __future__ import annotations

import json
from typing import Any


def describe_inventory(collections: dict[str, Any], package_id: str) -> str:
    where = {"$and": [{"package_id": package_id}, {"ingest_state": "active"}]}
    inventory: dict[str, list[dict[str, Any]]] = {}
    for name in ("documents", "media"):
        result = collections[name].get(where=where, include=["metadatas"])
        inventory[name] = [
            {key: metadata[key] for key in ("title", "caption", "alt_text")
             if metadata.get(key)}
            for metadata in result["metadatas"]
        ]
    return (
        "Dữ liệu local đã nhập (không phải toàn bộ dữ liệu trên website nguồn): "
        + json.dumps(inventory, ensure_ascii=False)
        + ". Chỉ chọn kho nếu dữ liệu đã nhập có khả năng trả lời request; "
        "mảng rỗng nghĩa là chưa có dữ liệu loại đó."
    )
