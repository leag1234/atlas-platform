"""Counts over the texts a turn holds, computed by code (defect class C2).

Staging -165 (2026-11-22): asked to count, "with the terminal", the pages of an
attached PDF and the occurrences of one word, the model said it had no
terminal, then "counted directly" on the extracted text and gave 44 (39 in an
earlier run) for a true count of 85. The attachment route offered no tool at
all (document_chat set model.tools to []), so every number was read, then
presented as a count.

Pages, words, characters and the occurrences of a term in a provided document
or a pasted text are facts code can establish exactly. The model decides what
to count (which terms, which file); this module counts (roadmap T8, like
exact_tools for dates and units). The tool is offered on the turns that ask
for a count only: every other turn keeps the request it has always sent, so
recorded provider exchanges stay valid.

Term matching, stated in the result so the answer can say what was counted:
- occurrences: whole words, case-insensitive, accents as written in the term;
- occurrences_case_sensitive: whole words, exact case;
- occurrences_ignoring_accents: whole words, case- and accent-insensitive
  (a term typed without its accents still finds the accented word);
- occurrences_including_longer_words: the term inside longer words too
  (plurals and compounds), case-insensitive.
A word split by a hyphen at a line end (PDF layout) is counted once, whole.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from contextvars import ContextVar, Token
from dataclasses import dataclass, field
import json
import re
import unicodedata
from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from packages.validation import describe_validation

NAME = "document_statistics"
MAX_TERMS = 10
# Pages of the result that list where a term appears.
MAX_PAGES_LISTED = 200
# The person's text counted as a pasted text, like document_quotes.PASTED_MINIMUM.
PASTED_MINIMUM = 400


@dataclass(frozen=True)
class Text:
    """A text the turn holds: an attachment (with its pages) or a pasted text."""

    name: str
    text: str
    # Pages of a paginated file (PDF pages, presentation slides); 0 otherwise.
    pages: int = 0
    errors: tuple[str, ...] = field(default_factory=tuple)
    pasted: bool = False


class Arguments(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    terms: list[Annotated[str, Field(min_length=1, max_length=200)]] = Field(
        default_factory=list, max_length=MAX_TERMS
    )
    filename: str | None = Field(default=None, max_length=255)


DESCRIPTION = (
    "Exact counts over the attached documents and pasted texts of this "
    "conversation, done by code over the full extracted text: pages (PDF pages, "
    "presentation slides), words, characters, and how many times each term "
    "appears (overall and per page). Use it for every count of pages, words, "
    "characters or occurrences: never count or estimate them yourself. Pass "
    "every word or phrase the person wants counted in terms, exactly as they "
    "wrote it; filename limits the count to one document (default: all). Each "
    "term returns occurrences (whole words, case-insensitive, accents as "
    "written), occurrences_case_sensitive, occurrences_ignoring_accents and "
    "occurrences_including_longer_words (inside longer words too, such as "
    "plurals). Give the numbers as returned and say what was counted."
)


def declaration() -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": NAME,
            "description": DESCRIPTION,
            "parameters": Arguments.model_json_schema(),
        },
    }


# What the model reads when the person asked for a terminal it does not have
# (code, not prompts/: the corpus fingerprint is unchanged).
TERMINAL_INSTRUCTION = (
    "\nThe person asked for a terminal or for code execution. Neither is "
    "available in this conversation: say so in one short sentence, then give "
    "the counts computed by code (document_statistics).\n"
)


# --- Relevance: does the question ask for a count? ---------------------------
# Patterns run on lower-case text without accents, in the five UI languages.
_UNITS_FR = (
    r"(?:fois|pages?|mots?|occurrences?|caracteres?|signes?|lignes?|paragraphes?|"
    r"phrases?|mentions?|diapos?(?:itives?)?|slides?|feuillets?|apparitions?)"
)
_UNITS_EN = (
    r"(?:times|pages?|words?|occurrences?|characters?|chars|lines?|paragraphs?|"
    r"sentences?|mentions?|slides?|instances?)"
)
_COUNT_CUES = re.compile(
    "|".join(
        (
            rf"\bcombien\b.{{0,40}}\b{_UNITS_FR}\b",
            rf"\bnombre\s+(?:total\s+)?(?:de\s+|d')(?:\w+\s+)?{_UNITS_FR}\b",
            # "compte les pages", never "compte rendu" nor "tenir compte de".
            r"(?<!tenir )(?<!tiens )(?<!tient )(?<!tenez )(?<!tenant )(?<!tenu )"
            r"\b(?:compte|comptez|compter|recompte|recomptez|recompter|denombre|"
            r"denombrez|denombrer|comptabilise|comptabilisez|comptabiliser)\s+"
            r"(?:le|les|la|l'|combien|chaque|tous|toutes|ses)\b",
            r"\boccurrences?\b|\bfrequence\s+(?:du|de|des|d')\b",
            rf"\bhow\s+many\b.{{0,40}}\b{_UNITS_EN}\b",
            r"\bhow\s+(?:many|often)\b.{0,80}\b(?:appears?|occurs?|mentioned|used|cited|comes?\s+up)\b",
            rf"\b(?:total\s+)?number\s+of\s+(?:\w+\s+)?{_UNITS_EN}\b",
            r"\bcount\s+(?:the|all|every|each|how|occurrences|words|pages|lines|"
            r"mentions|instances|up)\b|\bcounting\b|\bword\s+count\b|\bpage\s+count\b",
            r"\bfrequency\s+of\b",
            r"\bwie\s+(?:oft|viele)\b|\banzahl\s+(?:der|an)\b|\bzahle?n?\s+(?:die|wie|alle)\b|\bvorkommen\b",
            r"\bcuant[ao]s\s+(?:veces|paginas|palabras|caracteres|lineas|parrafos|frases|menciones|diapositivas)\b",
            r"\bnumero\s+(?:de|di)\s+(?:veces|volte|paginas|pagine|palabras|parole|caracteres|caratteri|apariciones|occorrenze)\b",
            r"\b(?:cuenta|contar|cuente)\s+(?:las|los|el|la|cuantas|cuantos)\b|\bapariciones\b",
            r"\bquant[ei]\s+(?:volte|pagine|parole|caratteri|righe|paragrafi|frasi|menzioni|diapositive)\b",
            r"\b(?:conta|contare|conti)\s+(?:le|i|il|la|gli|quante|quanti)\b|\boccorrenze\b",
        )
    )
)
_TERMINAL_CUES = re.compile(
    r"\b(?:terminal|shell|bash|console|python|grep|pdftotext)\b"
    r"|\bligne\s+de\s+commandes?\b|\binvite\s+de\s+commandes?\b|\bcommand\s+line\b"
    r"|\bkommandozeile\b|\blinea\s+de\s+comandos\b|\briga\s+di\s+comando\b"
    r"|\b(?:avec|par)\s+(?:du|un)\s+(?:code|script)\b|\b(?:with|using|in)\s+(?:a\s+)?(?:code|script)\b"
    r"|\bexecute\s+(?:du|le|un)\s+code\b|\brun\s+(?:a\s+|some\s+)?code\b"
)
# A term the person put in quotes: counted even if the model leaves it out.
_QUOTED = re.compile(r"«\s*([^«»\n]{1,80}?)\s*»|“([^“”\n]{1,80})”|\"([^\"\n]{1,80})\"")


def plain(text: str) -> str:
    """Lower case, typographic apostrophes as ', accents removed."""
    folded = unicodedata.normalize("NFKD", text.lower().replace("’", "'"))
    return "".join(c for c in folded if not unicodedata.combining(c))


