"""A delivered file is checked against the request before the answer claims it.

Owner rule (2026-11-16): Atlas challenges its own result for any request.
Before a deliverable is announced, what was produced is compared with what was
asked; a gap is fixed, or said plainly. On staging a picture book asked with
"one emoji per word" was delivered three times as a list of words (the font
dropped every emoji) and each answer said the emoji were there.

T8 (code does and checks, the model decides):
- code measures the file it built (facts): format, pages, sections, tables
  with their columns and rows, pictures drawn (emoji and images), characters
  the format could not draw and left out, the text of the file;
- one small JSON call compares those facts, the text and, where the format has
  pages, a picture of the first page (vision role, not in strict privacy) with
  the request and the conversation's earlier messages, and lists what the file
  lacks against it (language-neutral: no keyword list, the model judges);
- when the turn holds a source the person provided (an attached document, a
  pasted text), the file's text is also checked against that source, in
  parallel (source_faithfulness.py): statements it does not support are gaps;
- every specific detail of the file (a date, a figure with its unit, a
  version, a name), read back from the built file (readback.py), is matched by
  code against the turn's evidence: the documents provided, the pages read and
  the tool results (unsourced_details.deliverable_details, the matcher of the
  answers). One not found is a gap code is sure of: removed, sourced or marked
  as not verified in the repair;
- the first time gaps are found in a turn, the file is not published and the
  agent gets them to repair (one repair attempt); a file published with gaps
  still open carries them, and the closing instruction makes the answer say
  so (work_closing.py);
- when the file is a new version of a file the person already has (a change
  of a delivered chat file, current_file.py), code measures what it removed,
  replaced, added or moved against the current version (readback.changes) and
  the check judges each change against the request: an unrequested loss is a
  gap like any other (refused once, then said); a version identical to the
  current one is a gap code is sure of; when the check cannot run, the answer
  must name the measured changes instead of claiming the rest was kept.

Survey (sources in the PR that added this file): self-verification before answering
(Chain-of-Verification, Dhuliawala et al. 2023; Self-Refine, Madaan et al.
2023; CRITIC, Gou et al. 2024, which shows that a model's critique is reliable
when grounded in tool output, not in its own belief), rubric checks of a
produced artifact (LLM-as-judge with explicit criteria), and in this repository
the vision checks of pictures (image_scene.py, pictures/judge.py, PR 477), the
workbook calculation check (calculation_check, one refusal then a note) and
the source checks (source_truth.py).

Cost and latency: no call for a plain chat answer; one call per published
file, at most MAX_CHECKS per turn, each bounded by CHECK_BUDGET_EUR and
CHECK_SECONDS. Measured on staging: see the PR. A check that fails or times
out never blocks the delivery: the deterministic facts still add their notes.
"""

from __future__ import annotations

import asyncio
import base64
from dataclasses import dataclass, field
from decimal import ROUND_FLOOR, Decimal
import io
import json
import logging
import os
import time
from typing import Any

from packages.doc_render import emoji
from services.orchestrator import own_facts

CHECK_SECONDS = 45.0
# Under this, the publication has no time to spare for the call.
MIN_SECONDS = 8.0
CHECK_BUDGET_EUR = Decimal("0.02")
CHECK_TOKENS = 500
# Per turn: the first file, its repair and one more file.
MAX_CHECKS = 3
# Bounded inputs: the file's text and the conversation's earlier messages.
TEXT_CHARACTERS = 6000
EARLIER_MESSAGES = 3
EARLIER_CHARACTERS = 1500
MAX_GAPS = 5

SYSTEM = (
    "You check a file that an assistant produced, before the assistant presents "
    "it to the user. You receive the user's request (with earlier messages of "
    "the conversation, which may add to it or correct it), facts that code "
    "measured on the produced file, the file's text, and sometimes a picture of "
    "its first page. Facts measured by code are true: trust them over the text. "
    "Judge whether the file does what the person wants, as a careful person "
    "would read the request, not only its words. A purpose the person gives "
    "(a deck to present to a board, a letter to send) is met by a file made "
    "for it: never ask for proof that the event happened. Where the file was "
    "made from (a file or notes the person names) is not a gap: the person's "
    "own notes are kept in the speaker notes or the file's record, never "
    "listed back on the slides or pages. A gap needs evidence: when the file's "
    "text shows an item, it is there. List what the file lacks or "
    "gets wrong against that intent: requested items, pictures or data that "
    "are missing; a picture that does not show its item (an unrelated or "
    "merely approximate picture, or an emoji or symbol standing in for a "
    "requested picture, illustration or photo, unless the person asked for "
    "emoji or symbols); a number of columns, rows, items, slides or pages that "
    "differs from the one asked; characters the file could not draw and left "
    "out when the request needs them; placeholder text where the request or "
    "the earlier messages give the content; invented or empty content; "
    "facts about the person's own team, organisation or project that nobody "
    "gave are checked apart, not here: when facts.person_gave_documents is "
    "false, a bracketed placeholder standing for such a fact ([result to add], "
    "[date to add]) is the expected form, never a gap; "
    "content cut off; the wrong language or format; items the person gave "
    "written differently (split into syllables, renamed, translated, reworded) "
    "when nothing asked for that. Acceptable variants are "
    "not gaps: style, colours, layout and wording choices that still do what "
    'was asked; list them under "variants", which changes nothing. Do not '
    "invent requirements the person did not ask for. The picture shows only "
    "the first page at a small size: the file's text is the whole file, table "
    "rows included. Reply with JSON only: "
    '{"gaps": [{"gap": "...", "missing": "..."}], "variants": ["..."]}, at '
    "most five short gaps in English, each naming what is missing or wrong "
    'and what was asked, with "missing": the exact words or figures the file '
    "should show and does not (empty for something wrong rather than "
    "missing); an empty gaps list when the file does what the request asks."
)

