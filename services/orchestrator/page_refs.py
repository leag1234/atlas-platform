"""The page an answer gives for a named item is the page the document names it on.

Staging -184 (offline judge, class C3): a summary of an attached PDF cited
"p. 27-29" for a sentence naming the TechEU programme, which the document names
on page 8 only. The pages of an attached PDF are known: the attachment reader
marks each one ("[Page N]", packages/attachment_reader.py).

Code only (no model call), for each page reference of the answer ("(p. 3)",
"(p. 27–29)", "(pages 4 à 6)", "(S. 12)", "(pp. 3, 7)"):
1. the named items of its sentence before it: a word with two capitals or more
   (TechEU, BEI, NextGenerationEU), the names a reader looks up;
2. each item is looked up in the document's pages; one that the document names
   on one to MAX_PAGES pages, none of them cited, is misplaced (an item on
   more pages is too common to place: a running header, the Union's acronym);
3. the reference gets the item's pages added, in order ("p. 8, 27–29"): the
   other cited pages may hold the rest of the sentence, so none is removed.
An answer not yet sent is corrected in place; one already streamed gets a
short note naming each item with its page, in the answer's language.
A reference beyond the document's last page is left alone (the answer may use
the printed numbering), and so is a document without page marks.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import re

MAX_PAGES = 3
_PAGE_MARK = re.compile(r"(?m)^\[Page (\d{1,5})\]\n")
_RANGE = r"\d{1,4}+(?:\s*+(?:[–—-]|à|to|bis|al|a)\s*+\d{1,4}+)?+"
# A page reference in brackets, with its word ("p.", "pages", "S.", "pág.").
_REFERENCE = re.compile(
    r"(?P<word>\b(?:pp?\.|pages?|S\.|Seiten?|p[áa]g(?:ina)?s?\.?|pagine?)\s*+)"
    rf"(?P<pages>{_RANGE}(?:(?:\s*+,\s*+|\s++(?:et|and|und|y|e)\s++){_RANGE})*+)"
    r"(?=\s*+\))"
)
_NUMBER = re.compile(r"\d{1,4}+")
_LIST = re.compile(r"\s*+,\s*+|\s++(?:et|and|und|y|e)\s++")
_SPLIT_RANGE = re.compile(r"(\d{1,4}+)\s*+(?:[–—-]|à|to|bis|al|a)\s*+(\d{1,4}+)")
_WORD = re.compile(r"(?<![^\W_])[^\W\d_][^\W_]{1,40}+")
_HYPHEN_END = re.compile(r"(?<=\w)-[ \t]*+\r?\n\s*+(?=\w)")
NOTE = {
    "en": "Pages checked in the document: {items}.",
    "fr": "Pages vérifiées dans le document : {items}.",
    "de": "Seiten im Dokument geprüft: {items}.",
    "es": "Páginas comprobadas en el documento: {items}.",
    "it": "Pagine verificate nel documento: {items}.",
}
PAGE_WORD = {"en": "p.", "fr": "p.", "de": "S.", "es": "p.", "it": "p."}


def pages(text: str) -> dict[int, str]:
    """Page number -> text with spaces collapsed and line-end hyphens joined."""
    marks = list(_PAGE_MARK.finditer(text))
    found: dict[int, str] = {}
    for index, mark in enumerate(marks):
        end = marks[index + 1].start() if index + 1 < len(marks) else len(text)
        body = _HYPHEN_END.sub("", text[mark.end() : end])
        found[int(mark.group(1))] = " " + " ".join(body.split()) + " "
    return found


def cited(written: str) -> list[int]:
    found: list[int] = []
    for part in _LIST.split(written):
        span = _SPLIT_RANGE.fullmatch(part.strip())
        if span:
            first, last = int(span.group(1)), int(span.group(2))
            if first <= last <= first + 200:
                found.extend(range(first, last + 1))
                continue
        found.extend(int(n) for n in _NUMBER.findall(part)[:1])
    return found


def items(sentence: str) -> list[str]:
    """Named items: words with two capitals or more, three letters or more."""
    found: list[str] = []
    for word in _WORD.findall(sentence):
        if len(word) >= 3 and sum(c.isupper() for c in word) >= 2:
            if word not in found:
                found.append(word)
    return found


def _named(item: str, page: str) -> bool:
    return re.search(rf"(?<![^\W_]){re.escape(item)}(?![^\W_])", page) is not None


@dataclass
class Fix:
    start: int
    end: int
    text: str
    named: list[tuple[str, list[int]]]


@dataclass
class Outcome:
    text: str
    note: str = ""
    references: int = 0
    fixes: list[Fix] = field(default_factory=list)


def _written(numbers: list[int]) -> str:
    """Pages as ranges: [8, 27, 28, 29] -> "8, 27–29"."""
    parts: list[str] = []
    ordered = sorted(set(numbers))
    index = 0
    while index < len(ordered):
        last = index
        while last + 1 < len(ordered) and ordered[last + 1] == ordered[last] + 1:
            last += 1
        first_page, last_page = ordered[index], ordered[last]
        parts.append(
            str(first_page) if first_page == last_page else f"{first_page}–{last_page}"
        )
        index = last + 1
    return ", ".join(parts)


def check(body: str, documents: list[str], *, streamed: bool, language: str) -> Outcome:
    """Correct (or note) the page references of an answer; never raises."""
    from services.orchestrator.source_faithfulness import sentences

    result = Outcome(text=body)
    paged = [found for text in documents if (found := pages(text))]
    if not paged or "(" not in body:
        return result
    # One document with pages: the references are its own (with several, a
    # page number alone does not say which one).
    if len(paged) != 1:
        return result
    document = paged[0]
    last_page = max(document)
    spans = sentences(body)
    for match in _REFERENCE.finditer(body):
        result.references += 1
        numbers = cited(match.group("pages"))
        if not numbers or max(numbers) > last_page:
            continue
        start = next((s for s, e in spans if s <= match.start() < e), 0)
        before = body[start : match.start()]
        named: list[tuple[str, list[int]]] = []
        for item in items(before):
            where = [number for number, text in document.items() if _named(item, text)]
            if where and len(where) <= MAX_PAGES and not set(where) & set(numbers):
                named.append((item, where))
        if not named:
            continue
        added = [page for _, where in named for page in where]
        result.fixes.append(
            Fix(
                match.start("pages"),
                match.end("pages"),
                _written(numbers + added),
                named,
            )
        )
    if not result.fixes:
        return result
    if streamed:
        word = PAGE_WORD.get(language, "p.")
        listed = "; ".join(
            f"{item} {word} {_written(where)}"
            for fix in result.fixes
            for item, where in fix.named
        )
        result.note = (
            "*" + (NOTE.get(language) or NOTE["en"]).format(items=listed) + "*"
        )
        return result
    pieces: list[str] = []
    position = 0
    for fix in result.fixes:
        pieces.append(body[position : fix.start])
        pieces.append(fix.text)
        position = fix.end
    pieces.append(body[position:])
    result.text = "".join(pieces)
    return result
