"""Isolated test settings for the recovery rehearsal."""

import os

from zproject.test_settings import *  # noqa: F403

DATABASES["default"] = DATABASES["default"].copy()  # noqa: F405
DATABASES["default"].update(  # noqa: F405
    {
        "NAME": os.environ["GROW_TEAM_RECOVERY_DB_NAME"],
        "PASSWORD": os.environ["GROW_TEAM_RECOVERY_DB_PASSWORD"],
        "PORT": int(os.environ["GROW_TEAM_RECOVERY_DB_PORT"]),
    }
)