REFUSAL = (
    "deliverable_check: the file was built but not published, because a check "
    "of the file against the request found: {gaps}. Fix these and call "
    "publish_document again with the complete corrected content. If a point "
    "cannot be fixed (a limit of the format), publish the same content again: "
    "it is then published, and the answer must say plainly what the file lacks."
)
NOTE = (
    "A check of this file against the request found that it lacks: {gaps}. The "
    "answer must say so plainly, in the language of the request, and must not "
    "claim that the file has it. Say it of the file as delivered; never "
    "mention this check, a refusal, a regeneration, an approval or what was "
    "changed during this turn."
)
LEFT_OUT = "characters the file format cannot draw, left out of the file: {characters}"
# A new version of a file the person has (current_file.py).
REVISION = (
    " This file is a new version of a file the person already has: "
    "facts.changes_from_previous_version lists the text it removed, replaced, "
    "added or moved, measured by code on both versions. The request asks for a "
    "change of that version. A removed or replaced text that the request (with "
    "the earlier messages) did not ask to change is a gap, even a small one (a "
    "word, a figure, an earlier change undone): name it with its text and say "
    "it must be restored as it was. A requested change that is not among the "
    "changes is a gap. Moved text and requested changes are not gaps. Text "
    "shortened to keep the file within a size the person asked (about one "
    "page, five slides) is not a gap, unless a fact, a figure or a name the "
    "person gave is lost, or an item of a list, a recommendation or an "
    "enumeration is removed (shortening rewords sentences, it never drops "
    "an item: staging -181 lost the error rates of a success measure when a "
    "table was added)."
)
IDENTICAL = (
    "this new version of {name} is identical to its current version: the "
    "requested change is not in it (start from the current version and apply "
    "the change)"
)
CHANGED = (
    "This new version of {name} could not be checked against the request. "
    "Compared with its current version it changes: {changes}. The answer must "
    "name these changes plainly and must not claim that everything else was "
    "kept unchanged."
)
CHANGES_LISTED = 6


@dataclass
class Outcome:
    """What the check found: gaps (empty when none) and how it ran."""

    gaps: list[str] = field(default_factory=list)
    checked: bool = False
    seconds: float = 0.0
    cost_eur: Decimal = Decimal(0)
    image: bool = False
    # Gaps code is sure of (pictures drawn for the file and not placed, emoji
    # standing in for planned pictures): refused once like a judged gap.
    certain: list[str] = field(default_factory=list)
    # Statements of a file derived from a provided source that the source does
    # not support (source_faithfulness.py): refused once like a judged gap.
    unsupported: list[str] = field(default_factory=list)
    # A new version that could not be judged: the changes the answer must name.
    changed: str = ""
    # Details of the file no source of the turn holds, counted by kind
    # (detail_gaps): logged with the refusal, never their text.
    details: dict[str, int] = field(default_factory=dict)


def described(revision: dict[str, Any] | None) -> str:
    """The note naming what a new version changed, for an unjudged version."""
    if not revision or revision.get("identical"):
        return ""
    listed = [
        *(f'removed "{entry["text"]}"' for entry in revision.get("removed", [])),
        *(
            f'replaced "{entry["before"]}" with "{entry["after"]}"'
            for entry in revision.get("replaced", [])
        ),
        *(f'added "{entry["text"]}"' for entry in revision.get("added", [])),
    ]
    if not listed:
        return ""
    more = len(listed) - CHANGES_LISTED + int(revision.get("more_changes") or 0)
    text = "; ".join(listed[:CHANGES_LISTED]) + (
        f"; and {more} more changes" if more > 0 else ""
    )
    return CHANGED.format(
        name=revision.get("previous_file") or "the file", changes=text
    )


UNSUPPORTED = (
    "statements not supported by the source document the person provided: "
    "{statements} (state only what the source states: correct each one from the "
    "source or remove it)"
)


