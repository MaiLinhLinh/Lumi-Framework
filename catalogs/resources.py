"""Shared asset and layout-template resources for every Lumi experience."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .assets import AssetCatalog, AssetCatalogError, load_asset_catalog
from .templates import TemplateCatalog, TemplateCatalogError, load_template_catalog


class SharedResourceError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class SharedResources:
    assets: AssetCatalog
    templates: TemplateCatalog


class SharedResourceRegistry:
    """Load the one shared catalog; no category owns its files or entries."""

    def __init__(self, resource_root: Path) -> None:
        self._resource_root = resource_root.resolve()

    @property
    def resource_root(self) -> Path:
        return self._resource_root

    def load(self) -> SharedResources:
        try:
            return SharedResources(
                assets=load_asset_catalog(
                    catalog_path=self._resource_root / "assets" / "catalog.json",
                    resource_root=self._resource_root,
                ),
                templates=load_template_catalog(
                    catalog_path=self._resource_root / "templates" / "catalog.json",
                    resource_root=self._resource_root,
                ),
            )
        except (AssetCatalogError, TemplateCatalogError) as exc:
            raise SharedResourceError(str(exc)) from exc
