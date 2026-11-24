"""What an answer says about the file it delivered is held to the file.

A reply to a request for a document, a deck or a workbook states figures of
the file it just built: "no boxes to tick" over a page of four, "12 bullets"
over a file of nine, a word count the file does not have, "everything on one
page" over a workbook of three sheets, "tested" over a file nobody ran. The
writer reads its own draft, not the built file, so those statements are
guesses written as facts.

Code measures the built file (packages/doc_render/figures.py) and the answer
is held to it:

- a sentence that gives a figure (a number or a "no"/"one" and a unit: pages,
  slides, sheets, bullets, words, pictures, boxes to tick) that the file does
  not have goes, and one sentence written by code gives the measured figure
  of that unit instead;
- figures that agree with the file stay; limits ("at most 200 words") and
  statements about the request ("you asked for 5 slides") are not figures of
  the file and stay;
- a sentence that says the file was tested or verified goes unless a check
  ran and passed (``without_unproven_status``).

Numbers and units, in English, French, German, Spanish and Italian.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

# Unit words by figure. Matched whole, case-insensitive.
UNIT_WORDS: dict[str, str] = {
    "pages": r"pages?|seiten?|p[aá]ginas?|pagin[ae]",
    "slides": r"slides?|diapositives?|diapos?|folien|folie|diapositivas?|diapositive",
    "sheets": (
        r"sheets?|worksheets?|spreadsheets? tabs?|feuilles?|onglets?|tabellenbl[aä]tter"
        r"|tabellenblatt|bl[aä]tter|blatt|hojas?|fogli[o]?"
    ),
    "bullets": (
        r"bullets?|bullet[ -]points?|puces?|aufz[aä]hlungspunkt\w*|stichpunkt\w*"
        r"|vi[ñn]etas?|punti elenco|punti elenco puntato|elementi dell'elenco"
    ),
    "words": r"words?|mots?|w[oö]rter[n]?|wort|palabras?|parole|parola",
    "pictures": (
        r"pictures?|images?|photos?|illustrations?|bilder[n]?|bild|abbildung(?:en)?"
        r"|im[aá]genes|imagen|immagin[ei]|fotos?|fotografie|fotograf[ií]as?"
    ),
    "checkboxes": (
        r"check ?boxes?|tick ?boxes?|boxes to tick|case[s]? [aà] cocher|checkbox(?:en)?"
        r"|kontrollk[aä]stchen|ankreuzfeld\w*|casillas?(?: de verificaci[oó]n)?"
        r"|casella|caselle(?: di controllo| da spuntare)?"
    ),
}
_UNIT = {
    key: re.compile(rf"(?:{pattern})\b", re.I | re.U)
    for key, pattern in UNIT_WORDS.items()
}
_ANY_UNIT = "|".join(f"(?:{pattern})" for pattern in UNIT_WORDS.values())

NUMBER_WORDS = {
    "zero": 0,
    "no": 0,
    "none": 0,
    "aucun": 0,
    "aucune": 0,
    "kein": 0,
    "keine": 0,
    "keinen": 0,
    "ning[uú]n": 0,
    "ninguna": 0,
    "ninguno": 0,
    "nessun": 0,
    "nessuna": 0,
    "nessuno": 0,
    "sans": 0,
    "ohne": 0,
    "sin": 0,
    "senza": 0,
    "without": 0,
    "null": 0,
    "cero": 0,
    "pas de": 0,
    "non ci sono": 0,
    "non c['\u2019]\u00e8": 0,
    "alcun[ao]?": 0,
    "one": 1,
    "single": 1,
    "un": 1,
    "une": 1,
    "seule": 1,
    "seul": 1,
    "ein": 1,
    "eine": 1,
    "einen": 1,
    "einzige": 1,
    "einzigen": 1,
    "uno": 1,
    "una": 1,
    "sola": 1,
    "solo": 1,
    "unica": 1,
    "unico": 1,
    "two": 2,
    "deux": 2,
    "zwei": 2,
    "dos": 2,
    "due": 2,
    "three": 3,
    "trois": 3,
    "drei": 3,
    "tres": 3,
    "tre": 3,
    "four": 4,
    "quatre": 4,
    "vier": 4,
    "cuatro": 4,
    "quattro": 4,
    "five": 5,
    "cinq": 5,
    "f[uü]nf": 5,
    "cinco": 5,
    "cinque": 5,
    "six": 6,
    "sechs": 6,
    "seis": 6,
    "sei": 6,
    "seven": 7,
    "sept": 7,
    "sieben": 7,
    "siete": 7,
    "sette": 7,
    "eight": 8,
    "huit": 8,
    "acht": 8,
    "ocho": 8,
    "otto": 8,
    "nine": 9,
    "neuf": 9,
    "neun": 9,
    "nueve": 9,
    "nove": 9,
    "ten": 10,
    "dix": 10,
    "zehn": 10,
    "diez": 10,
    "dieci": 10,
    "eleven": 11,
    "onze": 11,
    "elf": 11,
    "once": 11,
    "undici": 11,
    "twelve": 12,
    "douze": 12,
    "zw[oö]lf": 12,
    "doce": 12,
    "dodici": 12,
}
# "sin" and "sei" are number words only before a unit; "una" and "un" too.
_NUMBER_WORD = re.compile("|".join(sorted(NUMBER_WORDS, key=len, reverse=True)), re.I)
_DIGITS = r"\d{1,3}(?:[   .,]\d{3})+|\d+(?:[.,]\d+)?"
APPROXIMATE = (
    r"about|around|approximately|approx\.?|roughly|nearly|almost|circa|ca\.|environ"
    r"|presque|etwa|rund|ungef[aä]hr|fast|unos|alrededor de|aproximadamente|casi"
    r"|quasi|pi[uù] o meno|~"
)
LIMIT = (
    r"at most|at least|up to|under|over|fewer than|more than|less than|maximum|max"
    r"|minimum|min|moins de|plus de|jusqu'[aà]|au moins|au plus|h[oö]chstens|mindestens"
    r"|weniger als|mehr als|bis zu|menos de|m[aá]s de|al menos|como m[aá]ximo|hasta"
    r"|meno di|pi[uù] di|almeno|al massimo|fino a|no more than|no fewer than"
)
CLAIM = re.compile(
    rf"(?<![\w.,])(?P<limit>(?:{LIMIT})\s+)?(?P<approx>(?:{APPROXIMATE})\s*)?"
    rf"(?P<number>{_DIGITS}|(?:{_NUMBER_WORD.pattern})(?![\w]))"
    rf"(?P<between>(?:\s+[^\W\d_]+(?:['’-][^\W\d_]+)*){{0,2}}?)\s+"
    rf"(?P<unit>{_ANY_UNIT})\b(?P<after>\s+(?:per|par|pro|por|each|chaque)\b)?",
    re.I | re.U,
)
# A sentence about the request, the sources or one part of the file, or that
# distributes a figure over parts ("one page per sheet"), is not a total.
_SKIP = re.compile(
    r"\b(?:asked|requested|wanted|requires?|required|limit|target|source|sources|"
    r"each|every|per|demand[eé]e?s?|souhait[eé]e?s?|chaque|par|gew[uü]nscht|"
    r"verlangt|jede[rsmn]?|pro|pedid[oa]s?|cada|richiest[oae]|ogni|"
    r"quoted|attached|provided|given)\b",
    re.I,
)
_ALL = re.compile(
    r"\b(?:all|everything|whole|entire|tout|toute|tous|ensemble|alle|alles|gesamte|"
    r"todo|toda|todos|completo|tutto|tutta|tutti|intero)\b",
    re.I,
)
_SENTENCES = re.compile(r"\S.*?(?:[.!?…]+(?=\s|$)|\Z)", re.S)
_VERBATIM = ("```", "|", "    ", "#")


def _value(text: str) -> int | float | None:
    token = text.strip()
    if token.isdigit() or re.fullmatch(_DIGITS, token):
        cleaned = re.sub(r"[   ]", "", token)
        if re.fullmatch(r"\d{1,3}(?:[.,]\d{3})+", cleaned):
            return int(re.sub(r"[.,]", "", cleaned))
        return float(cleaned.replace(",", "."))
    for pattern, value in NUMBER_WORDS.items():
        if re.fullmatch(pattern, token, re.I):
            return value
    return None


def _holds(unit: str, wanted: float, shown: int, approximate: bool) -> bool:
    if approximate or unit == "words":
        share = 0.15 if approximate else 0.05
        return abs(wanted - shown) <= max(
            1.0 if unit != "words" else 3.0, share * shown
        )
    return wanted == shown


def contradicted(
    sentence: str,
    files: Mapping[str, Mapping[str, int]],
    suffixes: Mapping[str, str],
    mentioned: list[str] | None = None,
) -> list[str]:
    """The units whose figure this sentence gives wrongly, for every file;
    mentioned collects every unit the sentence gives a figure for."""
    if _SKIP.search(sentence) or not files:
        return []
    wrong: list[str] = []
    position = 0
    while (match := CLAIM.search(sentence, position)) is not None:
        position = match.end()
        if match["limit"] or match["after"]:
            continue
        unit = next(
            (key for key, pattern in _UNIT.items() if pattern.fullmatch(match["unit"])),
            "",
        )
        wanted = _value(match["number"])
        if not unit or wanted is None:
            continue
        # A bare "no" or "one" far from the unit is a word, not a figure.
        if not match["number"].isdigit() and match["between"].count(" ") > 1:
            # Look again from the next word: "no tiene ninguna casilla".
            position = match.start() + len(match["number"])
            continue
        approximate = bool(match["approx"])
        if mentioned is not None:
            mentioned.append(unit)
        measured = [f[unit] for f in files.values() if unit in f]
        if unit == "pages":
            sheets = [
                f["sheets"]
                for name, f in files.items()
                if suffixes.get(name) == ".xlsx" and "sheets" in f
            ]
            if sheets and not measured and wanted == 1 and _ALL.search(sentence):
                if all(count > 1 for count in sheets):
                    wrong.append("sheets")
                continue
        if not measured:
            continue
        if wanted == 0:
            if all(count > 0 for count in measured):
                wrong.append(unit)
        elif not any(_holds(unit, wanted, count, approximate) for count in measured):
            wrong.append(unit)
    return wrong


LABELS: dict[str, dict[str, tuple[str, str]]] = {
    "en": {
        "pages": ("page", "pages"),
        "slides": ("slide", "slides"),
        "sheets": ("sheet", "sheets"),
        "bullets": ("bullet", "bullets"),
        "words": ("word", "words"),
        "pictures": ("picture", "pictures"),
        "checkboxes": ("box to tick", "boxes to tick"),
    },
    "fr": {
        "pages": ("page", "pages"),
        "slides": ("diapositive", "diapositives"),
        "sheets": ("feuille", "feuilles"),
        "bullets": ("puce", "puces"),
        "words": ("mot", "mots"),
        "pictures": ("image", "images"),
        "checkboxes": ("case à cocher", "cases à cocher"),
    },
    "de": {
        "pages": ("Seite", "Seiten"),
        "slides": ("Folie", "Folien"),
        "sheets": ("Blatt", "Blätter"),
        "bullets": ("Aufzählungspunkt", "Aufzählungspunkte"),
        "words": ("Wort", "Wörter"),
        "pictures": ("Bild", "Bilder"),
        "checkboxes": ("Kontrollkästchen", "Kontrollkästchen"),
    },
    "es": {
        "pages": ("página", "páginas"),
        "slides": ("diapositiva", "diapositivas"),
        "sheets": ("hoja", "hojas"),
        "bullets": ("viñeta", "viñetas"),
        "words": ("palabra", "palabras"),
        "pictures": ("imagen", "imágenes"),
        "checkboxes": ("casilla", "casillas"),
    },
    "it": {
        "pages": ("pagina", "pagine"),
        "slides": ("diapositiva", "diapositive"),
        "sheets": ("foglio", "fogli"),
        "bullets": ("punto elenco", "punti elenco"),
        "words": ("parola", "parole"),
        "pictures": ("immagine", "immagini"),
        "checkboxes": ("casella", "caselle"),
    },
}
OPENING = {
    "en": "Counted on the file",
    "fr": "Compté sur le fichier",
    "de": "Am Dokument gezählt",
    "es": "Contado en el archivo",
    "it": "Contato sul file",
}


def figure_text(language: str, unit: str, count: int) -> str:
    one, many = LABELS.get(language, LABELS["en"])[unit]
    return f"{count} {one if count == 1 else many}"


def note(
    language: str,
    files: Mapping[str, Mapping[str, int]],
    units: list[str],
) -> str:
    """The measured figures of the units a sentence got wrong, one line."""
    colon = " : " if language == "fr" else ": "
    parts = []
    for name, figures in files.items():
        shown = [
            figure_text(language, unit, figures[unit])
            for unit in dict.fromkeys(units)
            if unit in figures
        ]
        if shown:
            parts.append((name + colon if len(files) > 1 else "") + ", ".join(shown))
    if not parts:
        return ""
    return OPENING.get(language, OPENING["en"]) + colon + " ; ".join(parts) + "."


def held_to_file(
    text: str,
    files: Mapping[str, Mapping[str, int]] | None,
    language: str,
    suffixes: Mapping[str, str] | None = None,
) -> tuple[str, list[str]]:
    """The answer without the figure statements the file contradicts, with one
    code-written line of the measured figures; the wrong units found."""
    if not files or not text.strip():
        return text, []
    suffixes = suffixes or {
        name: "." + name.rsplit(".", 1)[-1].lower() for name in files
    }
    units: list[str] = []
    paragraphs: list[str] = []
    for paragraph in text.split("\n\n"):
        if paragraph.lstrip().startswith(_VERBATIM):
            paragraphs.append(paragraph)
            continue
        lines = []
        for line in paragraph.split("\n"):
            if line.lstrip().startswith(_VERBATIM):
                lines.append(line)
                continue
            kept = []
            for match in _SENTENCES.finditer(line):
                sentence = match.group(0)
                said: list[str] = []
                wrong = contradicted(sentence, files, suffixes, said)
                if wrong:
                    units += wrong + said
                else:
                    kept.append(sentence)
            if kept:
                lines.append(" ".join(kept))
        if lines:
            paragraphs.append("\n".join(lines))
    if not units:
        return text, []
    closing = note(language, files, units)
    if closing:
        paragraphs.append(closing)
    return "\n\n".join(paragraphs).strip(), list(dict.fromkeys(units))


# Sentences that say the file was tried out or verified: past forms of "to
# test" and "to verify" in the five languages, and "works as expected".
STATUS = re.compile(
    r"\b(?:tested|verified|test[eé]e?s?|v[eé]rifi[eé]e?s?|getestet|[uü]berpr[uü]ft"
    r"|gepr[uü]ft|probad[oa]s?|verificad[oa]s?|testat[oa]|testate|testati"
    r"|verificat[oa]|verificate|verificati|j'ai test[eé]|i tested|i ran|i opened"
    r"|ich habe getestet|he probado|ho testato)\b",
    re.I | re.U,
)
# A sentence that tells how to test is advice, not a status.
_ADVICE = re.compile(
    r"\b(?:to test|you can test|test it|try|essayez|testez|probieren|testen sie"
    r"|pru[eé]b|prova|puoi testare|can be tested)\b",
    re.I,
)


def without_unproven_status(text: str) -> str:
    """The answer without the sentences that say the file was tested or
    verified (the status of a check is the code's to state)."""
    paragraphs: list[str] = []
    for paragraph in text.split("\n\n"):
        if paragraph.lstrip().startswith(_VERBATIM):
            paragraphs.append(paragraph)
            continue
        lines = []
        for line in paragraph.split("\n"):
            if line.lstrip().startswith(_VERBATIM):
                lines.append(line)
                continue
            kept = [
                m.group(0)
                for m in _SENTENCES.finditer(line)
                if not (STATUS.search(m.group(0)) and not _ADVICE.search(m.group(0)))
            ]
            if kept:
                lines.append(" ".join(kept))
        if lines:
            paragraphs.append("\n".join(lines))
    return "\n\n".join(paragraphs).strip()


def figures_of(tools: Any) -> dict[str, dict[str, int]]:
    """The figures measured on the files this turn published."""
    found = getattr(tools, "file_figures", None)
    return found if isinstance(found, dict) else {}