def source_check(
    tools: Any,
    request: str,
    text: str,
    profile: str,
    seconds: float,
    call: Any,
) -> asyncio.Task[tuple[str, Decimal]] | None:
    """The file against the provided source the turn holds, started now.

    The task gives the unsupported statements (quoted, joined) and the cost; a
    failed judge gives none and is logged with its code (D5), never blocking.
    None when the turn holds no provided source or the check cannot run.
    """
    from services.orchestrator import document_quotes
    from services.orchestrator import source_faithfulness as faith

    if not faith.enabled():
        return None
    source = faith.provided(document_quotes.current.get())
    if (
        source is None
        or len(source.text) > faith.SOURCE_LIMIT
        or len(text.split()) < faith.MIN_WORDS
    ):
        return None
    judge_profile, _ = faith.profiles(profile)
    if judge_profile is None or seconds < MIN_SECONDS:
        return None
    log = logging.getLogger(__name__)

    async def run() -> tuple[str, Decimal]:
        try:
            screen = faith.screen_profile(profile)
            verdict = await faith.checked(
                request,
                source,
                text,
                judge_profile,
                screen,
                min(faith.check_seconds(screen), seconds),
                complete=call,
            )
        except faith.JudgeFailure as failure:
            log.warning(
                json.dumps(
                    {
                        "event": "deliverable_source_check_failed",
                        "code": failure.code,
                        "cost_eur": str(failure.cost_eur),
                        "unmeasured": failure.unmeasured,
                    }
                )
            )
            return "", failure.cost_eur
        log.warning(
            json.dumps(
                {
                    "event": "deliverable_source_check",
                    "findings": len(verdict.findings),
                    "links": verdict.links,
                    "unlocated": verdict.unlocated,
                    "screen": verdict.screen,
                    "seconds": round(verdict.seconds, 2),
                    "cost_eur": str(verdict.cost_eur),
                    "mode": faith.mode(),
                }
            )
        )
        if faith.mode() != "on" or not verdict.findings:
            return "", verdict.cost_eur
        quoted = "; ".join(
            "“" + faith.statement(text, finding) + "”" for finding in verdict.findings
        )
        return quoted, verdict.cost_eur

    return asyncio.create_task(run())


UNSOURCED_DETAILS = (
    "specific details found in no source of this turn (documents provided, pages "
    "read, tool results): {details} (for each one: remove it, replace it with the "
    'value a source of this turn states, or keep it followed by "(not '
    "verified)\" written in the file's language)"
)


UNSOURCED_NOTE = "details found in no source of this turn: {details}"


def detail_gaps(
    tools: Any,
    suffix: str,
    raw: bytes,
    files: list[str] | None = None,
    name: str = "",
) -> tuple[list[str], dict[str, int]]:
    """Details of the built file that the turn's evidence does not hold, as a
    gap, with their count by kind.

    Code only (no model call): the file is read back as the person will read
    it, and its details go through the matcher of the answers. ``files``: the
    texts of the source files the publication names (a project file the
    deck is made from), evidence like the turn's; ``name``: the file's path,
    whose words and dates are the person's. The person's messages count as
    sourced, and so does a value computed from their numbers or from sourced
    ones. Once the turn has refused a file, the gap is the short note the
    answer names, not a repair instruction (decide publishes it).
    """
    from packages.doc_render import readback
    from services.orchestrator import document_quotes
    from services.orchestrator import unsourced_details as details

    if details.mode() == "off" or suffix not in details.CHECKED_SUFFIXES:
        return [], {}
    sources = document_quotes.current.get()
    if sources is None and not files:
        return [], {}
    try:
        document = readback.read(raw, suffix)
    except Exception:  # noqa: BLE001 - an unreadable file is checked otherwise
        return [], {}
    # As Markdown, so the matcher reads headings and table rows as it reads an
    # answer's: their figures and dates are checked, their capitalised words
    # are labels, not names.
    text = (
        document.text
        if document.text is not None
        else "\n\n".join(
            [
                *(["# " + document.title] if document.title else []),
                *(
                    readback.section_markdown(section, document.title)
                    for section in document.sections
                ),
            ]
        )
    )
    request = getattr(tools, "request_text", "")
    earlier = getattr(tools, "earlier_requests", None) or []
    given = [
        str(item)
        for item in [*earlier, request]
        if isinstance(item, str) and item.strip()
    ]
    conversation = [
        str(item)
        for item in getattr(sources, "conversation", [])
        if isinstance(item, str)
    ]
    found = details.deliverable_details(
        text,
        [*details.turn_passages(sources), *(files or [])],
        [*given, *conversation, *([name] if name else [])],
        [*conversation, *given],
    )
    if not found:
        return [], {}
    kinds: dict[str, int] = {}
    for detail in found:
        kinds[detail.kind] = kinds.get(detail.kind, 0) + 1
    template = (
        UNSOURCED_DETAILS
        if getattr(tools, "deliverable_refused", True) is False
        else UNSOURCED_NOTE
    )
    return [template.format(details=details.listed(found))], kinds


UNITS_CHANGED = (
    "figures whose unit or currency differs from the request or the source "
    "document: {figures} (write each figure with the unit and currency its source "
    "gives, none where the source gives none, and never convert one)"
)


NOUNS_CHANGED = (
    "figures attached to another noun than in the request or the source "
    "document: {figures} (keep each figure with what it counts in its source; "
    "a number the sources do not give for that noun is not written)"
)


FILE_NAME_TITLE = (
    "the title is a file name ({title}): write the title in words, in natural "
    "case and in the language of the request, never the file name"
)


FORM = (
    "the pages are badly filled: {findings} (let the sections follow each "
    "other on the page, and remove each \\newpage line the person did not ask "
    "for)"
)


GROUPING = (
    "the text is misgrouped: {findings} (each group's title, then its own "
    "points, in order)"
)


