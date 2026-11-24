"""REQ-PROJ-05: scoped document tools; bytes go only to the networkless worker."""

from __future__ import annotations

import base64
import asyncio
import hashlib
import io
import json
import logging
from pathlib import PurePosixPath
import time
from typing import Any, Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator
from packages.doc_render.calculated import COLUMN, MISSING, ROW, Finding, finding
from packages.doc_render.schema import Content
from services.orchestrator.project_document_input import (
    DocumentInput,
    explained,
    normalize,
)
from packages.text_deliverables import (
    TEXT_CHARACTER_LIMIT,
    TEXT_MIMES,
    TextDeliverableError,
    validate_text,
    visible_text,
)


class Arguments(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class Selector(Arguments):
    page_start: int | None = Field(default=None, ge=1, le=1000)
    page_end: int | None = Field(default=None, ge=1, le=1000)
    paragraph_start: int | None = Field(default=None, ge=1, le=100000)
    paragraph_end: int | None = Field(default=None, ge=1, le=100000)
    sheet: str | None = Field(default=None, min_length=1, max_length=255)
    range: str | None = Field(default=None, max_length=32)


class Input(Arguments):
    file_id: str
    version: str
    selector: Selector = Field(default_factory=Selector)

    @field_validator("selector", mode="before")
    @classmethod
    def decode_selector(cls, value: Any) -> Any:
        # Some providers encode nested tool objects as JSON strings. Decode
        # once, then retain the same closed, strictly typed selector boundary.
        return json.loads(value) if isinstance(value, str) else value


class Read(Input):
    cursor: str | None = Field(default=None, max_length=100)


class Options(Arguments):
    fidelity: Literal["text"] = "text"


class Convert(Input):
    target: Literal["text", "markdown", "csv", "pdf"]
    options: Options = Field(default_factory=Options)


class Compare(Arguments):
    left: Input
    right: Input


class Move(Arguments):
    file_id: str
    base_version: str
    expected_revision: int = Field(ge=0)
    folder_id: str
    path: str = Field(min_length=1, max_length=1024)


class Organize(Arguments):
    mode: Literal["preview", "apply"]
    operations: list[Move] = Field(min_length=1, max_length=100)


class Publish(Arguments):
    path: str = Field(min_length=1, max_length=1024)
    content: Content
    sources: list[Input] = Field(max_length=100)
    strategy: Literal["created", "regenerated", "edited_in_place"] = "created"


class TextPublish(Arguments):
    """MD/CSV/TXT/HTML: the content is the complete file text."""

    path: str = Field(min_length=1, max_length=1024)
    title: str | None = Field(default=None, min_length=1, max_length=4000)
    content: str = Field(min_length=1, max_length=TEXT_CHARACTER_LIMIT)
    sources: list[Input] = Field(max_length=100)
    strategy: Literal["created", "regenerated", "edited_in_place"] = "created"


class ModelPublish(DocumentInput):
    path: str = Field(min_length=1, max_length=1024)
    sources: list[Input] = Field(max_length=100)
    strategy: Literal["created", "regenerated", "edited_in_place"] = "created"


SCHEMAS: dict[str, type[Arguments]] = {
    "read_document": Read,
    "convert_document": Convert,
    "compare_documents": Compare,
    "organize_files": Organize,
    "publish_document": Publish,
}
DESCRIPTIONS = {
    "read_document": "Read selected immutable PDF pages, DOCX body paragraphs/tables or XLSX stored cells with precise locators, bounded coverage and continuation. Formulas are never executed.",
    "convert_document": "Convert selected PDF text to text/Markdown, DOCX body to text/reflowed PDF, or one XLSX sheet to CSV/reflowed PDF. Text fidelity only; stages a new workspace file, never publishes or overwrites.",
    "compare_documents": "Compare two explicit immutable document inputs and their independent selectors; return located changes and bounded completeness. Partial comparisons are not exhaustive.",
    "organize_files": "Preview or atomically apply up to 100 version/revision-bound canonical moves in selected session folders; no swaps, overwrites or locality changes. Preview does not apply.",
    "publish_document": 'Create a durable DOCX, XLSX, PPTX, PDF, MD, CSV, TXT or HTML file in project deliverables with a verified text preview. For MD, CSV, TXT and HTML send the complete file text as content (UTF-8, non-empty; every CSV row has the same column count; slides and formulas are rejected; HTML is delivered as a download, never rendered). For DOCX/PDF/XLSX send content as Markdown text with headings, paragraphs, lists and simple pipe tables: the title of each section is a heading line (## Title) on its own, never a plain line or a bold line, so the structure is visible and navigable. In DOCX and PDF the sections follow each other on the page: a line \\newpage alone starts the next section on a new page, and at the very start gives the title a page of its own; write it only where the person asks for a new page or a title page. A line \\footer text, anywhere in the content, gives the running footer printed at the foot of every page (in a deck, its footer line): use it for anything the person wants in the footer, never a body line under the text. A point to tick (a checklist, boxes to tick) is written - [ ] text, or - [x] text when ticked: it shows as a box instead of a bullet in DOCX, PDF and PPTX. Text the person quoted to be written is written exactly as quoted, capitals and final punctuation included. In an XLSX table a cell starting with = is a formula, with or without formulas=true (a cell that must show a leading = as text starts with an apostrophe: \'=A1); formulas support + - * / ^ & %, comparisons, parentheses, SUM, SUMIF with exact criteria, AVERAGE, MIN, MAX, COUNT, ROUND, ABS, IF, AND, OR, NOT and IFERROR over bounded local cell/range references, including numbered sheets; cells a formula computes with hold numbers or are left empty. A table cell typed as a number is a number in XLSX, in every table, shown as typed: 12.50, 1 250,00 €, 1,250.00, $40; 45 % holds 0.45. Write amounts without a unit, and never a lone 1,250 (thousands or decimals?): a formula over text that begins like a number is refused with the cell. Inline Markdown (**bold**, *italic*, `code`, [text](address)) is read in headings, paragraphs, bullets and table cells of every format, never shown as markers. A table has up to 60 columns in XLSX and 14 in DOCX, PDF and PPTX. A table cell may be left empty in any format (a value to fill in later): write nothing between its pipes instead of a dash or placeholder text; an empty cell counts as 0 in formulas, as in Excel. A column or total the person wants calculated is a formula in every data row, rows left empty to fill in included, written so that an empty row shows nothing: =IFERROR(C2/B2,"") for a ratio, =IF(B2="","",B2-C2) otherwise. A header alone (Total, Difference, Pace) calculates nothing: a workbook asked to calculate that holds no formula, or whose calculated column is empty, is refused once. Each section is a sheet named 1, 2, etc., and its first table starts at cell A1 of that sheet: the header is row 1 and the first data row is row 2, so a total under four data rows is =SUM(B2:B5), and 2!B2 is cell B2 of sheet 2; the title, heading and texts are written below the table. Only that first table holds formulas, and a formula refers to cells inside such a table: give each table with formulas its own section. Formula caches are calculated and verified by code; unsupported formulas are rejected. For PPTX send title and slides, each with a title and bullets, a table, metrics or a chart (images: make_pictures references; notes: speaker notes); omit content. Code lays out each slide from what it holds: a first slide with one to three short lines is the cover, a short last slide the closing slide, one line alone a statement, two to four bullets cards or columns (a timeline only when they are ordered steps or dates, or sequence is true), metrics (two to four key numbers, each a value and a label) number callouts, chart (labels and values) a bar chart, notes speaker notes. Write short bullets, one idea each; a bullet written \'Label: text\' shows its label in bold. Follow what the kind of deck expects, within the slide count asked: an investor pitch covers problem, solution, product, market, business model, traction, team, competition, roadmap and the ask; a course objectives, notions, examples, an exercise and a summary; a status report progress, key numbers, risks and next steps; a launch the audience, offer, benefits, price, date and call to action. A fact (a number, name, date, status or result) not given in the request nor read in a page or file of this conversation is never invented. A result of their own team, organisation, project or trial that the person did not give (an outcome, a figure, a score, a duration, a follow-up date) is never written as observed in a DOCX, PDF, MD or TXT file: each one is a bracketed placeholder in the language of the file at its place, such as [result to add], or a point to assess, and the answer says once that these are to be filled in; figures the person gave are written as given. A value that follows by calculation from figures given in the conversation or its files (a product, a sum, a total, a difference, an average, a percentage, a unit conversion) is computed, with a code or calculation tool when needed, and written: it is never a placeholder, which stands only for what cannot be derived (an observation, a measurement or an outcome nobody gave). On a slide of two to four points, each bullet is one full informative sentence built from the facts given or read. The facts the person still has to supply are listed once, in a single to-confirm list on the ask or closing slide, never scattered over slides as placeholders; a slide that would hold only placeholders is left out. A table keeps every row, column and name of the facts given. A JSON-encoded strict Content object is also accepted in content. Declare immutable sources and selectors: sources lists only files of this session by their read_input identifiers; cite web pages in the content with their address. The excerpts of the declared files are never listed back as a Sources slide or section: a deck keeps those its slides do not quote in the speaker notes of its last slide, and the file records its source versions. Strict Content objects must already contain every selected source segment. Use an empty sources list for original content. A picture drawn by make_pictures in this conversation is placed in its section as its own Markdown line ![label](picture-reference), at most five per section; no other image is accepted. A figure keeps the unit and currency the request or the source document gives it: none is added, changed or converted, and a file that changes one is refused once. A new file never overwrites an existing path. A change the person asks to a file already delivered in the project is published under that file\'s own path with strategy regenerated or edited_in_place: it becomes its next version once the person approves the overwrite. Returns canonical download and preview links only after reopening and verification.',
}


def declarations() -> list[dict[str, object]]:
    return [
        dict(
            type="function",
            function=dict(
                name=name,
                description=DESCRIPTIONS[name],
                parameters=(
                    ModelPublish if name == "publish_document" else schema
                ).model_json_schema(),
            ),
        )
        for name, schema in SCHEMAS.items()
    ]


# A named SVG file is published as its markup, like HTML (staging -184: the
# format was missing, every attempt was refused as "Use Markdown content",
# and the person got no file over four turns).
DESCRIPTIONS["publish_document"] += (
    " An SVG picture (.svg) is a text file too: send its complete markup as "
    "content, one well-formed <svg> element with no DOCTYPE; the words it "
    "must show are <text> elements inside it. It is delivered as a download."
)
PUBLISHED_SUFFIXES = (".docx", ".xlsx", ".pptx", ".pdf", *TEXT_MIMES)
UNSUPPORTED_FORMAT = (
    "unsupported_format: publish_document cannot write {suffix} files. It "
    "writes {formats}: publish the file in one of them, or say plainly that "
    "this format cannot be delivered."
)

UNKNOWN_REFERENCE = (
    "unknown_file_reference: file_id and version must be the two identifiers of "
    "one file of this session: the read_input that publish_document returned in "
    "this turn, or a file selected for this session. A file name, a path, a link "
    "or a number is not a reference. Do not guess another one: if the file is "
    "not among them (a file delivered in an earlier turn, for example), write "
    "its content again in full from the conversation."
)


# What a calculated cell of a table to fill in looks like, checked with the
# evaluator: the cached value of both is empty on an empty row, and a SUM
# over such cells skips them.
ROW_FORMULAS = (
    "in every data row, rows left empty to fill in included, so that an "
    'empty row shows nothing: =IFERROR(C2/B2,"") '
    'for a ratio, =IF(B2="","",B2-C2) otherwise, with the cells of each row'
)
KEPT = (
    " publish the same content again: it is then accepted as it is, and the "
    "answer must not say that the file calculates it."
)


def calculation_refusal(found: Finding) -> str:
    """One line: the named reason, what to write, how to keep the file as it is."""
    name = found.name[:60]
    if found.code == MISSING:
        columns = "; ".join(
            f'column {letter} "{header[:60]}" of sheet {sheet}'
            for sheet, letter, header in found.columns
        )
        return (
            f"{MISSING}: the request asks for calculated values and this "
            "workbook holds no formula, so it calculates nothing. Write each "
            f"calculated column as a formula {ROW_FORMULAS}, and each total as "
            "=SUM(B2:B5) under its four data rows."
            + (f" Empty here: {columns}." if columns else "")
            + " If nothing in this file is to be calculated,"
            + KEPT
        )
    if found.code == COLUMN:
        return (
            f'{COLUMN}: column {found.where} "{name}" of sheet {found.sheet} is '
            "empty in every data row, so nothing is calculated there and a total "
            f"of it adds up empty cells. Write its formula {ROW_FORMULAS}. If the "
            "person types that column by hand," + KEPT
        )
    return (
        f'{ROW}: row {found.where} "{name}" of sheet {found.sheet} is empty under '
        "the rows it names a result of, so nothing is calculated there. Write "
        "its formulas: a total of the data rows above it is "
        f"=SUM(B2:B{int(found.where) - 1}) in column B. If the person types that "
        "row by hand," + KEPT
    )


def calculation_note(found: Finding) -> str:
    """What the answer must not claim about a file published as it is."""
    if found.code == MISSING:
        return (
            "This workbook holds no formula and calculates nothing: the answer "
            "must not describe a formula or an automatic calculation in it."
        )
    kind = "Column" if found.code == COLUMN else "Row"
    return (
        f'{kind} {found.where} "{found.name[:60]}" of sheet {found.sheet} holds no '
        "formula: the answer must not say that it is calculated."
    )


def calculation_check(tools: Any, content: Content) -> str:
    """Refuse once a workbook that does not calculate what it should.

    The request asked for "pace calculated automatically" and the workbook had
    the column and no formula, or a Difference column stayed empty beside the
    totals (staging 2026-11-12): the description of the tool is not enough.
    Each reason is refused once per turn. A header such as Total may name a
    value the person types, so the same content sent again is published, with
    a note for the answer instead of a claim that the file calculates.
    """
    request = getattr(tools, "request_text", "")
    refused = getattr(tools, "calculation_refusals", None)
    if not isinstance(request, str) or not isinstance(refused, set):
        return ""
    found = finding(content, request)
    if found is None:
        return ""
    if found.reason not in refused:
        refused.add(found.reason)
        raise ValueError(calculation_refusal(found))
    return calculation_note(found)


def reference(value: dict[str, Any]) -> tuple[str, str]:
    """The file and version identifiers of a document input, or a named refusal.

    A reference that was not an identifier got Python's own "badly formed
    hexadecimal UUID string" as the tool error, and the Work turn ended as
    failed with that sentence (staging 2026-11-12).
    """
    try:
        return str(UUID(str(value["file_id"]))), str(UUID(str(value["version"])))
    except ValueError:
        raise ValueError(UNKNOWN_REFERENCE) from None


def file_reference(value: dict[str, Any]) -> bool:
    """Whether a document input is written as file identifiers (a web address,
    a file name or a number is not); membership is checked when resolving."""
    try:
        reference(value)
    except ValueError:
        return False
    return True


IGNORED_SOURCES = (
    "{count} of the declared sources were not file identifiers and were "
    "ignored: sources lists only files of this session (read_input identifiers). "
    "Web pages are cited in the text of the document, with their address."
)


async def resolve(
    execution: Any, value: dict[str, Any], receipts: list[dict[str, Any]] | None = None
) -> dict[str, Any]:
    from pathlib import PurePosixPath
    from services.retrieval.project_identity import ProjectError

    file_id, version = reference(value)
    if dict(file_id=file_id, version_id=version) not in execution.session[
        "base_file_versions"
    ] and not any(
        receipt.get("file_version_id") == version
        and receipt.get("turn_id") == execution.request_id
        for receipt in (receipts or [])
    ):
        raise ProjectError(404, "not_found")
    await execution.check()
    own_receipt = next(
        (
            receipt
            for receipt in (receipts or [])
            if receipt.get("id") == file_id
            and receipt.get("file_version_id") == version
            and receipt.get("turn_id") == execution.request_id
        ),
        None,
    )
    if own_receipt is not None:
        status, listing = await execution.broker.public("GET", "files")
        if status != 200:
            raise ProjectError(status, "dependency_unavailable")
        published = next(
            (item for item in listing["items"] if item["version_id"] == version), None
        )
        if published is None:
            raise ProjectError(404, "not_found")
        file_id = published["id"]
    status, file = await execution.broker.public("GET", "files/" + file_id)
    if status != 200:
        raise ProjectError(status, str(file.get("error", "not_found")))
    if file["version_id"] != version:
        raise ProjectError(409, "revision_conflict")
    raw = await execution.broker.content(file_id, version, 16 * 1024 * 1024)
    if (
        len(raw) != file["size_bytes"]
        or hashlib.sha256(raw).hexdigest() != file["sha256"]
    ):
        raise ProjectError(503, "dependency_unavailable")
    return dict(
        value,
        file_id=file_id,
        version=version,
        extension=PurePosixPath(file["relative_path"]).suffix.lower(),
        content=base64.b64encode(raw).decode(),
    )


async def execute(
    tools: Any, name: str, arguments: str, timeout: float
) -> dict[str, Any]:
    value = json.loads(arguments)
    markdown_input = (
        name == "publish_document"
        and isinstance(value, dict)
        and isinstance(value.get("content"), str)
        and not value["content"].lstrip().startswith(("{", "["))
    )
    # Slides summarise their sources as Markdown does: the excerpts of a
    # declared source are appended (in speaker notes) rather than refused.
    slides_input = (
        name == "publish_document"
        and isinstance(value, dict)
        and value.get("slides") is not None
    )
    if name == "publish_document" and isinstance(value, dict):
        # A format no publication writes is named as such: it was refused as
        # "Use Markdown content for DOCX/PDF/XLSX", which the writer could not
        # act on (staging -184, four refusals in a row).
        asked = PurePosixPath(str(value.get("path", ""))).suffix.lower()
        if asked and asked not in PUBLISHED_SUFFIXES:
            raise ValueError(
                UNSUPPORTED_FORMAT.format(
                    suffix=asked, formats=", ".join(PUBLISHED_SUFFIXES)
                )
            )
    if name == "publish_document" and text_output(value):
        with explained(TextPublish):
            text_args = TextPublish.model_validate(value).model_dump(exclude_none=True)
        await tools.execution.check()
        return await publish(tools, text_args, timeout)
    missing: list[str] = []
    if name == "publish_document":
        value = normalize(value)
        if isinstance(value, dict):
            # Before validation adapts the text: what the format cannot draw.
            from services.orchestrator.deliverable_check import left_out

            missing = left_out(value.get("content"))
    with explained(SCHEMAS[name]):
        args = SCHEMAS[name].model_validate(value).model_dump(exclude_none=True)
    await tools.execution.check()
    if name == "publish_document":
        return await publish(
            tools,
            args,
            timeout,
            append_sources=markdown_input or slides_input,
            left_out=missing,
        )
    if name == "organize_files":
        return dict(
            await tools.execution.broker.call(
                "organize", dict(tools.execution.fence(), **args)
            )
        )
    if name == "compare_documents":
        payload = dict(
            left=await resolve(
                tools.execution,
                args["left"],
                tools.deliverables if isinstance(tools.deliverables, list) else [],
            ),
            right=await resolve(
                tools.execution,
                args["right"],
                tools.deliverables if isinstance(tools.deliverables, list) else [],
            ),
        )
    else:
        payload = await resolve(
            tools.execution,
            args,
            tools.deliverables if isinstance(tools.deliverables, list) else [],
        )
    result = await tools.workspace.invoke(name, payload, timeout)
    await tools.execution.check()
    return dict(result)


def text_output(value: Any) -> bool:
    return (
        isinstance(value, dict)
        and isinstance(value.get("path"), str)
        and PurePosixPath(value["path"]).suffix.lower() in TEXT_MIMES
    )


def text_document(text: str, suffix: str) -> Any:
    """Verify a text deliverable locally; its bytes are also its preview.

    The file is the text the writer sent: nothing is appended. The excerpts
    of its declared sources (the person's own files) used to be appended as a
    Sources section, or a CSV or HTML file without them was refused: either
    way the person's notes came back to them in a part they did not ask for
    (include_sources). The published version records the source versions it
    was built from (source_version_ids).
    """
    from services.orchestrator.terminal_client import VerifiedDocument

    failure = "Text deliverable verification failed: "
    try:
        visible_text(validate_text(text.encode("utf-8"), suffix), suffix)
    except UnicodeEncodeError:
        raise ValueError(failure + "text_not_utf8") from None
    except TextDeliverableError as error:
        raise ValueError(failure + error.code) from None
    raw = text.encode("utf-8")
    if len(raw) > 1048576:
        raise ValueError(failure + "text_size_limit")
    return VerifiedDocument(raw, raw, hashlib.sha256(raw).hexdigest())


def include_sources(
    content: Content, texts: list[str], *, deck: bool = False
) -> Content:
    """The content with the excerpts of its declared sources kept out of sight.

    The declared sources are files of this session: the person's own notes
    and documents (web pages are cited in the text by their address). They
    are not sources to list back to the person, and no part the person did
    not ask for is added to the file. A Sources slide or section repeated
    the notes a deck or report was made from, broke the size asked (five
    slides asked, six delivered) and pushed a one-page report onto a
    second page filled to 13 % (staging -151, 2026-11-21).

    A deck keeps the excerpts its slides do not quote in the speaker notes of
    its last slide, after the notes already there: the slides stay the ones
    written, the file still holds its source (staging -142: a deck from a
    project file failed the source coverage check). A document, a workbook
    and a PDF report receive nothing: the published version records the
    source versions it was built from (source_version_ids), and the check of
    the file against its sources (deliverable_check, source_faithfulness)
    reads the excerpts from the turn, not from an appendix.
    """
    if not deck:
        return content
    # An excerpt is looked for in the text as displayed and as typed: one
    # quoted with its inline markers ("the **first** clause") is in the file,
    # shown without them.
    rendered = [
        "".join("".join(written).split())
        for written in (content.texts(), content.typed())
    ]
    missing = tuple(
        part[i : i + 4000]
        for text in texts
        if not any("".join(text.split()) in written for written in rendered)
        for part in (" ".join(text.split()),)
        for i in range(0, len(part), 4000)
    )
    if not missing:
        return content
    last = content.sections[-1]
    notes = (*last.notes, *packed(missing))
    if len(notes) > 50:
        notes = packed(notes)
    return Content(
        title=content.title,
        sections=content.sections[:-1]
        + (type(last).model_validate({**last.model_dump(), "notes": notes}),),
        cover=content.cover,
    )


def packed(texts: tuple[str, ...], limit: int = 4000) -> tuple[str, ...]:
    """Excerpts joined into as few notes as their size allows."""
    notes: list[str] = []
    for text in texts:
        if notes and len(notes[-1]) + 1 + len(text) <= limit:
            notes[-1] += " " + text
        else:
            notes.append(text)
    return tuple(notes)


def placed_pictures(tools: Any, content: Content) -> tuple[Content, dict[str, bytes]]:
    """The content with its picture references as file names, and their bytes.

    A section's images are references returned by make_pictures in this
    conversation (work_pictures.py); anything else is refused by name.
    """
    references = [image for section in content.sections for image in section.images]
    if not references:
        return content, {}
    from services.orchestrator.work_pictures import resolve

    pictures = {
        reference + ".jpg": resolve(tools, reference)
        for reference in dict.fromkeys(references)
    }
    value = content.model_dump(mode="json")
    for section in value["sections"]:
        section["images"] = [image + ".jpg" for image in section["images"]]
    return Content.model_validate(value), pictures


# The publication itself (approval, upload, reopening, preview) keeps this
# much of the tool's time: the checks share the rest. Staging -184: checks run
# one after the other used the time to the end, and a rich deck came back with
# no file at all after 4 min 15 s.
PUBLISH_RESERVE_SECONDS = 25.0
# A refusal asks for one more writing call and one more publication: it is
# made only when the turn still has that time, with this margin.
REPAIR_MARGIN_SECONDS = 15.0


def repair_fits(tools: Any, publication_seconds: float) -> bool:
    """Whether the turn has time left for the repair a refusal would ask.

    The repair is the writing call again (as long as the one that wrote this
    file, loop.turn_time) and this publication again. Without both measures
    (outside a Work turn) a refusal stays possible, as before.
    """
    deadline = getattr(tools, "turn_deadline", None)
    writing = getattr(tools, "writing_seconds", None)
    if not isinstance(deadline, (int, float)) or not isinstance(writing, (int, float)):
        return True
    needed = float(writing) + max(0.0, publication_seconds) + REPAIR_MARGIN_SECONDS
    return float(deadline) - time.monotonic() >= needed


def keep_for_delivery(
    tools: Any, publication_seconds: float, revision: dict[str, Any] | None = None
) -> bool:
    """Publish this file with its gaps named when no repair fits in the turn.

    A refusal with no time left to repair ended the turn on its time limit
    with nothing delivered (staging -184: deck, three-page PDF, SVG file). The
    gaps still reach the answer (deliverable_check.decide's note): the file
    is delivered and what it lacks is said. Returns True when it decided so.

    A new version identical to the current one is never kept this way: the
    person already has that file, so delivering it again delivers nothing,
    and the answers announced it as the change asked (staging -185, class
    suite: corrections and complaints on decks and PDFs came back byte for
    byte the same, three turns in a row). It is refused once, as before.
    """
    if getattr(tools, "deliverable_refused", False) is True:
        return False
    if isinstance(revision, dict) and revision.get("identical"):
        return False
    if repair_fits(tools, publication_seconds):
        return False
    tools.deliverable_refused = True
    logging.getLogger(__name__).warning(
        json.dumps(
            {
                "event": "repair_skipped",
                "reason": "turn_time",
                "publication_seconds": round(publication_seconds, 1),
                "writing_seconds": round(
                    float(getattr(tools, "writing_seconds", 0) or 0), 1
                ),
            }
        )
    )
    return True


async def publish(
    tools: Any,
    args: dict[str, Any],
    timeout: float,
    *,
    append_sources: bool = False,
    left_out: list[str] | None = None,
) -> dict[str, Any]:
    from services.orchestrator.presentation_delivery import build_project_document
    from services.orchestrator.terminal_client import ProjectPublisher
    from services.orchestrator.file_results import project_deliverable_links
    from services.retrieval.project_identity import ProjectError

    began = time.monotonic()
    deadline = began + timeout
    # The checks end here: the rest of the tool's time is the publication's.
    checks_end = deadline - PUBLISH_RESERVE_SECONDS

    def remaining() -> float:
        seconds = deadline - time.monotonic()
        if seconds <= 0:
            raise TimeoutError("document_tool_timeout")
        return seconds

    path = PurePosixPath(args["path"])
    path = path.with_suffix(path.suffix.lower())
    if (
        path.is_absolute()
        or any(part in {"", ".", ".."} for part in args["path"].split("/"))
        or "\\" in args["path"]
        or path.suffix not in {".docx", ".xlsx", ".pptx", ".pdf", *TEXT_MIMES}
    ):
        raise ValueError("Invalid deliverable path or format")
    wanted = getattr(tools, "requested_suffix", None)
    if wanted and path.suffix != wanted and not tools.deliverables:
        # The request names one office format: the first file delivered is that one.
        raise ValueError(
            f"The request asks for a {wanted} file: call publish_document with a "
            f"path ending in {wanted} (not {path.suffix})."
        )

    calculated = (
        # Against the request, before anything is fetched or rendered.
        calculation_check(tools, Content.model_validate(args["content"]))
        if path.suffix == ".xlsx"
        else ""
    )

    execution = tools.execution
    status, project = await execution.broker.public("GET", "")
    if status != 200:
        raise ProjectError(status, str(project.get("error", "not_found")))
    status, folders = await execution.broker.public("GET", "folders")
    if status != 200:
        raise ProjectError(status, str(folders.get("error", "not_found")))
    folder = next(
        (f for f in folders["items"] if f["id"] == project["deliverables_folder_id"]),
        None,
    )
    if folder is None:
        raise ProjectError(404, "not_found")

    source_sheets: list[list[dict[str, Any]]] = []
    source_texts: list[str] = []
    versions: list[str] = []
    receipts = tools.deliverables if isinstance(tools.deliverables, list) else []
    # A researched document listed its web pages as sources, and the whole
    # publication was refused (staging corpus 2026-11-12, twice in one run). A
    # source not written as file identifiers (a web address, a file name) is
    # ignored and named in the result; the pages stay cited in the text. File
    # identifiers are resolved and verified as before: a file outside this
    # session is still refused.
    declared = list(args["sources"])
    sources = [source for source in declared if file_reference(source)]
    ignored = len(declared) - len(sources)
    for source in sources:
        resolved = await resolve(execution, source, receipts)
        read = await tools.workspace.invoke("read_document", resolved, remaining())
        await execution.check()
        if read.get("error"):
            raise ValueError(str(read["error"]))
        if not read["coverage"]["complete"]:
            raise ValueError("Source coverage is incomplete; select a smaller range")
        texts = [
            segment["text"] for segment in read["segments"] if segment["text"].strip()
        ]
        if not texts:
            raise ValueError("Source selection contains no verifiable text")
        if resolved["extension"] == ".xlsx":
            source_sheets.append(read["segments"])
        source_texts.extend(texts)
        versions.append(resolved["version"])

    pictures: dict[str, bytes] = {}
    if path.suffix in TEXT_MIMES:
        document = text_document(args["content"], path.suffix)
        content = None
    else:
        content = asked_title_set(
            tools,
            path,
            kept_title(tools, path, Content.model_validate(args["content"])),
        )
        if append_sources:
            content = include_sources(
                content, source_texts, deck=path.suffix == ".pptx"
            )
        if path.suffix == ".pptx":
            # What the person quoted for the slides is on the slides, not in
            # the speaker notes (slide_parts.py).
            from services.orchestrator import slide_parts

            asked_parts = slide_parts.requested(_asked_requests(tools))
            content = slide_parts.placed(content, asked_parts)
            # Bullet points asked: lists, never cards (expectations.py).
            from services.orchestrator import expectations as asked

            content = asked.bulleted_as_asked(tools, str(path), content)
        content, pictures = placed_pictures(tools, content)
        document = await asyncio.to_thread(
            build_project_document,
            content,
            path.suffix,
            # Written from Markdown or slides, a file keeps its declared
            # sources out of sight (include_sources): only a deck holds them,
            # in its speaker notes. A strict Content object still holds every
            # selected segment.
            source_texts if path.suffix == ".pptx" or not append_sources else [],
            max_bytes=16 * 1024 * 1024,
            timeout=remaining(),
            pictures=pictures,
            compact=kept_compact(tools, path) if path.suffix == ".docx" else 0,
        )
    if (
        content is not None
        and path.suffix == ".xlsx"
        and any(
            table.holds_formulas()
            for section in content.sections
            for table in section.tables
        )
    ):
        from packages.doc_render.spreadsheet import validate_sources

        validate_sources(document.content, source_sheets)
    # The built file against the request, before it is published and claimed
    # (deliverable_check.py): the first gaps of a turn are refused once for a
    # repair, later ones are published with a note the answer must say.
    from services.orchestrator import current_file, deliverable_check
    from services.orchestrator.work_save import project_file_paths

    # A new version of the file this turn changes is compared with its
    # current version (current_file.py): what it removed, replaced or added
    # goes to the check below, which refuses unrequested losses once. In
    # project mode this happens before the approval card, so the person
    # approves a version that was already checked.
    revision: dict[str, Any] | None = None
    delivered: dict[str, dict[str, Any]] = {}
    if getattr(tools, "numbered_copies", False):
        if getattr(tools, "revises_delivered", False):
            revision = await current_file.compared(tools, path.suffix, document.content)
    else:
        delivered = await project_file_paths(execution, str(folder["id"]))
        target = revision_target(
            tools, path, str(folder["id"]), delivered, args["strategy"]
        )
        if target is not None:
            tools.current_file = await current_file.revised(
                tools, str(folder["id"]), target, delivered
            )
            revision = await current_file.compared(tools, path.suffix, document.content)
    # Every requirement the person stated for this file across the
    # conversation, checked on the content it is built from (expectations.py):
    # unmet ones are refused at most twice in a turn, then said by name.
    from services.orchestrator import expectations

    current = getattr(tools, "current_file", None)
    # The ledger's measures of this file (slides, rendered pages), set by
    # document_check: what the closing may say of its length.
    tools.file_measures = None

    def review(built: Any) -> Any:
        return deliverable_check.review(
            tools,
            path.suffix,
            content,
            built.content,
            built.preview,
            list(left_out or ()),
            pictures=pictures if content is not None else {},
            revision=revision,
            name=str(path),
            sources=source_texts,
            seconds=checks_end - time.monotonic(),
        )

    # The check against the request and the ledger of requirements read the
    # same built file: they run side by side, not one after the other (each
    # has its own time limit, both end before the publication's reserve).
    built = document
    reviewing = asyncio.ensure_future(review(built))
    try:
        ledger_results = await expectations.document_check(
            tools,
            str(getattr(current, "name", "") or path)
            if revision is not None
            else str(path),
            path.suffix,
            content,
            deliverable_check.drawn_text(content)
            if content is not None
            else bytes(document.preview).decode("utf-8", "replace"),
            min(
                expectations.EXTRACT_SECONDS
                + expectations.JUDGE_SECONDS
                + expectations.PAGE_SECONDS,
                checks_end - time.monotonic(),
            ),
            raw=document.content,
        )
        if content is not None:
            covered = (
                source_texts if path.suffix == ".pptx" or not append_sources else []
            )
            document, ledger_results = await compacted_to_size(
                tools,
                content,
                path.suffix,
                document,
                ledger_results,
                covered,
                pictures,
                checks_end - time.monotonic(),
            )
            if document is built:
                document = await compacted_tail(
                    tools,
                    content,
                    path.suffix,
                    document,
                    covered,
                    pictures,
                    checks_end - time.monotonic(),
                )
        checked = await reviewing
    except BaseException:
        reviewing.cancel()
        raise
    if document is not built:
        # Laid out compact after the ledger's size check: the version
        # published is checked again, in the time left, as its own check.
        count = getattr(tools, "deliverable_checks", None)
        if isinstance(count, int) and count > 0:
            tools.deliverable_checks = count - 1
        checked = await review(document)
    if path.suffix == ".pptx" and content is not None:
        from services.orchestrator import slide_parts

        absent = slide_parts.missing(document.content, asked_parts)
        if absent:
            checked.gaps = absent + checked.gaps
    # A repair that cannot fit in the turn is not asked: the file is
    # published with its gaps named (keep_for_delivery).
    keep_for_delivery(tools, time.monotonic() - began, revision)
    expectations.document_refusal(tools, ledger_results, checked.gaps)
    ledger_gaps = expectations.document_gaps(tools, ledger_results)
    if ledger_gaps:
        checked.gaps = ledger_gaps + checked.gaps
    lacking = deliverable_check.decide(tools, checked)
    publisher = ProjectPublisher(execution)
    note: dict[str, str] = {}
    if getattr(tools, "numbered_copies", False):
        # The hidden chat project: a taken name becomes a numbered copy, and a
        # folder moved by another conversation's publication is retried.
        from services.orchestrator.chat_file_names import (
            folder_names,
            folder_revision,
            publish_copy,
        )
        from services.orchestrator.work_save import project_file_paths

        folder_id = str(folder["id"])
        existing = await project_file_paths(execution, folder_id)
        revised = await publish_revision(
            tools,
            publisher,
            document,
            path,
            folder_id,
            existing,
            args["strategy"],
            list(dict.fromkeys(versions)),
            deadline,
        )
        if revised is not None:
            receipt, saved = revised
            note["note"] = (
                f"This change of {saved}, delivered earlier in this conversation, "
                f"was saved as its new version (the earlier versions are kept). "
                f"Name it {saved} in the answer, as the updated file."
            )
        else:
            from services.orchestrator.work_save import taken_names

            # A chat has no folders: "deliverables/deck.pptx" (the model naming
            # the folder) is saved and shown as deck.pptx.
            wanted = path.name
            try:
                receipt, saved = await publish_copy(
                    lambda name, revision: publisher.publish(
                        document,
                        name,
                        args["strategy"],
                        list(dict.fromkeys(versions)),
                        revision,
                        str(uuid4()),
                        remaining(),
                    ),
                    wanted,
                    folder_names(existing, folder_id),
                    lambda: folder_revision(execution, folder_id),
                    lambda: taken_names(execution, folder_id),
                )
            except ProjectError as error:
                if "path_conflict" not in error.args:
                    raise
                # Never the same bare refusal again and again (staging
                # 2026-11-21: six path_conflict in a row, then no file).
                raise ValueError(
                    f"path_conflict: no free name was found for {wanted} among "
                    "the chat files after several tries. Publish the same content "
                    "once more with another, more specific file name."
                ) from None
            if saved != wanted:
                note["note"] = (
                    f"{wanted} already exists in the chat files: this file was saved "
                    f"as {saved}. Nothing was overwritten. Name it {saved} in the "
                    "answer."
                )
        # The link names the file actually saved.
        receipt = dict(receipt, relative_path=saved)
    else:
        folder_id = str(folder["id"])
        try:
            revised = await publish_revision(
                tools,
                publisher,
                document,
                path,
                folder_id,
                delivered,
                args["strategy"],
                list(dict.fromkeys(versions)),
                deadline,
            )
        except RevisionNotApproved as refused:
            if refused.path not in tools.not_published:
                tools.not_published.append(refused.path)
            return dict(
                not_published=dict(path=refused.path, reason=refused.reason),
                note=NOT_APPROVED.format(path=refused.path, reason=refused.reason),
            )
        if revised is not None:
            receipt, saved = revised
            receipt = dict(receipt, relative_path=saved)
            note["note"] = (
                f"This change of {saved}, delivered earlier in this project, was "
                f"saved as its new version after the person approved the overwrite "
                f"(the earlier versions are kept). Name it {saved} in the answer, "
                f"as the updated file."
            )
        else:
            receipt = await publish_with_free_name(
                lambda name: publisher.publish(
                    document,
                    name,
                    args["strategy"],
                    list(dict.fromkeys(versions)),
                    folder["revision"],
                    str(uuid4()),
                    remaining(),
                ),
                path,
                args["strategy"],
            )
    if calculated:
        note["note"] = " ".join(filter(None, (note.get("note"), calculated)))
    if lacking:
        note["note"] = " ".join(filter(None, (note.get("note"), lacking)))
    if ignored:
        note["note"] = " ".join(
            filter(None, (note.get("note"), IGNORED_SOURCES.format(count=ignored)))
        )
    expectations.document_saved(tools, str(receipt.get("relative_path") or path))
    length = measured_length(tools, path.suffix, document.content)
    # The figures of the file as built: the only ones the answer may give
    # (file_claims.py).
    from packages.doc_render.figures import measure as measured_figures

    figures = await asyncio.to_thread(
        measured_figures, document.content, path.suffix, length.get("pages")
    )
    # The answer may quote the file it delivers (document_quotes.py).
    from services.orchestrator import document_quotes

    document_quotes.add_built(
        deliverable_check.drawn_text(content)
        if content is not None
        else bytes(document.preview).decode("utf-8", "replace")
    )
    if figures:
        held = getattr(tools, "file_figures", None)
        if not isinstance(held, dict):
            held = {}
            tools.file_figures = held
        held[str(receipt.get("relative_path") or path)] = figures
    tools.file_unverified = bool(getattr(tools, "file_unverified", False)) or not (
        deliverable_check.verification(checked).get("status") == "passed"
    )
    links = project_deliverable_links(
        receipt, execution.broker.org, execution.broker.project
    )
    saved_name = str(receipt.get("relative_path") or path)
    turn_files = getattr(tools, "turn_files", None)
    if not isinstance(turn_files, dict):
        turn_files = {}
        tools.turn_files = turn_files
    turn_files[str(path)] = turn_files[saved_name] = saved_name
    if revised is None:
        # Created by this turn: Atlas's own retries replace it freely.
        created = getattr(tools, "turn_created", None)
        if not isinstance(created, set):
            created = set()
            tools.turn_created = created
        created.add(saved_name)
    # One link per file: a later version of a file of this turn replaces the
    # receipt of the earlier one.
    tools.deliverables[:] = [
        earlier
        for earlier in tools.deliverables
        if not (
            isinstance(earlier, dict) and earlier.get("relative_path") == saved_name
        )
    ]
    tools.deliverables.append(receipt)
    return dict(
        deliverable=receipt,
        text=links,
        file_version_ids=[receipt["file_version_id"]],
        read_input=dict(file_id=receipt["id"], version=receipt["file_version_id"]),
        **note,
        **(dict(gaps=checked.gaps) if lacking else {}),
        # The length measured on the built file: the only one the closing
        # may state (work_closing.py).
        **(dict(length=length) if length else {}),
        # Whether the file was checked against the request and passed: what
        # the plan and the closing may say of it (deliverable_check.py).
        verification=deliverable_check.verification(checked),
    )


def asked_title_set(tools: Any, path: PurePosixPath, content: Content) -> Content:
    """The content titled exactly as the person's message names it.

    "Title it 'Four Days, Full Output'": the title is that text, as written
    (punctuation and case), whatever title the writer sent; a heading that
    repeated the writer's title takes it too. Set by code, never left to a
    rewrite after a refusal (a rewrite kept dropping the comma). A message
    that names no title changes nothing.
    """
    from services.orchestrator.deliverable_check import asked_title

    request = getattr(tools, "request_text", "")
    asked = " ".join(asked_title(request).split()) if isinstance(request, str) else ""
    if not asked or content.title == asked:
        return content
    previous = content.title
    sections = [
        section.model_copy(update=dict(heading=asked))
        if section.heading == previous
        else section
        for section in content.sections
    ]
    titled = Content.model_validate(
        {
            **content.model_dump(),
            "title": asked,
            "sections": [section.model_dump() for section in sections],
        }
    )
    titles = getattr(tools, "built_titles", None)
    if isinstance(titles, dict):
        titles[str(path)] = asked
    return titled


def kept_title(tools: Any, path: PurePosixPath, content: Content) -> Content:
    """The content with the title an earlier attempt of this turn gave the
    file, or the title of its current version.

    A file the check refused is written again in the same turn: a rewrite
    that gives no title of its own (its title is the file name read as
    words, titles.retitled) keeps the title of the attempt before it.
    Staging -156: the rewrite of a report lost "Four Days, Full Output" for
    "Four Days Full Output", and the answer could only report it. A title
    the writer gives is remembered for the next attempt. Staging -170: a
    table added to "The Friday Experiment", saved as a numbered copy, came
    titled "Four_Days_Full_Output (4)": a new version titled by its file
    name keeps the title of the version it changes.
    """
    from packages.doc_render.titles import COPY, in_words

    titles = getattr(tools, "built_titles", None)
    if not isinstance(titles, dict):
        titles = {}
        tools.built_titles = titles
    key = str(path)
    stem = COPY.sub("", path.stem)
    fallback = {in_words(path.stem), path.stem, in_words(stem), stem}
    if content.title not in fallback:
        titles[key] = content.title
        return content
    earlier = titles.get(key)
    current = getattr(tools, "current_file", None)
    current_title = getattr(getattr(current, "document", None), "title", None)
    if (
        not isinstance(earlier, str)
        and PurePosixPath(str(getattr(current, "name", ""))).name == path.name
        and isinstance(current_title, str)
    ):
        earlier = current_title
    if not isinstance(earlier, str) or earlier in fallback:
        return content
    sections = tuple(
        section.model_copy(update=dict(heading=earlier))
        if section.heading == content.title
        else section
        for section in content.sections
    )
    return Content.model_validate(
        {
            **content.model_dump(),
            "title": earlier,
            "sections": [s.model_dump() for s in sections],
        }
    )


def compacted(raw: bytes) -> int:
    """The compact level a DOCX was built with (0 for the usual layout)."""
    try:
        from docx import Document
        from packages.doc_render.renderer import compact_level

        return compact_level(Document(io.BytesIO(raw)))
    except Exception:  # noqa: BLE001 - not a readable report
        return 0


def kept_compact(tools: Any, path: PurePosixPath) -> int:
    """Whether the version this publication follows was laid out compact.

    Staging -166 (2026-11-22): a report laid out compact to fit one page was
    changed in the next turn (a table added); its writer got the room
    measured on the compact print, the new version was built with the usual
    spacing and printed on two pages. A version of a compact file stays
    compact; the room said to its writer is then the room it gets.
    """
    current = getattr(tools, "current_file", None)
    raw = getattr(current, "raw", b"")
    if (
        not isinstance(raw, bytes)
        or not raw
        or getattr(current, "suffix", "") != ".docx"
        or PurePosixPath(str(getattr(current, "name", ""))).name != path.name
    ):
        return 0
    return compacted(raw)


# A report printed over the asked pages by up to this share of a page's
# capacity (about a quarter page) is laid out compact by code, level after
# level, before its text is cut by the writer.
COMPACT_SHARE = 1.25
# Each compact attempt is a build and a print: kept only with this time left.
COMPACT_STEP_SECONDS = 12.0


async def compacted_to_size(
    tools: Any,
    content: Content,
    suffix: str,
    document: Any,
    results: list[Any],
    covered: list[str],
    pictures: dict[str, bytes],
    seconds: float,
) -> tuple[Any, list[Any]]:
    """The document laid out compact when that alone brings it to the pages
    asked, and the ledger results of that version.

    Staging -162/-163 (2026-11-22): a report asked "about one page" printed on
    two (250 words where a page held about 236), and the rewrite after the
    refusal came back on two pages again. The compact report spacing put
    three of four such versions on one page. It is tried first, by code; the
    refusal with its measured target remains for a file that does not fit.
    Staging -170: a table version three lines over one page was already
    compact, and nothing more was tried. The levels are tried in turn, from
    the one after the file's own (renderer.COMPACT_LEVELS: spacing, margins,
    tables, body type down to 10 pt); the text is never cut by code.

    Pictures shrink from the first level (layout.PICTURE_SHARE): a one-page
    PDF asked with two pictures printed on two pages turn after turn, each
    picture 320 points high, and the writer was told to cut its few words.
    A PDF report (its pictures are what its levels change) and a report with
    pictures try the levels whatever the overflow: the pictures can give
    much more than spacing. The pages of a PDF are counted on the PDF built.
    """
    from services.orchestrator import expectations

    turn = getattr(tools, "expectations", None)
    measures = getattr(tools, "file_measures", None)
    pictured = any(section.images for section in content.sections)
    if (
        suffix not in {".docx", ".pdf"}
        or (suffix == ".pdf" and not pictured)
        or seconds < 15
        or not isinstance(turn, expectations.TurnLedger)
        or not isinstance(measures, dict)
    ):
        return document, results
    from packages.doc_render.renderer import COMPACT_LEVELS

    built_level = compacted(document.content) if suffix == ".docx" else 0
    if built_level >= COMPACT_LEVELS:
        return document, results
    failing = []
    for result in results:
        item = turn.ledger.find(result.id)
        if (
            result.passed is False
            and item is not None
            and item.method == "code"
            and item.check.get("part") == "size"
            and item.check.get("unit") == "pages"
        ):
            failing.append(item)
    if not failing:
        return document, results
    words, capacity = measures.get("words"), measures.get("words_per_page")
    size = min(int(item.check["count"]) for item in failing)
    span = measures.get("span_permille")
    # The printed height decides (a table is height with few words); the
    # words when the height was not measured.
    near = (
        span <= size * expectations.FULL_PAGE_SHARE * 1000 * COMPACT_SHARE
        if isinstance(span, int) and span > 0
        else bool(words and capacity and words <= capacity * size * COMPACT_SHARE)
    )
    if not pictured and (not words or not near):
        return document, results
    from services.orchestrator.presentation_delivery import build_project_document
    from services.retrieval import deliverable_render

    started = time.monotonic()
    log = logging.getLogger(__name__)
    for level in range(built_level + 1, COMPACT_LEVELS + 1):
        left = seconds - (time.monotonic() - started)
        if left < COMPACT_STEP_SECONDS:
            break
        try:
            compact = await asyncio.to_thread(
                build_project_document,
                content,
                suffix,
                covered,
                max_bytes=16 * 1024 * 1024,
                timeout=min(left / 2, 30),
                pictures=pictures,
                compact=level,
            )
            if suffix == ".pdf":
                total, filled = await asyncio.to_thread(_pdf_pages, compact.content)
            else:
                rendered = await asyncio.to_thread(
                    deliverable_render.render_pages,
                    compact.content,
                    suffix,
                    timeout=min(left / 2, expectations.PAGE_SECONDS),
                )
                total, filled = rendered.total, rendered.filled
        except Exception as error:  # noqa: BLE001 - the version as built stays
            log.warning(
                json.dumps({"event": "compact_failed", "reason": str(error)[:40]})
            )
            return document, results
        new = dict(
            measures,
            pages=total,
            words_per_page=expectations.page_capacity(words, filled),
            span_permille=round(sum(filled) * 1000),
        )
        again = {
            item.id: expectations.size_result(item, content, "", new)
            for item in failing
        }
        fits = all(r.passed for r in again.values())
        log.warning(
            json.dumps(
                {
                    "event": "compact_layout",
                    "level": level,
                    "pages": [measures.get("pages"), total],
                    "fits": fits,
                }
            )
        )
        if not fits:
            continue
        results = [again.get(result.id, result) for result in results]
        tools.file_measures = new
        turn.last_results = results
        expectations._plan_status(str(getattr(tools, "lang", "") or "en"), results)
        return compact, results
    return document, results


# A report whose last printed page holds only the tail of the text (a few
# lines) while the whole fits one page less is laid out compact up to this
# level (spacing, then margins), never smaller type: the person asked no size.
TAIL_LEVELS = 2
# The tail of a last page: at most this share of its height (about seven lines).
TAIL_SHARE = 0.15


async def compacted_tail(
    tools: Any,
    content: Content,
    suffix: str,
    document: Any,
    covered: list[str],
    pictures: dict[str, bytes],
    seconds: float,
) -> Any:
    """The document laid out compact when its print has a nearly empty last
    page that a compact layout removes (the print is measured after each level),
    else the document as built.

    Staging -189 (2026-11-24): a Word report with a title, a table and two
    sections printed on two pages, the second holding three lines: LibreOffice's
    print put the tail of the text after the page foot. The text is never cut
    and the layout stays inside the house style (renderer.COMPACT_LEVELS 1 and
    2). Skipped when the person asked a number of pages (a size requirement),
    asked page breaks, or the turn has no time for a print.
    """
    from services.orchestrator import expectations

    turn = getattr(tools, "expectations", None)
    asked_pages = isinstance(turn, expectations.TurnLedger) and any(
        item.method == "code"
        and item.check.get("part") == "size"
        and item.check.get("unit") == "pages"
        for item in turn.ledger.active()
    )
    if (
        suffix != ".docx"
        or asked_pages
        or seconds < 2 * COMPACT_STEP_SECONDS
        or content.cover
        or any(section.page_break for section in content.sections)
    ):
        return document
    from services.orchestrator.presentation_delivery import build_project_document
    from services.retrieval import deliverable_render

    log = logging.getLogger(__name__)
    started = time.monotonic()

    def tail(filled: tuple[float, ...]) -> bool:
        """A last page of a few lines."""
        # The print is checked after each level: what does not fit is left.
        return len(filled) >= 2 and 0 < filled[-1] <= TAIL_SHARE

    try:
        rendered = await asyncio.to_thread(
            deliverable_render.render_pages,
            document.content,
            suffix,
            timeout=min(seconds / 3, expectations.PAGE_SECONDS),
        )
    except Exception as error:  # noqa: BLE001 - the version as built stays
        log.warning(json.dumps({"event": "tail_failed", "reason": str(error)[:40]}))
        return document
    if not tail(rendered.filled):
        return document
    from packages.doc_render.renderer import COMPACT_LEVELS

    for level in range(compacted(document.content) + 1, TAIL_LEVELS + 1):
        left = seconds - (time.monotonic() - started)
        if left < COMPACT_STEP_SECONDS or level > COMPACT_LEVELS:
            break
        try:
            compact = await asyncio.to_thread(
                build_project_document,
                content,
                suffix,
                covered,
                max_bytes=16 * 1024 * 1024,
                timeout=min(left / 2, 30),
                pictures=pictures,
                compact=level,
            )
            printed = await asyncio.to_thread(
                deliverable_render.render_pages,
                compact.content,
                suffix,
                timeout=min(left / 2, expectations.PAGE_SECONDS),
            )
        except Exception as error:  # noqa: BLE001 - the version as built stays
            log.warning(json.dumps({"event": "tail_failed", "reason": str(error)[:40]}))
            return document
        fits = printed.total < rendered.total
        log.warning(
            json.dumps(
                {
                    "event": "tail_layout",
                    "level": level,
                    "pages": [rendered.total, printed.total],
                    "fits": fits,
                }
            )
        )
        if fits:
            return compact
    return document


def _pdf_pages(raw: bytes) -> tuple[int, tuple[float, ...]]:
    """The pages of a built PDF and the share of each one its text spans."""
    from packages.doc_render import form
    from services.orchestrator.deliverable_check import _units

    total = _units(raw, ".pdf")
    if total is None:
        raise ValueError("unreadable_pdf")
    return total, tuple(form.measure(raw).filled())


def _asked_requests(tools: Any) -> list[str]:
    """The person's requests of this conversation, earlier ones first."""
    earlier = getattr(tools, "earlier_requests", None)
    return [
        item
        for item in [
            *(earlier if isinstance(earlier, (list, tuple)) else ()),
            getattr(tools, "request_text", ""),
        ]
        if isinstance(item, str) and item
    ]


def measured_length(tools: Any, suffix: str, raw: bytes) -> dict[str, int]:
    """The slides of a deck and the rendered pages of a file, as measured.

    Staging -151 (2026-11-21): a report asked "about one page" printed on two
    pages and the answer called it "your one-page Word report". The pages are
    those LibreOffice printed for the size check of the expectations ledger
    (only when the person asked a size in pages); the slides are counted on
    the deck. Nothing unmeasured is given.
    """
    from services.orchestrator.deliverable_check import _units

    measures = getattr(tools, "file_measures", None)
    measures = measures if isinstance(measures, dict) else {}
    length = {
        unit: value
        for unit in ("slides", "pages")
        if isinstance(value := measures.get(unit), int)
        and not isinstance(value, bool)
        and value > 0
    }
    if suffix == ".pptx" and "slides" not in length:
        slides = _units(raw, suffix)
        if slides:
            length["slides"] = slides
    return length


class RevisionNotApproved(Exception):
    """Project mode: the person did not approve a delivered file's new version."""

    def __init__(self, path: str, reason: str) -> None:
        super().__init__(path, reason)
        self.path, self.reason = path, reason


NOT_APPROVED = (
    "The new version of {path} was not published: the person did not approve "
    "overwriting it ({reason}). Nothing was overwritten and {path} keeps its "
    "current version. Say so in the answer; do not publish this change again "
    "under another name in this turn."
)


def revision_target(
    tools: Any,
    path: PurePosixPath,
    folder_id: str,
    existing: dict[str, dict[str, Any]],
    strategy: str,
) -> str | None:
    """The delivered file this publication is the next version of, or None.

    In the chat, a turn the chat decided continues something delivered
    (revises_delivered) changes the file of the same format its answers
    linked last. In project mode, the model names the file: a regeneration
    or an in-place edit under the path of a delivered file of the project
    is its next version (docs/17, section 12, item 20). A created file, a
    free path or another format is a new file.
    """
    from services.orchestrator.chat_file_names import delivered_file

    # A file this turn already published under this name: the new one is its
    # next version (staging -157: published with a gap, then fixed in the
    # same turn, the chat saved the fix as "Four_Days_Full_Output (2).docx"
    # and the person kept that name for the next turns).
    # In project mode too: a retry of a file created in this turn is its next
    # version, never "-2" (staging -159).
    turn_files = getattr(tools, "turn_files", None)
    if isinstance(turn_files, dict):
        saved = turn_files.get(str(path))
        if isinstance(saved, str) and folder_id + "/" + saved in existing:
            return saved
    answers = getattr(tools, "earlier_answers", None)
    if getattr(tools, "revises_delivered", False) is True and isinstance(answers, list):
        target = delivered_file(
            existing, folder_id, answers, tools.execution.broker.project, {path.suffix}
        )
        if target is not None:
            return target
    if getattr(tools, "numbered_copies", False) or strategy == "created":
        # The chat project is shared by all of a person's conversations: a
        # taken name there is another conversation's file, never this one.
        return None
    return str(path) if folder_id + "/" + str(path) in existing else None


# The time an approval given on the person's behalf keeps before the deadline.
REVISION_APPROVAL_SECONDS = 3.0


async def publish_revision(
    tools: Any,
    publisher: Any,
    document: Any,
    path: PurePosixPath,
    folder_id: str,
    existing: dict[str, dict[str, Any]],
    strategy: str,
    versions: list[str],
    deadline: float,
) -> tuple[dict[str, Any], str] | None:
    """A change of a delivered file is published as that file's next version;
    None when it is a new file.

    "Change the title", then "add a table" gave three files with three names,
    each one "created" (staging 2026-11-20), while an app changed in the same
    way kept one artifact with a version per change. The turn is a change
    when the chat decided it continues something delivered earlier
    (revises_delivered: the router's follow-up, as for an app); the file is
    the one of the same format the conversation's answers linked last
    (chat_file_names.delivered_file), whatever name the model wrote this time.
    Its overwrite is approved on the person's behalf there (owner decision of
    2026-11-11), the earlier versions stay, and a link already given keeps
    serving its own version. A revision that cannot be saved falls back to a
    new file: the change is delivered either way.

    In project mode the overwrite stays the person's decision (owner decision
    of 2026-11-11): the new version waits for the approval card, and one
    declined or expired raises RevisionNotApproved. Nothing is then
    published, neither as a version nor as a new file.
    """
    from services.orchestrator.chat_file_names import folder_revision
    from services.orchestrator.work_save import approved_overwrite, overwrite_effects
    from services.retrieval.project_identity import ProjectError

    chat = bool(getattr(tools, "numbered_copies", False))
    target = revision_target(tools, path, folder_id, existing, strategy)
    if target is None:
        return None
    created = getattr(tools, "turn_created", None)
    own = isinstance(created, set) and target in created
    if not chat and not own and target in getattr(tools, "not_published", ()):
        # Declined once in this turn: the card is not shown again.
        raise RevisionNotApproved(target, "denied")
    execution = tools.execution
    entry = existing[folder_id + "/" + target]
    try:
        status, file = await execution.broker.public(
            "GET", "files/" + str(entry["file_id"])
        )
        if status != 200:
            raise ProjectError(status, str(file.get("error", "not_found")))
        approval, reason = await approved_overwrite(
            execution,
            tools,
            overwrite_effects(file, document.content),
            # Behind the chat (and for Atlas's own retry) the approval is given
            # at once: 20 s kept for a card nobody answers turned a correction
            # into a numbered copy near the end of a turn (staging -184,
            # "expired"). In project mode the person needs time to answer.
            deadline - (REVISION_APPROVAL_SECONDS if chat or own else 20),
            own=own,
        )
        if approval is None:
            if not chat:
                raise RevisionNotApproved(target, reason.split(":", 1)[0] or "denied")
            raise ProjectError(409, reason.split(":", 1)[0] or "approval_required")
        seconds = deadline - time.monotonic()
        if seconds <= 0:
            raise TimeoutError("document_tool_timeout")
        receipt = await publisher.publish(
            document,
            str(file["relative_path"]),
            "regenerated" if strategy == "created" else strategy,
            versions,
            await folder_revision(execution, folder_id),
            str(uuid4()),
            seconds,
            revision_of=dict(
                file_id=str(file["id"]),
                base_version=str(file["version_id"]),
                approval_id=str(approval["id"]),
            ),
        )
    except ProjectError as error:
        if not chat:
            # An approved version that cannot be saved is reported as it is.
            raise
        logging.getLogger(__name__).warning(
            "chat_file_revision_fallback suffix=%s error=%s",
            path.suffix,
            str(error.args[-1] if error.args else error)[:60],
        )
        return None
    return dict(receipt), str(file["relative_path"])


async def publish_with_free_name(
    publish: Any, path: Any, strategy: str
) -> dict[str, Any]:
    """Publish a new file under the next free numbered name.

    A new file never fails on a name already taken (two conversations asked for
    the same presentation and the second one failed, staging 2026-11-10).
    Nothing is overwritten; regenerations and in-place edits keep their name.
    """
    from services.retrieval.project_identity import ProjectError

    for attempt in range(1, 7):
        name = (
            path
            if attempt == 1
            else path.with_name(f"{path.stem}-{attempt}{path.suffix}")
        )
        try:
            # The receipt names the file saved: the answer's link says it, and
            # a later change of it finds it there (chat_file_names.delivered_file).
            return dict(await publish(str(name)), relative_path=str(name))
        except ProjectError as error:
            if (
                strategy != "created"
                or attempt == 6
                or "path_conflict" not in error.args
            ):
                raise
    raise AssertionError("unreachable")