def relevant(question: str) -> bool:
    """True when the question asks for a count (pages, words, occurrences)."""
    return bool(_COUNT_CUES.search(plain(question[:4000])))


def terminal_requested(question: str) -> bool:
    """True when the question asks for a terminal, a shell or code execution."""
    return bool(_TERMINAL_CUES.search(plain(question[:4000])))


def quoted_terms(question: str) -> list[str]:
    """Short phrases the person quoted (five words at most), in order."""
    found: list[str] = []
    for match in _QUOTED.finditer(question[:4000]):
        term = next((g for g in match.groups() if g), "").strip(" .,;:!?")
        if term and len(term.split()) <= 5 and term not in found:
            found.append(term)
    return found


def errors_of(value: object) -> tuple[str, ...]:
    """The reading errors of an extracted source, as strings."""
    return tuple(str(error) for error in value) if isinstance(value, list) else ()


def pasted_texts(messages: Iterable[tuple[str, str]]) -> tuple[Text, ...]:
    """The long texts the person pasted (role, text pairs), as countable texts."""
    texts = [
        text
        for role, text in messages
        if role == "user" and len(text) >= PASTED_MINIMUM
    ]
    return tuple(
        Text(f"pasted text {index}", text, pasted=True)
        for index, text in enumerate(texts, start=1)
    )