def form_gaps(
    suffix: str, content: Any, raw: bytes, pictures: dict[str, bytes] | None = None
) -> list[str]:
    """The form of a report's pages, measured on its rendered PDF (form.py).

    Staging -150 (2026-11-21): a six-section report took six pages, each about
    95 % empty, and every check passed (nothing scored the form of a
    document; deck_quality scores decks). A PDF is measured as built; a DOCX
    on the PDF of the same content, whose breaks it shares. A file whose form
    could not be measured has no gap here.
    """
    if suffix not in {".pdf", ".docx"}:
        return []
    import tempfile
    from pathlib import Path

    from packages.doc_render import form

    try:
        if content is None:
            if suffix != ".pdf":
                return []
            found = form.findings(form.measure(raw))
        else:
            with tempfile.TemporaryDirectory(prefix="atlas-form-") as directory:
                for name, data in (pictures or {}).items():
                    (Path(directory) / name).write_bytes(data)
                found = form.check(
                    content, Path(directory), raw if suffix == ".pdf" else None
                )
    except Exception as error:  # noqa: BLE001 - unmeasured is not a gap
        logging.getLogger(__name__).warning(
            json.dumps({"event": "form_check_failed", "error": type(error).__name__})
        )
        return []
    # Group titles over a single list (form.misgrouped) are no page defect.
    grouping = [item for item in found if item in form.misgrouped(content)]
    paged = [item for item in found if item not in grouping]
    gaps = []
    if grouping:
        gaps.append(GROUPING.format(findings="; ".join(grouping[:2])))
    if paged:
        gaps.append(FORM.format(findings="; ".join(paged[:4])))
    return gaps


def title_gaps(content: Any, name: str = "") -> list[str]:
    """A title that is the file name or a slug (titles.py, staging -145).

    The input normalisation already writes such a title in words; this is
    the check of the built file, for every deliverable type.
    """
    from packages.doc_render.titles import slug_like

    title = getattr(content, "title", None)
    if not isinstance(title, str):
        return []
    stem = name.rsplit("/", 1)[-1].rsplit(".", 1)[0] if name else ""
    return [FILE_NAME_TITLE.format(title=title)] if slug_like(title, stem) else []


def unit_gaps(tools: Any, content: Any, preview: bytes) -> list[str]:
    """A figure keeps the unit and currency of its source (figure_units.py),
    and what it counts (figure_nouns.py).

    The sources are the request, the earlier messages and the document the
    turn read; the file is judged by its written texts.
    """
    from services.orchestrator import figure_nouns, figure_units

    earlier = getattr(tools, "earlier_requests", None)
    sources = [
        text
        for text in (
            getattr(tools, "request_text", ""),
            *(earlier if isinstance(earlier, list) else []),
        )
        if isinstance(text, str) and text.strip()
    ]
    try:
        from services.orchestrator import document_quotes
        from services.orchestrator import source_faithfulness as faith

        provided = faith.provided(document_quotes.current.get())
        if provided is not None:
            sources.append(provided.text)
    except Exception:  # noqa: BLE001 - the request alone is still a source
        pass
    if not sources:
        return []
    if content is not None:
        written = list(content.texts())
    else:
        written = [preview.decode("utf-8", errors="replace")]
    found = figure_units.mismatches(written, sources)
    counted = figure_nouns.mismatches(written, sources)
    return [
        *([UNITS_CHANGED.format(figures="; ".join(found[:6]))] if found else []),
        *([NOUNS_CHANGED.format(figures="; ".join(counted[:6]))] if counted else []),
    ]


def planned_gaps(tools: Any, content: Any) -> list[str]:
    """What code knows the file lacks against the pictures planned for it.

    The plan (work_pictures.prepare) is the model's reading of the request;
    whether the file carries its pictures is measured here, not judged: on
    staging -87 a picture book was published with emoji and the judged check
    let it pass.
    """
    if content is None:
        return []
    placed = {
        image.removesuffix(".jpg")
        for section in content.sections
        for image in section.images
    }
    gaps: list[str] = []
    drawn = getattr(tools, "pictures_drawn", None)
    if isinstance(drawn, dict) and drawn:
        unplaced = [
            str(label) for reference, label in drawn.items() if reference not in placed
        ]
        if unplaced:
            gaps.append(
                "pictures drawn for this file are not placed: "
                + ", ".join(unplaced)
                + " (place each with its ![label](reference) line)"
            )
    from services.orchestrator.work_pictures import normal

    text = normal(drawn_text(content))
    earlier = getattr(tools, "pictures_earlier", None)
    if isinstance(earlier, dict) and earlier:
        # A picture drawn in an earlier turn for an item this file shows is
        # placed again (staging -95: the follow-up file lost them).
        dropped = [
            str(label)
            for reference, label in earlier.items()
            if reference not in placed and normal(str(label)) in text
        ]
        if dropped:
            gaps.append(
                "pictures drawn earlier in this conversation for items of this "
                "file are not placed: "
                + ", ".join(dropped)
                + " (place each with its ![label](reference) line)"
            )
    plan = getattr(tools, "picture_plan", None)
    asked = getattr(plan, "pictures", None)
    if isinstance(asked, list) and asked:
        # Every planned item stays in the file, its picture drawn or to come
        # (staging -95: the words not drawn yet were left out of the file).
        absent = [
            str(item.label)
            for item in asked
            if normal(str(getattr(item, "label", ""))) not in text
        ]
        if absent:
            gaps.append(
                "items of the request are missing from the file: "
                + ", ".join(absent)
                + " (keep each one, with a line saying its picture will come "
                "when it is not drawn yet)"
            )
    if (
        isinstance(asked, list)
        and asked
        and getattr(plan, "emoji_requested", True) is False
        and _pictures(drawn_text(content))
    ):
        gaps.append(
            "emoji stand in for the pictures the request asks for: remove them "
            "and place the drawn pictures"
        )
    return gaps


