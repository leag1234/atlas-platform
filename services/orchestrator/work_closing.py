"""The end of a Work turn once the file it asked for is published.

After publish_document returned, the loop made one more model call that read the
whole turn again and answered the request a second time: a closing message of
1,150 to 1,850 characters restating the document, its sections and its sources.
On staging (corpus of 2026-11-13, 14 document turns that published their file)
the turn ended 38 to 60 seconds after the publication, under "Checking the
result…"; twice the turn's time limit cut that call and a delivered file ended
as a failed turn. The same call measured alone on the staging gateway, on the
context of one of those turns: 10 to 31 seconds, 325 to 1,014 output tokens.

Once the requested file is published, that call reads a closing instruction
(code text, after the tool results, like the closing of a chat turn on a search
limit in closing.py): one or two sentences when the file was all the request
asked for; otherwise the rest of the request, which the model still does. The
same call with the instruction: 1.4 to 3.5 seconds, 39 to 57 output tokens, also
when the project session context puts the question after it
(session_context.py); with "and give me the five essential rules here" the model
still wrote the five rules.

"Verified" is said only of a file whose check against the request ran and
passed (deliverable_check.verification, in the publish_document result): the
instruction said "published and verified" of every file, and a presentation
was announced as "created and verified" while the plan showed its check step
not done (staging 2026-11-20).

The links stay the code's (file_results.append_deliverable_links adds the ones
the answer lacks), and the source checks are unchanged: an address in the
closing message is kept only if it was retrieved in this turn
(source_truth.without_unretrieved, in the loop).
"""

from __future__ import annotations

from collections.abc import Sequence
import re
from typing import Any

# The tool that delivers a requested file and returns its receipt.
PUBLISHING = "publish_document"

CLOSING_INSTRUCTION = (
    "The requested file is published ({files}).{check} The user already "
    "gets its download and preview links with your reply: do not write them "
    "and do not say where they are (above, below). If the request asked "
    "only for this file, end the turn now: reply in one or two short sentences, "
    "in the language of the request, that say what was delivered. Do not "
    "summarize the document, list its sections or its sources, or offer more "
    "work. If the request also asks for something else (another file, an answer "
    "or a summary in the conversation), do that now instead."
)


# What the instruction says of the file check (deliverable_check.py).
VERIFIED = " It was checked against the request, and the check passed."
UNVERIFIED = (
    " It was not checked against the request: do not say that it was checked, "
    "verified or reviewed."
)


def published(tool: str, output: object) -> dict[str, Any] | None:
    """The receipt of the file this tool call delivered, or None."""
    if tool != PUBLISHING or not isinstance(output, dict) or output.get("error"):
        return None
    receipt = output.get("deliverable")
    return receipt if isinstance(receipt, dict) else None


# A file published with gaps a check found against the request
# (deliverable_check.py): the short closing must not claim what it lacks.
GAPS_INSTRUCTION = (
    " A check of the file against the request found that it lacks: {gaps}. "
    "Say so plainly in your reply, once, in the language of the request, as a "
    "fact of the file (never comment on the check or call a gap normal), and "
    "do not claim that the file has it. The file is delivered as it is: never "
    "say that a gap was fixed, removed or corrected."
)
# What the reply never tells: how the file was checked and redone in the turn.
# Staging -159: "the budget now appears explicitly", "the regeneration was not
# approved", "the file is not a proof that the presentation took place".
INTERNALS = (
    " Never tell how the file was checked, refused, corrected, restored, "
    "put back or regenerated during this turn, nor mention an approval, a "
    "check that passed or could not run, or what now appears in it."
)

# Sentences of a file answer that tell Atlas's own corrections of the turn
# ("the recommendation ... has been restored", staging -165), in the five
# interface languages. Only words of putting back or redoing: a change the
# person asked for ("the title has been changed") is never one of them.
FIX_NARRATION = re.compile(
    r"\b(?:"
    r"restored|reinstated|put back|re-?added|regenerated"
    r"|r\u00e9tabli[es]{0,2}|remise?s? en place|restaur\u00e9[es]{0,2}"
    r"|r\u00e9g\u00e9n\u00e9r\u00e9[es]{0,2}|r\u00e9int\u00e9gr\u00e9[es]{0,2}"
    r"|wiederhergestellt|wieder eingef\u00fcgt|regeneriert"
    r"|restaurad[oa]s?|restablecid[oa]s?|regenerad[oa]s?|reincorporad[oa]s?"
    r"|ripristinat[oaie]|rigenerat[oaie]|reinserit[oaie]"
    r")\b",
    re.IGNORECASE,
)
_SENTENCE = re.compile(r"(?<=[.!?])\s+")
_CLAUSE = re.compile(r",\s+(?:and|et|und|y|e)\s+|;\s+", re.IGNORECASE)