# --- Counting ----------------------------------------------------------------
_PAGE_MARK = re.compile(r"^\[Page (\d{1,4})\]\n", re.MULTILINE)
# A hyphen at a line end between two letters: a compound kept whole
# ("Etats-\nUnis" reads "Etats-Unis") or a word split by the layout.
_LINE_END_HYPHEN = re.compile(r"(?<=\w)-[ \t]*\r?\n\s*(?=\w)")
_SPACES = re.compile(r"\s+")


def _pages(text: Text) -> list[tuple[int | None, str]]:
    """The text cut at the extractor's page marks ("[Page N]"), marks removed."""
    if text.pasted:
        return [(None, text.text)]
    marks = list(_PAGE_MARK.finditer(text.text))
    if not marks:
        return [(None, text.text)]
    parts: list[tuple[int | None, str]] = []
    if text.text[: marks[0].start()].strip():
        parts.append((None, text.text[: marks[0].start()]))
    for index, mark in enumerate(marks):
        end = marks[index + 1].start() if index + 1 < len(marks) else len(text.text)
        parts.append((int(mark.group(1)), text.text[mark.end() : end]))
    return parts


def _normal(text: str) -> str:
    return unicodedata.normalize("NFC", text.replace("’", "'"))


def _forms(segment: str) -> tuple[str, str]:
    """The segment with line-end hyphens kept, then removed; spaces collapsed."""
    text = _normal(segment)
    kept = _SPACES.sub(" ", _LINE_END_HYPHEN.sub("-", text))
    joined = _SPACES.sub(" ", _LINE_END_HYPHEN.sub("", text))
    return kept, joined


def _without_accents(text: str) -> str:
    folded = unicodedata.normalize("NFKD", text)
    return unicodedata.normalize(
        "NFC", "".join(c for c in folded if not unicodedata.combining(c))
    )


def _pattern(term: str, whole: bool, flags: int) -> re.Pattern[str]:
    body = r"\s+".join(re.escape(part) for part in term.split())
    return re.compile(rf"(?<!\w){body}(?!\w)" if whole else body, flags)


def _count(pattern: re.Pattern[str], forms: tuple[str, str]) -> int:
    # A split word is found in the joined form, a hyphenated compound in the
    # kept one: the larger of the two counts each occurrence once.
    return max(len(pattern.findall(forms[0])), len(pattern.findall(forms[1])))


def term_counts(
    term: str, segments: Sequence[tuple[int | None, tuple[str, str]]]
) -> dict[str, Any]:
    clean = _SPACES.sub(" ", _normal(term)).strip()
    whole_ci = _pattern(clean, True, re.IGNORECASE)
    whole_cs = _pattern(clean, True, 0)
    inside = _pattern(clean, False, re.IGNORECASE)
    plain_term = _pattern(_without_accents(clean), True, re.IGNORECASE)
    total = exact = longer = unaccented = 0
    per_page: dict[str, int] = {}
    for page, forms in segments:
        found = _count(whole_ci, forms)
        total += found
        exact += _count(whole_cs, forms)
        longer += _count(inside, forms)
        unaccented += _count(
            plain_term, (_without_accents(forms[0]), _without_accents(forms[1]))
        )
        if found and page is not None and len(per_page) < MAX_PAGES_LISTED:
            per_page[str(page)] = per_page.get(str(page), 0) + found
    result: dict[str, Any] = {
        "term": term,
        "occurrences": total,
        "occurrences_case_sensitive": exact,
        "occurrences_ignoring_accents": unaccented,
        "occurrences_including_longer_words": longer,
    }
    if per_page:
        result["occurrences_by_page"] = per_page
    return result