def _texts(value: Any) -> list[str]:
    """Every string of a tool argument, in order."""
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        return [text for item in value.values() for text in _texts(item)]
    if isinstance(value, list):
        return [text for item in value for text in _texts(item)]
    return []


def left_out(content: Any) -> list[str]:
    """The characters of the content as sent that the file cannot draw."""
    from packages.doc_render.glyphs import left_out as dropped

    found: dict[str, None] = {}
    for text in _texts(content):
        for character in dropped(text):
            found[character] = None
    return list(found)[:40]


def _pictures(text: str) -> list[str]:
    return [
        token
        for token in emoji.tokens(text)
        if emoji.is_emoji(token) and emoji.drawable(token)
    ]


def drawn_text(content: Any) -> str:
    """The text the file shows, in reading order, emoji included.

    A PDF's text layer lacks the emoji it draws as pictures (emoji.py): the
    check reads the content as laid out instead, which is what was drawn
    (characters the format left out are already absent from it). A deck's
    key numbers and chart are drawn on its slides, and its speaker notes are
    in the file, labelled as notes: staging -177, a slide of two key numbers
    (12 400 EUR budget, 3 people) read as empty, and both checks told the
    person it showed nothing.
    """
    from packages.doc_render.cells import shown
    from packages.doc_render.schema import number_text

    lines = [shown(content.title)]
    for index, section in enumerate(content.sections):
        # A first heading that repeats the title is not drawn again
        # (renderer.py): the check read the title twice and called it a gap.
        if index or not content.repeats_title():
            lines.append(shown(section.heading))
        lines.extend(map(shown, section.paragraphs))
        lines.extend("- " + shown(text) for text in section.bullets)
        lines.extend(
            f"{shown(metric.value)} {shown(metric.label)}"
            for metric in getattr(section, "metrics", ()) or ()
        )
        chart = getattr(section, "chart", None)
        if chart is not None:
            lines.append(
                (shown(chart.title) + ": " if chart.title else "")
                + "; ".join(
                    f"{shown(label)} {number_text(value)}{chart.unit}"
                    for label, value in zip(chart.labels, chart.values, strict=True)
                )
            )
        for table in section.tables:
            for row in (table.headers, *table.rows):
                lines.append(" | ".join(shown(cell) for cell in row))
        lines.extend(
            "(speaker notes, not on the slide) " + shown(note)
            for note in getattr(section, "notes", ()) or ()
        )
    return "\n".join(lines)


def _units(raw: bytes, suffix: str) -> int | None:
    try:
        if suffix == ".pdf":
            from pypdf import PdfReader

            return len(PdfReader(io.BytesIO(raw)).pages)
        if suffix == ".pptx":
            from pptx import Presentation

            return len(Presentation(io.BytesIO(raw)).slides)
        if suffix == ".xlsx":
            from openpyxl import load_workbook

            workbook = load_workbook(io.BytesIO(raw), read_only=True)
            try:
                return len(workbook.worksheets)
            finally:
                workbook.close()
    except Exception:  # noqa: BLE001 - a fact we could not measure is left out
        return None
    return None


def facts(
    suffix: str,
    content: Any,
    raw: bytes,
    preview: bytes,
    missing: list[str],
    names: dict[str, dict[str, str]] | None = None,
    pending: list[str] | None = None,
    requested: list[str] | None = None,
) -> dict[str, Any]:
    """What code measures on the built file (no model involved).

    names gives each picture placed (work_pictures.py) its item and the
    verdict of its own check; pending, the items whose picture this turn
    could not draw in its time.
    """
    names = names or {}
    measured: dict[str, Any] = {
        "format": suffix.lstrip("."),
        "size_bytes": len(raw),
    }
    units = _units(raw, suffix)
    if units is not None:
        measured[{".pdf": "pages", ".pptx": "slides", ".xlsx": "sheets"}[suffix]] = (
            units
        )
    if content is not None:
        from packages.doc_render.cells import shown

        sections = []
        for section in content.sections:
            texts = [
                shown(section.heading),
                *map(shown, section.paragraphs),
                *map(shown, section.bullets),
            ]
            cells = [
                cell
                for table in section.tables
                for row in (table.headers, *table.rows)
                for cell in row
            ]
            sections.append(
                {
                    "heading": shown(section.heading)[:120],
                    "paragraphs": len(section.paragraphs),
                    "bullets": len(section.bullets),
                    "tables": [
                        {
                            "columns": len(table.headers),
                            "headers": [shown(h)[:60] for h in table.headers],
                            "data_rows": len(table.rows),
                        }
                        for table in section.tables
                    ],
                    "emoji_drawn": [
                        e for text in texts + cells for e in _pictures(text)
                    ][:20],
                    "pictures": [
                        picture_fact(names.get(image.removesuffix(".jpg")))
                        for image in section.images
                    ],
                }
            )
        measured["title"] = shown(content.title)[:200]
        measured["sections"] = sections[:60]
        measured["emoji_drawn_in_file"] = len(_pictures(drawn_text(content)))
        measured["pictures_placed"] = sum(
            len(section.images) for section in content.sections
        )
    if missing:
        measured["left_out_characters"] = missing
    if pending:
        measured["pictures_not_drawn_in_time"] = pending[:20]
    if requested:
        measured["pictures_the_request_needs"] = requested[:20]
    text = preview.decode("utf-8", errors="replace")
    measured["text_characters"] = len(text)
    if not text.strip():
        measured["empty_text"] = True
    return measured


