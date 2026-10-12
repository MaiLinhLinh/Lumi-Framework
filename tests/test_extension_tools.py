import tempfile
import unittest
from pathlib import Path

from final_framework.extension_tools import main


class ExtensionToolsTests(unittest.TestCase):
    def test_scaffolded_widget_and_effect_validate_in_an_empty_root(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "extensions"
            self.assertEqual(main(["--extensions-root", str(root), "create-widget", "sample_widget"]), 0)
            self.assertEqual(main(["--extensions-root", str(root), "create-effect", "sample_effect"]), 0)
            self.assertEqual(main(["--extensions-root", str(root), "validate"]), 0)
            self.assertTrue((root / "widgets" / "sample_widget" / "tests" / "README.md").is_file())
            self.assertTrue((root / "effects" / "sample_effect" / "tests" / "README.md").is_file())

    def test_scaffold_refuses_invalid_or_existing_ids(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "extensions"
            self.assertEqual(main(["--extensions-root", str(root), "create-widget", "bad-id"]), 2)
            self.assertEqual(main(["--extensions-root", str(root), "create-widget", "sample"]), 0)
            self.assertEqual(main(["--extensions-root", str(root), "create-widget", "sample"]), 2)


if __name__ == "__main__":
    unittest.main()
