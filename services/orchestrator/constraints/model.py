"""Typed, atomic constraints: the closed vocabulary code can check.

A constraint is one thing the person asked about the form or content of a
reply or of a delivered file, small enough to be met or not met on its own
(InFoBench's decomposed requirements, IFEval's verifiable instructions).
Every constraint carries the person's words it came from (`quote`): code
refuses one whose quote is not in what the person wrote, so neither the
phrase tables nor the model can invent a requirement.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
import re
import unicodedata
from typing import Any, Sequence

# Kinds code checks on a view (checks.py); `judge` goes to the verifier.
KINDS = frozenset(
    {
        "starts_with",  # the reply or part begins with this exact text
        "ends_with",  # ... ends with it
        "contains",  # this exact text appears
        "excludes",  # this text never appears
        "sentences",  # number of sentences (count, relation)
        "items",  # number of list items (count, relation, style)
        "paragraphs",  # number of paragraphs (count, relation)
        "list_style",  # list items are bullets | numbered | checkboxes | plain
        "order",  # these texts appear in this order
        "titles",  # the page or slide titles are exactly these, in order
        "after_file",  # a line beginning with `text` comes after the file link
        "no_intro",  # no lead-in sentence before the requested output
        "only",  # the requested output and nothing around it
        "no_new_file",  # nothing is created or changed in this turn
        "judge",  # anything else: the verifier reads it
    }
)
SCOPES = frozenset({"answer", "file"})
RELATIONS = frozenset({"exact", "min", "max"})
STYLES = frozenset({"bullets", "numbered", "checkboxes", "plain", ""})
# Where in a file: a region, optionally on one page or slide ("page:2"), or
# "each" page or slide.
REGIONS = frozenset({"", "title", "body", "notes", "footer", "header"})
# Kinds that need a text, a list of texts or a count.
NEEDS_TEXT = frozenset(
    {"starts_with", "ends_with", "contains", "excludes", "after_file"}
)
NEEDS_ITEMS = frozenset({"order", "titles"})
NEEDS_COUNT = frozenset({"sentences", "items", "paragraphs"})
MAX_TEXT = 300
MAX_ITEMS = 20


@dataclass(frozen=True)
class Constraint:
    kind: str
    scope: str
    quote: str
    turn: int
    statement: str = ""
    text: str = ""
    items: tuple[str, ...] = ()
    count: int | None = None
    relation: str = "exact"
    style: str = ""
    region: str = ""
    # 0: every page or slide (or not a file); n: page or slide n only.
    page: int = 0
    # True: applies to each page or slide on its own (counts per slide).
    each: bool = False
    # Applies to later replies too ("always", "from now on").
    lasting: bool = False
    source: str = "code"
    id: str = field(default="", compare=False)

    def label(self) -> str:
        """What the person reads when the constraint is not met: their own
        words, always in their language (a model's statement may not be)."""
        return self.quote or self.statement

    def key(self) -> tuple[Any, ...]:
        """Two constraints with the same key are one requirement."""
        return (
            self.kind,
            self.scope,
            norm(self.text),
            tuple(norm(item) for item in self.items),
            self.count,
            self.relation,
            self.style,
            self.region,
            self.page,
            self.each,
        )


_QUOTES = str.maketrans(
    {
        "“": '"',
        "”": '"',
        "„": '"',
        "‟": '"',
        "«": '"',
        "»": '"',
        "‹": "'",
        "›": "'",
        "’": "'",
        "‘": "'",
        "ʼ": "'",
        " ": " ",
        " ": " ",
        "–": "-",
        "—": "-",
    }
)


def norm(text: str) -> str:
    """Exact-text comparison: case, spacing, quote style and markdown aside;
    accents and words kept."""
    text = unicodedata.normalize("NFKC", text).translate(_QUOTES)
    text = re.sub(r"[*_`]+", "", text)
    return " ".join(text.casefold().split())


def fold(text: str) -> str:
    """Loose comparison: also accents and punctuation aside (quotes of the
    person's words, which the model may copy with a fixed typo)."""
    decomposed = unicodedata.normalize("NFKD", text)
    plain = "".join(c for c in decomposed if not unicodedata.combining(c))
    return " ".join(re.sub(r"[^\w]+", " ", plain.casefold()).split())


def said(quote: str, texts: Sequence[str]) -> bool:
    """Whether the quoted words are in one of the texts: the whole quote, or
    at least 80 % of its words for a quote of three words or more."""
    words = fold(quote).split()
    if not words:
        return False
    for text in texts:
        folded = fold(text)
        if " ".join(words) in folded:
            return True
        present = set(folded.split())
        if len(words) >= 3 and sum(w in present for w in words) >= 0.8 * len(words):
            return True
    return False


def exact_in(text: str, texts: Sequence[str]) -> bool:
    """Whether an exact text the constraint names was written by the person
    (or shown in the previous reply they refer to): never invented."""
    wanted = norm(text).strip(" .:;!?\"'")
    return bool(wanted) and any(wanted in norm(source) for source in texts)


def _int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.strip().isdigit():
        return int(value.strip())
    return None


def _clean(value: Any) -> str:
    return " ".join(str(value or "").split())[:MAX_TEXT]


def validated(
    raw: dict[str, Any],
    turn: int,
    person: Sequence[str],
    shown: Sequence[str] = (),
    *,
    source: str = "model",
) -> Constraint | None:
    """A constraint from a model's JSON object, or None when it is not one.

    person: the person's messages (the quote must be among them); shown:
    earlier replies the person may refer to ("the second outline"), where an
    exact text may also come from.
    """
    if not isinstance(raw, dict):
        return None
    kind = str(raw.get("kind") or "").strip()
    scope = str(raw.get("scope") or "answer").strip()
    if kind not in KINDS or scope not in SCOPES:
        return None
    quote = _clean(raw.get("quote"))
    if not quote or not said(quote, person):
        return None
    text = _clean(raw.get("text"))
    items_raw = raw.get("items") or []
    items = (
        tuple(_clean(item) for item in items_raw if _clean(item))[:MAX_ITEMS]
        if isinstance(items_raw, list)
        else ()
    )
    count = _int(raw.get("count"))
    relation = str(raw.get("relation") or "exact")
    style = str(raw.get("style") or "")
    where = str(raw.get("where") or "").strip().casefold()
    region, page, each = "", 0, False
    for part in re.split(r"[\s,;]+", where):
        if part in REGIONS:
            region = part or region
        elif part == "each":
            each = True
        elif re.fullmatch(r"(?:page|slide):?\d{1,3}", part):
            page = int(re.sub(r"\D", "", part))
    if kind == "items" and style == "plain":
        # A count of list items in no particular style.
        style = ""
    if relation not in RELATIONS or style not in STYLES:
        return None
    if kind in NEEDS_TEXT and not text:
        return None
    if kind in NEEDS_ITEMS and len(items) < 2:
        return None
    if kind in NEEDS_COUNT and (count is None or not 0 <= count <= 200):
        return None
    if kind == "list_style" and not style:
        return None
    if kind == "judge" and not _clean(raw.get("statement")):
        return None
    # An exact text must have been written by the person or shown to them.
    sources = [*person, *shown]
    if text and kind != "judge" and not exact_in(text, sources):
        return None
    if items and not all(exact_in(item, sources) for item in items):
        return None
    if kind == "excludes" and not forbidden_word(text, person):
        # "No explanations" is not a forbidden word: only a word or phrase
        # the person quotes, or names as a word, is excluded by code.
        return None
    if kind == "titles" and scope == "answer":
        # A reply has no pages: named parts (headings, table columns) are
        # checked as texts in this order.
        kind = "order"
    if scope == "answer" and (region or page or each):
        region, page, each = "", 0, False
    if kind in {"starts_with", "ends_with"} and _PART.search(fold(quote)):
        # The beginning of a part of the reply ("start the second paragraph
        # with ..."), not of the reply: read by the verifier.
        kind, text = "judge", text
        statement = _clean(raw.get("statement")) or quote
        return Constraint(
            kind=kind,
            scope=scope,
            quote=quote,
            turn=turn,
            statement=statement,
            source=source,
        )
    lasting = bool(raw.get("lasting")) and lasting_said(quote)
    return Constraint(
        kind=kind,
        scope=scope,
        quote=quote,
        turn=turn,
        statement=_clean(raw.get("statement")),
        text=text,
        items=items,
        count=count,
        relation=relation,
        style=style,
        region=region,
        page=page,
        each=each,
        lasting=lasting,
        source=source,
    )


_PART = re.compile(
    r"\b(?:first|second|third|fourth|last|each|every|premier|premiere|deuxieme|"
    r"seconde?|troisieme|dernier|derniere|chaque|erste\w*+|zweite\w*+|dritte\w*+|"
    r"letzte\w*+|jede\w*+|primer\w*+|segund\w*+|tercer\w*+|ultim\w*+|cada|"
    r"prim\w*+|second\w*+|terz\w*+|ogni)\s(?:\w+\s)?(?:paragraph|paragraphe|absatz|"
    r"parrafo|paragrafo|line|ligne|zeile|linea|riga|sentence|phrase|satz|frase|item|"
    r"point|punkt|punto|section|abschnitt|seccion|sezione|bullet|puce)"
)
_WORD_CUE = re.compile(
    r"\b(?:words?|terms?|phrases?|expressions?|mots?|termes?|wort|worter|woerter|"
    r"begriff\w*|ausdruck\w*|palabras?|terminos?|expresion\w*|parol\w*|"
    r"termin\w*|espression\w*)\W+$"
)
_QUOTE_MARKS = "\"'«»“”„‘’`"


def forbidden_word(text: str, person: Sequence[str]) -> bool:
    """Whether the person quoted this text, or named it right after a word
    for "word" ("without the word river")."""
    for message in person:
        index = message.casefold().find(text.casefold())
        while index >= 0:
            before = message[max(0, index - 30) : index]
            after = message[index + len(text) : index + len(text) + 2]
            if (before[-2:].strip() and before.rstrip()[-1:] in _QUOTE_MARKS) or (
                after[:1] in _QUOTE_MARKS
            ):
                return True
            if _WORD_CUE.search(fold(before) + " "):
                return True
            index = message.casefold().find(text.casefold(), index + 1)
    return False


_LASTING = re.compile(
    r"\b(?:always|from now on|every (?:time|answer|reply)|each time|in (?:all|every) "
    r"(?:your )?(?:answers|replies)|"
    r"toujours|desormais|dorenavant|a chaque (?:fois|reponse)|a partir de maintenant|"
    r"immer|ab jetzt|von nun an|jedes mal|bei jeder antwort|kunftig|kuenftig|"
    r"siempre|a partir de ahora|cada vez|en cada respuesta|de ahora en adelante|"
    r"sempre|d ora in poi|da ora in poi|ogni volta|in ogni risposta)\b"
)


def lasting_said(quote: str) -> bool:
    """A constraint lasts past its own turn only when the person says so."""
    return bool(_LASTING.search(fold(quote)))


def merged(found: Sequence[Constraint]) -> list[Constraint]:
    """Duplicates dropped (the code's reading first), ids given in order."""
    seen: set[tuple[Any, ...]] = set()
    kept: list[Constraint] = []
    for item in found:
        key = item.key()
        if key in seen:
            continue
        seen.add(key)
        kept.append(replace(item, id=f"K{len(kept) + 1}"))
    return kept
