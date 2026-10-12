"""Test helper that constructs the same widget registry as the web application."""

from pathlib import Path

from final_framework.extension_loader import ExtensionLoader
from final_framework.widgets import WidgetRegistry


def runtime_widget_registry() -> WidgetRegistry:
    root = Path(__file__).resolve().parents[1]
    loaded = ExtensionLoader(root / "extensions").load()
    return loaded.widget_registry