def document_counts(text: Text, terms: Sequence[str]) -> dict[str, Any]:
    pages = _pages(text)
    segments = [(page, _forms(body)) for page, body in pages]
    words = sum(
        1
        for _, (kept, _) in segments
        for token in kept.split(" ")
        if any(c.isalnum() for c in token)
    )
    characters = sum(len(kept.strip()) for _, (kept, _) in segments)
    result: dict[str, Any] = {"filename": text.name}
    if text.pasted:
        result["pages"] = None
        result["pages_note"] = "a pasted text has no pages"
    elif text.pages:
        result["pages"] = text.pages
    else:
        result["pages"] = None
        result["pages_note"] = (
            "this file type has no fixed pages: its page count depends on the "
            "layout and was not computed"
        )
    result["words"] = words
    result["characters"] = characters
    result["counts"] = [term_counts(term, segments) for term in terms]
    if text.errors:
        result["complete"] = False
        result["reading_errors"] = list(text.errors[:3])
    else:
        result["complete"] = True
    return result


def summary(result: dict[str, Any]) -> str:
    """The counts in one plain sentence per document (evidence for the checks)."""
    lines = []
    for document in result.get("documents", []):
        parts = []
        if document.get("pages") is not None:
            parts.append(f"{document['pages']} pages")
        parts.append(f"{document['words']} words")
        parts.append(f"{document['characters']} characters")
        for count in document.get("counts", []):
            parts.append(
                f"'{count['term']}' {count['occurrences']} occurrences "
                "(whole words, case-insensitive)"
            )
        lines.append(f"{document['filename']}: " + ", ".join(parts))
    return "Counted by code: " + "; ".join(lines) + "."


def merged_terms(arguments: str, extra: Sequence[str]) -> str:
    """The call's arguments with the person's quoted terms added (bounded)."""
    try:
        value = json.loads(arguments)
    except ValueError:
        value = {}
    if not isinstance(value, dict):
        value = {}
    terms = value.get("terms")
    terms = [t for t in terms if isinstance(t, str)] if isinstance(terms, list) else []
    known = {t.casefold() for t in terms}
    for term in extra:
        if term.casefold() not in known and len(terms) < MAX_TERMS:
            terms.append(term)
            known.add(term.casefold())
    value["terms"] = terms
    return json.dumps(value, ensure_ascii=False)


def execute(arguments: str, texts: Sequence[Text]) -> dict[str, Any]:
    """The tool's result: counts per document, or an error the model can read."""
    try:
        request = Arguments.model_validate_json(arguments or "{}")
    except ValidationError as exc:
        return {
            "error": "invalid_arguments",
            "message": describe_validation(exc),
            "schema": Arguments.model_json_schema(),
        }
    if not texts:
        return {
            "error": "no_document",
            "message": "This conversation holds no attached document or pasted "
            "text to count.",
        }
    chosen = list(texts)
    if request.filename:
        wanted = request.filename.strip().casefold()
        chosen = [t for t in texts if t.name.casefold() == wanted] or [
            t for t in texts if wanted in t.name.casefold()
        ]
        if not chosen:
            return {
                "error": "unknown_filename",
                "message": "No document has this name.",
                "documents": [t.name for t in texts],
            }
    terms = [t for t in request.terms if t.strip()]
    result: dict[str, Any] = {
        "documents": [document_counts(text, terms) for text in chosen],
        "method": "counted by code over the full extracted text; words are "
        "space-separated tokens holding a letter or a digit",
    }
    result["summary"] = summary(result)
    return result


# --- Counts computed before the answer, on every path (staging -167) ---------
# Staging -167: with the forced tool call on the attachment-only route, the web
# UI turn still answered 48 for 85. That route only runs with the terminal off;
# the served instances set ATLAS_TERMINAL_ENABLED at start (serving.py), so an
# Open WebUI attachment, an API file and a pasted text all reach the general
# chat path, where the model reads a hierarchical summary of a long document
# and nothing counted. On a counting request, code now counts before the answer
# and the model receives the counts as a fact of the turn, whatever the path;
# the tool stays offered for a term code could not read from the question.


