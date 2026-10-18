from datetime import date

from zerver.lib.sanji_format import format_date_short
from zerver.lib.test_classes import ZulipTestCase


class SanjiFormatTest(ZulipTestCase):
    def test_format_date_short(self) -> None:
        day = date(2026, 10, 14)
        self.assertEqual(format_date_short(day, "en"), "Oct 14")
        self.assertEqual(format_date_short(day, "id"), "14 Okt")

    def test_format_date_short_names_every_month(self) -> None:
        self.assertEqual(
            [format_date_short(date(2026, month, 1), "id") for month in range(1, 13)],
            [
                "1 Jan",
                "1 Feb",
                "1 Mar",
                "1 Apr",
                "1 Mei",
                "1 Jun",
                "1 Jul",
                "1 Agu",
                "1 Sep",
                "1 Okt",
                "1 Nov",
                "1 Des",
            ],
        )
