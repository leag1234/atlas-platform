"""A figure keeps what it counts in its source, in every deliverable.

Staging -184 (offline judge, class C7): the person's notes said "application de
covoiturage pour les salariés d'un campus de 3 000 personnes"; the deck said
"campus de 3 000 salariés". The value is right and the noun is a word of the
notes, but it counts something else: the campus has 3 000 people, the
employees are the app's users, their number is not given. figure_units.py
checks units and currencies only; this is the same check for counted nouns.

Code only (no model call). A figure of the file is a mismatch when:
- its value is in the sources (the request, the earlier messages, the
  documents provided or named), each time followed by a noun;
- the noun after it in the file is none of those nouns;
- and that noun is a word of the sources elsewhere: a noun the sources never
  use is a rewording or a translation ("3,000 people" from "3 000
  personnes"), not a value moved to another noun of the notes.
Units, currencies and magnitudes are left to figure_units.py; years, months
and percentages are not counted nouns.
"""

from __future__ import annotations

import re
import unicodedata

from services.orchestrator.figure_units import (
    CURRENCIES,
    MAGNITUDES,
    _UNIT_WORD,
    _values,
)

_NUMBER = r"\d{1,3}(?:[   .,’']\d{3})++(?:[.,]\d+)?+|\d++(?:[.,]\d+)?+"
# A figure, then the word that follows it (the noun it counts).
_COUNTED = re.compile(
    rf"(?<![\w.,])(?P<number>{_NUMBER})(?![\w])\s++(?P<noun>[^\W\d_]{{4,30}}+)"
)
_WORD = re.compile(r"[^\W\d_]{4,30}+")
_YEAR = re.compile(r"(?:1[89]|20)\d\d")
# Words that follow a figure without being what it counts.
_NOT_NOUNS = frozenset(
    """
    janvier fevrier mars avril juin juillet aout septembre octobre novembre
    decembre january february march april june july august september october
    november december januar februar marz juni juli oktober dezember enero
    febrero marzo abril mayo junio julio agosto septiembre octubre noviembre
    diciembre gennaio febbraio aprile maggio giugno luglio settembre ottobre
    dicembre pour cent percent prozent ainsi dont plus moins about over more
    than with from pour avec dans entre jusqu
    """.split()
)
STEM = 5
_CURRENCY_WORDS = frozenset(form for forms in CURRENCIES.values() for form in forms)


def _plain(word: str) -> str:
    decomposed = unicodedata.normalize("NFKD", word.casefold())
    return "".join(c for c in decomposed if not unicodedata.combining(c))


def _stem(word: str) -> str:
    return _plain(word)[:STEM]


def _counted(text: str) -> list[tuple[str, frozenset[float], str]]:
    """(figure as written, its values, the noun after it) of a text."""
    text = unicodedata.normalize("NFC", text)
    found = []
    for match in _COUNTED.finditer(text):
        number, noun = match.group("number"), match.group("noun")
        lowered = noun.casefold()
        if (
            _plain(noun) in _NOT_NOUNS
            or lowered in _UNIT_WORD
            or lowered in _CURRENCY_WORDS
            or noun in MAGNITUDES
            or lowered in MAGNITUDES
            or _YEAR.fullmatch(number)
        ):
            continue
        found.append((match.group(0), _values(number, None), noun))
    return found


def mismatches(file_texts: list[str], source_texts: list[str]) -> list[str]:
    """Figures of the file attached to another noun of the sources than there.

    Each entry quotes the file's figure and the source's: "3 000 salariés"
    (the source has "3 000 personnes").
    """
    nouns: dict[float, set[str]] = {}
    written: dict[float, str] = {}
    words: set[str] = set()
    for text in source_texts:
        words.update(_stem(word) for word in _WORD.findall(text))
        for figure, values, noun in _counted(text):
            for value in values:
                nouns.setdefault(value, set()).add(_stem(noun))
                written.setdefault(value, figure)
    result: list[str] = []
    for text in file_texts:
        for figure, values, noun in _counted(text):
            held = [value for value in values if value in nouns]
            if not held:
                continue
            stem = _stem(noun)
            if any(stem in nouns[value] for value in held):
                continue
            if stem not in words:
                continue
            entry = (
                f"“{' '.join(figure.split())}” (the source has "
                f"“{' '.join(written[held[0]].split())}”)"
            )
            if entry not in result:
                result.append(entry)
    return result
