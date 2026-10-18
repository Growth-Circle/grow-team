"""DB-free tests for the Grow Team Indonesian translation guard."""

from __future__ import annotations

import importlib.machinery
import importlib.util
import tempfile
import unittest
from pathlib import Path
from typing import Any, ClassVar
from unittest import mock

import orjson
from typing_extensions import override

ROOT = Path(__file__).resolve().parents[3]
SCRIPT_PATH = ROOT / "tools/grow-team/check-id-translations"

PO_HEADER = 'msgid ""\nmsgstr ""\n"Content-Type: text/plain; charset=UTF-8\\n"\n\n'


def load_module() -> Any:
    loader = importlib.machinery.SourceFileLoader("check_id_translations", str(SCRIPT_PATH))
    spec = importlib.util.spec_from_loader("check_id_translations", loader)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


class CheckIdTranslationsTest(unittest.TestCase):
    tool: ClassVar[Any]

    @classmethod
    @override
    def setUpClass(cls) -> None:
        cls.tool = load_module()

    def make_root(
        self,
        tmp: str,
        *,
        catalog: dict[str, str] | None = None,
        po: str = "",
        pending: dict[str, dict[str, dict[str, str]]] | None = None,
    ) -> Path:
        root = Path(tmp)
        (root / "locale/id/LC_MESSAGES").mkdir(parents=True)
        (root / "locale/id/pending").mkdir()
        (root / "locale/id/translations.json").write_bytes(orjson.dumps(catalog or {}))
        (root / "locale/id/LC_MESSAGES/django.po").write_text(PO_HEADER + po, encoding="utf-8")
        for name, content in (pending or {}).items():
            (root / f"locale/id/pending/{name}.json").write_bytes(orjson.dumps(content))
        return root

    def write(self, root: Path, rel_path: str, text: str) -> str:
        path = root / rel_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return rel_path

    def test_template_string_without_translation_is_reported(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = self.make_root(tmp, catalog={"Save": "Simpan"})
            path = self.write(root, "a.hbs", '<b>{{t "Save"}}</b> {{t "Archive"}}')
            self.assertEqual(self.tool.untranslated_strings(root, [path]), [(path, "Archive")])

    def test_empty_catalog_translation_counts_as_missing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = self.make_root(tmp, catalog={"Save": ""})
            path = self.write(root, "a.hbs", '{{t "Save"}}')
            self.assertEqual(self.tool.untranslated_strings(root, [path]), [(path, "Save")])

    def test_pending_file_counts_as_translated(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = self.make_root(
                tmp,
                pending={
                    "v1": {"frontend": {"Archive": "Arsipkan"}, "backend": {"Gone.": "Hilang."}}
                },
            )
            hbs = self.write(root, "a.hbs", '{{t "Archive"}}')
            py = self.write(
                root, "a.py", 'from django.utils.translation import gettext as _\n_("Gone.")\n'
            )
            self.assertEqual(self.tool.untranslated_strings(root, [hbs, py]), [])

    def test_a_pending_string_is_for_its_own_side_only(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = self.make_root(tmp, pending={"v1": {"backend": {"Archive": "Arsipkan"}}})
            path = self.write(root, "a.hbs", '{{t "Archive"}}')
            self.assertEqual(self.tool.untranslated_strings(root, [path]), [(path, "Archive")])

    def test_python_strings(self) -> None:
        source = (
            "from django.utils.translation import gettext as _, ngettext\n"
            "_('One')\n"
            "x = _('Two {n}').format(n=1)\n"
            "ngettext('{n} file', '{n} files', 2)\n"
            "other('Not a message')\n"
            "_(variable)\n"
        )
        self.assertEqual(
            self.tool.python_strings(source), ["One", "Two {n}", "{n} file", "{n} files"]
        )

    def test_backend_string_checked_against_django_po(self) -> None:
        po = 'msgid "Done."\nmsgstr "Selesai."\n\nmsgid "Fuzzy."\nmsgstr "Kabur."\n'
        po = po.replace('msgid "Fuzzy."', '#, fuzzy\nmsgid "Fuzzy."')
        with tempfile.TemporaryDirectory() as tmp:
            root = self.make_root(tmp, po=po)
            path = self.write(
                root,
                "a.py",
                "from django.utils.translation import gettext as _\n_('Done.')\n_('Fuzzy.')\n_('New.')\n",
            )
            self.assertEqual(
                self.tool.untranslated_strings(root, [path]), [(path, "Fuzzy."), (path, "New.")]
            )

    def test_typescript_strings_are_checked(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = self.make_root(tmp, catalog={"Hello": "Halo"})
            path = self.write(root, "a.ts", "")
            with mock.patch.object(
                self.tool, "typescript_strings", return_value={path: ["Hello", "Goodbye"]}
            ):
                self.assertEqual(self.tool.untranslated_strings(root, [path]), [(path, "Goodbye")])

    def test_a_string_without_words_needs_no_translation(self) -> None:
        for text in ["·", "{count}", "{who} · # {room}", "12", "→"]:
            self.assertFalse(self.tool.has_words(text), text)
        self.assertTrue(self.tool.has_words("{count} tasks"))

    def test_only_listed_paths_are_checked(self) -> None:
        patterns = ["web/src/task_board*.ts", "web/templates/needs_view.hbs"]
        self.assertTrue(self.tool.is_checked("web/src/task_board_ui.ts", patterns))
        self.assertTrue(self.tool.is_checked("web/templates/needs_view.hbs", patterns))
        self.assertFalse(self.tool.is_checked("web/src/compose.ts", patterns))

    def test_repository_path_list_names_real_files(self) -> None:
        for pattern in self.tool.read_patterns(ROOT):
            if "*" not in pattern:
                self.assertTrue((ROOT / pattern).exists(), pattern)
            else:
                self.assertTrue(list(ROOT.glob(pattern)), pattern)

    def test_main_exit_code(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = self.make_root(tmp, catalog={"Save": "Simpan"})
            self.write(root, "tools/grow-team/id-translation-paths.txt", "# ours\n*.hbs\n")
            self.write(root, "ok.hbs", '{{t "Save"}}')
            bad = self.write(root, "bad.hbs", '{{t "Archive"}}')
            self.write(root, "skipped.txt", "not checked")
            with mock.patch("sys.argv", ["check-id-translations", "--root", tmp, "ok.hbs"]):
                self.assertEqual(self.tool.main(), 0)
            with mock.patch("sys.argv", ["check-id-translations", "--root", tmp, bad]):
                self.assertEqual(self.tool.main(), 1)
            with mock.patch("sys.argv", ["check-id-translations", "--root", tmp, "skipped.txt"]):
                self.assertEqual(self.tool.main(), 0)


if __name__ == "__main__":
    unittest.main()
