"""Repair the words a code-side removal leaves without their object.

Staging -173 (2026-11-22): an unfetched source was cut out of a French "to be
checked on [python.org](https://www.python.org/), not consulted here" and the
answer read "to be checked on, not consulted here": a preposition with nothing
after it. The
same family as "(unverified source SVG converted)" (staging -151): whatever
removes or replaces a URL, a source name or a detail inside a sentence must
leave a sentence that still reads.

``repair(before, after)`` compares the text before and after such an edit. A
run of prepositions and articles ("on", "on the", "auf der", "su", "en el")
that now stands before a comma, a closing bracket, the end of a sentence or the
end of a line goes, but only when the edit made it dangle: the same words were
followed by something in the text before the edit, and are not followed by
that punctuation there. A preposition that ends a clause on purpose ("what are
you waiting for?", "and so on.") was already there, or never had an object,
and stays. A clause-initial "see" / "voir" / "siehe" / "ver" / "vedi" left
alone goes with it, then the punctuation the removal left orphaned ("(, ",
", )", ",.", "()") and the doubled spaces it left. Code is never touched.

Staging -176 (2026-11-22): a source label cut out of "« ... » — [label](url) :"
left "« ... » — :" before the remaining link. A run of separators that only
stood around the removed words ("— :", ": ,", "– ;", "— —") is reduced to the
one mark that still reads: a colon, then a semicolon, then a comma, a dash
only when no other mark is left. A run the text held before the edit stays
("le meilleur — de loin —, et" is written so on purpose).
"""

from __future__ import annotations

from bisect import bisect_right
import re

# Prepositions, contractions and articles that cannot end a clause once their
# object is gone, in the answer languages (en, fr, de, es, it).
_FUNCTION_WORDS = frozenset(
    {
        # English
        "on",
        "at",
        "from",
        "via",
        "in",
        "into",
        "onto",
        "to",
        "by",
        "of",
        "for",
        "per",
        "with",
        "according",
        "the",
        "a",
        "an",
        # French
        "sur",
        "chez",
        "de",
        "du",
        "des",
        "d'",
        "depuis",
        "à",
        "au",
        "aux",
        "dans",
        "par",
        "pour",
        "selon",
        "d'après",
        "auprès",
        "le",
        "la",
        "les",
        "l'",
        "un",
        "une",
        # German
        "auf",
        "bei",
        "beim",
        "von",
        "vom",
        "unter",
        "laut",
        "gemäß",
        "aus",
        "im",
        "zu",
        "zum",
        "zur",
        "über",
        "der",
        "die",
        "das",
        "den",
        "dem",
        "ein",
        "eine",
        "einer",
        "einem",
        "einen",
        # Spanish
        "en",
        "del",
        "desde",
        "según",
        "al",
        "el",
        "los",
        "las",
        "una",
        "unos",
        "unas",
        # Italian
        "su",
        "sul",
        "sulla",
        "sui",
        "sugli",
        "presso",
        "da",
        "dal",
        "dalla",
        "di",
        "dei",
        "della",
        "dello",
        "secondo",
        "nel",
        "nella",
        "il",
        "lo",
        "gli",
        "dell'",
        "sull'",
        "dall'",
        "nell'",
        "un'",
    }
)
# Conjunctions left with nothing on one side ("on X and at Y" with both gone).
_CONJUNCTIONS = frozenset(
    {"and", "or", "et", "ou", "und", "oder", "sowie", "y", "o", "e", "ed"}
)
# Pointers that only introduce a reference: they go when nothing else is left
# of their clause ("(see https://..., not consulted)").
_POINTERS = frozenset(
    {"see", "voir", "consultez", "siehe", "vedi", "ver", "véase", "cf", "vgl"}
)
# What closes the clause a dangling word now stands before.
_CLOSERS = ",;:.!?)]"
# Where a clause starts: a pointer after one of these introduces nothing else.
_OPENERS = "([,;:—–"


def _word_start(text: str, end: int) -> int:
    start = end
    while start > 0 and (text[start - 1].isalnum() or text[start - 1] in "'’"):
        start -= 1
    return start


def _skip_spaces(text: str, end: int) -> int:
    while end > 0 and text[end - 1] in " \t":
        end -= 1
    return end


def _token_start(text: str, end: int) -> int:
    """Start of the word, or the single mark, that ends at end."""
    end = _skip_spaces(text, end)
    start = _word_start(text, end)
    return end - 1 if start == end and end > 0 else start


