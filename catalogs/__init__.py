"""Loaders for shared resources exposed to the framework."""
from .assets import AssetCatalog, AssetCatalogError, AssetDescriptor, load_asset_catalog
from .layout_templates import (
    LayoutTemplate,
    LayoutTemplateError,
    TemplateComponentContract,
    TemplateBinding,
    TemplateSpec,
)
from .templates import (
    TemplateCatalog,
    TemplateCatalogEntry,
    TemplateCatalogError,
    load_template_catalog,
)
from .resources import SharedResourceError, SharedResourceRegistry, SharedResources

__all__ = [
    "AssetCatalog",
    "AssetCatalogError",
    "AssetDescriptor",
    "LayoutTemplate",
    "LayoutTemplateError",
    "TemplateComponentContract",
    "TemplateBinding",
    "TemplateSpec",
    "TemplateCatalog",
    "TemplateCatalogEntry",
    "TemplateCatalogError",
    "load_asset_catalog",
    "load_template_catalog",
    "SharedResourceError",
    "SharedResourceRegistry",
    "SharedResources",
]
