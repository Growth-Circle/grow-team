"""DB-free tests for the Grow Team internal doc-link checker."""

from __future__ import annotations

import importlib.util
import tempfile
import unittest
from pathlib import Path
from typing import Any, ClassVar

from typing_extensions import override

ROOT = Path(__file__).resolve().parents[3]
SCRIPT_PATH = ROOT / "tools/grow-team/check-doc-links.py"


def load_module() -> Any:
    spec = importlib.util.spec_from_file_location("check_doc_links", SCRIPT_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class CheckDocLinksTest(unittest.TestCase):
    tool: ClassVar[Any]

    @classmethod
    @override
    def setUpClass(cls) -> None:
        cls.tool = load_module()

    def write(self, root: Path, rel_path: str, text: str) -> Path:
        path = root / rel_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return path

    def test_valid_link_and_anchor_pass(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.write(root, "target.md", "# Judul target\n\nIsi.\n")
            self.write(
                root,
                "source.md",
                "Lihat [target](target.md) dan [bagian](target.md#judul-target).\n",
            )
            self.assertEqual(self.tool.check(root), [])

    def test_missing_file_is_reported(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.write(root, "source.md", "Lihat [hilang](tidak-ada.md).\n")
            broken = self.tool.check(root)
            self.assertEqual(len(broken), 1)
            self.assertIn("broken link -> tidak-ada.md", broken[0])

    def test_missing_anchor_is_reported(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.write(root, "target.md", "# Judul target\n\nIsi.\n")
            self.write(root, "source.md", "Lihat [bagian](target.md#tidak-ada).\n")
            broken = self.tool.check(root)
            self.assertEqual(len(broken), 1)
            self.assertIn("broken anchor -> target.md#tidak-ada", broken[0])

    def test_same_file_anchor_checked_against_own_headings(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.write(
                root,
                "source.md",
                "# Bagian satu\n\nLihat [balik](#bagian-satu) dan [hilang](#tidak-ada).\n",
            )
            broken = self.tool.check(root)
            self.assertEqual(len(broken), 1)
            self.assertIn("broken anchor -> #tidak-ada", broken[0])

    def test_external_and_anchor_only_links_are_skipped(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.write(
                root,
                "source.md",
                "# Judul\n\n"
                "[web](https://example.com/tidak-ada) dan "
                "[email](mailto:a@example.com) dan [sini](#judul).\n",
            )
            self.assertEqual(self.tool.check(root), [])

    def test_link_inside_code_fence_is_skipped(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.write(
                root,
                "source.md",
                "# Judul\n\n```\n[contoh](tidak-ada.md)\n```\n",
            )
            self.assertEqual(self.tool.check(root), [])

    def test_duplicate_headings_get_numbered_slugs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.write(root, "target.md", "# Ulang\n\nSatu.\n\n# Ulang\n\nDua.\n")
            self.write(
                root,
                "source.md",
                "[pertama](target.md#ulang) dan [kedua](target.md#ulang-1).\n",
            )
            self.assertEqual(self.tool.check(root), [])


if __name__ == "__main__":
    unittest.main()
