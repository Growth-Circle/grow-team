"""Test the image check that proves libvips can write Zulip thumbnails."""

import ast
import unittest
from collections.abc import Iterable
from pathlib import Path
from typing import Any

try:
    import pyvips
except ImportError:  # pragma: no cover  # libvips is only installed in the app environment
    pyvips = None

CHECK_IMAGE = Path(__file__).parents[1] / "check_image.py"


def load_check() -> Any:
    # check_image.py boots Django at import time, so execute only the function.
    tree = ast.parse(CHECK_IMAGE.read_text())
    function = next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "check_thumbnail_formats"
    )
    namespace: dict[str, Any] = {"Any": Any, "Iterable": Iterable}
    exec(compile(ast.Module([function], []), str(CHECK_IMAGE), "exec"), namespace)  # noqa: S102
    return namespace["check_thumbnail_formats"]


class Webp:
    extension = "webp"
    max_width = 840
    max_height = 560
    opts = ""

    def __init__(self, *, animated: bool) -> None:
        self.animated = animated

    def __str__(self) -> str:
        return f"{self.max_width}x{self.max_height}{'-anim' if self.animated else ''}.webp"


class FakePyvips:
    """The real library, except that saving to WebP fails like a libvips built without it."""

    Error = pyvips.Error if pyvips else Exception

    def __getattr__(self, name: str) -> Any:
        return getattr(pyvips, name)

    @property
    def Image(self) -> Any:
        real_image = pyvips.Image
        error = self.Error

        class Image:
            def __init__(self, wrapped: Any) -> None:
                self.wrapped = wrapped

            def __getattr__(self, name: str) -> Any:
                return getattr(self.wrapped, name)

            def write_to_buffer(self, suffix: str) -> bytes:
                if suffix.startswith(".webp"):
                    raise error("unable to call VipsForeignSaveWebpTarget")
                return self.wrapped.write_to_buffer(suffix)

            @staticmethod
            def thumbnail_buffer(*args: Any, **kwargs: Any) -> Any:
                return Image(real_image.thumbnail_buffer(*args, **kwargs))

            black = staticmethod(real_image.black)
            arrayjoin = staticmethod(real_image.arrayjoin)

        return Image


@unittest.skipIf(pyvips is None, "pyvips is not installed")
class CheckThumbnailFormatsTest(unittest.TestCase):
    def test_passes_when_libvips_writes_still_and_animated_webp(self) -> None:
        load_check()(pyvips, [Webp(animated=True), Webp(animated=False)])

    def test_fails_when_libvips_cannot_save_webp(self) -> None:
        with self.assertRaises(SystemExit) as raised:
            load_check()(FakePyvips(), [Webp(animated=False)])

        self.assertIn(
            "840x560.webp: unable to call VipsForeignSaveWebpTarget", str(raised.exception)
        )


if __name__ == "__main__":
    unittest.main()
