"""Checkers by code, one per constraint kind, on a structured view.

Each returns passed True or False with the evidence code saw (a quotation of
what it read), or None when code cannot decide (the verifier or nobody
then decides; an undecided constraint is never claimed and never reported
as a failure). Comparisons ignore case, spacing, quote style and markdown,
and trailing punctuation for "begins with" and "ends with": a stricter
reading would send right answers to repair.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from itertools import permutations
import re
from typing import Any, Sequence

from services.orchestrator.constraints.model import Constraint, fold, norm
from services.orchestrator.constraints.views import Block, View

TEXT_KINDS = frozenset(
    {
        "heading",
        "paragraph",
        "card",
        "bullet",
        "numbered",
        "checkbox",
        "quote",
        "code",
        "cell",
        "control",
    }
)
PROSE_KINDS = frozenset(
    {"paragraph", "card", "bullet", "numbered", "checkbox", "quote"}
)
LIST_KINDS = {"bullets": "bullet", "numbered": "numbered", "checkboxes": "checkbox"}
EVIDENCE = 120


@dataclass(frozen=True)
class Result:
    id: str
    passed: bool | None
    evidence: str = ""
    by: str = "code"


def _short(text: str) -> str:
    text = " ".join(text.split())
    return text if len(text) <= EVIDENCE else text[: EVIDENCE - 1] + "…"


def _edges(text: str) -> str:
    """Text compared at its start or end: trailing punctuation and symbols aside."""
    return re.sub(r"^[\W_]+|[\W_]+$", "", norm(text))


def _scope(view: View, item: Constraint) -> list[Block]:
    blocks = view.select(item.page, item.region)
    if not item.region and view.kind != "answer":
        # Speaker notes are not on the slide: a requirement about the file is
        # met by what the reader sees. Where the text begins or ends, and its
        # sentences and lists, are read in the body, not in running headers
        # and footers.
        apart = {"notes"}
        if item.kind not in {"contains", "excludes", "order"}:
            apart |= {"header", "footer"}
        blocks = [block for block in blocks if block.region not in apart]
    return blocks


def _texts(blocks: Sequence[Block]) -> list[Block]:
    return [block for block in blocks if block.kind in TEXT_KINDS]


def _pages(view: View, item: Constraint) -> list[int]:
    if item.page:
        return [item.page]
    if item.each and view.pages:
        return list(range(1, view.pages + 1))
    return [0]


def _relation(found: int, item: Constraint) -> bool:
    assert item.count is not None
    if item.relation == "max":
        return found <= item.count
    if item.relation == "min":
        return found >= item.count
    return found == item.count


_ABBREVIATION = re.compile(
    r"\b(?:[A-Za-zÀ-ÿ]|z\.\s?B|e\.g|i\.e|etc|p\.\s?ex|bzw|usw|vgl|ca|env|nr|no|art|"
    r"sr|sra|dr|mr|mrs|ms|st|p|pp|ex|cf|vs)\.$",
    re.IGNORECASE,
)


def sentence_count(text: str) -> int:
    """Sentences of prose: terminal punctuation followed by a space or the end,
    abbreviations and decimals aside; a fragment without one counts once."""
    text = " ".join(text.split())
    if not text:
        return 0
    count = 0
    start = 0
    for match in re.finditer(r"[.!?…]+[\"»”’)\]]*(?=\s|$)", text):
        piece = text[start : match.end()]
        if match.group(0).startswith(".") and _ABBREVIATION.search(
            piece.rstrip('"»”’)] ')
        ):
            continue
        if re.search(r"\w", piece):
            count += 1
        start = match.end()
    if re.search(r"\w", text[start:]):
        count += 1
    return count


_LEAD_WORDS = re.compile(
    r"^(?:voici|voila|here is|here s|here are|sure|of course|certainly|bien sur|"
    r"avec plaisir|d accord|hier ist|hier sind|hier die|hier der|hier das|gerne|klar|"
    r"aqui tienes|aqui esta|aqui van|claro|por supuesto|ecco|certo|certamente|"
    r"ok|okay|alright|tres bien|tres bonne)\b"
)


def lead_in(view: View) -> Block | None:
    """The first block when it only introduces what follows."""
    blocks = [
        block for block in view.blocks if block.kind in TEXT_KINDS | {"file", "picture"}
    ]
    if len(blocks) < 2 or blocks[0].kind not in {"paragraph", "heading"}:
        return None
    first = blocks[0].text
    words = len(first.split())
    if first.rstrip().endswith(":") and words <= 30:
        return blocks[0]
    if _LEAD_WORDS.match(fold(first)) and words <= 14:
        return blocks[0]
    return None


def check(item: Constraint, view: View, facts: dict[str, Any] | None = None) -> Result:
    """The verdict of code on one constraint; None for kinds code cannot read."""
    facts = facts or {}
    kind = item.kind
    if kind == "judge":
        return Result(item.id, None)
    if kind == "no_new_file":
        produced = facts.get("produced")
        if produced is None:
            return Result(item.id, None)
        return Result(
            item.id,
            not produced,
            ""
            if not produced
            else "this turn made: " + _short(str(facts.get("made") or "a file")),
        )
    if kind == "only":
        raw = facts.get("raw")
        if not isinstance(raw, str):
            return Result(item.id, None)
        from services.orchestrator.only_request import restrict

        reduced = restrict(raw)
        if norm(reduced) != norm(raw):
            return Result(
                item.id,
                False,
                "around the requested output: " + _short(_removed(raw, reduced)),
            )
        # "Only X" leaves no room for a sentence introducing X either.
        lead = lead_in(view)
        return Result(
            item.id,
            lead is None,
            "" if lead is None else "begins with: " + _short(lead.text),
        )
    if kind == "no_intro":
        found = lead_in(view)
        return Result(
            item.id,
            found is None,
            "" if found is None else "begins with: " + _short(found.text),
        )
    if kind == "after_file":
        return _after_file(item, view)
    if kind == "titles":
        return _titles(item, view)

    verdicts: list[tuple[bool | None, str]] = []
    for page in _pages(view, item):
        scoped = replace(item, page=page) if page != item.page else item
        verdicts.append(_one(scoped, view))
    failed = [evidence for ok, evidence in verdicts if ok is False]
    if failed:
        return Result(item.id, False, _short("; ".join(failed[:3])))
    if any(ok is None for ok, _ in verdicts):
        return Result(item.id, None)
    return Result(item.id, True, verdicts[0][1] if verdicts else "")


def _one(item: Constraint, view: View) -> tuple[bool | None, str]:
    blocks = _texts(_scope(view, item))
    where = f"page {item.page}: " if item.page and view.kind != "answer" else ""
    if item.kind == "starts_with":
        first = blocks[0].text if blocks else ""
        ok = _edges(first).startswith(_edges(item.text))
        return ok, where + "begins with: " + _short(first or "(nothing)")
    if item.kind == "ends_with":
        last = blocks[-1].text if blocks else ""
        ok = _edges(last).endswith(_edges(item.text))
        return ok, where + "ends with: " + _short(last[-EVIDENCE:] or "(nothing)")
    text = "\n".join(block.text for block in blocks)
    if item.kind == "contains":
        ok = _edges(item.text) in norm(text)
        return ok, where + ("found" if ok else "not found: " + _short(item.text))
    if item.kind == "excludes":
        pattern = re.compile(r"(?<!\w)" + re.escape(fold(item.text)) + r"(?!\w)")
        hit = pattern.search(fold(text))
        return hit is None, where + (
            "absent" if hit is None else "found: " + _short(item.text)
        )
    if item.kind == "sentences":
        prose = " ".join(block.text for block in blocks if block.kind in PROSE_KINDS)
        found = sentence_count(prose)
        return _relation(found, item), f"{where}{found} sentence(s)"
    if item.kind == "paragraphs":
        # A paragraph of prose; a run of list items reads as one.
        count = 0
        previous = ""
        for block in blocks:
            in_list = block.kind in {"bullet", "numbered", "checkbox"}
            if block.kind in {"paragraph", "quote"} or (
                in_list and previous not in {"bullet", "numbered", "checkbox"}
            ):
                count += 1
            previous = block.kind
        return _relation(count, item), f"{where}{count} paragraph(s)"
    if item.kind in {"items", "list_style"}:
        listed = [
            block
            for block in blocks
            if block.kind in {"bullet", "numbered", "checkbox"}
        ]
        if item.kind == "items":
            # Every list item counts: the style is list_style's to check, so a
            # list in the wrong style does not hide a wrong count.
            return _relation(len(listed), item), f"{where}{len(listed)} item(s)"
        if item.style == "plain":
            return not listed, f"{where}{len(listed)} list item(s)"
        wanted = LIST_KINDS[item.style]
        if wanted == "checkbox" and not view.boxes_known:
            # The format draws its boxes: code cannot tell them from bullets.
            return None, ""
        other = [block for block in listed if block.kind != wanted]
        if not listed:
            cards = [block for block in blocks if block.kind == "card"]
            shown = cards or [block for block in blocks if block.kind == "paragraph"]
            return False, where + (
                "no list; points drawn as separate cards: "
                if cards
                else "no list; text as paragraphs: "
            ) + _short(" | ".join(block.text for block in shown[:2]))
        return not other, where + (
            f"{len(listed)} {wanted} item(s)"
            if not other
            else f"{len(other)} item(s) of another kind: " + _short(other[0].text)
        )
    if item.kind == "order":
        folded = norm(text)
        position = -1
        for entry in item.items:
            found = folded.find(_edges(entry), position + 1)
            if found < 0:
                return False, where + "missing or out of order: " + _short(entry)
            position = found
        return True, where + "in order"
    return True, ""


def _titles(item: Constraint, view: View) -> Result:
    if view.kind not in {"deck", "workbook"}:
        # A reply, a document or a page: its section headings in order; the
        # document's own title is not one of them.
        titles = [
            block.text
            for block in view.blocks
            if block.kind == "heading" and block.region != "title"
        ]
    else:
        # A slide or a sheet: its title.
        titles = []
        for page in range(1, view.pages + 1):
            first = next(
                (block.text for block in view.select(page) if block.region == "title"),
                "",
            )
            titles.append(first)
    wanted = [_edges(entry) for entry in item.items]
    seen = [_edges(entry) for entry in titles]
    if seen == wanted:
        return Result(item.id, True, f"{len(seen)} titles in order")
    for index, entry in enumerate(wanted):
        got = seen[index] if index < len(seen) else "(none)"
        if got != entry:
            return Result(
                item.id,
                False,
                _short(
                    f"{len(seen)} titles; position {index + 1}: {titles[index] if index < len(titles) else '(none)'}"
                ),
            )
    return Result(item.id, False, f"{len(seen)} titles for {len(wanted)} asked")


def _after_file(item: Constraint, view: View) -> Result:
    files = [
        index
        for index, block in enumerate(view.blocks)
        if block.kind in {"file", "picture"}
    ]
    lines = [
        index
        for index, block in enumerate(view.blocks)
        if block.kind in TEXT_KINDS and _edges(block.text).startswith(_edges(item.text))
    ]
    if not lines:
        return Result(item.id, False, "no line beginning with " + _short(item.text))
    if not files:
        return Result(item.id, None, "no file link in the reply")
    if min(lines) > min(files):
        return Result(item.id, True, "after the file link")
    return Result(
        item.id, False, "above the file link: " + _short(view.blocks[lines[0]].text)
    )


def _removed(raw: str, reduced: str) -> str:
    kept = set(norm(line) for line in reduced.splitlines() if line.strip())
    gone = [
        line.strip()
        for line in raw.splitlines()
        if line.strip() and norm(line) not in kept
    ]
    return " | ".join(gone[:3])


# ---- constraints about one part of the reply ---------------------------------------

LIST_ITEMS = frozenset({"bullet", "numbered", "checkbox"})
MAX_PARTS = 6


def list_runs(view: View) -> list[tuple[int, bool]]:
    """The reply's lists in order: the number of items of each run of list
    items, and whether a heading or a label line ("Option B:") introduces it.
    Any other block (a heading, a paragraph, a file link) ends a run."""
    runs: list[tuple[int, bool]] = []
    count = 0
    headed = False
    previous: Block | None = None
    for block in view.blocks:
        if block.kind in LIST_ITEMS:
            if not count:
                headed = previous is not None and (
                    previous.kind == "heading"
                    or (
                        previous.kind == "paragraph"
                        and previous.text.rstrip().endswith(":")
                        and len(previous.text.split()) <= 14
                    )
                )
            count += 1
        else:
            if count:
                runs.append((count, headed))
            count = 0
        previous = block
    if count:
        runs.append((count, headed))
    return runs


def part_results(items: Sequence[Constraint], view: View) -> dict[str, Result]:
    """Constraints about one part of the reply (one of two outlines, each
    option), checked per part when the parts are clearly delimited, otherwise
    undecided (left to the verifier, never stated as a gap).

    Only list-item counts are read per part: each run of list items is a part.
    "each": every part meets the count. "one": the constraints about single
    parts are matched one to one with the parts, and only when there are as
    many parts as such constraints; a count no part meets is a failure."""
    results = {item.id: Result(item.id, None) for item in items}
    runs = list_runs(view)
    if len(runs) < 2:
        return results
    counted = [
        item for item in items if item.kind == "items" and item.count is not None
    ]
    sizes = ", ".join(str(size) for size, _ in runs)
    for item in counted:
        if item.part != "each":
            continue
        failing = [
            index for index, (size, _) in enumerate(runs) if not _relation(size, item)
        ]
        if not failing:
            results[item.id] = Result(
                item.id, True, f"{len(runs)} lists: {sizes} item(s)"
            )
        elif all(headed for _, headed in runs):
            # Every list is introduced as a part of its own: a part that does
            # not meet the count is certain.
            first = failing[0]
            results[item.id] = Result(
                item.id, False, f"list {first + 1}: {runs[first][0]} item(s)"
            )
    ones = [item for item in counted if item.part == "one"]
    if ones and len(ones) == len(runs) and len(ones) <= MAX_PARTS:
        best = max(
            permutations(range(len(runs))),
            key=lambda order: sum(
                _relation(runs[index][0], item) for item, index in zip(ones, order)
            ),
        )
        for item, index in zip(ones, best):
            size = runs[index][0]
            results[item.id] = Result(
                item.id,
                _relation(size, item),
                f"list {index + 1}: {size} item(s)"
                if _relation(size, item)
                else f"lists of {sizes} item(s)",
            )
    return results