def documents_given() -> bool | None:
    """Whether the person gave this turn a document or a pasted text (the only
    places, with their messages, where their own results can come from); None
    when the turn's sources are not known.

    Staging -183: a one-page report "about our team trying a 4 day work week",
    asked with no data, stated a stable output, rising satisfaction and a
    follow-up in three months as the team's observed results.
    """
    from services.orchestrator import document_quotes

    sources = document_quotes.current.get()
    if sources is None:
        return None
    return bool(sources.provided) or any(
        len(text) >= document_quotes.PASTED_MINIMUM for text in sources.conversation
    )


def read_anything() -> bool:
    """Whether the turn read a text (a file of the project, a page, a tool
    result): the person's facts may come from it, so own_facts does not ask."""
    from services.orchestrator import document_quotes
    from services.orchestrator.unsourced_details import turn_passages

    return bool(turn_passages(document_quotes.current.get()))


def picture_fact(entry: dict[str, str] | None) -> dict[str, str]:
    """A placed picture as the check reads it: drawn for this item, and what
    its own vision check said when it found a mismatch (a check that could
    not run says nothing about the picture)."""
    if entry is None:
        return {"drawn_for": "unknown item"}
    fact = {"drawn_for": entry.get("label", "")}
    if entry.get("check") == "does_not_match":
        fact["own_check"] = "does not clearly show its item"
    return fact


def picture_names(tools: Any) -> dict[str, dict[str, str]]:
    """reference -> {label, check} of this conversation's pictures."""
    from services.orchestrator import work_pictures

    try:
        return work_pictures.described(tools)
    except OSError:
        return {}


def page_with_pictures(
    content: Any, suffix: str, pictures: dict[str, bytes]
) -> bytes | None:
    """The first page drawn from the layout, with the pictures it places."""
    import tempfile
    from pathlib import Path

    from packages.doc_render.preview import first_page

    with tempfile.TemporaryDirectory(prefix="atlas-check-") as directory:
        for name, data in pictures.items():
            (Path(directory) / name).write_bytes(data)
        return first_page(content, suffix, Path(directory))


def _data_url(png: bytes) -> str:
    return "data:image/png;base64," + base64.b64encode(png).decode("ascii")


def messages(
    request: str,
    earlier: list[str],
    measured: dict[str, Any],
    text: str,
    page: bytes | None,
    *,
    revision: bool = False,
) -> list[dict[str, Any]]:
    data = {
        "request": request[:4000],
        "earlier_user_messages": [m[:EARLIER_CHARACTERS] for m in earlier],
        "facts": measured,
        "file_text": text[:TEXT_CHARACTERS],
    }
    body = json.dumps(data, ensure_ascii=False)
    content: Any = body
    if page is not None:
        content = [
            {
                "type": "text",
                "text": body + "\n(The picture is the file's first page.)",
            },
            {"type": "image_url", "image_url": {"url": _data_url(page)}},
        ]
    return [
        {"role": "system", "content": SYSTEM + (REVISION if revision else "")},
        {"role": "user", "content": content},
    ]


def _squashed(text: str) -> str:
    return "".join(ch for ch in text.casefold() if ch.isalnum())


def gaps_of(reply: str, text: str = "") -> list[str] | None:
    """The gaps of a reply, or None when it is not the expected JSON.

    A gap reported as missing words or figures that the file's text shows is
    no gap (positive evidence only): staging -181, the check said the table
    lacked Q3 while its row "Q3 121 7" was in the file, and the person was
    told so. Such gaps are dropped and logged.
    """
    from packages.model_json import object_content

    try:
        value = object_content(reply)
    except ValueError:
        return None
    listed = value.get("gaps")
    if not isinstance(listed, list):
        return None
    shown_text = _squashed(text)
    gaps: list[str] = []
    dropped = 0
    for item in listed:
        if isinstance(item, str) and item.strip():
            gaps.append(" ".join(item.split())[:200])
            continue
        if not isinstance(item, dict):
            continue
        gap = item.get("gap")
        if not isinstance(gap, str) or not gap.strip():
            continue
        missing = item.get("missing")
        if (
            isinstance(missing, str)
            and len(_squashed(missing)) >= 2
            and shown_text
            and _squashed(missing) in shown_text
        ):
            dropped += 1
            continue
        gaps.append(" ".join(gap.split())[:200])
    if dropped:
        logging.getLogger(__name__).warning(
            json.dumps({"event": "deliverable_gap_dropped", "shown_in_file": dropped})
        )
    return gaps[:MAX_GAPS]


async def _complete(payload: dict[str, Any], seconds: float) -> dict[str, Any]:
    from packages.ports import atlas_ports
    from services.orchestrator.model import GatewayModel

    return await GatewayModel.post(
        os.environ.get("ATLAS_GATEWAY_URL", atlas_ports().url("gateway")).rstrip("/")
        + "/agent/complete",
        payload,
        seconds,
    )


