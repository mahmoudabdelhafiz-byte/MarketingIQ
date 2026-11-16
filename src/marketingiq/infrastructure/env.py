"""Read text settings that may contain non-ASCII characters.

cPanel Passenger can start Python without a UTF-8 locale. Environment variables are then decoded
as ASCII with ``surrogateescape``: a sender name such as "Sales – Barmageyat" (en dash) or an
Arabic name arrives as lone surrogates and fails later with "surrogates not allowed". Under that
locale the repaired text cannot be written back into ``os.environ`` either, so it is decoded here,
each time the setting is read.
"""

from __future__ import annotations

import os


def env_text(name: str, default: str | None = None) -> str | None:
    value = os.environ.get(name)
    if value is None:
        return default
    return repair_text(value)


def repair_text(value: str) -> str:
    if not any("\udc80" <= char <= "\udcff" for char in value):
        return value
    raw = value.encode("utf-8", "surrogateescape")
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return raw.decode("utf-8", "replace")
