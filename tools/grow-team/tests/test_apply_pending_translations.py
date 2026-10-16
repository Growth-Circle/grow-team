"""DB-free tests for the pending-translations installer."""

from __future__ import annotations

import importlib.machinery
import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any, ClassVar

import orjson
import polib
from typing_extensions import override

ROOT = Path(__file__).resolve().parents[3]
SCRIPT_PATH = ROOT / "tools/grow-team/apply-pending-translations"

PO_HEADER = """msgid ""
msgstr ""
"Language: id\\n"
"Content-Type: text/plain; charset=UTF-8\\n"
"""

PO_ENTRIES = """
#: zerver/lib/a.py
msgid "Already done"
msgstr "Sudah selesai"

#: zerver/lib/a.py
#, fuzzy, python-brace-format
msgid "Days: {days}"
msgstr ""

#: zerver/lib/a.py
msgid "Empty one"
msgstr ""
"""


def load_module() -> Any:
    loader = importlib.machinery.SourceFileLoader("apply_pending_translations", str(SCRIPT_PATH))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    loader.exec_module(module)
    return module


class ApplyPendingTranslationsTest(unittest.TestCase):
    tool: ClassVar[Any]

    @classmethod
    @override
    def setUpClass(cls) -> None:
        cls.tool = load_module()

    @override
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.locale_dir = Path(tmp.name) / "locale"
        (self.locale_dir / "id" / "LC_MESSAGES").mkdir(parents=True)
        self.json_path = self.locale_dir / "id" / "translations.json"
        self.po_path = self.locale_dir / "id" / "LC_MESSAGES" / "django.po"
        self.json_path.write_bytes(
            b'{\n  "Apple": "Apel",\n  "Cherry": "",\n  "Zebra": "Zebra"\n}\n'
        )
        self.po_path.write_text(PO_HEADER + PO_ENTRIES, encoding="utf-8")
        self.pending_path = Path(tmp.name) / "pending.json"

    def run_tool(self, pending: dict[str, Any], *extra: str) -> int:
        self.pending_path.write_bytes(orjson.dumps(pending))
        return int(
            self.tool.main(["--locale-dir", str(self.locale_dir), *extra, str(self.pending_path)])
        )

    def test_frontend_inserts_in_sorted_position_and_fills_empty(self) -> None:
        pending = {"frontend": {"Banana": "Pisang", "Cherry": "Ceri"}, "backend": {}}
        self.assertEqual(self.run_tool(pending), 0)
        self.assertEqual(
            self.json_path.read_text(),
            '{\n  "Apple": "Apel",\n  "Banana": "Pisang",\n  "Cherry": "Ceri",\n'
            '  "Zebra": "Zebra"\n}\n',
        )

    def test_backend_adds_fills_and_clears_fuzzy(self) -> None:
        pending = {
            "frontend": {},
            "backend": {
                "Days: {days}": "{days} hari",
                "Empty one": "Kosong",
                "New {count} thing": "{count} hal baru",
            },
        }
        self.assertEqual(self.run_tool(pending), 0)
        po = polib.pofile(str(self.po_path))
        entries = {entry.msgid: entry for entry in po}
        self.assertEqual(entries["Already done"].msgstr, "Sudah selesai")
        self.assertEqual(entries["Days: {days}"].msgstr, "{days} hari")
        self.assertEqual(entries["Days: {days}"].flags, ["python-brace-format"])
        self.assertEqual(entries["Empty one"].msgstr, "Kosong")
        self.assertEqual(entries["New {count} thing"].msgstr, "{count} hal baru")
        self.assertEqual(entries["New {count} thing"].flags, ["python-brace-format"])
        self.assertEqual(po.fuzzy_entries(), [])

    def test_second_run_changes_nothing(self) -> None:
        pending = {"frontend": {"Banana": "Pisang"}, "backend": {"Empty one": "Kosong"}}
        self.run_tool(pending)
        json_after = self.json_path.read_bytes()
        po_after = self.po_path.read_bytes()
        self.assertEqual(self.run_tool(pending), 0)
        self.assertEqual(self.json_path.read_bytes(), json_after)
        self.assertEqual(self.po_path.read_bytes(), po_after)

    def test_conflict_is_skipped_unless_forced(self) -> None:
        pending = {"frontend": {"Apple": "Jeruk"}, "backend": {"Already done": "Selesai"}}
        self.assertEqual(self.run_tool(pending), 1)
        self.assertIn('"Apple": "Apel"', self.json_path.read_text())
        self.assertIn('msgstr "Sudah selesai"', self.po_path.read_text())

        self.assertEqual(self.run_tool(pending, "--force"), 0)
        self.assertIn('"Apple": "Jeruk"', self.json_path.read_text())
        self.assertIn('msgstr "Selesai"', self.po_path.read_text())

    def test_dry_run_writes_nothing(self) -> None:
        before = (self.json_path.read_bytes(), self.po_path.read_bytes())
        pending = {"frontend": {"Banana": "Pisang"}, "backend": {"Empty one": "Kosong"}}
        self.assertEqual(self.run_tool(pending, "--dry-run"), 0)
        self.assertEqual((self.json_path.read_bytes(), self.po_path.read_bytes()), before)

    def test_placeholder_mismatch_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "placeholders differ"):
            self.run_tool({"frontend": {"{count} tasks": "{n} tugas"}, "backend": {}})
