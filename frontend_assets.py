"""Resolve only local Vite outputs; never serve TSX or legacy JS sources."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any


class FrontendBuild:
    def __init__(self, root: Path):
        self.root = root.resolve()
        self.frontend = self.root / "build" / "frontend"
        self.index = self.root / "build" / "openui" / "extension-index.json"

    def browser_catalog(self, source: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
        mapping = json.loads(self.index.read_text(encoding="utf-8"))["source_to_output"]
        result: dict[str, list[dict[str, Any]]] = {"widgets": [], "effects": []}
        for kind, field in (("widgets", "renderer"), ("effects", "handler")):
            for entry in source.get(kind, []):
                source_path = "extensions/" + entry[field].removeprefix("/extensions/")
                if source_path not in mapping:
                    continue
                built = {**entry, field: self._url(mapping[source_path])}
                if "styles" in entry:
                    style_path = "extensions/" + entry["styles"].removeprefix("/extensions/")
                    built["styles"] = self._url(mapping[style_path])
                result[kind].append(built)
        return result

    def _url(self, value: str) -> str:
        if not value.startswith("/assets/"):
            raise ValueError("Build output must use /assets/.")
        self.asset(value.removeprefix("/assets/"))
        return value

    def asset(self, relative: str) -> Path:
        path = (self.frontend / relative).resolve()
        path.relative_to(self.frontend.resolve())
        if not path.is_file():
            raise FileNotFoundError(path)
        return path
