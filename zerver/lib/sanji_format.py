from datetime import date

from django.utils.dates import MONTHS_3
from django.utils.translation import gettext as _
from django.utils.translation import override as override_language


def format_date_short(day: date, language: str) -> str:
    """A calendar day without its year, as the web app's sanji_format.ts
    writes it: "14 Okt" in Indonesian, "Oct 14" in English."""
    with override_language(language):
        month = str(MONTHS_3[day.month]).capitalize()
        # The order of day and month is the translator's to set.
        return _("{month} {day}").format(month=month, day=day.day)