def _dangling(text: str, stop: int) -> tuple[int, int]:
    """(start of the words dangling before stop, start of their evidence).

    The evidence is the word closest to stop with the token before it ("on" in
    "checked on"): the edit removed what followed that pair, so it is
    looked up, followed by something, in the text before the edit.
    """
    position = _skip_spaces(text, stop)
    chain = position
    nearest = -1
    while position > 0:
        start = _word_start(text, position)
        word = text[start:position].replace("’", "'").lower()
        # A word glued to what precedes it ("donne-le", a host name) is not free.
        glued = start > 0 and text[start - 1] not in " \t\n" + _OPENERS
        # A capital letter alone is a label ("A, B."), not an article.
        if (
            start == position
            or glued
            or text[start:position].isupper()
            and len(word) == 1
        ):
            break
        before = _skip_spaces(text, start)
        clause_start = before == 0 or text[before - 1] in "\n.!?" + _OPENERS
        if (
            word in _FUNCTION_WORDS
            or word in _CONJUNCTIONS
            or (word in _POINTERS and clause_start)
        ):
            if nearest < 0:
                nearest = _token_start(text, start)
            chain = start
            position = before
            continue
        break
    return chain, nearest


_TOKEN = re.compile(r"[\w'’]+|[^\w\s]|\n")


def _tokens(text: str) -> list[str]:
    return [token.replace("’", "'") for token in _TOKEN.findall(text.lower())]


class _Before:
    """The word pairs of the text before the edit, read once (linear time).

    ``followed``: pairs followed by a word; ``closed``: pairs with the mark that
    closed them ("\n" at a line end or at the end of the text).
    """

    def __init__(self, text: str) -> None:
        tokens = _tokens(text)
        self.followed: set[tuple[str, str]] = set()
        self.closed: set[tuple[str, str, str]] = set()
        previous = ""
        for index, token in enumerate(tokens):
            if token == "\n":
                previous = ""
                continue
            pair = (previous, token)
            following = tokens[index + 1] if index + 1 < len(tokens) else "\n"
            if following in _CLOSERS or following == "\n":
                self.closed.add((*pair, following))
            else:
                self.followed.add(pair)
            previous = token

    def orphaned(self, evidence: str, closer: str) -> bool:
        """True when the edit removed what followed this pair before it."""
        tokens = _tokens(evidence)[-2:]
        pair = ("", tokens[0]) if len(tokens) == 1 else (tokens[0], tokens[1])
        return pair in self.followed and (*pair, closer) not in self.closed


def _join(left: str, right: str) -> str:
    """Join two pieces around a removal without orphaned punctuation."""
    left = left.rstrip(" \t")
    sentence_start = not left or left[-1] in "\n.!?"
    if right[:1] in ",;:." and sentence_start:
        right = right[1:].lstrip(" \t")
    if sentence_start and right[:1].islower():
        # The words left out opened the sentence: the next one now does.
        right = right[0].upper() + right[1:]
    if right[:1] in ",;:" and left[-1:] in ("(", ",", ";", ":"):
        right = right[1:].lstrip(" \t")
    if right[:1] in ".!?)" and left[-1:] in ",;:":
        left = left[:-1].rstrip(" \t")
    if left.endswith("(") and right.startswith(")"):
        left, right = left[:-1].rstrip(" \t"), right[1:]
    if right[:1].isalnum() and left[-1:] not in ("", "\n", "(", "["):
        return left + " " + right
    return left + right


# Space runs are entered at their first character only: every expression stays
# linear on a long run.
_DOUBLED = re.compile(r"(?<=\S)[ \t]{2,}(?=\S)")
_SPACED = re.compile(r"(?<=\w)[ \t]+(?=[,.)])")
# Brackets a removal left empty, after a space: a call such as print() has none.
_EMPTY = re.compile(r"(?<![ \t])[ \t]+(?:\([ \t]*\)|\[[ \t]*\](?!\())")
_STOP = re.compile(r"[,;:.!?)\]\n]")


def _around(text: str, match: re.Match[str]) -> str:
    return text[max(0, match.start() - 12) : match.end() + 1]


def _existing(original: str, pattern: re.Pattern[str]) -> set[str]:
    """The marks of this kind the text held before the edit, with their context."""
    return {_around(original, match) for match in pattern.finditer(original)}


def _inside(spans: list[tuple[int, int]], starts: list[int], position: int) -> bool:
    index = bisect_right(starts, position) - 1
    return index >= 0 and position < spans[index][1]


# Each repair copies the text once: past this many in one pass (no real answer
# comes near it), the rest is left as it is so the pass stays linear.
_MAX_REPAIRS = 64
_FOLLOWERS = _FUNCTION_WORDS | _CONJUNCTIONS