# The full texts the turn holds (attachments read in process, announced files
# read whole, pasted texts) and the names only extracts were read of.
@dataclass
class Counting:
    """What one turn holds to count, and the counts code made (mutable, so a
    record made in a task the turn starts reaches the turn's checks)."""

    texts: list[Text] = field(default_factory=list)
    uncounted: list[str] = field(default_factory=list)
    facts: dict[str, Any] | None = None


_TURN: ContextVar[Counting | None] = ContextVar("atlas_counting", default=None)


def begin() -> Token[Counting | None]:
    """Start the turn's record of countable texts (reset with end)."""
    return _TURN.set(Counting())


def end(token: Token[Counting | None]) -> None:
    _TURN.reset(token)


def hold(text: Text) -> None:
    """Record a full text of the turn (no-op outside a turn)."""
    if (turn := _TURN.get()) is not None and text.text.strip():
        if all(t.name != text.name or t.text != text.text for t in turn.texts):
            turn.texts.append(text)


def hold_uncounted(name: str) -> None:
    """Record a file of which only extracts were read: it is never counted."""
    if (turn := _TURN.get()) is not None and name not in turn.uncounted:
        turn.uncounted.append(name)


def held_texts() -> tuple[Text, ...]:
    turn = _TURN.get()
    return tuple(turn.texts) if turn is not None else ()


def uncounted() -> tuple[str, ...]:
    turn = _TURN.get()
    return tuple(turn.uncounted) if turn is not None else ()


def current_facts() -> dict[str, Any] | None:
    turn = _TURN.get()
    return turn.facts if turn is not None else None


# A term named after "the word", "le mot"... in the question.
_NAMED_TERM = re.compile(
    r"\b(?:le|du|au|les|des|the|das|die|der|la|el|il|lo)\s+"
    r"(?:mot|mots|terme|termes|nom|expression|word|words|term|terms|name|wort|"
    r"begriff|palabra|termino|parola|termine)\s+"
    r"(?!(?:qui|que|dans|in|de|du|of|est|is|apparait|apparaît)\b)"
    r"([^\s,.;:!?()«»\"“”]{2,60})",
    re.IGNORECASE,
)
_OCCURRENCE_CUES = re.compile(
    r"\b(?:fois|occurrences?|occurences?|apparai\w*|appara\w*|revient|mentions?|"
    r"mentionne\w*|cite\w*|times|appears?|occurs?|mentioned|frequency|frequence|"
    r"wie\s+oft|vorkommen|veces|apariciones|volte|occorrenze)\b"
)
_PAGE_CUES = re.compile(r"\b(?:pages?|paginas?|pagine|seiten|slides?|diapos?\w*)\b")
_WORD_CUES = re.compile(r"\b(?:mots|words|worter|palabras|parole)\b")
_CHARACTER_CUES = re.compile(r"\b(?:caracteres|signes|characters|chars|zeichen)\b")
_STOP_TERMS = frozenset(
    {"le", "la", "les", "un", "une", "the", "a", "an", "de", "du", "des", "of"}
)


def asked_terms(question: str) -> list[str]:
    """The terms the question asks to count: quoted, or named after "the word"."""
    terms = quoted_terms(question)
    known = {t.casefold() for t in terms}
    for match in _NAMED_TERM.finditer(question[:4000]):
        term = match.group(1).strip("'’")
        if term.casefold() not in known and term.casefold() not in _STOP_TERMS:
            terms.append(term)
            known.add(term.casefold())
    return terms[:MAX_TERMS]


def occurrences_asked(question: str) -> bool:
    return bool(_OCCURRENCE_CUES.search(plain(question[:4000])))


