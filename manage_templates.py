"""Small CLI for deleting generated reusable layout templates safely.

Run this file from the ``lumi_openui`` directory.  It always changes the
layout JSON and its catalog record through TemplateCatalog, never separately.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(PROJECT_ROOT.parent))

from lumi_openui.catalogs.templates import (  # noqa: E402
    TemplateCatalog,
    TemplateCatalogError,
    load_template_catalog,
)


_GENERATED_TEMPLATE_ID = re.compile(r"tm[1-9][0-9]*\Z")


def _load_catalog() -> TemplateCatalog:
    resource_root = PROJECT_ROOT / "resources"
    return load_template_catalog(
        catalog_path=resource_root / "templates" / "catalog.json",
        resource_root=resource_root,
    )


def _generated_ids(catalog: TemplateCatalog) -> list[str]:
    return [entry.id for entry in catalog.entries if _GENERATED_TEMPLATE_ID.fullmatch(entry.id)]


def _delete(catalog: TemplateCatalog, template_id: str) -> TemplateCatalog:
    if not _GENERATED_TEMPLATE_ID.fullmatch(template_id):
        raise ValueError("Chỉ được xoá template tự sinh có ID dạng tm1, tm2, ...")
    if not catalog.contains(template_id):
        raise ValueError(f"Không có template '{template_id}' trong catalog dùng chung.")
    updated = catalog.delete_layout_template(template_id)
    print(f"Đã xoá {template_id} khỏi catalog dùng chung.")
    return updated


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Quản lý template layout tự sinh của Lumi.",
    )
    commands = parser.add_subparsers(dest="command", required=True)

    delete = commands.add_parser("delete", help="Xoá một template tự sinh, ví dụ tm2.")
    delete.add_argument("template_id", help="ID template tự sinh, ví dụ tm2.")

    clear = commands.add_parser("clear-generated", help="Xoá toàn bộ template tự sinh tmN trong catalog dùng chung.")

    listing = commands.add_parser("list", help="Liệt kê template tự sinh tmN trong catalog dùng chung.")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    try:
        catalog = _load_catalog()
        if args.command == "list":
            template_ids = _generated_ids(catalog)
            print("\n".join(template_ids) if template_ids else "Không có template tự sinh.")
            return 0
        if args.command == "delete":
            _delete(catalog, args.template_id)
            return 0
        if args.command == "clear-generated":
            template_ids = _generated_ids(catalog)
            if not template_ids:
                print("Không có template tự sinh để xoá.")
                return 0
            for template_id in template_ids:
                catalog = _delete(catalog, template_id)
            print(f"Đã xoá {len(template_ids)} template tự sinh.")
            return 0
    except (TemplateCatalogError, ValueError) as exc:
        print(f"Lỗi: {exc}", file=sys.stderr)
        return 2
    raise AssertionError(f"Lệnh chưa xử lý: {args.command}")


if __name__ == "__main__":
    raise SystemExit(main())
