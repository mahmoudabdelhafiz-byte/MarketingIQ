"""MarketingIQ."""

import os
import sys


def _repair_environment() -> None:
    """Re-read non-ASCII settings that a non-UTF-8 locale decoded as surrogates.

    Under a C/POSIX locale (cPanel Passenger, cron) Python decodes environment variables with
    ``surrogateescape``, so a setting such as an Arabic ``SMTP_FROM_NAME`` turns into lone
    surrogates and fails later with "surrogates not allowed". The original bytes are UTF-8, so
    they are recovered here once, when the package is first imported.
    """

    if sys.platform == "win32":
        return
    for key, value in list(os.environ.items()):
        if any("\udc80" <= char <= "\udcff" for char in value):
            try:
                os.environ[key] = value.encode("utf-8", "surrogateescape").decode("utf-8")
            except UnicodeError:
                continue


_repair_environment()
