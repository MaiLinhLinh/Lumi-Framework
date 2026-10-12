"""The independent registry composition must not depend on old domains."""

import unittest
from pathlib import Path

from final_framework.registry_composition import load_framework_registries


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class RegistryCompositionTests(unittest.TestCase):
    def test_loads_copied_widget_template_and_extension_registries(self) -> None:
        registries = load_framework_registries(PROJECT_ROOT)

        self.assertTrue(registries.widget_registry.widget_ids())
        self.assertTrue(registries.template_catalog().entries)
        self.assertIn("widgets", registries.extensions.browser_catalog(url_prefix="/extensions"))
        self.assertIsNone(registries.knowledge_registry)


if __name__ == "__main__":
    unittest.main()
