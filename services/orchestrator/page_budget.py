"""The page budget of a report asked with a page size, before it is written.

Staging -167 (2026-11-22): "a short word report ... about one page" came on
two pages in its first version (226 words); the writer learnt the size only
from the refusal of its first publication, and neither that rewrite nor the
compact layout (project_document_tools.compacted_to_size) always brought it
to one page. A change of a delivered file already gets the room measured on
its print (current_file.room_note); a new report now gets, before it is
written, the budget of the house layout measured on LibreOffice's print.

Measured on the report layout of packages/doc_render/renderer.py (Letter, one
inch margins, the template's type) with LibreOffice, 2026-11-22: 523 words
of plain paragraphs fill 78 % of a page (about 530 words a page); ten
headings with 26 words each fill a page (a heading takes the room of about
25 words); 25 list items of 13 words fill 54 % (an item takes a full line,
about 12 words).
"""

from __future__ import annotations

from decimal import Decimal
import json
import logging
from typing import Any

PLAIN_WORDS = 530
HEADING_WORDS = 25
LINE_WORDS = 12
TITLE_WORDS = 40
# A report as writers make it (a title, four to six headings, lists): the
# battery's one-page reports printed on one page up to about 230 words in
# all (measured capacities 210 to 250, staging -159 to -166).
REPORT_WORDS = 230
# The share of the pages the budget fills, to land under them.
BUDGET_SHARE = 0.85
SECTIONS_PER_PAGE = 4

# Staging -170: told that a page holds 530 words of plain paragraphs and how
# to count headings, the writer wrote 271 and 360 words: a writer does not
# count. It now gets one number of words in all and a number of sections.
BRIEF = (
    "The person asks for about {size} {unit}. With this report layout, a "
    "report of headings and short lists holds about {report} words a page: "
    "write at most about {budget} words in all (title, headings, list items "
    "and table cells counted), in at most {sections} short sections, so that "
    "it prints on {size} {unit}."
)


def brief(size: int) -> str:
    """The budget said to the writer of a report of this many pages."""
    return BRIEF.format(
        size=size,
        unit="page" if size == 1 else "pages",
        report=REPORT_WORDS,
        budget=int(REPORT_WORDS * size * BUDGET_SHARE),
        sections=SECTIONS_PER_PAGE * size,
    )


async def first_brief(tools: Any) -> str:
    """The page budget of the new report this turn writes; empty when the
    turn changes a delivered file (it gets the measured room), writes no
    Word report, or the person asked no page size.

    The page size is read from the request by the ledger's extraction (the
    same reading the check of the file makes), with a ledger of its own.
    """
    from services.orchestrator import deliverable_check, expectations

    if (
        getattr(tools, "current_file", None) is not None
        or getattr(tools, "requested_suffix", None) != ".docx"
        or not expectations.enabled()
        or not deliverable_check.enabled()
    ):
        return ""
    model = getattr(tools, "check_model", None)
    profile = getattr(model, "profile", None)
    request = getattr(tools, "request_text", "")
    if not isinstance(profile, str) or not isinstance(request, str) or not request:
        return ""

    def spend(amount: Decimal) -> None:
        spent = getattr(model, "spent", None)
        if isinstance(spent, Decimal):
            setattr(model, "spent", spent + amount)

    earlier = [
        str(item)
        for item in (getattr(tools, "earlier_requests", None) or [])
        if isinstance(item, str)
    ]
    turn = expectations.TurnLedger(
        "page-budget",
        expectations.Ledger("document"),
        profile,
        str(getattr(tools, "lang", "") or "en"),
        strict=bool(getattr(model, "strict", False)),
        spend=spend,
        complete=getattr(tools, "expectations_complete", None),
    )
    try:
        await turn.update(request, earlier, len(earlier) + 1)
    except Exception:  # noqa: BLE001 - the writer works without the budget
        return ""
    sizes = [
        int(item.check["count"])
        for item in turn.ledger.active()
        if item.method == "code"
        and item.check.get("part") == "size"
        and item.check.get("unit") == "pages"
        and item.check.get("relation") in {"about", "exact", "max"}
    ]
    if not sizes:
        return ""
    size = min(sizes)
    # Proof the budget reached the writer (staging -170 asked for it).
    logging.getLogger(__name__).warning(
        json.dumps(
            {
                "event": "page_budget_given",
                "pages": size,
                "words": int(REPORT_WORDS * size * BUDGET_SHARE),
                "sections": SECTIONS_PER_PAGE * size,
            }
        )
    )
    return brief(size)