def facts(
    question: str, texts: Sequence[Text], not_counted: Sequence[str] = ()
) -> dict[str, Any] | None:
    """The counts code makes before the answer of a counting request, or None.

    Pages, words and characters of every full text, and the occurrences of the
    terms the question names. Recorded for the turn (current_facts).
    """
    if not relevant(question) or not (texts or not_counted):
        return None
    terms = asked_terms(question)
    result: dict[str, Any] = (
        execute(json.dumps({"terms": terms}, ensure_ascii=False), texts)
        if texts
        else {"documents": []}
    )
    if "error" in result:
        return None
    if not_counted:
        result["not_counted"] = [
            {
                "filename": name,
                "reason": "only extracts of this file were read: it was not counted",
            }
            for name in not_counted
        ]
    result["asked"] = {
        "pages": bool(_PAGE_CUES.search(plain(question))),
        "words": bool(_WORD_CUES.search(plain(question))),
        "characters": bool(_CHARACTER_CUES.search(plain(question))),
        "terms": terms,
        "occurrences_without_named_term": occurrences_asked(question) and not terms,
    }
    if texts:
        result["summary"] = summary(result)
    if (turn := _TURN.get()) is not None:
        turn.facts = result
    return result


FACTS_INSTRUCTION = (
    "\nCounting rule: computed_document_statistics holds counts made by code "
    "over the full extracted text of the provided documents and texts (pages, "
    "words, characters, occurrences of the terms asked; whole words, "
    "case-insensitive, unless another field is named). Give these numbers as "
    "they are. Never count, estimate or correct them yourself, and never write "
    "that you counted: write that they were counted by code. For a term the "
    "results do not hold, call document_statistics when it is offered; "
    "otherwise say that this count was not computed. A file listed in "
    "not_counted was not counted: say so.\n"
)


def facts_message(result: dict[str, Any]) -> dict[str, object]:
    """The counts as a message of the turn, beside the attachments."""
    shown = {key: value for key, value in result.items() if key != "summary"}
    return {
        "role": "user",
        "content": json.dumps(
            {"computed_document_statistics": shown}, ensure_ascii=False
        ),
    }


def _numbers(text: str) -> set[str]:
    joined = re.sub(r"(?<=\d)[\s  .,](?=\d{3}\b)", "", text)
    return set(re.findall(r"\d+", joined))


CORRECTION_LABELS: dict[str, tuple[str, str, str, str, str]] = {
    # note opening, pages, words, characters, occurrences of a term
    "fr": (
        "Comptage fait par code sur le texte complet",
        "{n} pages",
        "{n} mots",
        "{n} caractères",
        "« {term} » : {n} occurrences",
    ),
    "en": (
        "Counted by code over the full text",
        "{n} pages",
        "{n} words",
        "{n} characters",
        "“{term}”: {n} occurrences",
    ),
    "de": (
        "Per Code im vollständigen Text gezählt",
        "{n} Seiten",
        "{n} Wörter",
        "{n} Zeichen",
        "„{term}“: {n} Vorkommen",
    ),
    "es": (
        "Contado por código sobre el texto completo",
        "{n} páginas",
        "{n} palabras",
        "{n} caracteres",
        "«{term}»: {n} apariciones",
    ),
    "it": (
        "Contato dal codice sul testo completo",
        "{n} pagine",
        "{n} parole",
        "{n} caratteri",
        "«{term}»: {n} occorrenze",
    ),
}


def correction(answer: str, result: dict[str, Any] | None, language: str) -> str:
    """A closing note with the counts code made that the answer does not give.

    The answer is expected to give each asked count; one that is missing (or
    replaced by another number) is stated after the answer, as code counted it.
    """
    if not result or not result.get("documents"):
        return ""
    asked = result.get("asked") or {}
    present = _numbers(answer)
    labels = CORRECTION_LABELS.get(language, CORRECTION_LABELS["en"])
    missing: list[str] = []
    for document in result["documents"]:
        found: list[str] = []
        pages = document.get("pages")
        if asked.get("pages") and isinstance(pages, int) and str(pages) not in present:
            found.append(labels[1].format(n=pages))
        if asked.get("words") and str(document["words"]) not in present:
            found.append(labels[2].format(n=document["words"]))
        if asked.get("characters") and str(document["characters"]) not in present:
            found.append(labels[3].format(n=document["characters"]))
        for count in document.get("counts", []):
            if str(count["occurrences"]) not in present:
                found.append(
                    labels[4].format(term=count["term"], n=count["occurrences"])
                )
        if found:
            name = document["filename"]
            missing.append(
                (name + " : " if language == "fr" else name + ": ") + ", ".join(found)
            )
    if not missing:
        return ""
    separator = " : " if language == "fr" else ": "
    return labels[0] + separator + " ; ".join(missing) + "."
