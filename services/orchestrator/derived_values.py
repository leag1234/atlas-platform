"""Values that follow by calculation from the figures a conversation gives.

Class suite, staging 2026-11-24 (class C7): a one-page PDF had to show lengths
computed from the counts and sizes the person gave (a number of pieces times a
length, the sum of two lots, the same values in another unit). The file came
back with "[result to add]" in every result cell. The rule for the person's own
facts (own_facts.py) asks for a bracketed placeholder where a figure about
their case was not given; read word by word, a computed total is "a figure the
messages do not give", and the own-facts question listed every result line.

The rule: a value that follows by calculation from figures given in the
conversation or its files is computed and written; a placeholder stands only
for what cannot be derived (an observation, a measurement or an outcome nobody
gave). Two uses here, both bounded and without any model call:

- derivable(): whether a stated figure is one of the given figures, or follows
  from them by one operation between two of them (product, sum, difference,
  ratio), by a sum or difference of two such results (the total of two lots),
  each result possibly scaled by a power of ten (a unit conversion, a
  percentage). A single given figure is scaled only by a factor of 100 or more
  (mm to m, g to kg, a share as a percentage): "40 hours" never follows from
  "4 days". Own facts use it so that a computed value is not taken for an
  invented one.
- placeholder_gaps(): in a built file, a bracketed placeholder on a table row
  or a line that holds two or more of the given figures (the inputs of the
  value) stands for a value that can be computed; the other placeholders of
  the same table columns (a total row) go with it. The file is refused once
  (deliverable_check) with the cells to compute.

In this repository the workbook calculation check (calculation_check)
recomputes formula caches and number_provenance matches written calculations;
neither tells a computable value from a missing one. The closure here is small
enough to search exhaustively.
"""

from __future__ import annotations

from bisect import bisect_left, bisect_right
from collections.abc import Iterable, Sequence
from decimal import Decimal, InvalidOperation, localcontext
import re
from typing import Any

from services.orchestrator.number_provenance import NUMBER, number, places

# Bounds: the figures a message gives are few; the closure stays exhaustive.
MAX_FIGURES = 12
# Sums of two results only from this many figures on (a longer list would
# make almost every integer reachable).
MAX_PAIR_SUMS = 8
SCALES = tuple(Decimal(10) ** exponent for exponent in range(-6, 7))
# A single given figure is converted, never multiplied by ten.
CONVERSIONS = tuple(
    scale for scale in SCALES if scale >= 100 or scale <= Decimal("0.01")
)
MAX_LISTED = 6

PLACEHOLDER = re.compile(r"\[[^\[\]\n]{1,80}\]")
WHOLE_PLACEHOLDER = re.compile(r"\s*\[[^\[\]\n]{1,80}\]\s*")
# A written calculation on a line: two figures joined by an operator, or "=".
CALCULATION = re.compile(r"\d\s*[×x*/÷+−-]\s*\d|=")

GAP = (
    "bracketed placeholders standing for values that follow by calculation "
    "from figures the person gave: {cells}. Compute each of these values from "
    "the figures of the request and the earlier messages (with the code tool "
    "when needed) and write it in place of its placeholder, in the unit and "
    "number format asked; keep a placeholder only for a value that cannot be "
    "derived from what was given (an observation, a measurement or an outcome "
    "nobody gave)"
)


def figures(text: str) -> list[Decimal]:
    """The figures a text writes, in order, repeats kept (12 boxes of 12)."""
    found: list[Decimal] = []
    for match in NUMBER.finditer(text):
        value = number(match[0])
        if value is not None and value.is_finite():
            found.append(value)
    return found


def _results(given: Sequence[Decimal]) -> tuple[list[Decimal], list[Decimal]]:
    """The given figures and the results of one operation between two of them."""
    base = list(given[:MAX_FIGURES])
    results: set[Decimal] = set()
    with localcontext() as context:
        context.prec = 28
        for index, first in enumerate(base):
            for second in base[index + 1 :]:
                results.update((first * second, first + second, abs(first - second)))
                try:
                    if second:
                        results.add(first / second)
                    if first:
                        results.add(second / first)
                except (InvalidOperation, ArithmeticError):
                    continue
    return sorted(set(base)), sorted(results)