def enabled() -> bool:
    """ATLAS_DELIVERABLE_CHECK=0 turns the model check off (facts still apply)."""
    return os.environ.get("ATLAS_DELIVERABLE_CHECK", "1").strip() != "0"


async def review(
    tools: Any,
    suffix: str,
    content: Any,
    raw: bytes,
    preview: bytes,
    missing: list[str],
    *,
    pictures: dict[str, bytes] | None = None,
    seconds: float = CHECK_SECONDS,
    complete: Any = None,
    revision: dict[str, Any] | None = None,
    name: str = "",
    sources: list[str] | None = None,
) -> Outcome:
    """Check one built file against the request; never raises.

    seconds is what the publication can spare (the check is skipped under
    MIN_SECONDS); complete is the gateway call (tests pass a recorded one);
    revision, the changes of a new version against the current one
    (current_file.compared); name, the file's path (its stem is never its
    title); sources, the texts of the files the publication names as its
    sources.
    """
    seconds = min(CHECK_SECONDS, seconds)
    started = time.monotonic()
    outcome = Outcome()
    certain = (
        planned_gaps(tools, content)
        + unit_gaps(tools, content, preview)
        + title_gaps(content, name)
        + form_gaps(suffix, content, raw, pictures)
        + (found_details := detail_gaps(tools, suffix, raw, sources, name))[0]
    )
    if revision is not None and revision.get("identical"):
        certain.append(
            IDENTICAL.format(name=revision.get("previous_file") or "the file")
        )
    outcome.certain = certain
    outcome.details = found_details[1]
    deterministic = certain + (
        [LEFT_OUT.format(characters=" ".join(missing))] if missing else []
    )
    model = getattr(tools, "check_model", None)
    request = getattr(tools, "request_text", "")
    count = getattr(tools, "deliverable_checks", 0)
    count = count if isinstance(count, int) else MAX_CHECKS
    profile = getattr(model, "profile", None) if model is not None else None
    profile = profile if isinstance(profile, str) else None
    if (
        not enabled()
        or not isinstance(request, str)
        or not request.strip()
        or not profile
        or count >= MAX_CHECKS
        or seconds < MIN_SECONDS
    ):
        outcome.gaps = deterministic + (unchecked(tools) if count < MAX_CHECKS else [])
        outcome.changed = described(revision)
        return outcome
    tools.deliverable_checks = count + 1
    strict = bool(getattr(model, "strict", False))
    listed = getattr(tools, "pictures_pending", None)
    pending = [
        label
        for label in (listed if isinstance(listed, list) else [])
        if isinstance(label, str)
    ]
    plan = getattr(tools, "picture_plan", None)
    asked = getattr(plan, "pictures", None)
    measured = facts(
        suffix,
        content,
        raw,
        preview,
        missing,
        picture_names(tools),
        pending,
        [str(getattr(item, "label", "")) for item in asked]
        if isinstance(asked, list)
        else None,
    )
    given = documents_given()
    if given is not None:
        measured["person_gave_documents"] = given
    if revision is not None:
        measured["changes_from_previous_version"] = revision
    if getattr(tools, "picture_plan_failed", False) is True:
        measured["picture_plan"] = (
            "could not be made: judge from the request whether pictures are asked"
        )
    page: bytes | None = None
    if content is not None and not strict:
        try:
            page = page_with_pictures(content, suffix, pictures or {})
        except Exception:  # noqa: BLE001 - the text and facts are still checked
            page = None
    earlier = [
        str(text)
        for text in (getattr(tools, "earlier_requests", None) or [])[-EARLIER_MESSAGES:]
        if isinstance(text, str) and text.strip()
    ]
    allowance = Decimal(CHECK_BUDGET_EUR)
    spent_left = getattr(model, "allowance", None)
    if isinstance(spent_left, Decimal):
        allowance = min(allowance, spent_left)
    if allowance <= 0:
        outcome.gaps = deterministic
        outcome.changed = described(revision)
        return outcome
    from services.orchestrator.work_pictures import plan_profiles

    file_text = (
        drawn_text(content)
        if content is not None
        else preview.decode("utf-8", "replace")
    )
    body = messages(
        request, earlier, measured, file_text, page, revision=revision is not None
    )
    call = complete or _complete
    # A file derived from a provided source is also checked against that source
    # (source_faithfulness.py), in parallel with the check against the request.
    against_source = source_check(
        tools, request, file_text, profile, seconds - (time.monotonic() - started), call
    )
    # A file made from nothing the person gave or the turn read: its facts
    # about the person's own case are asked apart (own_facts.py), in parallel.
    own = (
        own_facts.start(
            tools,
            request,
            earlier,
            file_text,
            seconds - (time.monotonic() - started),
            call,
            allowance,
        )
        if given is False and not sources and not read_anything()
        else None
    )
    found: list[str] | None = None
    cost = Decimal(0)
    # The fast profile first (reasoning off), then the turn's own profile: a
    # check that timed out on a slow profile let a file without its pictures
    # pass (staging -91).
    for name, limit in plan_profiles(tools) or [(profile, seconds)]:
        left = seconds - (time.monotonic() - started)
        if left < MIN_SECONDS:
            break
        payload: dict[str, Any] = {
            "messages": body,
            "tools": [],
            "response_format": {"type": "json_object"},
            "profile": name,
            "max_tokens": CHECK_TOKENS,
            "timeout": min(limit, left),
            "budget_eur": str(
                allowance.quantize(Decimal("0.0001"), rounding=ROUND_FLOOR)
            ),
            "reasoning_effort": "none",
            "reasoning_disabled": True,
            "sampling_temperature": 0.0,
            "single_attempt": True,
            "local_enabled": False,
        }
        if strict:
            payload["privacy"] = "strict"
        try:
            reply = await call(payload, min(limit, left) + 5)
        except Exception as error:  # noqa: BLE001 - never blocks the delivery
            logging.getLogger(__name__).warning(
                json.dumps(
                    {"event": "deliverable_check_failed", "error": type(error).__name__}
                )
            )
            continue
        try:
            spent = Decimal(str(reply.get("cost_eur") or 0))
        except ArithmeticError:
            spent = Decimal(0)
        if spent.is_finite() and spent > 0:
            cost += spent
            if model is not None and isinstance(getattr(model, "spent", None), Decimal):
                model.spent += spent
        found = gaps_of(str(reply.get("text") or ""), file_text)
        if found is not None:
            if given is False:
                found = own_facts.without_placeholder_gaps(found)
            break
    if against_source is not None:
        unsupported, source_cost = await against_source
        cost += source_cost
        if (
            source_cost > 0
            and model is not None
            and isinstance(getattr(model, "spent", None), Decimal)
        ):
            model.spent += source_cost
        if unsupported:
            outcome.unsupported = [UNSUPPORTED.format(statements=unsupported)]
    if own is not None:
        stated, own_cost = await own
        cost += own_cost
        if (
            own_cost > 0
            and model is not None
            and isinstance(getattr(model, "spent", None), Decimal)
        ):
            model.spent += own_cost
        if stated:
            outcome.unsupported.append(own_facts.gap(stated))
    outcome.cost_eur = cost
    outcome.seconds = time.monotonic() - started
    outcome.image = page is not None
    if found is None:
        outcome.gaps = deterministic + outcome.unsupported + unchecked(tools)
        outcome.changed = described(revision)
        return outcome
    outcome.checked = True
    outcome.gaps = certain + outcome.unsupported + found
    logging.getLogger(__name__).warning(
        json.dumps(
            {
                "event": "deliverable_check",
                "format": suffix,
                "gaps": len(found),
                "certain": len(certain),
                "image": outcome.image,
                "seconds": round(outcome.seconds, 2),
                "cost_eur": str(cost),
            }
        )
    )
    return outcome


