"""Spaces a line never breaks at: inside a number and between it and its unit.

Staging -145 (2026-11-21): the cover of a deck read "campus de 3" / "000
personnes", the number cut at its thousands separator. A number written with
spaced thousands ("3 000", "12 400 €") and a number with its unit ("20 %",
"12 400 €", "3 km") are bound with a no-break space (U+00A0, which the
content face draws) where the file is written: decks, documents and PDF
pages. The content keeps the text as typed; a workbook keeps its cells as
typed, since its source rows are compared cell by cell.
"""

from __future__ import annotations

import re

NBSP = " "
# A breakable space: any whitespace but the no-break space.
BREAK = re.compile(r"[^\S ]+")
_GROUP = re.compile(r"(?<=\d)[   ](?=\d{3}(?!\d))")
UNITS = (
    r"%|‰|€|\$|£|¥|°C|°F|°|k€|M€|Md€|kWh|MWh|GWh|kW|MW|W|km/h|km|cm|mm|m²|m³|m"
    r"|kg|g|t|h|min|s|ms|ko|Mo|Go|To|kB|KB|MB|GB|TB|EUR|USD|GBP|CHF"
)
_UNIT = re.compile(rf"(?<=\d)[   ](?=(?:{UNITS})(?![\w]))")


def bound(text: str) -> str:
    """The text with its number groups and number-unit pairs bound."""
    return _UNIT.sub(NBSP, _GROUP.sub(NBSP, text))


def words(text: str) -> list[str]:
    """The pieces a line may break between (a bound number is one piece)."""
    return [piece for piece in BREAK.split(text) if piece]


def unbound(text: str) -> list[str]:
    """Number groups or number-unit pairs still joined by a breakable space."""
    return [
        text[max(0, m.start() - 6) : m.end() + 4].strip()
        for pattern in (_GROUP, _UNIT)
        for m in pattern.finditer(text)
    ]