def _within(ordered: list[Decimal], low: Decimal, high: Decimal) -> bool:
    return bisect_left(ordered, low) < bisect_right(ordered, high)


def derivable(stated: Decimal, decimals: int, given: Sequence[Decimal]) -> bool:
    """Whether a figure written with this many decimals is given or follows from
    the given figures (see the module docstring); rounding as written."""
    if not given or not stated.is_finite():
        return False
    try:
        half = Decimal(5).scaleb(-decimals - 1)
    except InvalidOperation:
        return False
    base, results = _results(given)
    sums = len(base) <= MAX_PAIR_SUMS
    with localcontext() as context:
        context.prec = 28
        low, high = abs(stated) - half, abs(stated) + half
        if _within(base, low, high) or _within(results, low, high):
            return True
        for scale in CONVERSIONS:
            if _within(base, low / scale, high / scale):
                return True
        # The total of two lots: two results added or subtracted.
        operands = results
        for scale in SCALES:
            lowest, highest = low / scale, high / scale
            if _within(results, lowest, highest):
                return True
            if not sums:
                continue
            for value in operands:
                # value + other or value - other lands in the window.
                if _within(operands, lowest - value, highest - value) or _within(
                    operands, value - highest, value - lowest
                ):
                    return True
    return False


def without_derivable(text: str, given_texts: Iterable[str]) -> str:
    """The text without the figures that are given or follow from the given ones."""
    given = figures(" ".join(given_texts))

    def kept(match: re.Match[str]) -> str:
        value = number(match[0])
        if value is not None and derivable(value, places(match[0]), given):
            return " "
        return match[0]

    return NUMBER.sub(kept, text)


def _given_in(texts: Iterable[str], given: set[Decimal]) -> int:
    return sum(value in given for text in texts for value in figures(text))


def _table_cells(table: Any, given: set[Decimal]) -> list[str]:
    from packages.doc_render.cells import shown

    found: list[str] = []
    headers = [shown(str(cell)) for cell in table.headers]
    rows = [[shown(str(cell)) for cell in row] for row in table.rows]
    computable_columns: set[int] = set()
    for row in rows:
        holes = [i for i, cell in enumerate(row) if WHOLE_PLACEHOLDER.fullmatch(cell)]
        if (
            holes
            and _given_in(
                [cell for cell in row if not WHOLE_PLACEHOLDER.fullmatch(cell)], given
            )
            >= 2
        ):
            computable_columns.update(holes)
    if not computable_columns:
        return found
    for row in rows:
        label = next(
            (cell for cell in row if cell and not WHOLE_PLACEHOLDER.fullmatch(cell)),
            "",
        )
        for column in sorted(computable_columns):
            if column < len(row) and WHOLE_PLACEHOLDER.fullmatch(row[column]):
                name = headers[column] if column < len(headers) else ""
                found.append(
                    " / ".join(filter(None, (label.strip(), name.strip())))
                    + f" ({row[column].strip()})"
                )
    return found


def placeholder_cells(content: Any, given_texts: Iterable[str]) -> list[str]:
    """The placeholders of a file that stand for computable values."""
    given = set(figures(" ".join(given_texts)))
    if len(given) < 2 or content is None:
        return []
    found: list[str] = []
    for section in getattr(content, "sections", ()):
        for table in getattr(section, "tables", ()):
            found.extend(_table_cells(table, given))
        for line in (
            *getattr(section, "paragraphs", ()),
            *getattr(section, "bullets", ()),
        ):
            text = str(line)
            rest = PLACEHOLDER.sub(" ", text)
            if (
                PLACEHOLDER.search(text)
                and CALCULATION.search(rest)
                and _given_in([rest], given) >= 2
            ):
                found.append(" ".join(text.split())[:120])
    return list(dict.fromkeys(found))


def placeholder_gaps(content: Any, given_texts: Iterable[str]) -> list[str]:
    """One gap naming the placeholders to compute, or none."""
    cells = placeholder_cells(content, given_texts)
    if not cells:
        return []
    listed = "; ".join(cells[:MAX_LISTED]) + (
        "; ..." if len(cells) > MAX_LISTED else ""
    )
    return [GAP.format(cells=listed)]