def unchecked(tools: Any) -> list[str]:
    """When neither the plan nor the check could run, the answer must not
    claim that the file has everything the request asks (pictures included)."""
    if getattr(tools, "picture_plan_failed", False) is True:
        return [
            "the file could not be checked against the request, pictures it may "
            "need included: do not claim it has everything that was asked"
        ]
    return []


def verification(outcome: Outcome) -> dict[str, Any]:
    """The check's result as a Work tool event carries it (VerificationStatus of
    the project contract): the plan closes its check step only on "passed",
    and the closing instruction says "verified" only then (work_plan.py,
    work_closing.py)."""
    if outcome.gaps:
        status, reason = "failed", "the file lacks what the request asks"
    elif outcome.checked:
        status, reason = "passed", "the file was checked against the request"
    else:
        status, reason = "not_run", "the file could not be checked against the request"
    return {"status": status, "command": None, "exit_code": None, "reason": reason}


def joined(gaps: list[str]) -> str:
    return "; ".join(f"({index}) {gap}" for index, gap in enumerate(gaps, 1))


def decide(tools: Any, outcome: Outcome) -> str:
    """Refuse the first file with gaps in a turn; return the note otherwise.

    The first refusal is the repair attempt. A file published with gaps still
    open carries them: the note for the answer (and output["gaps"]). A new
    version that could not be judged adds the changes the answer must name.
    """
    if not outcome.gaps:
        return outcome.changed
    # Pictures this turn had no time to draw cannot be repaired in it: the
    # file is published and the answer says what is missing.
    pending = getattr(tools, "pictures_pending", None)
    repairable = not (isinstance(pending, list) and pending)
    refusable = getattr(tools, "deliverable_refused", True) is False
    if refusable and (
        outcome.certain
        or (outcome.unsupported and repairable)
        or (outcome.checked and repairable)
    ):
        tools.deliverable_refused = True
        _log_decision(outcome, refused=True)
        raise ValueError(REFUSAL.format(gaps=joined(outcome.gaps)))
    # Published with its gaps, said in the answer: a turn refuses a file at
    # most once, whichever check finds the gaps (expectations.document_refusal
    # shares the flag), so a turn always ends with its file.
    _log_decision(outcome, refused=False)
    return " ".join(
        filter(None, (NOTE.format(gaps=joined(outcome.gaps)), outcome.changed))
    )


def _log_decision(outcome: Outcome, *, refused: bool) -> None:
    """Why a file was refused or published with gaps: counts only."""
    logging.getLogger(__name__).warning(
        json.dumps(
            {
                "event": "deliverable_decision",
                "refused": refused,
                "gaps": len(outcome.gaps),
                "certain": len(outcome.certain),
                "unsupported": len(outcome.unsupported),
                "checked": outcome.checked,
                "details": outcome.details,
            }
        )
    )