def without_fix_narration(text: str, request: str = "") -> str:
    """The answer without the clauses that tell a correction of the turn.

    A word whose stem the request itself uses ("restore the old title") is
    the person's change, and kept. A sentence of two clauses keeps the other
    one ("the file runs to 2 pages, and the recommendation has been
    restored" keeps "the file runs to 2 pages").
    """
    request = request.casefold()

    def told(part: str) -> bool:
        # The stem of the word ("restor" of "restored") in the request is
        # the person's own words ("restore the old title").
        return any(
            match.group(0).casefold()[:6] not in request
            for match in FIX_NARRATION.finditer(part)
        )

    paragraphs = []
    for paragraph in text.split("\n"):
        kept = []
        for sentence in _SENTENCE.split(paragraph):
            if not told(sentence):
                kept.append(sentence)
                continue
            clauses = [part for part in _CLAUSE.split(sentence) if not told(part)]
            if clauses:
                joined = ", ".join(part.strip() for part in clauses).rstrip(" .,;")
                kept.append(joined + ".")
        paragraphs.append(" ".join(kept))
    return "\n".join(paragraphs).strip()


# The length of the file, measured on it (project_document_tools.
# measured_length): an answer called a two-page report "one-page" while the
# check counted its size as met (staging -151, 2026-11-21).
LENGTH_INSTRUCTION = (
    " Measured on the built file, {lengths}. If your reply mentions the "
    "length of the file, it gives this measure: never call it shorter, and "
    "never say it fits on fewer pages or slides."
)
UNITS = {"slides": ("slide", "slides"), "pages": ("page", "pages")}


def lengths_text(receipts: Sequence[tuple[str, dict[str, Any]]]) -> str:
    """'report.docx has 2 pages', one clause per measured file."""
    clauses = []
    for name, length in receipts:
        said = [
            f"{count} {UNITS[unit][count != 1]}"
            for unit in ("slides", "pages")
            if isinstance(count := length.get(unit), int) and count > 0
        ]
        if said:
            clauses.append(f"{name or 'the file'} has {' and '.join(said)}")
    return "; ".join(clauses)


def verified(output: object) -> bool:
    """The check of the published file against the request ran and passed."""
    check = output.get("verification") if isinstance(output, dict) else None
    return isinstance(check, dict) and check.get("status") == "passed"


def closing_instruction(
    receipts: Sequence[dict[str, Any]],
    gaps: Sequence[str] = (),
    checked: bool = False,
    lengths: Sequence[tuple[str, dict[str, Any]]] = (),
) -> str:
    names = [str(receipt.get("relative_path") or "") for receipt in receipts]
    text = CLOSING_INSTRUCTION.format(
        files=", ".join(name for name in names if name) or "one file",
        # With gaps the check did not pass: the gaps say what it found.
        check=VERIFIED if checked and not gaps else "" if gaps else UNVERIFIED,
    )
    if gaps:
        text += GAPS_INSTRUCTION.format(gaps="; ".join(gaps))
    text += INTERNALS
    measured = lengths_text(lengths)
    if measured:
        text += LENGTH_INSTRUCTION.format(lengths=measured)
    return text


def closing_step(
    tools: Any, file_requested: bool, executed: Sequence[tuple[str, object]]
) -> str | None:
    """The instruction after a batch of tool results, or None.

    Only in a turn that asked for a file, after a batch that published one, and
    once no requested file is still missing.
    """
    if not file_requested:
        return None
    receipts = [
        receipt
        for tool, output in executed
        if (receipt := published(tool, output)) is not None
    ]
    if not receipts:
        return None
    pending = getattr(getattr(tools, "selection", None), "deliverable_pending", None)
    if callable(pending) and pending():
        return None
    gaps = [
        str(gap)
        for tool, output in executed
        if published(tool, output) is not None and isinstance(output, dict)
        for gap in output.get("gaps") or ()
    ]
    checked = all(
        verified(output)
        for tool, output in executed
        if published(tool, output) is not None
    )
    lengths = [
        (str(receipt.get("relative_path") or ""), output["length"])
        for tool, output in executed
        if (receipt := published(tool, output)) is not None
        and isinstance(output, dict)
        and isinstance(output.get("length"), dict)
    ]
    return closing_instruction(receipts, gaps, checked, lengths)