def _repair_words(before: _Before, doubled: set[str], text: str) -> str:
    """Leave out the words a removal left without their object, outside code."""
    from services.orchestrator.citation_fragments import code_spans

    spans = code_spans(text)
    starts = [start for start, _ in spans]
    repairs = 0
    # A removal inside a sentence leaves two spaces where it was: the words
    # before them dangle when a preposition or a conjunction follows
    # ("to be checked on  and on the website").
    stops = [(m.start(), "") for m in _STOP.finditer(text)]
    stops += [
        (m.start(), m[0])
        for m in _DOUBLED.finditer(text)
        if _around(text, m) not in doubled
    ]
    stops.append((len(text), ""))
    floor = len(text) + 1
    for stop, gap in sorted(stops, reverse=True):
        # A stop inside words already left out, or inside code, is not one.
        if stop >= floor or _inside(spans, starts, stop):
            continue
        if repairs >= _MAX_REPAIRS:
            break
        chain, evidence = _dangling(text, stop)
        end = _skip_spaces(text, stop)
        if chain == end or evidence < 0:
            continue
        right = text[stop:]
        if gap:
            after = right.lstrip(" \t")
            found = _TOKEN.match(after)
            following = found[0].lower().replace("’", "'") if found else ""
            if following not in _FOLLOWERS:
                continue
            if found and following in _CONJUNCTIONS:
                after = after[found.end() :].lstrip(" \t")
            closer = ""
            right = after
        else:
            closer = text[stop : stop + 1] or "\n"
        if not before.orphaned(text[evidence:end], closer):
            continue
        text = _join(text[:chain], right)
        floor = chain
        repairs += 1
    return text


def _spaces(original: str, text: str) -> str:
    """Empty brackets and spaces a removal left, never ones already there."""
    from services.orchestrator.citation_fragments import split_code

    for pattern, replacement in ((_EMPTY, ""), (_DOUBLED, " "), (_SPACED, "")):
        existing = _existing(original, pattern)
        parts = split_code(text)
        for index in range(0, len(parts), 2):
            current = parts[index]

            def new(
                match: re.Match[str], value: str = replacement, current: str = current
            ) -> str:
                # Written so before the edit, the mark stays.
                return match[0] if _around(current, match) in existing else value

            parts[index] = pattern.sub(new, current)
        text = "".join(parts)
    return text


# Separators a removal can leave side by side, strongest first: the colon
# still introduces what follows, a dash only opened the removed aside.
_SEPARATORS = ":;,"
_DASHES = "—–"


def _mark_runs(text: str, after_code: bool = False) -> list[tuple[int, int, str]]:
    """(start, end, marks) of each run of two or more separators in prose.

    A run starts after a word, a closing mark or code, holds only separators and
    spaces, and is followed by a space, a line end or the end of the text:
    ":-)", "12:30", "a,b" and "http://" are never one. A hyphen counts only
    between spaces, as a dash written with it.
    """
    runs: list[tuple[int, int, str]] = []
    index, size = 0, len(text)
    while index < size:
        if text[index] not in " \t" + _SEPARATORS + _DASHES + "-":
            index += 1
            continue
        start = index
        # At the start of prose that follows code, the code closed the word.
        if (start == 0 and not after_code) or (
            start > 0 and text[start - 1] in " \t\n" + _SEPARATORS + _DASHES
        ):
            index += 1
            continue
        marks = ""
        position = start
        while position < size:
            char = text[position]
            if char in " \t":
                position += 1
            elif char in _SEPARATORS or char in _DASHES:
                marks += char
                position += 1
            elif (
                char == "-"
                and text[position - 1] in " \t"
                and (position + 1 == size or text[position + 1] in " \t\n")
            ):
                marks += "—"
                position += 1
            else:
                break
        end = position
        while end > start and text[end - 1] in " \t":
            end -= 1
        followed = end == size or text[end] in " \t\n"
        if len(marks) >= 2 and followed:
            runs.append((start, end, marks))
        index = max(position, start + 1)
    return runs


# French typography puts a space before a colon or a semicolon.
_SPACED_COLON = re.compile(r"[^\s] [:;](?=\s)")


def _reduced(marks: str, spaced: bool) -> str:
    """The one mark a run of separators keeps; spaced: the text writes " :"."""
    for mark in _SEPARATORS:
        if mark in marks:
            return (" " if spaced and mark != "," else "") + mark
    return " " + marks[0]


def _separator_runs(original: str, text: str) -> str:
    """Reduce the separator runs a removal left, outside code."""
    from services.orchestrator.citation_fragments import split_code

    def context(value: str, start: int, end: int) -> str:
        return value[max(0, start - 12) : end + 1]

    existing = {context(original, start, end) for start, end, _ in _mark_runs(original)}
    spaced = any(_SPACED_COLON.search(part) for part in split_code(original)[::2])
    parts = split_code(text)
    for index in range(0, len(parts), 2):
        current = parts[index]
        pieces: list[str] = []
        last = 0
        for start, end, marks in _mark_runs(current, index > 0):
            if context(current, start, end) in existing:
                continue
            pieces += [current[last:start], _reduced(marks, spaced)]
            last = end
        parts[index] = "".join(pieces) + current[last:]
    return "".join(parts)


def repair(original: str, edited: str) -> str:
    """edited, with the words and marks its removals left dangling repaired."""
    if original == edited:
        return edited
    before = _Before(original)
    doubled = _existing(original, _DOUBLED)
    for _ in range(2):
        repaired = _separator_runs(
            original, _spaces(original, _repair_words(before, doubled, edited))
        )
        if repaired == edited:
            break
        edited = repaired
    return edited
